"""Provider factory: config -> a live LLM client (V11 3.x extension).

This is the single place that maps a provider id to a concrete adapter, so
adding a provider is one registry entry. It lives in ``providers/`` (not
``engine/``) because the AST guard forbids ``engine/`` from importing
adapters; the engine receives an already-built client.

The factory is **provider-agnostic**: it returns an
:class:`~dev_harness.providers.base.LLMClient`, which structurally satisfies
the engine's ``CompletionClient`` protocol. Nothing downstream knows which
vendor is behind it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from dev_harness.config import HarnessConfig, ProviderConfig
from dev_harness.contracts.enums import ProviderId
from dev_harness.contracts.errors import HarnessError
from dev_harness.providers.auth import AuthStrategy, build_auth
from dev_harness.providers.base import LLMClient
from dev_harness.secrets import SecretsProvider


class UnknownProviderError(HarnessError):
    """The requested provider has no registered adapter."""

    remediation = "Use a provider with a registered adapter (openrouter, anthropic)."


class ProviderNotConfiguredError(HarnessError):
    """The provider has no config block."""

    remediation = "Add a [providers.<name>] block to dev-harness.toml."


@dataclass(frozen=True)
class ProviderSpec:
    """How to build one provider's client.

    :param provider: the canonical provider id.
    :param secret_name: the :class:`SecretsProvider` key holding the credential.
    :param default_model: the model id used when the caller passes none.
    :param build: constructs the adapter from (api_key, base_url).
    """

    provider: ProviderId
    secret_name: str
    default_model: str
    build: Callable[[str, str | None], LLMClient]


def _build_openrouter(api_key: str, base_url: str | None) -> LLMClient:
    from dev_harness.providers.openrouter import OpenRouterClient

    if base_url:
        return OpenRouterClient(api_key, base_url=base_url)
    return OpenRouterClient(api_key)


def _build_anthropic(api_key: str, base_url: str | None) -> LLMClient:
    from dev_harness.providers.anthropic import AnthropicClient

    if base_url:
        return AnthropicClient(api_key, base_url=base_url)
    return AnthropicClient(api_key)


#: The provider registry. Adding a provider is one entry here.
REGISTRY: dict[ProviderId, ProviderSpec] = {
    ProviderId.OPENROUTER: ProviderSpec(
        provider=ProviderId.OPENROUTER,
        secret_name="OPENROUTER_API_KEY",
        default_model="anthropic/claude-3.5-sonnet",
        build=_build_openrouter,
    ),
    ProviderId.ANTHROPIC: ProviderSpec(
        provider=ProviderId.ANTHROPIC,
        secret_name="ANTHROPIC_API_KEY",
        default_model="claude-3-5-sonnet-latest",
        build=_build_anthropic,
    ),
}


def register(spec: ProviderSpec) -> None:
    """Register (or replace) a provider spec — the extension point."""
    REGISTRY[spec.provider] = spec


def resolve_provider(name: str) -> ProviderId:
    """Map a provider name to its id, raising for an unknown name."""
    normalized = name.strip().lower()
    for pid in ProviderId:
        if pid.value == normalized:
            return pid
    raise UnknownProviderError(f"unknown provider: {name!r}")


def build_client(
    provider: str | ProviderId,
    *,
    config: HarnessConfig | None = None,
    secrets: SecretsProvider | None = None,
    base_url: str | None = None,
) -> LLMClient:
    """Build a live client for ``provider`` from config + secrets.

    Raises :class:`UnknownProviderError` for an unregistered provider,
    :class:`ProviderNotConfiguredError` when the config has no block, and
    :class:`~dev_harness.providers.auth.AuthError` when the credential is
    missing or the auth mode is unimplemented.
    """
    pid = provider if isinstance(provider, ProviderId) else resolve_provider(provider)
    spec = REGISTRY.get(pid)
    if spec is None:
        raise UnknownProviderError(f"no adapter registered for {pid.value!r}")

    cfg = config or HarnessConfig()
    pconf: ProviderConfig | None = cfg.providers.get(pid.value)
    if pconf is None:
        raise ProviderNotConfiguredError(
            f"provider {pid.value!r} has no config block",
        )

    auth: AuthStrategy = build_auth(
        pconf.auth,
        provider_name=pid.value,
        secret_name=spec.secret_name,
        secrets=secrets or SecretsProvider(),
    )
    return spec.build(auth.resolve(), base_url or pconf.base_url)


def default_model_for(provider: str | ProviderId) -> str:
    """The default model id for a provider."""
    pid = provider if isinstance(provider, ProviderId) else resolve_provider(provider)
    spec = REGISTRY.get(pid)
    if spec is None:
        raise UnknownProviderError(f"no adapter registered for {pid.value!r}")
    return spec.default_model
