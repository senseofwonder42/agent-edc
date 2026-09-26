"""Logging (loguru) and tracing (Langfuse).

Nothing in this module ever raises: tracing is a convenience, and an
unreachable Langfuse server must never prevent a manager from getting an
answer (§ 10.2).

PORTED FROM: files_late_fee/src/deces_risk/observability.py
PORTED ON:   2026-09-18
DIVERGENCE:  instrumented_node replaced by traced_tool (§ 10.4); a masking
             function is passed to the Langfuse client (§ 10.5); the handler is
             subclassed to set the session, the tags and the tool metadata.
"""

from __future__ import annotations

import inspect
import re
import sys
import time
from collections.abc import Callable
from contextvars import ContextVar
from functools import cache, wraps
from pathlib import Path
from typing import Any
from uuid import UUID

from loguru import logger

from agent_edc.config import LangfuseConfig


def setup_logging(level: str = "INFO", log_dir: Path = Path("logs")) -> None:
    """Configure the loguru sinks for the whole application.

    Args:
        level: Console sink level (the file sink always records DEBUG).
        log_dir: Directory receiving the rotating ``agent_edc.log`` file.
    """
    logger.remove()
    logger.add(sys.stderr, level=level.upper())
    log_dir.mkdir(parents=True, exist_ok=True)
    logger.add(log_dir / "agent_edc.log", level="DEBUG", rotation="10 MB", retention=10, encoding="utf-8")
    logger.debug("Logging configured (console={}, dir={})", level, log_dir)


_BIRTH_DATE = re.compile(r"(n[ée]e?\s*(?:\(e\))?\s+le\s+)\d{2}/\d{2}/\d{4}", re.IGNORECASE)
_SECRET = re.compile(r"\b(password|passwd|pwd|api_key|apikey|secret_key|dsn)\s*[=:]\s*\S+", re.IGNORECASE)


def mask_trace_payload(data: Any, **kwargs: Any) -> Any:
    """Mask birth dates and secret-looking fragments in anything sent to Langfuse.

    Args:
        data: Payload of a trace/observation (str, dict, list or other).
        **kwargs: Extra arguments passed by the Langfuse SDK (ignored).

    Returns:
        The payload with ``né(e) le JJ/MM/AAAA`` and ``password=…``-style
        fragments neutralized; non-text values are returned unchanged.
    """
    if isinstance(data, str):
        masked = _BIRTH_DATE.sub(r"\1[date masquée]", data)
        return _SECRET.sub(r"\1=[masqué]", masked)
    if isinstance(data, dict):
        return {key: mask_trace_payload(value) for key, value in data.items()}
    if isinstance(data, list | tuple):
        return type(data)(mask_trace_payload(value) for value in data)
    return data


@cache
def _handler_class() -> type[Any]:
    """The project's Langfuse handler; built lazily so the SDK is imported only when tracing is on.

    The stock handler reads the session and the tags from the metadata of the
    *root* run only (a ``langfuse_session_id`` set deeper is ignored). This
    subclass fills them at the root, and attaches the ``traced_tool``
    metadata to the tool spans.
    """
    from langfuse.langchain import CallbackHandler

    class EdcCallbackHandler(CallbackHandler):
        def __init__(self, tags: list[str]) -> None:
            super().__init__()
            self.trace_tags = tags

        def on_chain_start(
            self,
            serialized: dict[str, Any] | None,
            inputs: Any,
            *,
            run_id: UUID,
            parent_run_id: UUID | None = None,
            tags: list[str] | None = None,
            metadata: dict[str, Any] | None = None,
            **kwargs: Any,
        ) -> Any:
            if parent_run_id is None:
                metadata = dict(metadata or {})
                # LangGraph copies ``configurable.thread_id`` into the metadata (REPL and server).
                if metadata.get("thread_id") and "langfuse_session_id" not in metadata:
                    metadata["langfuse_session_id"] = str(metadata["thread_id"])
                metadata["langfuse_tags"] = [*metadata.get("langfuse_tags", []), *self.trace_tags]
            return super().on_chain_start(
                serialized,
                inputs,
                run_id=run_id,
                parent_run_id=parent_run_id,
                tags=tags,
                metadata=metadata,
                **kwargs,
            )

        def on_chain_end(
            self, outputs: Any, *, run_id: UUID, parent_run_id: UUID | None = None, **kwargs: Any
        ) -> Any:
            if parent_run_id is None and isinstance(outputs, dict) and outputs.get("edc_id"):
                # The dossier is only known once the graph has run: tag the root span now.
                self._tag_trace(run_id, [*self.trace_tags, f"dossier:{outputs['edc_id']}"])
            return super().on_chain_end(outputs, run_id=run_id, parent_run_id=parent_run_id, **kwargs)

        def _tag_trace(self, run_id: UUID, tags: list[str]) -> None:
            try:  # Relies on SDK internals: never break the conversation over a tag.
                from langfuse._client.attributes import LangfuseOtelSpanAttributes

                span = self._runs.get(run_id)
                if span is not None:
                    span._otel_span.set_attribute(LangfuseOtelSpanAttributes.TRACE_TAGS, tags)
            except Exception as exc:
                logger.debug("Langfuse dossier tag not attached: {}", exc)

        def attach_tool_metadata(self, run_id: UUID, metadata: dict[str, Any]) -> None:
            """Attach ``metadata`` to the span of the tool run ``run_id``."""
            try:
                span = self._runs.get(run_id)
                if span is not None:
                    span.update(metadata=metadata)
            except Exception as exc:
                logger.debug("Langfuse tool metadata not attached: {}", exc)

    return EdcCallbackHandler


def build_langfuse_callbacks(config: LangfuseConfig, tags: list[str] | None = None) -> list[Any]:
    """Build the Langfuse LangChain callbacks, or an empty list.

    Returns an empty list — never raises — when tracing is disabled, when the
    keys are missing, or when the SDK fails to initialize.

    Args:
        config: Langfuse settings.
        tags: Trace tags describing the configuration (model, reasoning, ...).

    Returns:
        ``[EdcCallbackHandler]`` when tracing is active, otherwise ``[]``.
    """
    if not config.is_active:
        logger.debug(
            "Langfuse tracing disabled (enabled={} keys_set={})",
            config.enabled,
            bool(config.public_key and config.secret_key),
        )
        return []
    try:
        from langfuse import Langfuse

        # Register the client singleton the handler relies on.
        Langfuse(
            public_key=config.public_key,
            secret_key=config.secret_key,
            host=config.host or None,
            mask=mask_trace_payload,
        )
        return [_handler_class()(tags or [])]
    except Exception as exc:  # Tracing must never break the conversation.
        logger.warning("Langfuse initialization failed — tracing disabled: {}", exc)
        return []


_tool_metadata: ContextVar[dict[str, Any] | None] = ContextVar("_tool_metadata", default=None)


def record_tool_metadata(**metadata: Any) -> None:
    """Attach diagnostic metadata to the tool call in progress (see ``traced_tool``)."""
    current = _tool_metadata.get()
    if current is not None:
        current.update(metadata)


def _result_chars(result: Any) -> int:
    update = getattr(result, "update", None)
    if isinstance(update, dict):  # a langgraph Command
        return sum(len(str(getattr(m, "content", ""))) for m in update.get("messages", []))
    return len(str(result))


def traced_tool(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Log duration and attach tool metadata to the tool's Langfuse span.

    Metadata comes from ``record_tool_metadata`` calls made inside the tool,
    plus ``result_chars`` and ``duration_ms``. A no-op on the Langfuse side
    when tracing is disabled; the loguru log is always emitted (counters only,
    never contents — § 10.5 rule 4).

    The wrapper declares a ``callbacks`` parameter: LangChain then passes the
    tool's child callback manager (and hides the parameter from the schema
    sent to the model), whose ``parent_run_id`` is the tool run.
    """

    @wraps(fn)
    def wrapper(*args: Any, callbacks: Any = None, **kwargs: Any) -> Any:
        token = _tool_metadata.set({})
        start = time.perf_counter()
        try:
            result = fn(*args, **kwargs)
            metadata = _tool_metadata.get() or {}
        finally:
            _tool_metadata.reset(token)
        metadata["result_chars"] = _result_chars(result)
        metadata["duration_ms"] = round((time.perf_counter() - start) * 1000, 1)
        logger.info("Tool '{}' {}", fn.__name__, metadata)
        _attach_tool_metadata(callbacks, metadata)
        return result

    signature = inspect.signature(fn)
    callbacks_param = inspect.Parameter("callbacks", inspect.Parameter.KEYWORD_ONLY, default=None)
    wrapper.__signature__ = signature.replace(  # type: ignore[attr-defined]
        parameters=[*signature.parameters.values(), callbacks_param]
    )
    return wrapper


def _attach_tool_metadata(callbacks: Any, metadata: dict[str, Any]) -> None:
    run_id = getattr(callbacks, "parent_run_id", None)
    if run_id is None:
        return
    for handler in getattr(callbacks, "handlers", []):
        attach = getattr(handler, "attach_tool_metadata", None)
        if attach is not None:
            attach(run_id, metadata)


def flush_tracing() -> None:
    """Send the pending Langfuse events (end of a short-lived CLI run). Never raises."""
    if "langfuse" not in sys.modules:  # tracing never initialized
        return
    try:
        from langfuse import get_client

        get_client().flush()
    except Exception as exc:  # Tracing must never break the conversation.
        logger.debug("Langfuse flush failed: {}", exc)
