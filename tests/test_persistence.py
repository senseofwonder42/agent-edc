"""The snapshot survives the server's checkpointer (§ 11.2).

Under Aegra the state is persisted by ``AsyncPostgresSaver`` with LangGraph's
default serializer — not the REPL's allow-listed one (``cli._SERDE``).
"""

import warnings

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer


def test_default_serializer_round_trips_the_snapshot(case_file):
    serde = JsonPlusSerializer()
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        restored = serde.loads_typed(serde.dumps_typed(case_file))
    assert restored == case_file
