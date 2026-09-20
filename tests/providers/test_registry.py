"""Model registry tests (V11 3.2)."""

from __future__ import annotations

import pytest

from dev_harness.config import HarnessConfig, ProviderConfig
from dev_harness.contracts.errors import UnknownModelError
from dev_harness.providers.registry import ModelRegistry

pytestmark = pytest.mark.unit


def _config() -> HarnessConfig:
    return HarnessConfig(
        providers={
            "anthropic": ProviderConfig(
                context_window=200000,
                usd_per_mtok_in=3.0,
                usd_per_mtok_out=15.0,
            ),
            "openrouter": ProviderConfig(
                context_window=128000,
                usd_per_mtok_in=0.5,
                usd_per_mtok_out=1.5,
            ),
            "ollama": ProviderConfig(
                context_window=32768,
                num_ctx=8192,
                usd_per_mtok_in=0.0,
                usd_per_mtok_out=0.0,
            ),
        }
    )


def test_every_model_resolves() -> None:
    registry = ModelRegistry(_config())
    for model_id in registry.all_models():
        entry = registry.resolve(model_id)
        assert entry.context_window > 0
        assert entry.usd_per_mtok_in >= 0
        assert entry.usd_per_mtok_out >= 0


def test_max_output_less_than_context() -> None:
    registry = ModelRegistry(_config())
    for model_id in registry.all_models():
        entry = registry.resolve(model_id)
        assert entry.max_output < entry.context_window


def test_unknown_model_raises() -> None:
    registry = ModelRegistry(_config())
    with pytest.raises(UnknownModelError):
        registry.resolve("no-such-model")


def test_ollama_models_registered() -> None:
    registry = ModelRegistry(_config())
    assert "qwen2.5-coder:7b" in registry
    assert "llama3:8b" in registry


def test_anthropic_default_registered() -> None:
    registry = ModelRegistry(_config())
    assert "anthropic-default" in registry


def test_contains_operator() -> None:
    registry = ModelRegistry(_config())
    assert "qwen2.5-coder:7b" in registry
    assert "bogus" not in registry


def test_empty_config_no_models() -> None:
    registry = ModelRegistry(HarnessConfig())
    assert registry.all_models() == []
