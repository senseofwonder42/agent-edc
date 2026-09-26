"""Compact French rendering of a case file and its fragments.

This module is the "token budget" contract (§ 6.8): every text the model
reads comes from here. Empty fields are omitted, dates are ``JJ/MM/AAAA``,
amounts are ``42 300,00 €``. Lists truncate event details to one line of
160 characters; the full renderings (``format_event_full``, the dossier
note) never truncate anything (§ 7.1).
"""

from __future__ import annotations

from datetime import datetime

from agent_edc.models import Beneficiary, CaseFile, Event
from agent_edc.snapshot import (
    SearchHit,
    TimelineStats,
    events_per_beneficiary,
    filter_events,
    has_no_amount,
    is_unpaid,
)

#: Maximum length of an event detail in a list line.
LIST_DETAIL_CHARS = 160

#: Gaps longer than this are listed by the timeline statistics…
NOTABLE_GAP_DAYS = 60
#: …up to this many (besides the longest one).
MAX_NOTABLE_GAPS = 5

#: Above this many events, the summary advises targeted tools (§ 14, risk 4).
LARGE_DOSSIER_EVENTS = 1000

UNKNOWN_IDENTITY = "(identité inconnue)"


def format_date(value: datetime | None) -> str:
    """``JJ/MM/AAAA``, or ``sans date``."""
    return value.strftime("%d/%m/%Y") if value else "sans date"


def format_int(value: int) -> str:
    """Integer with French thousands separators: ``1 842``."""
    return f"{value:,}".replace(",", " ")


def format_amount(value: float | None) -> str:
    """French euro amount: ``42 300,00 €``."""
    if value is None:
        return "—"
    return f"{value:,.2f}".replace(",", " ").replace(".", ",") + " €"


def format_age(loaded_at: datetime, now: datetime) -> str:
    """Human age of a snapshot: ``il y a 4 min``, ``il y a 3 h 12 min``, ``il y a 2 jours``."""
    minutes = max(int((now - loaded_at).total_seconds() // 60), 0)
    if minutes < 60:
        return f"il y a {minutes} min"
    if minutes < 24 * 60:
        return f"il y a {minutes // 60} h {minutes % 60:02d} min"
    days = minutes // (24 * 60)
    return f"il y a {days} jour{'s' if days > 1 else ''}"


def one_line(text: str, max_chars: int = LIST_DETAIL_CHARS) -> tuple[str, bool]:
    """Collapse whitespace and cut to ``max_chars``.

    Returns:
        The single-line text (ending with ``…`` when cut) and whether it was cut.
    """
    flat = " ".join(text.split())
    if len(flat) <= max_chars:
        return flat, False
    return flat[:max_chars] + "…", True


def beneficiary_name(beneficiary: Beneficiary) -> str:
    """Display name, or the explicit "unknown identity" marker."""
    return beneficiary.full_name or UNKNOWN_IDENTITY


def format_event_line(event: Event, excerpt: str | None = None) -> tuple[str, bool]:
    """One list line: ``date · référence · libellé · [motif] · [interlocuteur] · « détail »``.

    Args:
        event: The event.
        excerpt: Text to quote instead of the start of the detail (search hits).

    Returns:
        The line and whether the quoted detail was truncated.
    """
    parts = [format_date(event.date), event.event_id, event.label]
    parts += [value for value in (event.motif, event.interlocuteur) if value]
    truncated = False
    if excerpt is not None:
        parts.append(f"« {excerpt} »")
    elif event.details:
        detail, truncated = one_line(event.details)
        parts.append(f"« {detail} »")
    return " · ".join(parts), truncated


def format_event_lines(events: list[Event]) -> tuple[list[str], bool]:
    """Indented list lines, and whether any of them was truncated."""
    lines, any_truncated = [], False
    for event in events:
        line, truncated = format_event_line(event)
        lines.append(f"  {line}")
        any_truncated |= truncated
    return lines, any_truncated


TRUNCATION_HINT = "Les détails suivis de « … » sont tronqués : texte entier via « detail_evenement »."


def format_event_full(event: Event, beneficiary: Beneficiary | None = None) -> str:
    """The complete event block — nothing is ever truncated here.

    The raw codes are shown next to the label so the agent can explain a
    code without inventing it (§ 9.5). The XML comment gets its own labelled
    block, never a ``". "`` join with the bloc-note (§ 7.4).
    """
    lines = [f"Événement {event.event_id}"]
    dates = f"Date de création : {format_date(event.date)}"
    if event.update_date:
        dates += f" · dernière mise à jour : {format_date(event.update_date)}"
    lines.append(dates)
    codes = []
    if event.type_code:
        codes.append(f"TYPEEVT={event.type_code}")
    if event.subtype_code:
        codes.append(f"SOUSTYPEEVT={event.subtype_code}")
    lines.append(f"Type : {event.label}" + (f"  (codes {', '.join(codes)})" if codes else ""))
    lines.append(f"Interlocuteur : {event.interlocuteur or '—'}")
    lines.append(f"Motif : {event.motif or '—'}")
    if event.intercalaire_ref:
        attached = f"Rattaché à l'intercalaire : {event.intercalaire_ref}"
        if beneficiary is not None:
            name = beneficiary_name(beneficiary)
            attached = f"Rattaché au bénéficiaire : {beneficiary.beneficiary_id} (« {name} »)"
        lines.append(attached)
    lines.append("Bloc-note :")
    lines.append(event.details if event.details else "(aucun)")
    if event.comment:
        lines.append("Commentaire (XML) :")
        lines.append(event.comment)
    return "\n".join(lines)


def format_beneficiary_line(beneficiary: Beneficiary, event_count: int) -> str:
    """``88412 · MARTIN Claire · payé … · restant … · NON SOLDÉ · 24 événements rattachés``."""
    parts = [beneficiary.beneficiary_id, beneficiary_name(beneficiary)]
    if beneficiary.presumed:
        parts.append("présumé")
    if has_no_amount(beneficiary):
        parts.append("aucun montant")
    else:
        parts.append(f"payé {format_amount(beneficiary.amount_paid or 0)}")
        parts.append(f"restant {format_amount(beneficiary.amount_remaining or 0)}")
        parts.append("NON SOLDÉ" if is_unpaid(beneficiary) else "soldé")
    parts.append(_count(event_count, "événement rattaché", "événements rattachés"))
    return " · ".join(parts)


def _count(n: int, singular: str, plural: str) -> str:
    if n == 0:
        return f"aucun {singular}"
    return f"{format_int(n)} {singular if n == 1 else plural}"


def format_summary(case_file: CaseFile, loaded_at: datetime, now: datetime, summary_events: int) -> str:
    """The dossier sheet returned by ``charger_dossier`` and ``resume_dossier`` (§ 5.4)."""
    dossier = case_file.dossier
    lines = []
    head = f"Dossier {case_file.edc_id}"
    if dossier and dossier.etat:
        etat = dossier.etat + (f" / {dossier.etat_precision}" if dossier.etat_precision else "")
        head += f" — état « {etat} »"
    if dossier and dossier.network_label:
        head += f" · réseau {dossier.network_label}"
    lines.append(head)
    if dossier:
        info = []
        if dossier.ref_dossier:
            info.append(f"Référence interne {dossier.ref_dossier}")
        if dossier.creation_date:
            info.append(f"créé le {format_date(dossier.creation_date)}")
        if dossier.effect_date:
            info.append(f"date d'effet {format_date(dossier.effect_date)}")
        if info:
            lines.append(" · ".join(info))

    events = case_file.events
    dated = [e for e in events if e.date is not None]
    if dated:
        last_days = (now.date() - dated[-1].date.date()).days
        lines.append(
            f"Événements : {format_int(len(events))}, du {format_date(dated[0].date)} "
            f"au {format_date(dated[-1].date)} (dernier il y a {last_days} jours)"
        )
    else:
        lines.append(f"Événements : {format_int(len(events))}")
    if len(events) > LARGE_DOSSIER_EVENTS:
        lines.append("Dossier volumineux — privilégie « chercher_evenements » et les filtres de date.")

    beneficiaries = case_file.beneficiaries
    presumed = sum(b.presumed for b in beneficiaries)
    lines.append(
        f"Bénéficiaires : {len(beneficiaries)}"
        + (f" (dont {presumed} présumé{'s' if presumed > 1 else ''})" if presumed else "")
    )
    paid = sum(b.amount_paid or 0 for b in beneficiaries)
    remaining = sum(b.amount_remaining or 0 for b in beneficiaries)
    lines.append(f"Montants : payé {format_amount(paid)} · restant à payer {format_amount(remaining)}")
    unpaid = [b for b in beneficiaries if is_unpaid(b)]
    if unpaid:
        lines.append(
            "Non soldés : "
            + ", ".join(
                f"« {beneficiary_name(b)} » (restant {format_amount(b.amount_remaining)})" for b in unpaid
            )
        )

    note = dossier.global_comment if dossier else None
    if note:
        lines.append(
            f"Bloc-note du dossier : {format_int(len(note))} caractères — lire avec « bloc_note_dossier »."
        )
    else:
        lines.append("Bloc-note du dossier : vide.")

    recent = filter_events(events, order="recent")[:summary_events]
    if recent:
        event_lines, truncated = format_event_lines(recent)
        lines += ["", f"{len(recent)} derniers événements :", *event_lines]
        if truncated:
            lines.append(TRUNCATION_HINT)

    lines += [
        "",
        f"Instantané chargé le {loaded_at.strftime('%d/%m/%Y à %H:%M')} ({format_age(loaded_at, now)}).",
    ]
    return "\n".join(lines)


def format_staleness(loaded_at: datetime | None, now: datetime, stale_after_minutes: int) -> str | None:
    """Freshness warning appended to tool outputs once the snapshot is old (§ 5.5)."""
    if loaded_at is None or (now - loaded_at).total_seconds() < stale_after_minutes * 60:
        return None
    return (
        f"Instantané chargé {format_age(loaded_at, now)} — utilise « rafraichir_dossier » "
        "si la fraîcheur importe."
    )


def format_search_hits(hits: list[SearchHit], query: str, limit: int) -> str:
    """Search result: total count, then the ``limit`` most recent hits with centered excerpts."""
    if not hits:
        return f"Aucun événement ne contient « {query} »."
    shown = hits[:limit]
    head = f"{len(hits)} événement{'s contiennent' if len(hits) > 1 else ' contient'} « {query} »."
    head += f" Les {len(shown)} plus récents :" if len(shown) > 1 else " Le plus récent :"
    lines = [head]
    for hit in shown:
        line, _ = format_event_line(hit.event, excerpt=hit.excerpt)
        lines.append(f"  {line}")
    lines.append("Extraits centrés sur la correspondance : texte entier via « detail_evenement ».")
    return "\n".join(lines)


def format_timeline_stats(stats: TimelineStats, now: datetime) -> str:
    """Timeline shape: volume, span, types, mean interval, longest and notable gaps."""
    if stats.dated_count == 0:
        return "Aucun événement daté ; les statistiques chronologiques ne s'appliquent pas."
    lines = [
        f"{format_int(stats.total_count)} événement{'s' if stats.total_count > 1 else ''}, "
        f"du {format_date(stats.first.date)} au {format_date(stats.last.date)} ({stats.span_days} jours)."
    ]
    if stats.total_count != stats.dated_count:
        lines.append(f"Dont {stats.total_count - stats.dated_count} sans date.")
    lines.append("Répartition par type : " + " · ".join(f"{name} {count}" for name, count in stats.by_type))
    if stats.mean_gap_days is not None:
        mean = f"{stats.mean_gap_days:.1f}".replace(".", ",")
        lines.append(f"Intervalle moyen entre deux événements : {mean} jours.")
    if stats.gaps:
        longest = stats.gaps[0]
        lines.append(f"Plus long intervalle sans événement : {_format_gap(longest)}.")
        others = [g for g in stats.gaps[1:] if g.days > NOTABLE_GAP_DAYS][:MAX_NOTABLE_GAPS]
        if others:
            lines.append(f"Autres intervalles de plus de {NOTABLE_GAP_DAYS} jours :")
            lines += [f"  {_format_gap(g)}" for g in others]
    lines.append(f"Dernier événement il y a {(now.date() - stats.last.date.date()).days} jours.")
    return "\n".join(lines)


def _format_gap(gap) -> str:
    return (
        f"{gap.days} jours, du {format_date(gap.before.date)} ({gap.before.event_id}) "
        f"au {format_date(gap.after.date)} ({gap.after.event_id})"
    )


def format_beneficiary_list(case_file: CaseFile, max_items: int) -> str:
    """Every beneficiary with its payment status and attached-event count."""
    beneficiaries = case_file.beneficiaries
    if not beneficiaries:
        return "Aucun bénéficiaire connu dans ce dossier."
    counts = events_per_beneficiary(case_file)
    presumed = sum(b.presumed for b in beneficiaries)
    head = f"{len(beneficiaries)} bénéficiaire{'s' if len(beneficiaries) > 1 else ''}"
    head += f" (dont {presumed} présumé{'s' if presumed > 1 else ''}) :" if presumed else " :"
    lines = [head]
    lines += [f"  {format_beneficiary_line(b, counts[b.beneficiary_id])}" for b in beneficiaries[:max_items]]
    if len(beneficiaries) > max_items:
        lines.append(f"  … liste coupée : {len(beneficiaries) - max_items} bénéficiaires non affichés.")
    return "\n".join(lines)


def format_amounts_summary(case_file: CaseFile) -> str:
    """Financial situation; distinguishes "fully paid" from "no amount at all" (§ 6.5)."""
    beneficiaries = case_file.beneficiaries
    paid = sum(b.amount_paid or 0 for b in beneficiaries)
    remaining = sum(b.amount_remaining or 0 for b in beneficiaries)
    lines = [f"Total payé : {format_amount(paid)}", f"Total restant à payer : {format_amount(remaining)}"]
    unpaid = [b for b in beneficiaries if is_unpaid(b)]
    if unpaid:
        lines.append(f"Bénéficiaires non soldés ({len(unpaid)} sur {len(beneficiaries)}) :")
        lines += [
            f"  {b.beneficiary_id} · {beneficiary_name(b)} · restant {format_amount(b.amount_remaining)}"
            for b in unpaid
        ]
    else:
        lines.append(f"Aucun bénéficiaire non soldé ({len(beneficiaries)} au total).")
    no_amount = [b for b in beneficiaries if has_no_amount(b)]
    if no_amount:
        lines.append(f"Bénéficiaires sans aucun montant renseigné ({len(no_amount)}) :")
        lines += [
            f"  {b.beneficiary_id} · {beneficiary_name(b)}" + (" · présumé" if b.presumed else "")
            for b in no_amount
        ]
    lines.append(
        "Montants issus des ordonnancements EDC (listeOrdonnancements), convertis des centimes en euros."
    )
    return "\n".join(lines)
