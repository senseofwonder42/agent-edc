"""Pure read functions over an in-memory :class:`CaseFile`.

Filters, sorting, pagination, full-text search and timeline statistics.
Never any SQL here: every tool except the two loaders answers from these
functions (§ 5.2). Results are data; ``formatting.py`` renders them.
"""

from __future__ import annotations

import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Generic, Literal, TypeVar

from agent_edc.models import Beneficiary, CaseFile, Event

T = TypeVar("T")

#: Half-width of the excerpt centered on a search match.
EXCERPT_RADIUS = 80


def fold(text: str) -> str:
    """Case- and accent-insensitive form of ``text`` (NFKD, diacritics stripped)."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


def parse_french_date(value: str) -> date:
    """Parse a ``JJ/MM/AAAA`` date.

    Raises:
        ValueError: If the value is not in the expected format.
    """
    return datetime.strptime(value.strip(), "%d/%m/%Y").date()


def clamp_page(offset: int, limit: int, max_limit: int) -> tuple[int, int]:
    """Bring ``offset`` to ``>= 0`` and ``limit`` into ``[1, max_limit]``."""
    return max(offset, 0), min(max(limit, 1), max_limit)


@dataclass
class Page(Generic[T]):
    """One page of a filtered result."""

    items: list[T]
    total: int
    offset: int

    @property
    def next_offset(self) -> int | None:
        """Offset of the next page, or None when this page is the last one."""
        end = self.offset + len(self.items)
        return end if end < self.total else None


def paginate(items: list[T], offset: int, limit: int) -> Page[T]:
    """Slice ``items`` into a page (arguments assumed already clamped)."""
    return Page(items=items[offset : offset + limit], total=len(items), offset=offset)


def _matches_type(event: Event, type_filter: str) -> bool:
    wanted = type_filter.strip()
    if wanted.isdigit():
        return event.type_code == wanted
    return fold(wanted) in fold(event.label)


def filter_events(
    events: list[Event],
    *,
    date_min: date | None = None,
    date_max: date | None = None,
    type_filter: str | None = None,
    beneficiary_id: str | None = None,
    order: Literal["recent", "ancien"] = "recent",
) -> list[Event]:
    """Filter and order the (chronologically sorted) events.

    Date bounds are inclusive; an undated event never passes a date filter.
    Undated events always come last, whatever the order.

    Args:
        events: Events sorted chronologically, undated last.
        date_min: Keep events on or after this day.
        date_max: Keep events on or before this day.
        type_filter: Event type code (``"5"``) or a fragment of its label.
        beneficiary_id: Keep events attached to this intercalaire.
        order: ``"recent"`` (newest first) or ``"ancien"`` (chronological).

    Returns:
        The filtered, ordered events.
    """
    kept = []
    for event in events:
        if date_min or date_max:
            if event.date is None:
                continue
            if date_min and event.date.date() < date_min:
                continue
            if date_max and event.date.date() > date_max:
                continue
        if type_filter and not _matches_type(event, type_filter):
            continue
        if beneficiary_id and event.intercalaire_ref != beneficiary_id:
            continue
        kept.append(event)
    if order == "recent":
        dated = [e for e in kept if e.date is not None]
        undated = [e for e in kept if e.date is None]
        return dated[::-1] + undated
    return kept


@dataclass
class SearchHit:
    """One event matching a search, with the excerpt centered on the match."""

    event: Event
    field: str
    excerpt: str


def _folded_with_positions(text: str) -> tuple[str, list[int]]:
    """Fold ``text`` and map every folded character back to its source index."""
    folded_chars: list[str] = []
    positions: list[int] = []
    for index, ch in enumerate(text):
        for folded_ch in fold(ch):
            folded_chars.append(folded_ch)
            positions.append(index)
    return "".join(folded_chars), positions


def _excerpt(text: str, start: int, end: int) -> str:
    left = max(start - EXCERPT_RADIUS, 0)
    right = min(end + EXCERPT_RADIUS, len(text))
    snippet = " ".join(text[left:right].split())
    return ("…" if left > 0 else "") + snippet + ("…" if right < len(text) else "")


def search_events(events: list[Event], query: str) -> list[SearchHit]:
    """Find the events whose text contains ``query``, newest first.

    The search ignores case and accents, and looks at the label, the
    bloc-note, the XML comment, the motif and the interlocutor. No fuzzy
    matching: an approximate hit would make citations unverifiable.

    Args:
        events: Events sorted chronologically, undated last.
        query: The word or expression to look for (at least 2 characters).

    Returns:
        One hit per matching event (first matching field), newest first.
    """
    needle = fold(query.strip())
    hits = []
    for event in filter_events(events, order="recent"):
        for field_name in ("details", "comment", "label", "motif", "interlocuteur"):
            text = getattr(event, field_name)
            if not text:
                continue
            folded, positions = _folded_with_positions(text)
            index = folded.find(needle)
            if index < 0:
                continue
            start = positions[index]
            end = positions[index + len(needle) - 1] + 1
            hits.append(SearchHit(event=event, field=field_name, excerpt=_excerpt(text, start, end)))
            break
    return hits


@dataclass
class Gap:
    """A period without any event, bounded by the two events around it."""

    days: int
    before: Event
    after: Event


@dataclass
class TimelineStats:
    """Shape of the timeline of a dossier (dated events only)."""

    dated_count: int
    total_count: int
    first: Event | None = None
    last: Event | None = None
    span_days: int = 0
    by_type: list[tuple[str, int]] = field(default_factory=list)
    mean_gap_days: float | None = None
    gaps: list[Gap] = field(default_factory=list)


def _type_name(event: Event) -> str:
    return event.label.split(" — ")[0]


def timeline_stats(events: list[Event]) -> TimelineStats:
    """Compute volume, span, type breakdown and gaps (longest first).

    Args:
        events: Events sorted chronologically, undated last.

    Returns:
        The statistics; ``gaps`` holds every gap between consecutive dated
        events, sorted from the longest to the shortest.
    """
    dated = [e for e in events if e.date is not None]
    stats = TimelineStats(
        dated_count=len(dated),
        total_count=len(events),
        by_type=Counter(_type_name(e) for e in events).most_common(),
    )
    if not dated:
        return stats
    stats.first, stats.last = dated[0], dated[-1]
    stats.span_days = (dated[-1].date.date() - dated[0].date.date()).days
    if len(dated) > 1:
        stats.mean_gap_days = stats.span_days / (len(dated) - 1)
        stats.gaps = sorted(
            (
                Gap(days=(after.date.date() - before.date.date()).days, before=before, after=after)
                for before, after in zip(dated, dated[1:], strict=False)
            ),
            key=lambda gap: gap.days,
            reverse=True,
        )
    return stats


def find_event(case_file: CaseFile, event_id: str) -> Event | None:
    """Return the event with this reference, or None."""
    wanted = event_id.strip()
    return next((e for e in case_file.events if e.event_id == wanted), None)


def find_beneficiaries(case_file: CaseFile, reference_or_name: str) -> list[Beneficiary]:
    """Resolve a beneficiary by reference, or else by a (case/accent-insensitive) name.

    Returns:
        A one-element list on an exact reference, every beneficiary whose
        name contains the query otherwise (possibly none, possibly several).
    """
    wanted = reference_or_name.strip()
    by_ref = [b for b in case_file.beneficiaries if b.beneficiary_id == wanted]
    if by_ref or not wanted:
        return by_ref
    needle = fold(wanted)
    return [b for b in case_file.beneficiaries if b.full_name and needle in fold(b.full_name)]


def events_per_beneficiary(case_file: CaseFile) -> Counter[str]:
    """Number of events attached to each intercalaire reference."""
    return Counter(e.intercalaire_ref for e in case_file.events if e.intercalaire_ref)


def is_unpaid(beneficiary: Beneficiary) -> bool:
    """True when a positive amount remains to be paid."""
    return (beneficiary.amount_remaining or 0) > 0


def has_no_amount(beneficiary: Beneficiary) -> bool:
    """True when nothing was ever ordered — distinct from "fully paid" (§ 6.5)."""
    return beneficiary.amount_paid is None and beneficiary.amount_remaining is None


def new_events(previous: CaseFile, current: CaseFile) -> list[Event]:
    """Events of ``current`` absent from ``previous`` (by ``event_id``), newest first."""
    known = {e.event_id for e in previous.events}
    return [e for e in filter_events(current.events, order="recent") if e.event_id not in known]
