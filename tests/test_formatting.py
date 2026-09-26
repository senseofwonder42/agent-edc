"""French compact rendering (§ 6.8, § 12.1)."""

from datetime import datetime

from agent_edc.formatting import (
    format_amount,
    format_date,
    format_event_full,
    format_event_line,
    format_int,
    format_staleness,
    format_summary,
    one_line,
)
from agent_edc.models import Event

from .conftest import LONG_NOTE


def test_french_date_and_numbers():
    assert format_date(datetime(2026, 9, 2)) == "02/09/2026"
    assert format_date(None) == "sans date"
    assert format_amount(42300) == "42 300,00 €"
    assert format_amount(0.5) == "0,50 €"
    assert format_int(1842) == "1 842"


def test_empty_fields_are_omitted():
    line, _ = format_event_line(Event(event_id="EVT-1", date=datetime(2026, 9, 2), label="Alerte — X"))
    assert line == "02/09/2026 · EVT-1 · Alerte — X"
    assert "None" not in line


def test_list_line_truncated_at_160():
    text, cut = one_line("a" * 200)
    assert cut and text == "a" * 160 + "…"
    assert one_line("court") == ("court", False)


def test_full_rendering_never_truncates():
    event = Event(
        event_id="EVT-1", date=datetime(2026, 9, 2), details=LONG_NOTE, type_code="6", subtype_code="16"
    )
    rendered = format_event_full(event)
    assert LONG_NOTE in rendered
    assert "codes TYPEEVT=6, SOUSTYPEEVT=16" in rendered


def test_summary(case_file):
    loaded = datetime(2026, 9, 18, 14, 2)
    text = format_summary(case_file, loaded, loaded, 5)
    assert text.startswith("Dossier 01234567 — état « En cours » · réseau CE")
    assert "Événements : 40" in text
    assert "Bénéficiaires : 3 (dont 1 présumé)" in text
    assert "payé 42 300,00 € · restant à payer 12 000,00 €" in text
    assert "« MARTIN Claire » (restant 12 000,00 €)" in text
    assert "1 800 caractères" in text and "bloc_note_dossier" in text
    assert "5 derniers événements :" in text
    assert "Instantané chargé le 18/09/2026 à 14:02 (il y a 0 min)." in text


def test_staleness_line():
    now = datetime(2026, 9, 18, 15, 0)
    assert format_staleness(datetime(2026, 9, 18, 14, 50), now, 30) is None
    warning = format_staleness(datetime(2026, 9, 18, 14, 13), now, 30)
    assert "il y a 47 min" in warning and "rafraichir_dossier" in warning
