"""State carried by one conversation thread (§ 4.3).

Only ``messages`` accumulates (``add_messages``). The other keys use
LangGraph's default reducer — replacement — on purpose: a refresh must
*replace* the snapshot, never append to it, and a compaction replaces the
previous summary.
"""

from __future__ import annotations

from datetime import datetime
from typing import (
    Annotated,
    NotRequired,  # pydantic requires it on Python < 3.12
)

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from agent_edc.models import CaseFile


class AgentState(TypedDict):
    """State carried by one conversation thread (one dossier)."""

    messages: Annotated[list[AnyMessage], add_messages]
    # NotRequired: absent until the first load, and the tools' injected state
    # is validated against this schema.
    edc_id: NotRequired[str | None]
    snapshot: NotRequired[CaseFile | None]
    loaded_at: NotRequired[datetime | None]
    # Compaction (agent/compaction.py): summary of the messages up to and
    # including ``summary_until``; ``messages`` itself is never cut.
    summary: NotRequired[str | None]
    summary_until: NotRequired[str | None]
