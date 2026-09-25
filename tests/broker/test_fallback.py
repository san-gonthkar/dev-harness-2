"""Provider fallback chain tests (V11 9.3).

Acceptance is exact: a 529/``ProviderOverloadedError`` on the primary falls
back to the secondary within 1 retry; the chain reports ``DEGRADED``; an
exhausted chain raises ``AllProvidersUnavailable``. The provider call is
injected, so no network is used.
"""

from __future__ import annotations

import pytest

from dev_harness.broker.fallback import FallbackChain
from dev_harness.contracts.enums import ProviderHealth, ProviderId
from dev_harness.contracts.errors import (
    AllProvidersUnavailable,
    AuthError,
    ProviderOverloadedError,
    TransientError,
)

PRIMARY = ProviderId.ANTHROPIC
SECONDARY = ProviderId.OPENROUTER


@pytest.mark.unit
def test_overload_on_primary_falls_back_to_secondary_within_one_retry() -> None:
    """(a) A 529 on the primary falls back to the secondary in exactly 1 retry."""
    calls: list[ProviderId] = []

    def call(provider: ProviderId) -> str:
        calls.append(provider)
        if provider is PRIMARY:
            raise ProviderOverloadedError("529 overloaded")
        return "ok"

    chain = FallbackChain([PRIMARY, SECONDARY])
    result = chain.call(call)

    assert result.value == "ok"
    assert result.provider is SECONDARY
    assert result.retries == 1
    assert calls == [PRIMARY, SECONDARY]


@pytest.mark.unit
def test_registry_reports_degraded_after_fallback() -> None:
    """(b) The chain reports DEGRADED for the bypassed primary."""

    def call(provider: ProviderId) -> str:
        if provider is PRIMARY:
            raise ProviderOverloadedError("529 overloaded")
        return "ok"

    chain = FallbackChain([PRIMARY, SECONDARY])
    chain.call(call)

    assert chain.health(PRIMARY) is ProviderHealth.DEGRADED
    assert chain.health(SECONDARY) is ProviderHealth.HEALTHY
    assert chain.is_degraded() is True
    assert chain.status()[PRIMARY] is ProviderHealth.DEGRADED


@pytest.mark.unit
def test_transient_error_is_retryable_and_falls_back() -> None:
    """A TransientError is retryable and advances to the next provider."""

    def call(provider: ProviderId) -> str:
        if provider is PRIMARY:
            raise TransientError("connection reset")
        return "ok"

    chain = FallbackChain([PRIMARY, SECONDARY])
    result = chain.call(call)

    assert result.provider is SECONDARY
    assert result.retries == 1


@pytest.mark.unit
def test_healthy_primary_does_not_fall_back() -> None:
    """A healthy primary serves the call with zero retries."""

    def call(provider: ProviderId) -> str:
        return f"served-by-{provider.value}"

    chain = FallbackChain([PRIMARY, SECONDARY])
    result = chain.call(call)

    assert result.provider is PRIMARY
    assert result.retries == 0
    assert chain.is_degraded() is False


@pytest.mark.negative
def test_exhausted_chain_raises_all_providers_unavailable() -> None:
    """(c) Every provider overloaded -> AllProvidersUnavailable."""

    def call(provider: ProviderId) -> str:
        raise ProviderOverloadedError(f"{provider.value} overloaded")

    chain = FallbackChain([PRIMARY, SECONDARY])
    with pytest.raises(AllProvidersUnavailable) as excinfo:
        chain.call(call)

    assert excinfo.value.remediation
    assert chain.is_degraded() is True


@pytest.mark.negative
def test_auth_error_does_not_fall_back() -> None:
    """A non-retryable AuthError propagates without trying the secondary."""
    calls: list[ProviderId] = []

    def call(provider: ProviderId) -> str:
        calls.append(provider)
        raise AuthError("bad key")

    chain = FallbackChain([PRIMARY, SECONDARY])
    with pytest.raises(AuthError):
        chain.call(call)

    assert calls == [PRIMARY]
    assert chain.is_degraded() is False


@pytest.mark.negative
def test_empty_provider_list_is_rejected() -> None:
    """A chain with no providers is a programming error."""
    with pytest.raises(ValueError):
        FallbackChain([])
