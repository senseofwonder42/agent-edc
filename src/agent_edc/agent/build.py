"""Assembly of the ReAct graph: a compaction step, two nodes and a loop (§ 4.1). No business logic here."""

from __future__ import annotations

from functools import cache
from typing import Any, Literal
from uuid import uuid4

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from loguru import logger
from pydantic import BaseModel, Field, ValidationError, create_model

from agent_edc.agent.compaction import compact_node
from agent_edc.agent.history import messages_after, stub_old_tool_results
from agent_edc.agent.state import AgentState
from agent_edc.agent.tools import ALL_TOOLS
from agent_edc.config import get_settings
from agent_edc.llm import get_chat_model, tool_binding_kwargs
from agent_edc.observability import build_langfuse_callbacks
from agent_edc.prompts import JSON_PROTOCOL_PROMPT, SUMMARY_SECTION, SYSTEM_PROMPT

TOOL_ERROR_MESSAGE = (
    "L'outil a rencontré une erreur inattendue. Reformule la demande ou utilise un autre outil ; "
    "si le problème persiste, signale-le au gestionnaire."
)


def _tool_error_message(error: Exception) -> str:
    """Last resort: turn an unexpected tool exception into a French message.

    The technical detail goes to the logs, never to the model (§ 10.5).
    """
    logger.opt(exception=error).error("Unexpected tool error")
    return TOOL_ERROR_MESSAGE


@cache
def _tool_invocation_model() -> type[BaseModel]:
    """Constrained-JSON schema of one tool call, generated from the tool list (§ 8.4)."""
    names = tuple(tool.name for tool in ALL_TOOLS)
    return create_model(
        "ToolInvocation",
        __doc__="Fallback protocol: one tool call expressed as constrained JSON.",
        outil=(Literal[names] | None, None),  # type: ignore[valid-type]
        arguments=(dict[str, str | int | None], Field(default_factory=dict)),
        reponse_finale=(str | None, None),
    )


@cache
def _json_protocol_prompt() -> str:
    """The JSON fallback instructions with the tool catalog (names, arguments, descriptions)."""
    lines = []
    for tool in ALL_TOOLS:
        properties = tool.tool_call_schema.model_json_schema().get("properties", {})
        args = ", ".join(f"{name}: {spec.get('description', '')}" for name, spec in properties.items())
        lines.append(f"- {tool.name}({args}) : {tool.description}")
    return JSON_PROTOCOL_PROMPT.format(catalog="\n".join(lines))


def _invoke_json_protocol(
    model: BaseChatModel, system: str, history: list[AnyMessage], config: RunnableConfig
) -> AIMessage:
    """Ask for one constrained-JSON tool call and translate it into a native ``AIMessage``.

    ``ToolNode`` and the graph are unchanged: they receive regular ``tool_calls``.
    The constraint uses the standard OpenAI ``response_format`` (json_schema),
    which vLLM enforces at decoding time — after the reasoning when a
    ``--reasoning-parser`` is set.
    """
    invocation_model = _tool_invocation_model()
    response_format = {
        "type": "json_schema",
        "json_schema": {"name": "ToolInvocation", "schema": invocation_model.model_json_schema()},
    }
    messages = [SystemMessage(system + _json_protocol_prompt()), *history]
    raw = model.bind(response_format=response_format).invoke(messages, config=config)
    try:
        invocation = invocation_model.model_validate_json(str(raw.content))
    except ValidationError:
        logger.warning("JSON protocol: unparseable model output, returned as a plain answer")
        return AIMessage(content=str(raw.content))
    if invocation.outil:
        args = {key: value for key, value in invocation.arguments.items() if value is not None}
        call = {"name": invocation.outil, "args": args, "id": f"call_{uuid4().hex}", "type": "tool_call"}
        return AIMessage(content="", tool_calls=[call])
    return AIMessage(content=invocation.reponse_finale or "")


def agent_node(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Call the model with the tools bound and the system prompt prepended.

    The system prompt is rebuilt every turn and never stored in the history,
    so it can change without invalidating persisted threads (§ 4.2). The
    model sees a lighter view of the history (``agent/history.py``): the
    compaction summary instead of the summarized turns, and a reminder
    instead of old tool results. The state itself is never cut.
    """
    settings = get_settings()
    summary = state.get("summary")
    system = SYSTEM_PROMPT + (SUMMARY_SECTION.format(summary=summary) if summary else "")
    history = stub_old_tool_results(
        messages_after(state["messages"], state.get("summary_until")),
        settings.history.tool_results_keep_turns,
    )
    if settings.vlm.tool_protocol == "json":
        return {"messages": [_invoke_json_protocol(get_chat_model(), system, history, config)]}
    model = get_chat_model().bind_tools(ALL_TOOLS, **tool_binding_kwargs(settings.vlm))
    response = model.invoke([SystemMessage(content=system), *history], config=config)
    return {"messages": [response]}


def trace_tags() -> list[str]:
    """Langfuse tags describing the configuration, to compare runs across settings (§ 10.3)."""
    settings = get_settings()
    tags = [f"modele:{settings.vlm.model}", f"raisonnement:{settings.vlm.reasoning_label}"]
    if settings.vlm.tool_protocol == "json":
        tags.append("repli:json")
    if settings.history.compaction:
        tags.append("compactage:on")
    return tags


def build_agent(checkpointer: BaseCheckpointSaver | None = None) -> CompiledStateGraph:
    """Build and compile the conversational EDC agent.

    Args:
        checkpointer: Persistence for the REPL and tests; left to None under
            the server (Aegra or ``langgraph dev``), which imposes its own (§ 11.2).

    Returns:
        The compiled graph, with the Langfuse callbacks attached when tracing
        is active.
    """
    builder = StateGraph(AgentState)
    builder.add_node("compact", compact_node)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", ToolNode(ALL_TOOLS, handle_tool_errors=_tool_error_message))
    builder.add_edge(START, "compact")
    builder.add_edge("compact", "agent")
    builder.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: END})
    builder.add_edge("tools", "agent")
    graph = builder.compile(checkpointer=checkpointer)
    callbacks = build_langfuse_callbacks(get_settings().langfuse, tags=trace_tags())
    return graph.with_config({"callbacks": callbacks}) if callbacks else graph


#: Exposed to langgraph.json; the platform supplies its own persistence (§ 11.2).
graph = build_agent()
