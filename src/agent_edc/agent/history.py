"""What of the conversation history is sent to the model.

Pure functions over a message list. The thread state is never cut: these
functions only build the *view* passed to the model, so Agent Chat UI and a
resumed thread still show the whole conversation. Every cut falls on a
``HumanMessage``, so a tool call is never separated from its ``ToolMessage``.
"""

from __future__ import annotations

from collections.abc import Sequence

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage

#: Rough characters-per-token ratio, used only when the server reported no usage.
CHARS_PER_TOKEN = 3


def turn_starts(messages: Sequence[AnyMessage]) -> list[int]:
    """Indices of the ``HumanMessage`` objects, i.e. where each turn starts."""
    return [index for index, message in enumerate(messages) if isinstance(message, HumanMessage)]


def recent_turns_start(messages: Sequence[AnyMessage], keep_turns: int) -> int:
    """Index where the last ``keep_turns`` turns start (0 when there are not more turns than that)."""
    starts = turn_starts(messages)
    if keep_turns <= 0:
        return len(messages)
    return starts[-keep_turns] if len(starts) > keep_turns else 0


def messages_after(messages: Sequence[AnyMessage], message_id: str | None) -> list[AnyMessage]:
    """The messages following ``message_id`` (all of them when it is None or unknown)."""
    if message_id is not None:
        for index, message in enumerate(messages):
            if message.id == message_id:
                return list(messages[index + 1 :])
    return list(messages)


def stub_old_tool_results(messages: Sequence[AnyMessage], keep_turns: int) -> list[AnyMessage]:
    """Replace the content of tool results older than ``keep_turns`` turns by a short reminder.

    The tools answer from memory, so calling one again costs nothing; keeping
    every past result verbatim would fill the context for no benefit.

    Args:
        messages: The history, oldest first.
        keep_turns: Number of recent turns whose tool results stay intact; 0 keeps everything.

    Returns:
        A new list; the stubbed messages are copies, the originals are untouched.
    """
    if keep_turns <= 0:
        return list(messages)
    cutoff = recent_turns_start(messages, keep_turns)
    tool_names = {
        call["id"]: call["name"]
        for message in messages[:cutoff]
        if isinstance(message, AIMessage)
        for call in message.tool_calls
    }
    view: list[AnyMessage] = []
    for index, message in enumerate(messages):
        if index < cutoff and isinstance(message, ToolMessage):
            name = message.name or tool_names.get(message.tool_call_id, "l'outil")
            reminder = f"[Résultat antérieur retiré du contexte — rappelle « {name} » si besoin.]"
            message = message.model_copy(update={"content": reminder})
        view.append(message)
    return view


def _chars(messages: Sequence[AnyMessage]) -> int:
    return sum(len(str(message.content)) for message in messages)


def estimate_tokens(messages: Sequence[AnyMessage]) -> int:
    """Estimate the context size of the next model call.

    Uses the usage reported by the server on the last model answer (exact for
    the view it was sent), plus a character estimate for what came after.

    Args:
        messages: The view about to be sent (without the system prompt).

    Returns:
        An approximate number of tokens.
    """
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        usage = message.usage_metadata if isinstance(message, AIMessage) else None
        if usage:
            later = messages[index + 1 :]
            return usage["input_tokens"] + usage["output_tokens"] + _chars(later) // CHARS_PER_TOKEN
    return _chars(messages) // CHARS_PER_TOKEN
