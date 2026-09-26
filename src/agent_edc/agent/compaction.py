"""Optional compaction of long conversations (``history.compaction`` in config.yaml).

Runs at the start of each turn. Once the estimated context exceeds the
threshold, the turns preceding the last ``compaction_keep_turns`` ones are
summarized by the model (reasoning off). The summary goes into the state
next to ``summary_until``; ``messages`` is never cut, only the view sent to
the model shrinks. A failed compaction is logged and ignored: it must never
prevent a manager from getting an answer.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from loguru import logger

from agent_edc.agent.history import estimate_tokens, messages_after, recent_turns_start
from agent_edc.agent.state import AgentState
from agent_edc.config import get_settings
from agent_edc.llm import get_chat_model, without_thinking
from agent_edc.prompts import COMPACTION_PROMPT

#: Longest tool result copied into the compaction transcript.
TRANSCRIPT_TOOL_CHARS = 4000


def render_transcript(messages: Sequence[AnyMessage]) -> str:
    """Render messages as a plain French transcript for the compaction call."""
    lines = []
    for message in messages:
        if isinstance(message, HumanMessage):
            lines.append(f"Gestionnaire : {message.content}")
        elif isinstance(message, AIMessage):
            lines += [f"Assistant → {call['name']}({call['args']})" for call in message.tool_calls]
            if message.content:
                lines.append(f"Assistant : {message.content}")
        elif isinstance(message, ToolMessage):
            content = str(message.content)
            if len(content) > TRANSCRIPT_TOOL_CHARS:
                content = content[:TRANSCRIPT_TOOL_CHARS] + " […]"
            lines.append(f"Résultat de {message.name or 'l’outil'} : {content}")
    return "\n".join(lines)


def _summarize(messages: Sequence[AnyMessage], previous: str | None) -> str:
    earlier = f"\nRésumé déjà établi des échanges antérieurs, à intégrer :\n{previous}\n" if previous else ""
    prompt = COMPACTION_PROMPT.format(previous=earlier, transcript=render_transcript(messages))
    answer = without_thinking(get_chat_model()).invoke(
        [HumanMessage(prompt)], config={"run_name": "compactage", "tags": ["compactage"]}
    )
    return str(answer.content).strip()


def compact_node(state: AgentState) -> dict[str, Any]:
    """Summarize the old turns when the context grows past the threshold (no-op otherwise)."""
    settings = get_settings().history
    if not settings.compaction:
        return {}
    view = messages_after(state["messages"], state.get("summary_until"))
    tokens = estimate_tokens(view)
    if tokens < settings.compaction_trigger_tokens:
        return {}
    cut = recent_turns_start(view, settings.compaction_keep_turns)
    if cut == 0:
        return {}
    old = view[:cut]
    try:
        summary = _summarize(old, state.get("summary"))
    except Exception:  # Compaction is a convenience: never break the conversation.
        logger.opt(exception=True).warning("Compaction failed — history sent unchanged")
        return {}
    if not summary:
        logger.warning("Compaction returned an empty summary — history sent unchanged")
        return {}
    logger.info(
        "Compaction: {} message(s) summarized (~{} tokens) into {} chars", len(old), tokens, len(summary)
    )
    return {"summary": summary, "summary_until": old[-1].id}
