"""Provider fallback chain with a DEGRADED notice (V11 9.3).

An ordered list of providers is tried in turn. A *retryable* provider error
(``ProviderOverloadedError`` / ``TransientError``) on the primary moves the
call to the next provider **once per provider**; a *non-retryable* error
(``AuthError``) does not fall back. When every provider is exhausted the chain
raises ``AllProvidersUnavailable``.

The provider call is injected so the chain is testable without any network.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TypeVar

from dev_harness.contracts.enums import ProviderHealth, ProviderId
from dev_harness.contracts.errors import AllProvidersUnavailable, ProviderError

T = TypeVar("T")

#: A provider call: given a provider id, return a result or raise ProviderError.
ProviderCall = Callable[[ProviderId], T]


@dataclass(frozen=True)
class FallbackResult:
    """The outcome of a successful fallback-chain call."""

    value: object
    provider: ProviderId
    retries: int
    health: ProviderHealth


class FallbackChain:
    """Tries an ordered list of providers, falling back once per provider."""

    def __init__(self, providers: Sequence[ProviderId]) -> None:
        if not providers:
            raise ValueError("FallbackChain requires at least one provider")
        self._providers: tuple[ProviderId, ...] = tuple(providers)
        self._health: dict[ProviderId, ProviderHealth] = {
            pid: ProviderHealth.HEALTHY for pid in self._providers
        }

    @property
    def providers(self) -> tuple[ProviderId, ...]:
        """The ordered provider list."""
        return self._providers

    def health(self, provider: ProviderId) -> ProviderHealth:
        """The recorded health of a provider."""
        return self._health.get(provider, ProviderHealth.HEALTHY)

    def status(self) -> dict[ProviderId, ProviderHealth]:
        """A snapshot of every provider's health."""
        return dict(self._health)

    def is_degraded(self) -> bool:
        """True when any provider has been bypassed as DEGRADED."""
        return any(h is ProviderHealth.DEGRADED for h in self._health.values())

    def call(self, fn: ProviderCall[T]) -> FallbackResult:
        """Call ``fn`` against each provider until one succeeds.

        Retryable errors advance to the next provider (one retry per provider);
        non-retryable errors propagate immediately. An exhausted chain raises
        ``AllProvidersUnavailable``.
        """
        retries = 0
        last_error: ProviderError | None = None
        for provider in self._providers:
            try:
                value = fn(provider)
            except ProviderError as exc:
                if not getattr(exc, "retryable", False):
                    raise
                self._health[provider] = ProviderHealth.DEGRADED
                last_error = exc
                retries += 1
                continue
            self._health[provider] = ProviderHealth.HEALTHY
            return FallbackResult(
                value=value,
                provider=provider,
                retries=retries,
                health=ProviderHealth.HEALTHY,
            )
        raise AllProvidersUnavailable(
            f"all {len(self._providers)} providers unavailable",
            remediation=(
                "Restore at least one provider, or resume from the last "
                "checkpoint once connectivity returns."
            ),
        ) from last_error
