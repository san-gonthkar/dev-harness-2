"""Secrets provider: env -> keyring -> 0600 file (V11 0.9).

Secrets never enter HarnessState; they live in env, the OS keyring, or a
0600 file, and their repr is masked.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from pathlib import Path

import keyring

from dev_harness.contracts.errors import InsecureKeyFileError, SecretsError

_SECRET_PREFIX = "DEV_HARNESS_"


class Secret:
    """A secret whose repr is masked."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret('***')"

    def __str__(self) -> str:
        return "***"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Secret):
            return self._value == other._value
        return NotImplemented


def is_insecure_mode(mode: int) -> bool:
    """True if the mode grants group/other access (looser than 0600)."""
    return bool(mode & 0o077)


def _check_permissions(path: Path) -> None:
    """Refuse a key file whose permissions are looser than 0600 (POSIX).

    On non-POSIX platforms (Windows) the POSIX mode bits are not meaningful,
    so enforcement is limited to POSIX; the check is unit-tested directly via
    :func:`is_insecure_mode`.
    """
    if os.name != "posix":
        return
    mode = stat.S_IMODE(path.stat().st_mode)
    if is_insecure_mode(mode):
        raise InsecureKeyFileError(
            f"key file {path} has permissions {oct(mode)}",
            remediation="chmod 600 the key file, or move the secret to the OS keyring.",
        )


class SecretsProvider:
    """Resolves secrets from env, the keyring, then a 0600 file."""

    def __init__(
        self,
        *,
        key_file: str | Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._key_file = Path(key_file) if key_file else None
        self._env = env if env is not None else os.environ

    def _from_env(self, name: str) -> str | None:
        value = self._env.get(_SECRET_PREFIX + name)
        return value

    def _from_keyring(self, name: str) -> str | None:
        try:
            return keyring.get_password("dev-harness", name)
        except Exception:  # noqa: BLE001 - keyring backends raise arbitrary errors; treat as missing
            return None

    def _from_file(self, name: str) -> str | None:
        if self._key_file is None:
            return None
        if not self._key_file.exists():
            return None
        _check_permissions(self._key_file)
        return self._key_file.read_text(encoding="utf-8").strip()

    def get(self, name: str) -> Secret:
        """Return a secret resolved by precedence env > keyring > file.

        Raises :class:`SecretsError` (a ``KeyError``) if not found anywhere.
        """
        value = self._from_env(name)
        if value is not None:
            return Secret(value)
        value = self._from_keyring(name)
        if value is not None:
            return Secret(value)
        value = self._from_file(name)
        if value is not None:
            return Secret(value)
        raise SecretsError(f"no secret found for {name!r}")
