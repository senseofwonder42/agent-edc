"""Dossier tools: load, refresh, summary, global bloc-note.

``charger_dossier`` and ``rafraichir_dossier`` are the only two tools that
touch Oracle and the only two that write the state (they return a
``Command``). Tool docstrings are in French: they are the description the
model reads (§ 6.1).
"""

from typing import Annotated

import oracledb
from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command
from loguru import logger

from agent_edc.agent.state import AgentState
from agent_edc.agent.tools._common import NO_SNAPSHOT, agent_settings, get_snapshot, now, with_freshness
from agent_edc.config import EDCConfig
from agent_edc.edc.loader import load_case_file
from agent_edc.edc.normalizers import normalize_edc_id
from agent_edc.formatting import format_age, format_date, format_int, format_summary
from agent_edc.models import CaseFile
from agent_edc.observability import record_tool_metadata, traced_tool
from agent_edc.snapshot import new_events

EDC_UNAVAILABLE = "La base E-décès est momentanément inaccessible ; je ne peux pas charger le dossier."


def _reply(text: str, tool_call_id: str) -> Command:
    """A Command that only answers the tool call, leaving the state untouched."""
    return Command(update={"messages": [ToolMessage(text, tool_call_id=tool_call_id)]})


def _loaded(case_file: CaseFile, text: str, tool_call_id: str) -> Command:
    record_tool_metadata(events=len(case_file.events), beneficiaries=len(case_file.beneficiaries))
    return Command(
        update={
            "edc_id": case_file.edc_id,
            "snapshot": case_file,
            "loaded_at": now(),
            "messages": [ToolMessage(text, tool_call_id=tool_call_id)],
        }
    )


def _load(edc_id: str) -> CaseFile | None:
    """Load from Oracle, timing it; Oracle errors propagate to the caller."""
    start = now()
    try:
        return load_case_file(EDCConfig(), edc_id)
    finally:
        record_tool_metadata(load_duration_ms=int((now() - start).total_seconds() * 1000))


@tool("charger_dossier", parse_docstring=True)
@traced_tool
def load_dossier(
    edc_id: str,
    state: Annotated[AgentState, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Charge un dossier E-décès en mémoire et renvoie sa fiche de synthèse.

    À appeler UNE SEULE FOIS, dès que le gestionnaire mentionne un identifiant.
    Tous les autres outils travaillent ensuite sur ce dossier sans nouvel accès
    à la base. Appelle cet outil seul, puis attends son résultat avant tout
    autre appel.

    Args:
        edc_id: Identifiant du dossier E-décès, 8 chiffres (un identifiant à
            7 chiffres est accepté, le zéro de tête est restauré).
    """
    try:
        normalized = normalize_edc_id(edc_id)
    except ValueError:
        return _reply(
            f"Identifiant invalide : « {edc_id} ». Un identifiant E-décès comporte 8 chiffres.", tool_call_id
        )

    settings = agent_settings()
    snapshot, loaded_at = state.get("snapshot"), state.get("loaded_at")
    if (
        state.get("edc_id") == normalized
        and snapshot is not None
        and loaded_at is not None
        and (now() - loaded_at).total_seconds() < settings.stale_after_minutes * 60
    ):
        record_tool_metadata(cache_hit=True)
        summary = format_summary(snapshot, loaded_at, now(), settings.summary_events)
        return _reply(
            f"Dossier déjà chargé {format_age(loaded_at, now())} ; la base n'a pas été relue.\n\n{summary}",
            tool_call_id,
        )

    record_tool_metadata(cache_hit=False)
    try:
        case_file = _load(normalized)
    except oracledb.Error:
        logger.exception("EDC load failed for dossier {}", normalized)
        return _reply(EDC_UNAVAILABLE, tool_call_id)
    if case_file is None:
        return _reply(
            f"Aucun dossier E-décès ne porte l'identifiant {normalized}. "
            "Vérifie le numéro auprès du gestionnaire.",
            tool_call_id,
        )
    return _loaded(case_file, format_summary(case_file, now(), now(), settings.summary_events), tool_call_id)


@tool("rafraichir_dossier", parse_docstring=True, error_on_invalid_docstring=False)
@traced_tool
def refresh_dossier(
    state: Annotated[AgentState, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Relit le dossier dans la base E-décès et remplace la version en mémoire.

    À utiliser quand la fraîcheur compte : le gestionnaire vient de saisir
    quelque chose, la question porte sur la situation d'aujourd'hui, ou les
    données en mémoire datent de plus d'une demi-heure. Inutile pour une
    question sur des faits passés.
    """
    previous = state.get("snapshot")
    if previous is None:
        return _reply(NO_SNAPSHOT, tool_call_id)

    loaded_at = state.get("loaded_at")
    kept = (
        f"je continue avec l'instantané du {loaded_at.strftime('%d/%m/%Y à %H:%M')}."
        if loaded_at
        else "je continue avec l'instantané en mémoire."
    )
    try:
        case_file = _load(previous.edc_id)
    except oracledb.Error:
        logger.exception("EDC refresh failed for dossier {}", previous.edc_id)
        return _reply(f"La relecture a échoué ; {kept}", tool_call_id)
    if case_file is None:
        return _reply(f"Le dossier {previous.edc_id} est introuvable à la relecture ; {kept}", tool_call_id)

    added = new_events(previous, case_file)
    if added:
        latest = added[0]
        delta = (
            f"{format_int(len(added))} {'nouveaux événements' if len(added) > 1 else 'nouvel événement'} "
            "depuis le chargement précédent "
            f"(le plus récent : {format_date(latest.date)} · {latest.event_id})."
        )
    else:
        delta = "Aucun nouvel événement depuis le chargement précédent."
    summary = format_summary(case_file, now(), now(), agent_settings().summary_events)
    return _loaded(case_file, f"{delta}\n\n{summary}", tool_call_id)


@tool("resume_dossier", parse_docstring=True, error_on_invalid_docstring=False)
@traced_tool
def get_dossier_summary(state: Annotated[AgentState, InjectedState]) -> str:
    """Redonne la fiche de synthèse du dossier chargé : état, réseau, dates,
    nombre d'événements et de bénéficiaires, montants payés et restants,
    bénéficiaires non soldés, et les derniers événements.

    Utile pour se resituer au milieu d'une conversation longue. Ne relit pas
    la base.
    """
    snapshot = get_snapshot(state)
    if snapshot is None:
        return NO_SNAPSHOT
    loaded_at = state.get("loaded_at") or now()
    return format_summary(snapshot, loaded_at, now(), agent_settings().summary_events)


@tool("bloc_note_dossier", parse_docstring=True, error_on_invalid_docstring=False)
@traced_tool
def get_dossier_note(state: Annotated[AgentState, InjectedState]) -> str:
    """Renvoie le bloc-note global du dossier, en intégralité et sans coupure.

    C'est le commentaire libre tenu par les gestionnaires : la source la plus
    riche pour comprendre ce qui a été fait, tenté ou décidé sur le dossier.
    À lire systématiquement avant de porter un jugement sur la qualité de la
    gestion.
    """
    snapshot = get_snapshot(state)
    if snapshot is None:
        return NO_SNAPSHOT
    note = snapshot.dossier.global_comment if snapshot.dossier else None
    if not note:
        return with_freshness(state, "Ce dossier n'a pas de bloc-note global.")
    header = f"Bloc-note du dossier {snapshot.edc_id} ({format_int(len(note))} caractères) :"
    return with_freshness(state, f"{header}\n{note}")
