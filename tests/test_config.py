"""config.yaml loading: priority, refusal of secrets, reasoning switch."""

from pathlib import Path

import pytest

from agent_edc.config import AgentConfig, EDCConfig, Settings, VLMConfig
from agent_edc.llm import build_chat_model, without_thinking

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty working directory (no .env, no config.yaml) and no VLM_/AGENT_ variables."""
    monkeypatch.chdir(tmp_path)
    for name in ("VLM_MODEL", "VLM_ENABLE_THINKING", "VLM_TEMPERATURE", "AGENT_PAGE_SIZE", "EDC_POOL_MAX"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def _write_config(workdir: Path, text: str) -> None:
    (workdir / "config.yaml").write_text(text, encoding="utf-8")


def test_yaml_section_is_read(workdir: Path) -> None:
    _write_config(
        workdir,
        "vlm:\n  model: qwen-test\n  enable_thinking: true\n  extra_body:\n    top_k: 20\n"
        "agent:\n  page_size: 7\n",
    )
    vlm = VLMConfig()
    assert vlm.model == "qwen-test"
    assert vlm.enable_thinking is True
    assert vlm.extra_body == {"top_k": 20}
    assert AgentConfig().page_size == 7


def test_environment_overrides_yaml(workdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(workdir, "vlm:\n  model: from-yaml\n  enable_thinking: true\n")
    monkeypatch.setenv("VLM_MODEL", "from-env")
    monkeypatch.setenv("VLM_ENABLE_THINKING", "false")
    vlm = VLMConfig()
    assert vlm.model == "from-env"
    assert vlm.enable_thinking is False


def test_missing_file_or_section_falls_back_to_defaults(workdir: Path) -> None:
    assert VLMConfig().enable_thinking is None
    _write_config(workdir, "agent:\n  page_size: 7\n")
    assert VLMConfig().temperature == 0.2


@pytest.mark.parametrize(
    ("section", "key"), [("vlm", "api_key"), ("vlm", "base_url"), ("langfuse", "secret_key")]
)
def test_secrets_are_refused_in_yaml(workdir: Path, section: str, key: str) -> None:
    _write_config(workdir, f"{section}:\n  {key}: leaked\n")
    with pytest.raises(ValueError, match=key):
        Settings()


def test_unknown_key_is_refused(workdir: Path) -> None:
    _write_config(workdir, "vlm:\n  temprature: 0.6\n")
    with pytest.raises(ValueError, match="temprature"):
        VLMConfig()


def test_reasoning_switch_is_merged_into_chat_template_kwargs(workdir: Path) -> None:
    config = VLMConfig(
        enable_thinking=False,
        extra_body={"top_k": 20, "chat_template_kwargs": {"custom": 1}},
    )
    assert config.request_extra_body() == {
        "top_k": 20,
        "chat_template_kwargs": {"custom": 1, "enable_thinking": False},
    }
    assert VLMConfig(enable_thinking=None, extra_body={}).request_extra_body() == {}


def test_chat_model_receives_extra_body(workdir: Path) -> None:
    config = VLMConfig(
        base_url="http://vllm.invalid/v1", model="qwen-test", enable_thinking=True, extra_body={"top_k": 20}
    )
    model = build_chat_model(config)
    assert model.extra_body == {"top_k": 20, "chat_template_kwargs": {"enable_thinking": True}}


def test_shipped_config_yaml_is_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(PROJECT_ROOT)
    settings = Settings()
    assert settings.vlm.model
    assert settings.vlm.tool_protocol in {"native", "json"}


def test_reasoning_effort_is_validated(workdir: Path) -> None:
    _write_config(workdir, "vlm:\n  reasoning_effort: high\n")
    with pytest.raises(ValueError, match="reasoning_effort"):
        VLMConfig()


def test_reasoning_effort_is_sent_only_with_thinking(workdir: Path) -> None:
    thinking = VLMConfig(enable_thinking=True, reasoning_effort="medium", extra_body={})
    assert thinking.request_extra_body() == {
        "chat_template_kwargs": {"enable_thinking": True, "reasoning_effort": "medium"}
    }
    assert thinking.reasoning_label == "medium"
    off = VLMConfig(enable_thinking=False, reasoning_effort="medium", extra_body={})
    assert off.request_extra_body() == {"chat_template_kwargs": {"enable_thinking": False}}
    assert off.reasoning_label == "off"


def test_edc_section_accepts_pool_sizes_only(workdir: Path) -> None:
    _write_config(workdir, "edc:\n  pool_max: 8\n")
    assert EDCConfig().pool_max == 8
    _write_config(workdir, "edc:\n  password: leaked\n")
    with pytest.raises(ValueError, match="password"):
        EDCConfig()


def test_without_thinking_keeps_the_other_parameters(workdir: Path) -> None:
    config = VLMConfig(
        base_url="http://vllm.invalid/v1",
        model="qwen-test",
        enable_thinking=True,
        reasoning_effort="low",
        extra_body={"top_k": 20},
    )
    bound = without_thinking(build_chat_model(config))
    assert bound.kwargs["extra_body"] == {"top_k": 20, "chat_template_kwargs": {"enable_thinking": False}}
