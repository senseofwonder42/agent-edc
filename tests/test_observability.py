"""Langfuse wiring without a Langfuse server: session, tags, tool metadata (§ 10.3, 10.4)."""

from datetime import datetime
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.callbacks import BaseCallbackHandler
from langfuse.langchain import CallbackHandler

from agent_edc.agent.tools import ALL_TOOLS
from agent_edc.agent.tools.events import list_events
from agent_edc.observability import _handler_class

from .conftest import EDC_ID


def test_callbacks_parameter_is_hidden_from_the_model():
    for tool in ALL_TOOLS:
        assert "callbacks" not in tool.tool_call_schema.model_json_schema().get("properties", {})


def test_tool_metadata_reaches_the_tool_run(state: dict[str, Any]):
    seen: dict[str, Any] = {}

    class Recorder(BaseCallbackHandler):
        def on_tool_start(self, serialized, input_str, *, run_id, **kwargs):
            seen["tool_run"] = run_id

        def attach_tool_metadata(self, run_id, metadata):
            seen["attached"] = (run_id, metadata)

    call = {
        "type": "tool_call",
        "id": "c1",
        "name": "lister_evenements",
        "args": {"state": state, "limite": 2},
    }
    list_events.invoke(call, config={"callbacks": [Recorder()]})
    run_id, metadata = seen["attached"]
    assert run_id == seen["tool_run"]
    assert {"snapshot_age_s", "truncated", "result_chars", "duration_ms"} <= set(metadata)


@pytest.fixture
def handler(monkeypatch: pytest.MonkeyPatch):
    """The project handler with the stock root hooks replaced by recorders."""
    received: dict[str, Any] = {}
    monkeypatch.setattr(
        CallbackHandler,
        "on_chain_start",
        lambda self, *a, metadata=None, **k: received.update(metadata=metadata),
    )
    monkeypatch.setattr(CallbackHandler, "on_chain_end", lambda self, *a, **k: None)
    instance = _handler_class()(["modele:qwen-test", "raisonnement:medium"])
    instance.received = received
    return instance


def test_root_run_gets_session_and_tags(handler):
    handler.on_chain_start({}, {}, run_id=uuid4(), metadata={"thread_id": "fil-1"})
    metadata = handler.received["metadata"]
    assert metadata["langfuse_session_id"] == "fil-1"
    assert metadata["langfuse_tags"] == ["modele:qwen-test", "raisonnement:medium"]


def test_child_runs_are_left_alone(handler):
    handler.on_chain_start({}, {}, run_id=uuid4(), parent_run_id=uuid4(), metadata={"thread_id": "fil-1"})
    assert handler.received["metadata"] == {"thread_id": "fil-1"}


class _FakeSpan:
    def __init__(self) -> None:
        self.attributes: dict[str, Any] = {}
        self.metadata: Any = None
        self._otel_span = self

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def update(self, *, metadata: Any = None, **kwargs: Any) -> None:
        self.metadata = metadata


def test_dossier_tag_is_set_at_the_end_of_the_run(handler):
    run_id, span = uuid4(), _FakeSpan()
    handler._runs[run_id] = span
    handler.on_chain_end({"edc_id": EDC_ID, "loaded_at": datetime.now()}, run_id=run_id)
    assert list(span.attributes.values()) == [
        ["modele:qwen-test", "raisonnement:medium", f"dossier:{EDC_ID}"]
    ]


def test_attach_tool_metadata_updates_the_tool_span(handler):
    run_id, span = uuid4(), _FakeSpan()
    handler._runs[run_id] = span
    handler.attach_tool_metadata(run_id, {"cache_hit": True})
    handler.attach_tool_metadata(uuid4(), {"ignored": True})  # unknown run: no error
    assert span.metadata == {"cache_hit": True}
