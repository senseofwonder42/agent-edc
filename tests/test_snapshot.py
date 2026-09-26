"""Filters, pagination, search and statistics on the in-memory snapshot (§ 12.1)."""

from datetime import date, datetime

from agent_edc.models import Event
from agent_edc.snapshot import (
    clamp_page,
    filter_events,
    find_beneficiaries,
    paginate,
    search_events,
    timeline_stats,
)


def test_date_bounds_are_inclusive(case_file):
    kept = filter_events(case_file.events, date_min=date(2024, 3, 12), date_max=date(2024, 3, 18))
    assert {e.event_id for e in kept} == {"EVT-1000", "EVT-1001"}


def test_type_filter_by_code_and_label(case_file):
    by_code = filter_events(case_file.events, type_filter="5")
    by_label = filter_events(case_file.events, type_filter="alerte")
    assert by_code and by_code == by_label
    assert all(e.type_code == "5" for e in by_code)


def test_beneficiary_filter(case_file):
    kept = filter_events(case_file.events, beneficiary_id="88413")
    assert kept and all(e.intercalaire_ref == "88413" for e in kept)


def test_order_and_undated_last(case_file):
    recent = filter_events(case_file.events, order="recent")
    oldest = filter_events(case_file.events, order="ancien")
    assert recent[0].event_id == "EVT-2018"
    assert oldest[0].event_id == "EVT-1000"
    assert recent[-1].date is None and oldest[-1].date is None


def test_pagination(case_file):
    events = case_file.events
    first = paginate(events, 0, 20)
    assert len(first.items) == 20 and first.next_offset == 20
    middle = paginate(events, 10, 20)
    assert middle.items[0] is events[10]
    last = paginate(events, 20, 20)
    assert len(last.items) == 20 and last.next_offset is None
    assert paginate(events, 500, 20).items == []


def test_clamp_page():
    assert clamp_page(0, 500, 50) == (0, 50)
    assert clamp_page(0, 0, 50) == (0, 1)
    assert clamp_page(-3, 10, 50) == (0, 10)


def test_search_is_case_and_accent_insensitive(case_file):
    hits = search_events(case_file.events, "NOTAIRE")
    assert {h.event.event_id for h in hits} >= {"EVT-1002", "EVT-2000"}
    assert search_events(case_file.events, "devolution")  # accent-free query finds "dévolution"


def test_search_covers_every_field(case_file):
    assert search_events(case_file.events, "arrivée courrier")  # label
    assert search_events(case_file.events, "attente notaire")  # motif
    assert search_events(case_file.events, "classée au dossier")  # XML comment
    assert search_events(case_file.events, "inexistant-xyz") == []


def test_search_excerpt_is_centered(case_file):
    hit = next(h for h in search_events(case_file.events, "ordonnancement") if h.event.event_id == "EVT-2000")
    assert "ordonnancement" in hit.excerpt
    assert hit.excerpt.startswith("…")
    assert len(hit.excerpt) <= 2 * 80 + len("ordonnancement") + 2


def test_search_excerpt_keeps_original_accents():
    event = Event(event_id="1", date=datetime(2024, 1, 1), details="Œuvre de l'Étude — déjà reçue")
    hit = search_events([event], "etude")[0]
    assert "Étude" in hit.excerpt


def test_timeline_stats(case_file):
    stats = timeline_stats(case_file.events)
    assert stats.total_count == 40 and stats.dated_count == 39
    longest = stats.gaps[0]
    assert (longest.before.event_id, longest.after.event_id) == ("EVT-1019", "EVT-2000")
    assert longest.days > 200
    assert stats.mean_gap_days is not None
    assert dict(stats.by_type)["Evénement standard"] > 0


def test_timeline_stats_edge_cases():
    assert timeline_stats([]).dated_count == 0
    single = timeline_stats([Event(event_id="1", date=datetime(2024, 1, 1))])
    assert single.dated_count == 1 and single.gaps == [] and single.mean_gap_days is None


def test_find_beneficiaries(case_file):
    assert [b.beneficiary_id for b in find_beneficiaries(case_file, "88412")] == ["88412"]
    assert len(find_beneficiaries(case_file, "martin")) == 2
    assert [b.beneficiary_id for b in find_beneficiaries(case_file, "Claire")] == ["88412"]
    assert find_beneficiaries(case_file, "DUPONT") == []
