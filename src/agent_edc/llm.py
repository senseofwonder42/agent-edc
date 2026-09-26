"""The single chat-model factory of the project (§ 8).

Changing backend means changing these lines only — not the graph, the tools
or the prompts. ``BaseChatModel`` already is the abstraction; no provider
registry on top of it.

PORTED FROM: files_late_fee/src/deces_risk/llm/client.py (build_chat_model only)
PORTED ON:   2026-09-18
DIVERGENCE:  VLMClient and the vision helpers not ported; get_chat_model and
             check_tool_calling added.
"""

from __future__ import annotations

from functools import cache

from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from agent_edc.config import VLMConfig, get_settings


def build_chat_model(config: VLMConfig, *, temperature: float | None = None) -> ChatOpenAI:
    """Build a LangChain chat model backed by the OpenAI-compatible vLLM server.

    Args:
        config: VLM settings (base URL, API key, model name, generation,
            reasoning switch and free ``extra_body`` parameters).
        temperature: Override for ``config.temperature``.

    Returns:
        ChatOpenAI pointed at VLM_BASE_URL.

    Raises:
        ValueError: If VLM_BASE_URL or VLM_MODEL is missing.
    """
    if not config.base_url or not config.model:
        raise ValueError(
            "VLM_BASE_URL (.env) et vlm.model (config.yaml) doivent être renseignés "
            "(serveur vLLM OpenAI-compatible) — voir .env.example et config.yaml."
        )
    return ChatOpenAI(
        model=config.model,
        temperature=config.temperature if temperature is None else temperature,
        max_tokens=config.max_tokens,
        timeout=config.timeout,
        base_url=config.base_url,
        api_key=config.api_key,
        extra_body=config.request_extra_body() or None,
    )


@cache
def get_chat_model() -> BaseChatModel:
    """Return the process-wide chat model built from the environment and ``config.yaml``."""
    return build_chat_model(get_settings().vlm)


def tool_binding_kwargs(config: VLMConfig) -> dict[str, bool]:
    """Extra ``bind_tools`` arguments from the settings (``parallel_tool_calls`` when set)."""
    if config.parallel_tool_calls is None:
        return {}
    return {"parallel_tool_calls": config.parallel_tool_calls}


def without_thinking(model: BaseChatModel) -> Runnable[LanguageModelInput, BaseMessage]:
    """The same model with reasoning switched off (for the auxiliary compaction call).

    ``bind(extra_body=...)`` replaces the model's own ``extra_body``: the
    configured parameters are merged back in.
    """
    body = dict(getattr(model, "extra_body", None) or {})
    template_kwargs = {
        key: value
        for key, value in (body.get("chat_template_kwargs") or {}).items()
        if key != "reasoning_effort"
    }
    body["chat_template_kwargs"] = {**template_kwargs, "enable_thinking": False}
    return model.bind(extra_body=body)


@tool("additionner", parse_docstring=True)
def _add(a: int, b: int) -> int:
    """Additionne deux entiers.

    Args:
        a: Premier entier.
        b: Second entier.
    """
    return a + b


def check_tool_calling(model: BaseChatModel, **bind_kwargs: bool) -> None:
    """Run the full tool-calling round trip required by the agent (§ 8.3).

    Checks that the model emits a structured tool call with valid arguments,
    and that it can read a ``ToolMessage`` back and answer.

    Args:
        model: The chat model to check.
        **bind_kwargs: Extra ``bind_tools`` arguments used by the agent
            (see ``tool_binding_kwargs``), so the server is checked with them.

    Raises:
        AssertionError: With a French explanation of the failing step.
    """
    bound = model.bind_tools([_add], **bind_kwargs)
    question = HumanMessage("Combien font 17 plus 25 ?")
    first = bound.invoke([question])
    assert first.tool_calls, (
        "PAS DE TOOL CALLING — vérifier --enable-auto-tool-choice et --tool-call-parser côté vLLM, "
        "sinon VLM_TOOL_PROTOCOL=json (§ 8.4)"
    )
    call = first.tool_calls[0]
    assert call["name"] == "additionner", f"Nom d'outil altéré : {call['name']!r}"
    assert call["args"] == {"a": 17, "b": 25}, f"Arguments inattendus : {call['args']!r}"
    second = bound.invoke([question, first, ToolMessage("42", tool_call_id=call["id"])])
    assert not second.tool_calls and "42" in str(second.content), (
        "Le modèle ne sait pas lire un ToolMessage (second tour)"
    )
