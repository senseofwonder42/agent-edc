"""Application settings for the conversational EDC agent.

Every sub-config is a ``pydantic-settings`` model reading its values from the
environment (or a local ``.env`` file) under a dedicated prefix: ``EDC_``,
``VLM_``, ``LANGFUSE_``, ``AGENT_``, ``HISTORY_``. See ``.env.example`` for the full list.

Non-sensitive tuning (model, generation, reasoning, agent knobs, logging) also
lives in ``config.yaml``, one section per sub-config. Priority, highest first:
environment > ``.env`` > ``config.yaml`` > defaults.

Credentials are never hardcoded: they must be provided through the
environment / ``.env``. ``config.yaml`` refuses them (``env_only`` fields).

PORTED FROM: files_late_fee/src/deces_risk/config.py
PORTED ON:   2026-09-18
DIVERGENCE:  NumeaConfig, IDDConfig, GedAdoConfig and relevant_document_types
             removed; VLMConfig.tool_protocol and AgentConfig added.
"""

from __future__ import annotations

import os
from functools import cache
from pathlib import Path
from typing import ClassVar, Literal

import yaml
from pydantic import Field, JsonValue
from pydantic_settings import (
    BaseSettings,
    InitSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

#: Non-sensitive settings file, resolved from the working directory like ``.env``.
CONFIG_FILE = Path("config.yaml")


def _yaml_section(settings_cls: type[YamlSettings]) -> dict[str, JsonValue]:
    """Read the ``config.yaml`` section of ``settings_cls`` (empty when absent).

    Raises:
        ValueError: If the section holds an unknown key, or a key reserved to
            the environment (credentials, endpoints).
    """
    if not CONFIG_FILE.is_file():
        return {}
    data = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}
    section = data.get(settings_cls.yaml_section) or {}
    allowed = set(settings_cls.model_fields) - settings_cls.env_only
    rejected = sorted(set(section) - allowed)
    if rejected:
        raise ValueError(
            f"{CONFIG_FILE} [{settings_cls.yaml_section}] : clé(s) inconnue(s) ou réservée(s) "
            f"au .env : {', '.join(rejected)}"
        )
    return section


class YamlSettings(BaseSettings):
    """Settings also read from their ``config.yaml`` section, below env and ``.env``."""

    #: Section of ``config.yaml`` holding this sub-config.
    yaml_section: ClassVar[str]
    #: Fields that may only come from the environment (never from ``config.yaml``).
    env_only: ClassVar[frozenset[str]] = frozenset()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Insert ``config.yaml`` between ``.env`` and the defaults."""
        yaml_settings = InitSettingsSource(settings_cls, _yaml_section(cls))
        return init_settings, env_settings, dotenv_settings, yaml_settings, file_secret_settings


class EDCConfig(YamlSettings):
    """Access to the E-décès (EDC / ESD) Oracle database.

    Environment prefix: ``EDC_``; ``config.yaml`` section: ``edc`` (pool sizes only).
    """

    model_config = SettingsConfigDict(env_prefix="EDC_", env_file=".env", extra="ignore")
    yaml_section: ClassVar[str] = "edc"
    env_only: ClassVar[frozenset[str]] = frozenset({"dsn", "user", "password", "tns_admin", "oracle_home"})

    dsn: str = Field(default="", description="Oracle DSN of the E-décès database")
    user: str = Field(default="", description="Oracle user")
    password: str = Field(default="", description="Oracle password")
    tns_admin: str = Field(default="", description="Path to the Oracle wallet (TNS_ADMIN)")
    oracle_home: str = Field(default="", description="Path to the Oracle client (ORACLE_HOME)")
    pool_min: int = Field(default=0, ge=0, description="Connections kept open by the pool")
    pool_max: int = Field(default=4, ge=1, description="Upper bound of the connection pool")

    def apply_env(self) -> None:
        """Inject ``TNS_ADMIN`` / ``ORACLE_HOME`` into ``os.environ`` when configured."""
        if self.tns_admin:
            os.environ["TNS_ADMIN"] = self.tns_admin
        if self.oracle_home:
            os.environ["ORACLE_HOME"] = self.oracle_home


class VLMConfig(YamlSettings):
    """Configuration for the OpenAI-compatible LLM server (vLLM).

    Environment prefix: ``VLM_``; ``config.yaml`` section: ``vlm``.
    """

    model_config = SettingsConfigDict(env_prefix="VLM_", env_file=".env", extra="ignore")
    yaml_section: ClassVar[str] = "vlm"
    env_only: ClassVar[frozenset[str]] = frozenset({"base_url", "api_key"})

    base_url: str = Field(default="", description="OpenAI-compatible endpoint, e.g. http://host:8000/v1")
    api_key: str = Field(default="EMPTY", description="API key (vLLM usually accepts any value)")
    model: str = Field(default="", description="Model name as served by vLLM")
    max_tokens: int = Field(default=2000, ge=100, le=131072)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    timeout: int = Field(default=300, ge=10)
    tool_protocol: Literal["native", "json"] = Field(
        default="native", description="Native tool calling, or the constrained-JSON fallback (§ 8.4)"
    )
    enable_thinking: bool | None = Field(
        default=None,
        description="Reasoning on/off, sent as chat_template_kwargs.enable_thinking; None = model default",
    )
    # Qwen3.8's chat template rejects any other value with an HTTP 500: validate here.
    reasoning_effort: Literal["xhigh", "medium", "low"] | None = Field(
        default=None,
        description="Reasoning depth, sent as chat_template_kwargs.reasoning_effort; None = model default",
    )
    parallel_tool_calls: bool | None = Field(
        default=None, description="False = at most one tool call per model turn; None = server default"
    )
    extra_body: dict[str, JsonValue] = Field(
        default_factory=dict,
        description="Any other request parameter passed as-is to vLLM (top_p, top_k, min_p, ...)",
    )

    @property
    def reasoning_label(self) -> str:
        """``off``, the effort level, or ``defaut`` — for traces and the ``check`` command."""
        if self.enable_thinking is False:
            return "off"
        return self.reasoning_effort or "defaut"

    def request_extra_body(self) -> dict[str, JsonValue]:
        """``extra_body`` with the reasoning settings merged into ``chat_template_kwargs``."""
        template_kwargs: dict[str, JsonValue] = {}
        if self.enable_thinking is not None:
            template_kwargs["enable_thinking"] = self.enable_thinking
        if self.reasoning_effort is not None and self.enable_thinking is not False:
            template_kwargs["reasoning_effort"] = self.reasoning_effort
        body = dict(self.extra_body)
        if template_kwargs:
            existing = body.get("chat_template_kwargs")
            existing = existing if isinstance(existing, dict) else {}
            body["chat_template_kwargs"] = {**existing, **template_kwargs}
        return body


class LangfuseConfig(YamlSettings):
    """Langfuse tracing configuration.

    Tracing is a no-op when ``enabled`` is false or when the keys are absent.
    Environment prefix: ``LANGFUSE_``; ``config.yaml`` section: ``langfuse``.
    """

    model_config = SettingsConfigDict(env_prefix="LANGFUSE_", env_file=".env", extra="ignore")
    yaml_section: ClassVar[str] = "langfuse"
    env_only: ClassVar[frozenset[str]] = frozenset({"host", "public_key", "secret_key"})

    enabled: bool = Field(default=False, description="Master switch for tracing")
    host: str = Field(default="", description="Langfuse host URL (internal instance only)")
    public_key: str = Field(default="", description="Langfuse public key")
    secret_key: str = Field(default="", description="Langfuse secret key")

    @property
    def is_active(self) -> bool:
        """True when tracing is enabled and both keys are configured."""
        return self.enabled and bool(self.public_key) and bool(self.secret_key)


class AgentConfig(YamlSettings):
    """Tuning knobs of the agent — the only ones the project exposes.

    Environment prefix: ``AGENT_``; ``config.yaml`` section: ``agent``.
    """

    model_config = SettingsConfigDict(env_prefix="AGENT_", env_file=".env", extra="ignore")
    yaml_section: ClassVar[str] = "agent"

    summary_events: int = Field(default=5, ge=0, description="Events listed in the dossier summary")
    page_size: int = Field(default=20, ge=1, description="Default page size of the lists")
    max_page_size: int = Field(default=50, ge=1, description="Hard cap on the 'limite' argument")
    stale_after_minutes: int = Field(default=30, ge=1, description="Snapshot age that triggers a warning")
    checkpoint_db: Path = Field(
        default=Path(".checkpoints/agent_edc.sqlite"), description="SQLite file used by the REPL"
    )


class HistoryConfig(YamlSettings):
    """What of the conversation history is sent to the model (the state is never cut).

    Environment prefix: ``HISTORY_``; ``config.yaml`` section: ``history``.
    """

    model_config = SettingsConfigDict(env_prefix="HISTORY_", env_file=".env", extra="ignore")
    yaml_section: ClassVar[str] = "history"

    tool_results_keep_turns: int = Field(
        default=3, ge=0, description="Tool results older than this many turns are stubbed; 0 = never"
    )
    compaction: bool = Field(default=False, description="Summarize old turns once the context grows")
    compaction_trigger_tokens: int = Field(
        default=60000, ge=1000, description="Estimated context size that triggers a compaction"
    )
    compaction_keep_turns: int = Field(
        default=4, ge=1, description="Most recent turns always sent verbatim, never summarized"
    )


class Settings(YamlSettings):
    """Top-level settings composing all sub-configurations.

    ``log_level`` / ``log_dir`` come from the ``logging`` section of ``config.yaml``.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    yaml_section: ClassVar[str] = "logging"
    env_only: ClassVar[frozenset[str]] = frozenset({"edc", "vlm", "langfuse", "agent", "history"})

    log_level: str = Field(default="INFO", description="Console log level")
    log_dir: Path = Field(default=Path("logs"), description="Directory for rotating log files")

    edc: EDCConfig = Field(default_factory=EDCConfig)
    vlm: VLMConfig = Field(default_factory=VLMConfig)
    langfuse: LangfuseConfig = Field(default_factory=LangfuseConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    history: HistoryConfig = Field(default_factory=HistoryConfig)


@cache
def get_settings() -> Settings:
    """The settings, read once per process (``.env`` and ``config.yaml`` are not re-read)."""
    return Settings()
