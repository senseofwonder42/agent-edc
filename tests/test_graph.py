"""End-to-end ReAct loop on a scripted model — mechanics only, no real LLM (§ 12.3)."""

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from agent_edc.agent.build import TOOL_ERROR_MESSAGE, build_agent
from agent_edc.prompts import SYSTEM_PROMPT

from .conftest import EDC_ID


def _call(name: str, call_id: str, **args) -> AIMessage:
    return AIMessage(
        content="", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}]
    )


def _run(agent, text: str, thread: str = "t1") -> dict:
    return agent.invoke({"messages": [HumanMessage(text)]}, config={"configurable": {"thread_id": thread}})


def test_load_then_answer(fake_model, fake_loader):
    fake_loader()
    fake_model(_call("charger_dossier", "c1", edc_id=EDC_ID), AIMessage("Le dossier est en cours."))
    result = _run(build_agent(), f"Ouvre {EDC_ID}")
    assert result["edc_id"] == EDC_ID
    assert result["snapshot"] is not None and result["loaded_at"] is not None
    tool_message = result["messages"][2]
    assert isinstance(tool_message, ToolMessage) and tool_message.tool_call_id == "c1"
    assert result["messages"][-1].content == "Le dossier est en cours."


def test_three_tools_in_sequence(fake_model, fake_loader):
    fake_loader()
    fake_model(
        _call("charger_dossier", "c1", edc_id=EDC_ID),
        _call("statistiques_chronologie", "c2"),
        _call("bloc_note_dossier", "c3"),
        AIMessage("Synthèse."),
    )
    result = _run(build_agent(), "Ce dossier est-il bien géré ?")
    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert [m.tool_call_id for m in tool_messages] == ["c1", "c2", "c3"]
    assert tool_messages[1].content.startswith("40 événements")
    assert tool_messages[2].content.startswith("Bloc-note du dossier")


def test_read_tool_before_load_guides_the_model(fake_model):
    fake_model(_call("resume_dossier", "c1"), AIMessage("Il faut un identifiant."))
    result = _run(build_agent(), "Résume le dossier")
    assert "Aucun dossier n'est chargé" in result["messages"][2].content


def test_tool_exception_does_not_break_the_conversation(fake_model, fake_loader):
    fake_loader(RuntimeError("bug inattendu"))
    fake_model(_call("charger_dossier", "c1", edc_id=EDC_ID), AIMessage("Désolé."))
    result = _run(build_agent(), f"Ouvre {EDC_ID}")
    assert result["messages"][2].content == TOOL_ERROR_MESSAGE
    assert result["messages"][-1].content == "Désolé."


def test_dossier_not_found_leaves_snapshot_empty(fake_model, fake_loader):
    fake_loader(None)
    fake_model(_call("charger_dossier", "c1", edc_id=EDC_ID), AIMessage("Introuvable."))
    result = _run(build_agent(), f"Ouvre {EDC_ID}")
    assert result.get("snapshot") is None
    assert "Aucun dossier E-décès" in result["messages"][2].content


def test_second_turn_sees_the_snapshot(fake_model, fake_loader):
    calls = fake_loader()
    fake_model(
        _call("charger_dossier", "c1", edc_id=EDC_ID),
        AIMessage("Chargé."),
        _call("synthese_montants", "c2"),
        AIMessage("12 000 € restent à payer."),
    )
    agent = build_agent(checkpointer=InMemorySaver())
    _run(agent, f"Ouvre {EDC_ID}")
    result = _run(agent, "Combien reste-t-il à payer ?")
    assert calls == [EDC_ID]  # Oracle read once for the whole conversation
    assert "Total restant à payer : 12 000,00 €" in result["messages"][-2].content


def test_system_prompt_first_and_not_stored(fake_model):
    model = fake_model(AIMessage("Bonjour."), AIMessage("Encore bonjour."))
    agent = build_agent(checkpointer=InMemorySaver())
    _run(agent, "Bonjour")
    result = _run(agent, "Re-bonjour")
    for received in model.received:
        assert isinstance(received[0], SystemMessage) and received[0].content == SYSTEM_PROMPT
        assert sum(isinstance(m, SystemMessage) for m in received) == 1
    assert not any(isinstance(m, SystemMessage) for m in result["messages"])


def _answer(text: str, input_tokens: int) -> AIMessage:
    usage = {"input_tokens": input_tokens, "output_tokens": 10, "total_tokens": input_tokens + 10}
    return AIMessage(text, usage_metadata=usage)


def test_old_tool_results_are_stubbed_for_the_model_only(fake_model, fake_loader, settings):
    settings("history", tool_results_keep_turns=1)
    fake_loader()
    model = fake_model(_call("charger_dossier", "c1", edc_id=EDC_ID), AIMessage("Chargé."), AIMessage("Oui."))
    agent = build_agent(checkpointer=InMemorySaver())
    _run(agent, f"Ouvre {EDC_ID}")
    result = _run(agent, "Merci ?")
    sent_tool = next(m for m in model.received[-1] if isinstance(m, ToolMessage))
    assert "rappelle « charger_dossier »" in sent_tool.content
    stored_tool = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert stored_tool.content.startswith("Dossier")  # the state keeps the full summary


def test_compaction_summarizes_old_turns_without_cutting_the_state(fake_model, fake_loader, settings):
    settings("history", compaction=True, compaction_trigger_tokens=1000, compaction_keep_turns=1)
    fake_loader()
    model = fake_model(
        _call("charger_dossier", "c1", edc_id=EDC_ID),
        _answer("Chargé.", input_tokens=5000),
        AIMessage("- Dossier 01234567 chargé, 40 événements."),  # the compaction call
        AIMessage("12 000 € restent à payer."),
    )
    agent = build_agent(checkpointer=InMemorySaver())
    first = _run(agent, f"Ouvre {EDC_ID}")
    result = _run(agent, "Combien reste-t-il ?")

    assert result["summary"] == "- Dossier 01234567 chargé, 40 événements."
    assert result["summary_until"] == first["messages"][-1].id
    assert len(result["messages"]) == len(first["messages"]) + 2  # nothing removed from the state
    compaction_prompt = model.received[2][0].content
    assert "Gestionnaire : Ouvre" in compaction_prompt and "Assistant → charger_dossier" in compaction_prompt
    system, *sent = model.received[3]
    assert "Résumé des échanges antérieurs" in system.content and "40 événements" in system.content
    assert [m.content for m in sent] == ["Combien reste-t-il ?"]


def test_compaction_below_threshold_is_a_no_op(fake_model, settings):
    settings("history", compaction=True, compaction_trigger_tokens=100_000, compaction_keep_turns=1)
    fake_model(_answer("Bonjour.", input_tokens=5000), AIMessage("Re."))
    agent = build_agent(checkpointer=InMemorySaver())
    _run(agent, "Bonjour")
    assert not _run(agent, "Re").get("summary")


def test_failed_compaction_does_not_block_the_answer(fake_model, settings, monkeypatch):
    settings("history", compaction=True, compaction_trigger_tokens=1000, compaction_keep_turns=1)

    def boom(*args, **kwargs):
        raise RuntimeError("serveur indisponible")

    monkeypatch.setattr("agent_edc.agent.compaction._summarize", boom)
    fake_model(_answer("Bonjour.", input_tokens=5000), AIMessage("Réponse."))
    agent = build_agent(checkpointer=InMemorySaver())
    _run(agent, "Bonjour")
    result = _run(agent, "Question")
    assert result["messages"][-1].content == "Réponse."
    assert not result.get("summary")


def test_json_fallback_translates_to_tool_calls(fake_model, fake_loader, settings):
    settings("vlm", tool_protocol="json")
    fake_loader()
    model = fake_model(
        AIMessage(
            f'{{"outil": "charger_dossier", "arguments": {{"edc_id": "{EDC_ID}"}}, "reponse_finale": null}}'
        ),
        AIMessage('{"outil": null, "arguments": {}, "reponse_finale": "Dossier chargé."}'),
    )
    result = _run(build_agent(), f"Ouvre {EDC_ID}")
    assert result["edc_id"] == EDC_ID
    assert result["messages"][1].tool_calls[0]["name"] == "charger_dossier"
    assert result["messages"][-1].content == "Dossier chargé."
    assert "Protocole d'appel d'outils" in model.received[0][0].content
