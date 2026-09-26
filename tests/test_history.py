"""The history view sent to the model: turn boundaries, tool-result stubs, token estimate."""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent_edc.agent.history import (
    estimate_tokens,
    messages_after,
    recent_turns_start,
    stub_old_tool_results,
    turn_starts,
)


def _turn(number: int, tool_output: str = "résultat complet") -> list:
    call_id = f"c{number}"
    return [
        HumanMessage(f"question {number}", id=f"h{number}"),
        AIMessage(
            content="",
            id=f"a{number}",
            tool_calls=[{"name": "resume_dossier", "args": {}, "id": call_id, "type": "tool_call"}],
        ),
        ToolMessage(tool_output, tool_call_id=call_id, id=f"t{number}"),
        AIMessage(f"réponse {number}", id=f"r{number}"),
    ]


HISTORY = _turn(1) + _turn(2) + _turn(3)


def test_turns_start_on_human_messages():
    assert turn_starts(HISTORY) == [0, 4, 8]
    assert recent_turns_start(HISTORY, 1) == 8
    assert recent_turns_start(HISTORY, 2) == 4
    assert recent_turns_start(HISTORY, 5) == 0


def test_old_tool_results_are_stubbed_with_the_tool_name():
    view = stub_old_tool_results(HISTORY, keep_turns=1)
    stubbed = [m for m in view[:8] if isinstance(m, ToolMessage)]
    assert all("rappelle « resume_dossier »" in m.content for m in stubbed)
    assert view[10].content == "résultat complet"
    # Pairing and ids are preserved: no orphan ToolMessage.
    assert [m.tool_call_id for m in view if isinstance(m, ToolMessage)] == ["c1", "c2", "c3"]
    assert [m.id for m in view] == [m.id for m in HISTORY]


def test_stubbing_never_mutates_the_state():
    stub_old_tool_results(HISTORY, keep_turns=1)
    assert HISTORY[2].content == "résultat complet"


def test_keep_turns_zero_disables_stubbing():
    assert stub_old_tool_results(HISTORY, keep_turns=0) == HISTORY


def test_messages_after_summary_point():
    assert messages_after(HISTORY, "r1")[0].id == "h2"
    assert messages_after(HISTORY, None) == HISTORY
    assert messages_after(HISTORY, "inconnu") == HISTORY


def test_estimate_uses_server_usage_then_characters():
    answered = AIMessage(
        "ok", usage_metadata={"input_tokens": 5000, "output_tokens": 20, "total_tokens": 5020}
    )
    assert estimate_tokens([HumanMessage("x"), answered, HumanMessage("a" * 300)]) == 5020 + 100
    assert estimate_tokens([HumanMessage("a" * 300)]) == 100
