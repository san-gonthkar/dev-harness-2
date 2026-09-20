"""Model registry: id -> provider, context window, pricing (V11 3.2).

Config-sourced. Every configured model resolves context window + pricing;
unknown ids raise UnknownModelError.
"""

from __future__ import annotations

from dataclasses import dataclass

from dev_harness.config import HarnessConfig, ProviderConfig
from dev_harness.contracts.enums import ProviderId
from dev_harness.contracts.errors import UnknownModelError


@dataclass(frozen=True)
class ModelEntry:
    """A resolved model registry entry."""

    model_id: str
    provider: ProviderId
    context_window: int
    max_output: int
    usd_per_mtok_in: float
    usd_per_mtok_out: float
    tokenizer: str = "estimate"


class ModelRegistry:
    """Resolves model ids to their configuration."""

    def __init__(self, config: HarnessConfig | None = None) -> None:
        self._config = config or HarnessConfig()
        self._entries: dict[str, ModelEntry] = {}
        self._load()

    def _load(self) -> None:
        """Build entries from the provider config blocks."""
        for provider_name, pconf in self._config.providers.items():
            provider = _to_provider_id(provider_name)
            context = pconf.context_window or 8192
            max_output = max(1, int(context * 0.5))
            for model_id in _model_ids_for(provider, pconf):
                self._entries[model_id] = ModelEntry(
                    model_id=model_id,
                    provider=provider,
                    context_window=context,
                    max_output=max_output,
                    usd_per_mtok_in=pconf.usd_per_mtok_in or 0.0,
                    usd_per_mtok_out=pconf.usd_per_mtok_out or 0.0,
                )

    def resolve(self, model_id: str) -> ModelEntry:
        """Resolve a model id, raising UnknownModelError if absent."""
        if model_id not in self._entries:
            raise UnknownModelError(
                f"unknown model id: {model_id}",
                remediation="Add the model to the config registry or use a known model id.",
            )
        return self._entries[model_id]

    def all_models(self) -> list[str]:
        """All registered model ids."""
        return sorted(self._entries)

    def __contains__(self, model_id: str) -> bool:
        return model_id in self._entries


def _to_provider_id(name: str) -> ProviderId:
    """Map a config provider name to a ProviderId."""
    normalized = name.lower()
    for pid in ProviderId:
        if pid.value == normalized:
            return pid
    return ProviderId.OLLAMA  # default


def _model_ids_for(provider: ProviderId, pconf: ProviderConfig) -> list[str]:
    """The model ids a provider block registers.

    Anthropic/OpenRouter register a single default model id; Ollama registers
    the configured num_ctx models (qwen2.5-coder:7b, llama3:8b).
    """
    if provider == ProviderId.OLLAMA:
        return ["qwen2.5-coder:7b", "llama3:8b"]
    return [f"{provider.value}-default"]
