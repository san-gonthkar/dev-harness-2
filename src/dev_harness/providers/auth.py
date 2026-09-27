"""Provider authentication strategies (V11 3.x extension).

Authentication is pluggable so a provider can be added without touching the
pipeline. Two strategies are defined:

* :class:`ApiKeyAuth` — resolves a static API key via
  :class:`~dev_harness.secrets.SecretsProvider` (env -> keyring -> 0600 file).
  This is the implemented path.
* :class:`OAuthDeviceAuth` — the interface for a future browser/device-code
  login (as used by GitHub Copilot / Claude Code). It is **not implemented**;
  constructing it raises :class:`AuthNotImplementedError` so a caller can never
  silently get an unauthenticated client.

The config selects the mode with ``auth = "api_key" | "login"`` on a provider
block. ``login`` is accepted by the config schema but fails closed at build
time until the OAuth strategy lands.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from dev_harness.contracts.errors import HarnessError
from dev_harness.secrets import Secret, SecretsProvider


class AuthError(HarnessError):
    """Authentication could not be resolved."""

    remediation = "Set the provider API key or choose a supported auth mode."


class AuthNotImplementedError(AuthError):
    """The requested auth mode is not implemented yet."""

    remediation = "Use auth = 'api_key' until the login flow is implemented."


@runtime_checkable
class AuthStrategy(Protocol):
    """Resolves the credential a provider adapter needs."""

    def resolve(self) -> str:
        """Return the credential (API key or bearer token)."""
        ...


@dataclass(frozen=True)
class ApiKeyAuth:
    """Resolve a static API key from env, keyring, or a 0600 file.

    ``secret_name`` is the key passed to :class:`SecretsProvider`, e.g.
    ``OPENROUTER_API_KEY`` (resolved from ``DEV_HARNESS_OPENROUTER_API_KEY``).
    """

    secret_name: str
    provider: SecretsProvider

    def resolve(self) -> str:
        """Return the API key, raising :class:`AuthError` when absent."""
        try:
            secret: Secret = self.provider.get(self.secret_name)
        except HarnessError as exc:
            raise AuthError(
                f"no credential for {self.secret_name}",
                remediation=(
                    f"Set DEV_HARNESS_{self.secret_name} in the environment, "
                    "store it in the OS keyring, or provide a 0600 key file."
                ),
            ) from exc
        return secret.reveal()


@dataclass(frozen=True)
class OAuthDeviceAuth:
    """Placeholder for a future device-code login flow.

    Deliberately unimplemented: constructing it raises so no caller can
    proceed with an unauthenticated client.
    """

    provider_name: str

    def __post_init__(self) -> None:
        raise AuthNotImplementedError(
            f"login auth is not implemented for {self.provider_name}",
        )

    def resolve(self) -> str:  # pragma: no cover - unreachable by construction
        """Unreachable; construction always raises."""
        raise AuthNotImplementedError(
            f"login auth is not implemented for {self.provider_name}",
        )


def build_auth(
    mode: str,
    *,
    provider_name: str,
    secret_name: str,
    secrets: SecretsProvider,
) -> AuthStrategy:
    """Build an auth strategy from a config ``auth`` mode.

    ``api_key`` (the default) resolves a static key; ``login`` fails closed
    until the OAuth flow is implemented.
    """
    normalized = (mode or "api_key").strip().lower()
    if normalized == "api_key":
        return ApiKeyAuth(secret_name=secret_name, provider=secrets)
    if normalized == "login":
        return OAuthDeviceAuth(provider_name=provider_name)
    raise AuthError(
        f"unknown auth mode: {mode!r}",
        remediation="Use auth = 'api_key' or auth = 'login'.",
    )
