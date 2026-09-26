"""Guards and helpers shared by the tools (French messages, freshness line)."""

from __future__ import annotations

from datetime import datetime
from functools import cache

from agent_edc.agent.state import AgentState
from agent_edc.config import AgentConfig
from agent_edc.formatting import format_staleness
from agent_edc.models import CaseFile
from agent_edc.observability import record_tool_metadata

NO_SNAPSHOT = (
    "Aucun dossier n'est chargé. Appelle d'abord « charger_dossier » avec l'identifiant à 8 chiffres."
)


@cache
def agent_settings() -> AgentConfig:
    """The ``AGENT_*`` settings, read once per process."""
    return AgentConfig()


def now() -> datetime:
    """Current local time (a function so that tests can freeze it)."""
    return datetime.now()


def get_snapshot(state: AgentState) -> CaseFile | None:
    """The loaded snapshot, recording its age in the trace metadata."""
    snapshot = state.get("snapshot")
    loaded_at = state.get("loaded_at")
    if snapshot is not None and loaded_at is not None:
        record_tool_metadata(snapshot_age_s=int((now() - loaded_at).total_seconds()))
    return snapshot


def with_freshness(state: AgentState, text: str) -> str:
    """Append the snapshot-age warning once the snapshot is stale (§ 5.5)."""
    warning = format_staleness(state.get("loaded_at"), now(), agent_settings().stale_after_minutes)
    return f"{text}\n{warning}" if warning else text
