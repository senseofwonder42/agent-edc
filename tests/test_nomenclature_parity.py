"""Parity of the 7 nomenclature tables with files_late_fee (§ 2.4). Run with ``-m parity``.

The source module is loaded by file path: no dependency on deces_risk is created.
"""

import importlib.util
from pathlib import Path

import pytest

from agent_edc.edc import nomenclatures

SOURCE = (
    Path(__file__).resolve().parents[2] / "files_late_fee/src/deces_risk/datasources/edc/nomenclatures.py"
)
TABLES = [
    "TYPE_EVT",
    "SOUS_TYPE_EVT",
    "SOUS_TYPE_ALERTE",
    "TYPE_INTERCALAIRE",
    "MOTIF_FT",
    "MOTIF_ATTENTE",
    "INTERLOCUTEUR",
]


@pytest.mark.parity
@pytest.mark.skipif(not SOURCE.exists(), reason="files_late_fee absent de cette machine")
@pytest.mark.parametrize("table", TABLES)
def test_nomenclature_parity(table):
    spec = importlib.util.spec_from_file_location("_source_nomenclatures", SOURCE)
    source = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(source)
    assert getattr(nomenclatures, table) == getattr(source, table)
