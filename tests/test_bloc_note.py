"""§ 7.6 — reassembly of truncated bloc-notes. Pure functions, no Oracle."""

from pathlib import Path

from agent_edc.agent.tools.events import get_event, list_events
from agent_edc.edc.normalizers import (
    get_xml_attribute,
    normalize_dossier,
    normalize_edc_event,
    parse_info_spec,
)
from agent_edc.formatting import format_event_full, format_event_line

from .conftest import LONG_NOTE

FIXTURES = Path(__file__).parent / "fixtures"


def _infospec(**nodes: str) -> str:
    inner = "".join(f'<string nom="{name}">{value}</string>' for name, value in nodes.items())
    return f'<vo nom="racine">{inner}</vo>'


def _row(note: str | None, infospec: str | None = None) -> dict:
    return {"REFEVT": 1, "TYPEEVT": "6", "SOUSTYPEEVT": "16", "BLOCNOTEEVT": note, "INFOSPECEVT": infospec}


def test_long_note_with_overflow():
    base = ("x" * 244) + "…doit confir"
    assert len(base) == 256
    overflow = "mer la dévolution."
    event = normalize_edc_event(_row(base, _infospec(blocNoteExtensible=overflow)))
    assert "doit confirmer la dévolution." in event.details
    assert ". mer" not in event.details
    assert len(event.details) == len(base) + len(overflow)


def test_long_note_without_overflow():
    base = "  Note longue, avec espaces de tête et de fin.\n" * 8
    event = normalize_edc_event(_row(base, _infospec(motif="3")))
    assert event.details == base


def test_overflow_without_base():
    overflow = "mer la dévolution."
    event = normalize_edc_event(_row(None, _infospec(blocNoteExtensible=overflow)))
    assert event.details == overflow


def test_neither_present():
    for note in (None, "", "   "):
        assert normalize_edc_event(_row(note, _infospec(motif="3"))).details is None
        assert normalize_edc_event(_row(note)).details is None


def test_no_length_heuristic():
    base = "Note courte, coupée "
    assert len(base) == 20
    event = normalize_edc_event(_row(base, _infospec(blocNoteExtensible="quand même.")))
    assert event.details == "Note courte, coupée quand même."


def test_comment_stays_separate():
    note = "Le notaire a été relancé."
    event = normalize_edc_event(_row(note, _infospec(commentaire="pièce classée.")))
    assert event.details == note
    assert event.comment == "pièce classée."
    rendered = format_event_full(event)
    assert "Bloc-note :\nLe notaire a été relancé.\nCommentaire (XML) :\npièce classée." in rendered
    assert "relancé. pièce" not in rendered
    assert ". ".join([note, "pièce classée."]) not in rendered


def test_automatic_event_sentinel():
    assert normalize_edc_event(_row("Evénement Automatique")).details is None
    event = normalize_edc_event(_row("Evénement Automatique", _infospec(blocNoteExtensible=" et sa suite")))
    assert event.details == "Evénement Automatique et sa suite"


def test_newlines_are_preserved():
    note = "02/09 : appel du notaire.\n05/09 : relance écrite."
    event = normalize_edc_event(_row(note))
    assert "\n" in event.details
    line, _ = format_event_line(event)
    assert "\n" not in line


def test_dossier_note_from_sql():
    stitched = "Début de la note coupée au milieu d'un mo" + "t, puis la suite."
    assert normalize_dossier("01234567", {"BLOC_NOTE": stitched}).global_comment == stitched
    assert normalize_dossier("01234567", {"BLOC_NOTE": None}).global_comment is None
    assert normalize_dossier("01234567", {"BLOC_NOTE": ""}).global_comment is None


def test_real_xml_payload():
    xml = (FIXTURES / "infospec_evt.xml").read_text(encoding="utf-8")
    element = parse_info_spec(xml)
    assert get_xml_attribute(element, "blocNoteExtensible") == "mer la dévolution avant ordonnancement."
    assert get_xml_attribute(element, "commentaire") == "pièce classée au dossier le 03/09."
    event = normalize_edc_event(_row("…le notaire doit confir", xml))
    assert event.details == "…le notaire doit confirmer la dévolution avant ordonnancement."
    assert event.comment == "pièce classée au dossier le 03/09."


def test_full_note_reaches_the_tool(state):
    detail = get_event.func(evenement_id="EVT-2000", state=state)
    assert LONG_NOTE in detail
    listing = list_events.func(state=state, date_min="16/02/2025", date_max="16/02/2025")
    assert "doit confir" not in listing  # the long note is cut before the stitch point
    assert "…" in listing
    assert "detail_evenement" in listing
