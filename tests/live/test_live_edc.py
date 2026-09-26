"""Tests requiring the secured environment: Oracle + wallet + vLLM (§ 12.4). Run with ``-m live``.

Dossier ids come from the environment (LIVE_EDC_ID, LIVE_EDC_ID_LONG_NOTE), never hardcoded.
"""

import os
import time
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage
from loguru import logger

from agent_edc.agent.build import build_agent
from agent_edc.cli import _checkpointer
from agent_edc.config import EDCConfig, VLMConfig
from agent_edc.edc import queries
from agent_edc.edc.connection import edc_connection, ensure_thick_mode, fetch_rows
from agent_edc.edc.loader import load_case_file
from agent_edc.llm import build_chat_model, check_tool_calling, tool_binding_kwargs

pytestmark = pytest.mark.live


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} non renseigné")
    return value


def test_thick_mode_initializes():
    EDCConfig().apply_env()
    ensure_thick_mode()
    ensure_thick_mode()  # the second call must be a no-op


def test_five_queries_run():
    edc_id = _env("LIVE_EDC_ID")
    with edc_connection(EDCConfig()) as connection:
        for sql in (
            queries.QUERY_DOSSIER,
            queries.QUERY_EVENTS,
            queries.QUERY_BENEFICIARIES,
            queries.QUERY_AMOUNTS_PAID,
            queries.QUERY_AMOUNTS_REMAINING,
        ):
            fetch_rows(connection, sql, {"id_dossier": edc_id})


def test_lob_handler_returns_str():
    edc_id = _env("LIVE_EDC_ID")
    with edc_connection(EDCConfig()) as connection:
        rows = fetch_rows(connection, queries.QUERY_EVENTS, {"id_dossier": edc_id})
    payloads = [row["INFOSPECEVT"] for row in rows if row["INFOSPECEVT"] is not None]
    assert payloads and all(isinstance(value, str) for value in payloads)


def test_long_event_note_exceeds_256():
    case_file = load_case_file(EDCConfig(), _env("LIVE_EDC_ID_LONG_NOTE"))
    assert case_file is not None
    assert max(len(e.details or "") for e in case_file.events) > 256


def test_dossier_note_is_stitched():
    case_file = load_case_file(EDCConfig(), _env("LIVE_EDC_ID_LONG_NOTE"))
    assert case_file is not None and len(case_file.dossier.global_comment or "") > 256


def test_load_duration():
    start = time.perf_counter()
    assert load_case_file(EDCConfig(), _env("LIVE_EDC_ID")) is not None
    assert time.perf_counter() - start < 5


def test_vllm_tool_calling():
    config = VLMConfig()
    check_tool_calling(build_chat_model(config), **tool_binding_kwargs(config))


def test_checkpoint_growth(tmp_path: Path):
    """Measure (log) the SQLite growth over 5 turns on a large dossier — to decide on § 5.6 later."""
    edc_id = _env("LIVE_EDC_ID_LONG_NOTE")
    db = tmp_path / "checkpoints.sqlite"
    questions = [
        f"Ouvre le dossier {edc_id}.",
        "Quelles sont les plus longues périodes sans événement ?",
        "Que dit le bloc-note du dossier ?",
        "Qui reste à payer ?",
        "Y a-t-il eu des relances ?",
    ]
    with _checkpointer(db) as checkpointer:
        agent = build_agent(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": "live-growth"}}
        for number, question in enumerate(questions, start=1):
            agent.invoke({"messages": [HumanMessage(question)]}, config=config)
            logger.info("Checkpoint DB after turn {}: {:.0f} KB", number, db.stat().st_size / 1024)
    assert db.stat().st_size > 0
