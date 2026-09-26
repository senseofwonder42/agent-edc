"""Shared fixtures: a realistic in-memory snapshot and a scripted chat model (§ 12.2, 12.3)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from agent_edc.models import Beneficiary, CaseFile, EdcDossier, Event

EDC_ID = "01234567"

#: A 300+ character note whose VARCHAR2 part was cut mid-word ("confir|mer").
LONG_NOTE_BASE = (
    "Dossier en attente de la dévolution successorale. " * 4
    + "Relance téléphonique faite le 28/08, le notaire Me Dupont doit confir"
)
LONG_NOTE_OVERFLOW = "mer la dévolution avant ordonnancement.\nPoint à refaire fin septembre."
LONG_NOTE = LONG_NOTE_BASE + LONG_NOTE_OVERFLOW

#: A long note that was never truncated (no overflow).
LONG_NOTE_NO_OVERFLOW = "Pièces reçues : acte de décès, RIB, pièce d'identité. " * 5

DOSSIER_NOTE = ("12/03/2024 ouverture du dossier, demande des pièces au notaire. " * 30)[:1800]

_LABELS = {
    "1": ("Courrier (entrant ou sortant) — Arrivée courrier", "1.1"),
    "3": ("Communication — Réception com. téléphonique", "3.1"),
    "5": ("Alerte — Dossier en attente", "12"),
    "6": ("Evénement standard — Relance manuelle de demande de pièces", "16"),
}


def _event(number: int, when: datetime | None, type_code: str, ref: str, **fields: Any) -> Event:
    label, subtype = _LABELS[type_code]
    return Event(
        event_id=f"EVT-{number}",
        intercalaire_ref=ref,
        date=when,
        label=label,
        type_code=type_code,
        subtype_code=subtype,
        **fields,
    )


def build_case_file() -> CaseFile:
    """40 events over two years with a 7-month gap, and 3 beneficiaries."""
    refs = ["88412", "88413", "88414"]
    types = ["6", "6", "1", "6", "3", "6", "5"]
    events = []
    start = datetime(2024, 3, 12)
    # 20 events from 12/03/2024 to 04/07/2024, one every 6 days.
    for i in range(20):
        events.append(_event(1000 + i, start + timedelta(days=6 * i), types[i % 7], refs[i % 3]))
    # 7-month gap, then 19 events from 16/02/2025, one every 20 days.
    restart = datetime(2025, 2, 16)
    for i in range(19):
        events.append(_event(2000 + i, restart + timedelta(days=20 * i), types[i % 7], refs[i % 3]))
    # One undated event.
    events.append(_event(3000, None, "6", "88412"))

    by_id = {e.event_id: e for e in events}
    by_id["EVT-2000"].details = LONG_NOTE
    by_id["EVT-2000"].comment = "pièce classée au dossier le 03/09."
    by_id["EVT-2000"].interlocuteur = "Notaire"
    by_id["EVT-2001"].details = LONG_NOTE_NO_OVERFLOW
    by_id["EVT-1002"].details = "Reçu acte de notoriété du NOTAIRE Me Dupont, transmis au service succession."
    by_id["EVT-1002"].interlocuteur = "Notaire"
    by_id["EVT-2005"].details = "3e relance, sans réponse depuis le 12/06"
    by_id["EVT-2006"].motif = "Attente notaire"

    return CaseFile(
        edc_id=EDC_ID,
        dossier=EdcDossier(
            edc_id=EDC_ID,
            ref_dossier="4412887",
            etat="En cours",
            network_code="8",
            network_label="CE",
            creation_date=datetime(2024, 3, 12),
            effect_date=datetime(2024, 3, 5),
            global_comment=DOSSIER_NOTE,
        ),
        events=events,
        beneficiaries=[
            Beneficiary(
                beneficiary_id="88412",
                full_name="MARTIN Claire",
                birth_date=datetime(1960, 5, 1),
                amount_paid=30300.0,
                amount_remaining=12000.0,
            ),
            Beneficiary(
                beneficiary_id="88413", full_name="MARTIN Paul", amount_paid=12000.0, amount_remaining=0.0
            ),
            Beneficiary(beneficiary_id="88414", presumed=True),
        ],
    )


@pytest.fixture
def case_file() -> CaseFile:
    """A realistic in-memory snapshot: 40 events over 2 years, 3 beneficiaries."""
    return build_case_file()


@pytest.fixture
def state(case_file: CaseFile) -> dict[str, Any]:
    """A thread state with the snapshot freshly loaded."""
    return {"messages": [], "edc_id": EDC_ID, "snapshot": case_file, "loaded_at": datetime.now()}


@pytest.fixture
def empty_state() -> dict[str, Any]:
    """A thread state before any load."""
    return {"messages": [], "edc_id": None, "snapshot": None, "loaded_at": None}


class FakeChatModel(BaseChatModel):
    """Replays scripted ``AIMessage`` objects and records what it received."""

    responses: list[AIMessage]
    received: list[list[BaseMessage]] = []

    @property
    def _llm_type(self) -> str:
        return "fake-chat"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> FakeChatModel:
        return self

    def _generate(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any
    ):
        self.received.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=self.responses.pop(0))])


@pytest.fixture
def fake_model(monkeypatch: pytest.MonkeyPatch):
    """Factory installing a FakeChatModel with the given scripted answers (agent and compaction)."""

    def install(*responses: AIMessage) -> FakeChatModel:
        model = FakeChatModel(responses=list(responses), received=[])
        monkeypatch.setattr("agent_edc.agent.build.get_chat_model", lambda: model)
        monkeypatch.setattr("agent_edc.agent.compaction.get_chat_model", lambda: model)
        return model

    return install


@pytest.fixture
def fake_loader(monkeypatch: pytest.MonkeyPatch, case_file: CaseFile):
    """Replace the Oracle loader by a function returning the fixture (or raising / None)."""

    def install(result: Any = "fixture") -> list[str]:
        calls: list[str] = []

        def load(config: Any, edc_id: str) -> CaseFile | None:
            calls.append(edc_id)
            if isinstance(result, Exception):
                raise result
            return case_file if result == "fixture" else result

        monkeypatch.setattr("agent_edc.agent.tools.dossier.load_case_file", load)
        return calls

    return install


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch):
    """Factory overriding cached settings for one test, e.g. ``settings("history", compaction=True)``."""
    from agent_edc.config import get_settings

    def override(section: str, **values: Any) -> None:
        for name, value in values.items():
            monkeypatch.setattr(getattr(get_settings(), section), name, value)

    return override
