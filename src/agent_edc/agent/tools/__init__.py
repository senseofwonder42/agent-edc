"""The 12 tools bound to the model (§ 6.2)."""

from langchain_core.tools import BaseTool

from agent_edc.agent.tools.beneficiaries import get_amounts_summary, get_beneficiary, list_beneficiaries
from agent_edc.agent.tools.dossier import get_dossier_note, get_dossier_summary, load_dossier, refresh_dossier
from agent_edc.agent.tools.events import get_event, get_timeline_stats, list_events, search_events
from agent_edc.agent.tools.nomenclature import lookup_nomenclature

ALL_TOOLS: list[BaseTool] = [
    load_dossier,
    refresh_dossier,
    get_dossier_summary,
    get_dossier_note,
    list_events,
    search_events,
    get_event,
    get_timeline_stats,
    list_beneficiaries,
    get_beneficiary,
    get_amounts_summary,
    lookup_nomenclature,
]
