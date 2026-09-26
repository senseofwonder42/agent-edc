"""Terminal REPL for debugging without a server or web UI (§ 11.4).

The CLI help is in French (it is user interface); the code stays in English.
``chat 01234567`` only injects a first message containing the id: the REPL
exercises exactly the same graph as the web UI.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

import typer
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.errors import GraphRecursionError
from loguru import logger
from rich.console import Console

from agent_edc.agent.build import build_agent
from agent_edc.config import Settings, get_settings
from agent_edc.edc.connection import edc_connection, fetch_rows
from agent_edc.llm import build_chat_model, check_tool_calling, tool_binding_kwargs
from agent_edc.models import Beneficiary, CaseFile, EdcDossier, Event
from agent_edc.observability import flush_tracing, setup_logging

app = typer.Typer(help="Agent conversationnel E-décès (lecture seule).", no_args_is_help=True)
console = Console()


#: The snapshot is persisted with the thread (§ 5.6): its classes are explicitly
#: allowed for deserialization instead of relying on LangGraph's permissive default.
_SERDE = JsonPlusSerializer(
    allowed_msgpack_modules=[
        (model.__module__, model.__name__) for model in (CaseFile, EdcDossier, Event, Beneficiary)
    ]
)


@contextmanager
def _checkpointer(path: Path) -> Iterator[SqliteSaver]:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), check_same_thread=False)
    try:
        yield SqliteSaver(connection, serde=_SERDE)
    finally:
        connection.close()


def _settings() -> Settings:
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_dir)
    return settings


def _print_update(update: Any, *, answer_streamed: bool) -> None:
    """Show tool calls and tool results in grey, final answers in plain text.

    A final answer already streamed token by token is not printed again.
    """
    updates = update if isinstance(update, list) else [update]
    for item in updates:
        if not isinstance(item, dict):  # a Command: its payload is in .update
            item = getattr(item, "update", {}) or {}
        for message in item.get("messages", []):
            if isinstance(message, AIMessage) and message.tool_calls:
                for call in message.tool_calls:
                    console.print(f"→ {call['name']}({call['args']})", style="grey50")
            elif isinstance(message, ToolMessage):
                console.print(
                    f"← {message.name or 'outil'} ({len(str(message.content))} car.)", style="grey50"
                )
            elif isinstance(message, AIMessage) and not answer_streamed:
                console.print(str(message.content))


def _run_turn(agent: Any, thread_id: str, text: str) -> None:
    """Run one turn, streaming the answer; errors are reported without leaving the REPL."""
    # The Langfuse session comes from thread_id (observability.EdcCallbackHandler, § 10.3).
    config = {"configurable": {"thread_id": thread_id}}
    # In the JSON fallback the model streams raw JSON: show the translated answer only.
    stream_tokens = get_settings().vlm.tool_protocol == "native"
    streamed = False
    status = console.status("réflexion…", spinner="dots")
    status.start()
    try:
        for mode, chunk in agent.stream(
            {"messages": [HumanMessage(text)]}, config=config, stream_mode=["updates", "messages"]
        ):
            if mode == "messages":
                message, metadata = chunk
                if (
                    stream_tokens
                    and metadata.get("langgraph_node") == "agent"
                    and isinstance(message, AIMessageChunk)
                    and isinstance(message.content, str)
                    and message.content
                ):
                    status.stop()
                    console.print(message.content, end="", markup=False, highlight=False)
                    streamed = True
                continue
            status.stop()
            for node, update in chunk.items():
                answer_streamed = streamed and node == "agent"
                if answer_streamed:
                    console.print()  # close the streamed line
                    streamed = False
                _print_update(update, answer_streamed=answer_streamed)
            status.start()
    except GraphRecursionError:
        console.print("\n❌ Trop d'étapes pour cette question ; reformule-la plus précisément.")
    except Exception as exc:
        logger.error("Turn failed: {}", type(exc).__name__)
        logger.opt(exception=exc).debug("Turn failure detail")  # file sink only
        console.print(
            f"\n❌ Erreur ({type(exc).__name__}) — détail dans les journaux. Tu peux reposer la question."
        )
    finally:
        status.stop()


@app.command()
def chat(
    edc_id: Annotated[
        str | None, typer.Argument(help="Identifiant du dossier à ouvrir (8 chiffres).")
    ] = None,
    thread: Annotated[str | None, typer.Option(help="Reprendre une conversation existante.")] = None,
) -> None:
    """Ouvre une conversation interactive (Ctrl-D ou « quit » pour sortir)."""
    settings = _settings()
    thread_id = thread or str(uuid4())
    console.print(f"Conversation {thread_id}", style="bold")
    with _checkpointer(settings.agent.checkpoint_db) as checkpointer:
        agent = build_agent(checkpointer=checkpointer)
        if edc_id:
            _run_turn(agent, thread_id, f"Ouvre le dossier {edc_id}.")
        while True:
            try:
                text = console.input("[bold]> [/bold]").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if text.lower() in {"quit", "exit"}:
                break
            if text:
                _run_turn(agent, thread_id, text)
    flush_tracing()
    console.print(f"Pour reprendre : agent-edc chat --thread {thread_id}", style="grey50")


@app.command()
def ask(
    edc_id: Annotated[str, typer.Argument(help="Identifiant du dossier (8 chiffres).")],
    question: Annotated[str, typer.Argument(help="La question à poser.")],
) -> None:
    """Pose une seule question sur un dossier, puis quitte."""
    settings = _settings()
    with _checkpointer(settings.agent.checkpoint_db) as checkpointer:
        _run_turn(build_agent(checkpointer=checkpointer), str(uuid4()), f"Dossier {edc_id} : {question}")
    flush_tracing()


@app.command()
def check() -> None:
    """Vérifie l'accès à E-décès, au serveur vLLM et l'appel d'outils (§ 8.3)."""
    settings = _settings()
    ok = True
    try:
        with edc_connection(settings.edc) as connection:
            fetch_rows(connection, "SELECT 1 AS OK FROM DUAL", {})
        console.print("✅ E-décès : connexion Oracle établie")
    except Exception as exc:
        ok = False
        console.print(f"❌ E-décès : connexion impossible ({type(exc).__name__}) — détail dans les journaux")
    vlm = settings.vlm
    console.print(
        f"Modèle {vlm.model} · raisonnement {vlm.reasoning_label} · "
        f"appels parallèles {'serveur' if vlm.parallel_tool_calls is None else vlm.parallel_tool_calls}"
    )
    try:
        check_tool_calling(build_chat_model(vlm), **tool_binding_kwargs(vlm))
        console.print(f"✅ vLLM : appel d'outils natif fonctionnel ({vlm.model})")
    except Exception as exc:
        ok = False
        console.print(f"❌ vLLM : {exc}")
    raise typer.Exit(code=0 if ok else 1)
