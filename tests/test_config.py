"""Config loader precedence tests (V11 0.8)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dev_harness.config import load_config
from dev_harness.contracts.errors import ConfigError

pytestmark = pytest.mark.unit


def test_env_override_beats_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "c.toml"
    cfg.write_text("[providers.anthropic]\nrpm = 50\n", encoding="utf-8")
    monkeypatch.setenv("DEV_HARNESS_ANTHROPIC__RPM", "10")
    config = load_config(cfg)
    assert config.providers["anthropic"].rpm == 10


def test_broker_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "c.toml"
    cfg.write_text("[broker]\nallow_unbrokered = false\n", encoding="utf-8")
    monkeypatch.setenv("DEV_HARNESS_BROKER__ALLOW_UNBROKERED", "true")
    config = load_config(cfg)
    assert config.broker.allow_unbrokered is True


def test_missing_required_key_raises_config_error(tmp_path: Path) -> None:
    cfg = tmp_path / "c.toml"
    cfg.write_text("workspace_path = 123\n", encoding="utf-8")
    with pytest.raises(ConfigError) as excinfo:
        load_config(cfg)
    assert excinfo.value.remediation


def test_invalid_toml_raises_config_error(tmp_path: Path) -> None:
    cfg = tmp_path / "bad.toml"
    cfg.write_text("this is not toml [", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(cfg)


def test_example_config_loads() -> None:
    example = Path(__file__).resolve().parents[1] / "dev-harness.example.toml"
    config = load_config(example)
    assert config.providers["anthropic"].rpm == 50
    assert config.providers["ollama"].num_ctx == 8192
    assert config.broker.allow_unbrokered is False
