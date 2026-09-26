"""Beneficiary and amount tools. Memory only, zero SQL."""

from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from agent_edc.agent.state import AgentState
from agent_edc.agent.tools._common import NO_SNAPSHOT, agent_settings, get_snapshot, with_freshness
from agent_edc.formatting import (
    TRUNCATION_HINT,
    beneficiary_name,
    format_amount,
    format_amounts_summary,
    format_beneficiary_list,
    format_date,
    format_event_lines,
    format_int,
)
from agent_edc.observability import record_tool_metadata, traced_tool
from agent_edc.snapshot import filter_events, find_beneficiaries, has_no_amount, is_unpaid

#: Number of attached events shown in a beneficiary sheet.
BENEFICIARY_EVENTS = 10


@tool("lister_beneficiaires", parse_docstring=True, error_on_invalid_docstring=False)
@traced_tool
def list_beneficiaries(state: Annotated[AgentState, InjectedState]) -> str:
    """Liste tous les bénéficiaires connus du dossier, avec leur statut de
    paiement.

    Un bénéficiaire « présumé » n'a pas encore été qualifié : c'est une étape
    de gestion qui reste à faire. Un bénéficiaire dont le restant à payer est
    supérieur à zéro n'est pas soldé. « (identité inconnue) » signifie qu'aucune
    personne n'est rattachée dans E-décès, ce n'est pas une erreur technique.
    """
    snapshot = get_snapshot(state)
    if snapshot is None:
        return NO_SNAPSHOT
    return with_freshness(state, format_beneficiary_list(snapshot, agent_settings().max_page_size))


@tool("detail_beneficiaire", parse_docstring=True)
@traced_tool
def get_beneficiary(
    beneficiaire_id: str,
    state: Annotated[AgentState, InjectedState],
) -> str:
    """Donne la fiche d'un bénéficiaire : identité, statut, montants, et les
    derniers événements qui le concernent.

    Args:
        beneficiaire_id: Référence du bénéficiaire donnée par
            « lister_beneficiaires » (par exemple « 88412 »). Un nom de famille
            est également accepté s'il ne correspond qu'à un seul bénéficiaire.
    """
    snapshot = get_snapshot(state)
    if snapshot is None:
        return NO_SNAPSHOT
    matches = find_beneficiaries(snapshot, beneficiaire_id)
    if not matches:
        return (
            f"Aucun bénéficiaire « {beneficiaire_id} » dans ce dossier. "
            "Utilise « lister_beneficiaires » pour voir les références disponibles."
        )
    if len(matches) > 1:
        candidates = ", ".join(f"{b.beneficiary_id} ({beneficiary_name(b)})" for b in matches)
        return (
            f"« {beneficiaire_id} » correspond à {len(matches)} bénéficiaires : {candidates}. "
            "Précise la référence."
        )

    beneficiary = matches[0]
    lines = [f"Bénéficiaire {beneficiary.beneficiary_id} · {beneficiary_name(beneficiary)}"]
    lines.append(
        "Statut : bénéficiaire présumé (pas encore qualifié)"
        if beneficiary.presumed
        else "Statut : bénéficiaire"
    )
    if beneficiary.birth_date:
        lines.append(f"Né(e) le {format_date(beneficiary.birth_date)}")
    if has_no_amount(beneficiary):
        lines.append("Montants : aucun montant renseigné (rien d'ordonnancé)")
    else:
        status = "NON SOLDÉ" if is_unpaid(beneficiary) else "soldé"
        lines.append(
            f"Montants : payé {format_amount(beneficiary.amount_paid or 0)} · "
            f"restant {format_amount(beneficiary.amount_remaining or 0)} · {status}"
        )

    events = filter_events(snapshot.events, beneficiary_id=beneficiary.beneficiary_id, order="recent")
    if events:
        shown = events[:BENEFICIARY_EVENTS]
        event_lines, truncated = format_event_lines(shown)
        record_tool_metadata(truncated=truncated)
        lines.append(f"{format_int(len(events))} événements rattachés. Les {len(shown)} plus récents :")
        lines += event_lines
        if truncated:
            lines.append(TRUNCATION_HINT)
        if len(events) > len(shown):
            lines.append(
                f'Suite : « lister_evenements » avec beneficiaire_id="{beneficiary.beneficiary_id}".'
            )
    else:
        lines.append("Aucun événement rattaché.")
    return with_freshness(state, "\n".join(lines))


@tool("synthese_montants", parse_docstring=True, error_on_invalid_docstring=False)
@traced_tool
def get_amounts_summary(state: Annotated[AgentState, InjectedState]) -> str:
    """Donne la situation financière du dossier : total payé, total restant à
    payer, et la liste des bénéficiaires qui ne sont pas intégralement payés.

    Le restant à payer est l'assiette sur laquelle se calculeraient
    d'éventuels intérêts de retard : c'est l'indicateur central pour juger de
    l'urgence d'un dossier.
    """
    snapshot = get_snapshot(state)
    if snapshot is None:
        return NO_SNAPSHOT
    return with_freshness(state, format_amounts_summary(snapshot))
