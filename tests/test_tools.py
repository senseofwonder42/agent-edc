"""The 12 tools on the snapshot fixture (§ 12.2)."""

from datetime import datetime, timedelta

import oracledb
import pytest

from agent_edc.agent.tools import ALL_TOOLS
from agent_edc.agent.tools._common import NO_SNAPSHOT
from agent_edc.agent.tools.beneficiaries import get_amounts_summary, get_beneficiary, list_beneficiaries
from agent_edc.agent.tools.dossier import get_dossier_note, get_dossier_summary, load_dossier, refresh_dossier
from agent_edc.agent.tools.events import get_event, get_timeline_stats, list_events, search_events
from agent_edc.agent.tools.nomenclature import lookup_nomenclature
from agent_edc.models import Event

from .conftest import DOSSIER_NOTE, EDC_ID

FRENCH_WORDS = ("dossier", "événement", "bénéficiaire", "code", "nomenclature")


def test_injected_arguments_are_hidden_from_the_model():
    """The model must never be asked to supply the snapshot itself."""
    assert len(ALL_TOOLS) == 12
    for tool in ALL_TOOLS:
        schema = tool.tool_call_schema.model_json_schema()
        assert "state" not in schema.get("properties", {})
        assert "tool_call_id" not in schema.get("properties", {})
        assert tool.description and tool.description.strip()
        assert any(word in tool.description.lower() for word in FRENCH_WORDS), tool.name


READ_TOOLS = [
    (get_dossier_summary, {}),
    (get_dossier_note, {}),
    (list_events, {}),
    (search_events, {"requete": "notaire"}),
    (get_event, {"evenement_id": "EVT-1000"}),
    (get_timeline_stats, {}),
    (list_beneficiaries, {}),
    (get_beneficiary, {"beneficiaire_id": "88412"}),
    (get_amounts_summary, {}),
]


@pytest.mark.parametrize(("tool", "kwargs"), READ_TOOLS)
def test_read_tools_guide_when_nothing_is_loaded(tool, kwargs, empty_state):
    assert tool.func(state=empty_state, **kwargs) == NO_SNAPSHOT


# --- charger / rafraichir -------------------------------------------------------------


def test_load_dossier_updates_the_state(empty_state, fake_loader, case_file):
    calls = fake_loader()
    command = load_dossier.func(edc_id="1234567", state=empty_state, tool_call_id="c1")
    assert calls == [EDC_ID]
    assert command.update["edc_id"] == EDC_ID
    assert command.update["snapshot"] is case_file
    assert isinstance(command.update["loaded_at"], datetime)
    message = command.update["messages"][0]
    assert message.tool_call_id == "c1"
    assert message.content.startswith("Dossier 01234567")


def test_load_dossier_invalid_id(empty_state, fake_loader):
    calls = fake_loader()
    command = load_dossier.func(edc_id="123ABC", state=empty_state, tool_call_id="c1")
    assert calls == []
    assert set(command.update) == {"messages"}
    assert "Identifiant invalide : « 123ABC »" in command.update["messages"][0].content


def test_load_dossier_not_found(empty_state, fake_loader):
    fake_loader(None)
    command = load_dossier.func(edc_id=EDC_ID, state=empty_state, tool_call_id="c1")
    assert set(command.update) == {"messages"}
    assert "Aucun dossier E-décès ne porte l'identifiant 01234567" in command.update["messages"][0].content


def test_load_dossier_edc_unreachable_hides_details(empty_state, fake_loader):
    fake_loader(oracledb.DatabaseError("ORA-12154: dsn=SECRETHOST user=scott"))
    command = load_dossier.func(edc_id=EDC_ID, state=empty_state, tool_call_id="c1")
    content = command.update["messages"][0].content
    assert "momentanément inaccessible" in content
    assert "SECRETHOST" not in content and "ORA-" not in content


def test_load_dossier_cache_hit(state, fake_loader):
    calls = fake_loader()
    command = load_dossier.func(edc_id=EDC_ID, state=state, tool_call_id="c1")
    assert calls == []
    assert set(command.update) == {"messages"}
    assert "déjà chargé" in command.update["messages"][0].content


def test_load_dossier_reloads_a_stale_snapshot(state, fake_loader):
    calls = fake_loader()
    state["loaded_at"] = datetime.now() - timedelta(hours=2)
    load_dossier.func(edc_id=EDC_ID, state=state, tool_call_id="c1")
    assert calls == [EDC_ID]


def test_refresh_reports_the_delta(state, fake_loader, case_file):
    fresh = case_file.model_copy(deep=True)
    fresh.events.append(Event(event_id="EVT-9930", date=datetime(2026, 9, 18), label="Alerte — X"))
    fake_loader(fresh)
    command = refresh_dossier.func(state={**state, "snapshot": case_file}, tool_call_id="c1")
    assert command.update["snapshot"] is fresh
    assert command.update["messages"][0].content.startswith(
        "1 nouvel événement depuis le chargement précédent (le plus récent : 18/09/2026 · EVT-9930)."
    )


def test_refresh_without_change(state, fake_loader):
    fake_loader()
    command = refresh_dossier.func(state=state, tool_call_id="c1")
    assert command.update["messages"][0].content.startswith("Aucun nouvel événement")


def test_refresh_failure_keeps_the_previous_snapshot(state, fake_loader):
    fake_loader(oracledb.DatabaseError("boom"))
    command = refresh_dossier.func(state=state, tool_call_id="c1")
    assert set(command.update) == {"messages"}  # snapshot untouched
    assert command.update["messages"][0].content.startswith("La relecture a échoué ; je continue avec")


# --- lecture ------------------------------------------------------------------------------


def test_dossier_note_is_complete(state):
    text = get_dossier_note.func(state=state)
    assert text.startswith("Bloc-note du dossier 01234567 (1 800 caractères) :")
    assert DOSSIER_NOTE in text


def test_dossier_note_empty(state):
    state["snapshot"].dossier.global_comment = None
    assert get_dossier_note.func(state=state) == "Ce dossier n'a pas de bloc-note global."


def test_list_events_pages(state):
    first = list_events.func(state=state, limite=20)
    assert first.startswith("Événements 1 à 20 sur 40.")
    assert "Page suivante : decalage=20." in first
    last = list_events.func(state=state, decalage=20, limite=20)
    assert last.startswith("Événements 21 à 40 sur 40.")
    assert "Page suivante" not in last


def test_list_events_limit_is_capped(state):
    text = list_events.func(state=state, limite=500)
    assert "Limite ramenée à 50." in text


def test_list_events_offset_out_of_bounds(state):
    assert list_events.func(state=state, decalage=500) == (
        "Le dossier compte 40 événements ; le décalage 500 est hors bornes."
    )


def test_list_events_bad_date_is_not_ignored(state):
    assert list_events.func(state=state, date_min="2024-07-14") == (
        "Date illisible : « 2024-07-14 ». Utilise le format JJ/MM/AAAA."
    )


def test_list_events_no_match(state):
    text = list_events.func(state=state, date_min="01/01/2030")
    assert text.startswith("Aucun événement ne correspond (40 événements au total).")
    assert "à partir du 01/01/2030" in text


def test_list_events_filter_header(state):
    assert "(filtre : type « alerte »)" in list_events.func(state=state, type_evenement="alerte")


def test_staleness_line_appears_on_old_snapshots(state):
    state["loaded_at"] = datetime.now() - timedelta(minutes=47)
    assert "Instantané chargé il y a 47 min" in list_events.func(state=state)


def test_search(state):
    text = search_events.func(requete="notaire", state=state)
    assert "« notaire »" in text and "EVT-1002" in text
    assert search_events.func(requete="x", state=state) == "Précise un terme d'au moins deux caractères."


def test_event_detail(state):
    text = get_event.func(evenement_id="EVT-2000", state=state)
    assert text.startswith("Événement EVT-2000")
    assert "Rattaché au bénéficiaire : 88412 (« MARTIN Claire »)" in text
    assert "Commentaire (XML) :\npièce classée au dossier le 03/09." in text
    unknown = get_event.func(evenement_id="EVT-9999", state=state)
    assert unknown.startswith("Aucun événement EVT-9999 dans ce dossier.")


def test_timeline_stats(state):
    text = get_timeline_stats.func(state=state)
    assert text.startswith("40 événements, du 12/03/2024 au")
    assert (
        "Plus long intervalle sans événement : 227 jours, du 04/07/2024 (EVT-1019) au 16/02/2025 (EVT-2000)"
        in text
    )


def test_list_beneficiaries(state):
    text = list_beneficiaries.func(state=state)
    assert text.startswith("3 bénéficiaires (dont 1 présumé) :")
    assert "88412 · MARTIN Claire · payé 30 300,00 € · restant 12 000,00 € · NON SOLDÉ" in text
    assert "88413 · MARTIN Paul · payé 12 000,00 € · restant 0,00 € · soldé" in text
    assert "88414 · (identité inconnue) · présumé · aucun montant" in text


def test_beneficiary_by_name_disambiguation(state):
    text = get_beneficiary.func(beneficiaire_id="MARTIN", state=state)
    assert "correspond à 2 bénéficiaires : 88412 (MARTIN Claire), 88413 (MARTIN Paul)" in text


def test_beneficiary_sheet(state):
    text = get_beneficiary.func(beneficiaire_id="Claire", state=state)
    assert text.startswith("Bénéficiaire 88412 · MARTIN Claire")
    assert "NON SOLDÉ" in text and "Les 10 plus récents" in text
    assert 'beneficiaire_id="88412"' in text


def test_amounts_distinguish_paid_and_no_amount(state):
    text = get_amounts_summary.func(state=state)
    assert "Total payé : 42 300,00 €" in text
    assert "Bénéficiaires non soldés (1 sur 3) :\n  88412 · MARTIN Claire · restant 12 000,00 €" in text
    assert "Bénéficiaires sans aucun montant renseigné (1) :\n  88414 · (identité inconnue) · présumé" in text
    assert "88413" not in text  # fully paid: neither unpaid nor "no amount"
    assert "listeOrdonnancements" in text


def test_nomenclature():
    assert lookup_nomenclature.func(table="type_evenement", code="5") == (
        "Nomenclature des types d'événement · code « 5 » → « Alerte »"
    )
    unknown = lookup_nomenclature.func(table="motif_fin_traitement", code="77")
    assert unknown.startswith(
        "Le code « 77 » n'existe pas dans la nomenclature des motifs de fin de traitement"
    )
    assert "10 codes connus : 1, 2" in unknown
    assert lookup_nomenclature.func(table="type_evenement", code="*").count("→") == 6
