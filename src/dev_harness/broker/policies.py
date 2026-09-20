"""Provider policy registry: RPM/TPM/max_concurrency (V11 4.2).

Policies are sourced from the HarnessConfig provider blocks. Unknown
providers raise UnknownProviderError. ``ollama`` is a local provider: it
exposes max_concurrency but no rpm (it is not rate-limited by a remote
quota; it is limited by local memory).
"""

from __future__ import annotations

from dataclasses import dataclass

from dev_harness.config import HarnessConfig
from dev_harness.contracts.enums import ProviderId
from dev_harness.contracts.errors import HarnessError


class UnknownProviderError(HarnessError):
    """A provider id is not in the policy registry."""

    remediation = "Add the provider to the config or use a known provider id."


@dataclass(frozen=True)
class ProviderPolicy:
    """Rate-limit policy for one provider."""

    provider: ProviderId
    rpm: int | None = None
    tpm: int | None = None
    max_concurrency: int | None = None

    @property
    def is_local(self) -> bool:
        """Local providers (ollama) are concurrency-limited, not RPM-limited."""
        return self.provider == ProviderId.OLLAMA


class PolicyRegistry:
    """Resolves provider policies from configuration."""

    def __init__(self, config: HarnessConfig | None = None) -> None:
        self._config = config or HarnessConfig()
        self._policies: dict[ProviderId, ProviderPolicy] = {}
        self._load()

    def _load(self) -> None:
        for name, pconf in self._config.providers.items():
            provider = _to_provider_id(name)
            self._policies[provider] = ProviderPolicy(
                provider=provider,
                rpm=pconf.rpm,
                tpm=pconf.tpm,
                max_concurrency=pconf.max_concurrency,
            )

    def get(self, provider: ProviderId | str) -> ProviderPolicy:
        """Resolve a policy, raising UnknownProviderError if absent."""
        if isinstance(provider, ProviderId):
            pid = provider
        else:
            pid = _to_provider_id(provider)
            if pid.value != provider.lower():
                raise UnknownProviderError(
                    f"no policy for provider: {provider}",
                    remediation="Add a provider block to the config or use a known provider.",
                )
        if pid not in self._policies:
            raise UnknownProviderError(
                f"no policy for provider: {pid.value}",
                remediation="Add a provider block to the config or use a known provider.",
            )
        return self._policies[pid]

    def all(self) -> list[ProviderPolicy]:
        """All registered policies."""
        return list(self._policies.values())


def _to_provider_id(name: str) -> ProviderId:
    """Map a config provider name to a ProviderId."""
    normalized = name.lower()
    for pid in ProviderId:
        if pid.value == normalized:
            return pid
    return ProviderId.OLLAMA  # default
