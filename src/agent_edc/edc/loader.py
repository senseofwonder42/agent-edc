"""Load a whole dossier from EDC in a single connection.

This is the only module of the project that talks to Oracle (§ 5.2).

PORTED FROM: files_late_fee/src/deces_risk/datasources/edc/source.py (EdcDataSource)
PORTED ON:   2026-09-18
DIVERGENCE:  intentional — the three methods (three connections) are merged
             into ``load_case_file`` (one connection), § 2.3 a.
"""

from __future__ import annotations

import time
from datetime import datetime

from loguru import logger

from agent_edc.config import EDCConfig
from agent_edc.edc.connection import edc_connection, fetch_rows
from agent_edc.edc.normalizers import (
    aggregate_amounts,
    normalize_beneficiaries,
    normalize_dossier,
    normalize_edc_event,
)
from agent_edc.edc.queries import (
    QUERY_AMOUNTS_PAID,
    QUERY_AMOUNTS_REMAINING,
    QUERY_BENEFICIARIES,
    QUERY_DOSSIER,
    QUERY_EVENTS,
)
from agent_edc.models import CaseFile


def load_case_file(config: EDCConfig, edc_id: str) -> CaseFile | None:
    """Run every EDC query for one dossier inside a single connection.

    Args:
        config: Oracle settings for the EDC database.
        edc_id: Normalized 8-digit dossier id.

    Returns:
        The in-memory snapshot (events sorted chronologically, undated last),
        or None when the dossier does not exist.
    """
    start = time.perf_counter()
    params = {"id_dossier": edc_id}
    with edc_connection(config) as connection:
        dossier_rows = fetch_rows(connection, QUERY_DOSSIER, params)
        if not dossier_rows:
            logger.info("EDC: dossier {} not found", edc_id)
            return None
        event_rows = fetch_rows(connection, QUERY_EVENTS, params)
        beneficiary_rows = fetch_rows(connection, QUERY_BENEFICIARIES, params)
        paid_rows = fetch_rows(connection, QUERY_AMOUNTS_PAID, params)
        remaining_rows = fetch_rows(connection, QUERY_AMOUNTS_REMAINING, params)

    case_file = CaseFile(
        edc_id=edc_id,
        dossier=normalize_dossier(edc_id, dossier_rows[0]),
        events=sorted(
            (normalize_edc_event(row) for row in event_rows),
            key=lambda e: (e.date is None, e.date or datetime.max),
        ),
        beneficiaries=normalize_beneficiaries(
            beneficiary_rows,
            aggregate_amounts(paid_rows, "MONTANTPAYE"),
            aggregate_amounts(remaining_rows, "MONTANTRESTANTAPAYER"),
        ),
    )
    rows_fetched = sum(
        len(rows) for rows in (dossier_rows, event_rows, beneficiary_rows, paid_rows, remaining_rows)
    )
    logger.info(
        "EDC: dossier {} loaded — {} row(s), {} event(s), {} beneficiary(ies) in {:.0f} ms",
        edc_id,
        rows_fetched,
        len(case_file.events),
        len(case_file.beneficiaries),
        (time.perf_counter() - start) * 1000,
    )
    return case_file
