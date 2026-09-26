"""Oracle connection and row fetching for the E-décès (EDC / ESD) database.

PORTED FROM: files_late_fee/src/deces_risk/datasources/edc/connection.py
             (+ ensure_thick_mode from datasources/common.py)
PORTED ON:   2026-09-18
DIVERGENCE:  the DSN is no longer logged (§ 10.5); connections come from a
             process-wide pool (the LangGraph server is long-lived).
"""

from __future__ import annotations

import threading
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

import oracledb
from loguru import logger

from agent_edc.config import EDCConfig

_thick_mode_initialized = False
_pool: oracledb.ConnectionPool | None = None
_pool_lock = threading.Lock()


def ensure_thick_mode() -> None:
    """Initialize the Oracle thick client once per process (wallet auth).

    ``init_oracle_client`` raises when called twice; the LangGraph server is
    a long-lived process serving many threads, hence the guard.
    """
    global _thick_mode_initialized
    if not _thick_mode_initialized:
        oracledb.init_oracle_client()
        _thick_mode_initialized = True


def _get_pool(config: EDCConfig) -> oracledb.ConnectionPool:
    """The process-wide pool, created on first use (a failed creation is retried next time)."""
    global _pool
    with _pool_lock:
        if _pool is None:
            config.apply_env()
            ensure_thick_mode()
            _pool = oracledb.create_pool(
                dsn=config.dsn,
                user=config.user,
                password=config.password,
                min=config.pool_min,
                max=config.pool_max,
                increment=1,
            )
            logger.debug("EDC pool created (min={}, max={})", config.pool_min, config.pool_max)
        return _pool


@contextmanager
def edc_connection(config: EDCConfig) -> Generator[oracledb.Connection, None, None]:
    """Context manager acquiring (and always releasing) an EDC connection from the pool.

    Args:
        config: Oracle connection settings for E-décès.

    Yields:
        An open ``oracledb`` connection.
    """
    connection = _get_pool(config).acquire()
    logger.debug("EDC connection acquired")
    try:
        yield connection
    finally:
        connection.close()  # returns it to the pool
        logger.debug("EDC connection released")


def _lob_output_handler(
    cursor: oracledb.Cursor,
    name: str,
    default_type: Any,
    size: int,
    precision: int,
    scale: int,
) -> oracledb.Var | None:
    """Read CLOB/BLOB columns fully and avoid wide VARCHAR truncation.

    Without this handler the driver returns LOB objects, while the EDC XML
    payloads (``INFOSPECEVT``, ``INFOSPECINTERCALAIRE``) and bloc-notes must
    come back as plain ``str`` (§ 7.7).
    """
    if default_type in (oracledb.DB_TYPE_CLOB, oracledb.DB_TYPE_NCLOB):
        return cursor.var(oracledb.DB_TYPE_LONG, arraysize=cursor.arraysize)
    if default_type is oracledb.DB_TYPE_BLOB:
        return cursor.var(oracledb.DB_TYPE_LONG_RAW, arraysize=cursor.arraysize)
    if default_type in (oracledb.DB_TYPE_VARCHAR, oracledb.DB_TYPE_CHAR) and size > 256:
        return cursor.var(default_type, size, arraysize=cursor.arraysize)
    return None


def fetch_rows(connection: oracledb.Connection, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Run a bind-variable query and return the rows as dicts.

    Args:
        connection: Open EDC connection.
        sql: SQL text with named bind variables.
        params: Bind variable values.

    Returns:
        One dict per row, keyed by upper-case column name.
    """
    with connection.cursor() as cursor:
        cursor.outputtypehandler = _lob_output_handler
        cursor.execute(sql, params)
        columns = [description[0] for description in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
