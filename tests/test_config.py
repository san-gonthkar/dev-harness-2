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
    # Ollama integration disabled (2026-09-27).
    assert "ollama" not in config.providers
    assert config.broker.allow_unbrokered is False


def test_secret_env_vars_are_not_config_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DEV_HARNESS_*_API_KEY belongs to SecretsProvider, not the config schema."""
    cfg = tmp_path / "c.toml"
    cfg.write_text("[providers.openrouter]\nrpm = 50\n", encoding="utf-8")
    monkeypatch.setenv("DEV_HARNESS_OPENROUTER_API_KEY", "sk-secret")
    monkeypatch.setenv("DEV_HARNESS_ANTHROPIC_TOKEN", "tok")
    config = load_config(cfg)
    assert config.providers["openrouter"].rpm == 50


def test_provider_auth_defaults_to_api_key(tmp_path: Path) -> None:
    cfg = tmp_path / "c.toml"
    cfg.write_text("[providers.openrouter]\nrpm = 50\n", encoding="utf-8")
    config = load_config(cfg)
    assert config.providers["openrouter"].auth == "api_key"


def test_provider_auth_login_is_accepted_by_schema(tmp_path: Path) -> None:
    """The schema accepts auth = 'login'; the factory fails closed at build time."""
    cfg = tmp_path / "c.toml"
    cfg.write_text(
        '[providers.openrouter]\nrpm = 50\nauth = "login"\n', encoding="utf-8"
    )
    config = load_config(cfg)
    assert config.providers["openrouter"].auth == "login"
