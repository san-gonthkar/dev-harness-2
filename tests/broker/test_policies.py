"""Policy registry tests (V11 4.2)."""

from __future__ import annotations

import pytest

from dev_harness.broker.policies import (
    PolicyRegistry,
    ProviderPolicy,
    UnknownProviderError,
)
from dev_harness.config import HarnessConfig, ProviderConfig
from dev_harness.contracts.enums import ProviderId


def _config() -> HarnessConfig:
    return HarnessConfig(
        providers={
            "anthropic": ProviderConfig(rpm=50, tpm=100000),
            "ollama": ProviderConfig(max_concurrency=2),
        }
    )


@pytest.mark.unit
def test_unknown_provider_raises() -> None:
    reg = PolicyRegistry(_config())
    with pytest.raises(UnknownProviderError):
        reg.get("nonexistent")


@pytest.mark.unit
def test_ollama_exposes_max_concurrency_no_rpm() -> None:
    reg = PolicyRegistry(_config())
    policy = reg.get(ProviderId.OLLAMA)
    assert policy.max_concurrency == 2
    assert policy.rpm is None
    assert policy.is_local is True


@pytest.mark.unit
def test_anthropic_exposes_rpm() -> None:
    reg = PolicyRegistry(_config())
    policy = reg.get(ProviderId.ANTHROPIC)
    assert policy.rpm == 50
    assert policy.tpm == 100000
    assert policy.is_local is False


@pytest.mark.unit
def test_get_by_string_name() -> None:
    reg = PolicyRegistry(_config())
    policy = reg.get("anthropic")
    assert policy.provider == ProviderId.ANTHROPIC


@pytest.mark.unit
def test_all_returns_registered_policies() -> None:
    reg = PolicyRegistry(_config())
    providers = {p.provider for p in reg.all()}
    assert providers == {ProviderId.ANTHROPIC, ProviderId.OLLAMA}


@pytest.mark.unit
def test_empty_config_has_no_policies() -> None:
    reg = PolicyRegistry(HarnessConfig())
    assert reg.all() == []


@pytest.mark.unit
def test_provider_policy_frozen() -> None:
    import dataclasses

    policy = ProviderPolicy(provider=ProviderId.ANTHROPIC, rpm=10)
    with pytest.raises(dataclasses.FrozenInstanceError):
        policy.rpm = 20  # type: ignore[misc]
