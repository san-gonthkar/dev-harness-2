"""Provider factory + auth tests (V11 3.x extension)."""

from __future__ import annotations

import pytest

from dev_harness.config import HarnessConfig, ProviderConfig
from dev_harness.contracts.enums import ProviderId
from dev_harness.providers.auth import (
    ApiKeyAuth,
    AuthError,
    AuthNotImplementedError,
    OAuthDeviceAuth,
    build_auth,
)
from dev_harness.providers.factory import (
    REGISTRY,
    ProviderNotConfiguredError,
    UnknownProviderError,
    build_client,
    default_model_for,
    register,
    resolve_provider,
)
from dev_harness.secrets import SecretsProvider

_KEY = "sk-test-123"


def _config(**overrides: object) -> HarnessConfig:
    block = ProviderConfig(
        rpm=50,
        context_window=128000,
        usd_per_mtok_in=0.5,
        usd_per_mtok_out=1.5,
        **overrides,  # type: ignore[arg-type]
    )
    return HarnessConfig(providers={"openrouter": block})


def _secrets() -> SecretsProvider:
    return SecretsProvider(env={"DEV_HARNESS_OPENROUTER_API_KEY": _KEY})


# --- auth -------------------------------------------------------------------


@pytest.mark.unit
def test_api_key_auth_resolves_from_env() -> None:
    auth = ApiKeyAuth(secret_name="OPENROUTER_API_KEY", provider=_secrets())
    assert auth.resolve() == _KEY


@pytest.mark.unit
def test_api_key_auth_missing_raises_with_remediation() -> None:
    auth = ApiKeyAuth(
        secret_name="OPENROUTER_API_KEY", provider=SecretsProvider(env={})
    )
    with pytest.raises(AuthError) as excinfo:
        auth.resolve()
    assert excinfo.value.remediation
    assert "DEV_HARNESS_OPENROUTER_API_KEY" in excinfo.value.remediation


@pytest.mark.unit
def test_login_auth_fails_closed() -> None:
    """The OAuth strategy is deliberately unimplemented."""
    with pytest.raises(AuthNotImplementedError) as excinfo:
        OAuthDeviceAuth(provider_name="openrouter")
    assert excinfo.value.remediation


@pytest.mark.unit
def test_build_auth_defaults_to_api_key() -> None:
    auth = build_auth(
        "",
        provider_name="openrouter",
        secret_name="OPENROUTER_API_KEY",
        secrets=_secrets(),
    )
    assert isinstance(auth, ApiKeyAuth)


@pytest.mark.unit
def test_build_auth_login_raises() -> None:
    with pytest.raises(AuthNotImplementedError):
        build_auth(
            "login",
            provider_name="openrouter",
            secret_name="OPENROUTER_API_KEY",
            secrets=_secrets(),
        )


@pytest.mark.unit
def test_build_auth_unknown_mode_raises() -> None:
    with pytest.raises(AuthError):
        build_auth(
            "magic",
            provider_name="openrouter",
            secret_name="OPENROUTER_API_KEY",
            secrets=_secrets(),
        )


# --- factory ----------------------------------------------------------------


@pytest.mark.unit
def test_resolve_provider_known() -> None:
    assert resolve_provider("openrouter") is ProviderId.OPENROUTER
    assert resolve_provider("  ANTHROPIC ") is ProviderId.ANTHROPIC


@pytest.mark.unit
def test_resolve_provider_unknown_raises() -> None:
    with pytest.raises(UnknownProviderError):
        resolve_provider("gemini")


@pytest.mark.unit
def test_build_client_openrouter() -> None:
    client = build_client("openrouter", config=_config(), secrets=_secrets())
    assert client is not None
    assert hasattr(client, "complete")


@pytest.mark.unit
def test_build_client_requires_config_block() -> None:
    with pytest.raises(ProviderNotConfiguredError):
        build_client("openrouter", config=HarnessConfig(), secrets=_secrets())


@pytest.mark.unit
def test_build_client_unknown_provider_raises() -> None:
    with pytest.raises(UnknownProviderError):
        build_client("gemini", config=_config(), secrets=_secrets())


@pytest.mark.unit
def test_build_client_login_mode_fails_closed() -> None:
    with pytest.raises(AuthNotImplementedError):
        build_client(
            "openrouter",
            config=_config(auth="login"),
            secrets=_secrets(),
        )


@pytest.mark.unit
def test_build_client_missing_key_fails_closed() -> None:
    with pytest.raises(AuthError):
        build_client("openrouter", config=_config(), secrets=SecretsProvider(env={}))


@pytest.mark.unit
def test_build_client_honours_base_url_override() -> None:
    client = build_client(
        "openrouter",
        config=_config(),
        secrets=_secrets(),
        base_url="http://fake",
    )
    assert client.base_url == "http://fake"  # type: ignore[attr-defined]


@pytest.mark.unit
def test_default_model_for_openrouter() -> None:
    assert (
        default_model_for("openrouter") == REGISTRY[ProviderId.OPENROUTER].default_model
    )


@pytest.mark.unit
def test_default_model_for_unknown_raises() -> None:
    with pytest.raises(UnknownProviderError):
        default_model_for("gemini")


@pytest.mark.unit
def test_register_extends_the_registry() -> None:
    """The extension point: a new provider is one registry entry."""
    from dev_harness.providers.factory import ProviderSpec

    spec = ProviderSpec(
        provider=ProviderId.OLLAMA,
        secret_name="OLLAMA_API_KEY",
        default_model="qwen2.5-coder:7b",
        build=lambda key, url: object(),  # type: ignore[arg-type,return-value]
    )
    original = REGISTRY.get(ProviderId.OLLAMA)
    try:
        register(spec)
        assert REGISTRY[ProviderId.OLLAMA] is spec
        assert default_model_for("ollama") == "qwen2.5-coder:7b"
    finally:
        if original is None:
            REGISTRY.pop(ProviderId.OLLAMA, None)
        else:
            REGISTRY[ProviderId.OLLAMA] = original
