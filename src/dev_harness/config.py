"""Configuration loader: TOML + DEV_HARNESS_* env overrides (V11 0.8).

Provider blocks carry rpm, tpm, max_concurrency, context_window, num_ctx,
keep_alive, usd_per_mtok_in/out.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from dev_harness.contracts.errors import ConfigError


class ProviderConfig(BaseModel):
    """Rate-limit and model configuration for a provider."""

    model_config = ConfigDict(extra="forbid")

    rpm: int | None = None
    tpm: int | None = None
    max_concurrency: int | None = None
    context_window: int | None = None
    num_ctx: int | None = None
    keep_alive: str | None = None
    usd_per_mtok_in: float | None = None
    usd_per_mtok_out: float | None = None


class BrokerConfig(BaseModel):
    """Broker topology (ADR-0002: host-scoped)."""

    model_config = ConfigDict(extra="forbid")

    socket_path: str | None = None
    allow_unbrokered: bool = False


class HarnessConfig(BaseModel):
    """Top-level configuration."""

    model_config = ConfigDict(extra="forbid")

    workspace_path: str = "."
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    broker: BrokerConfig = Field(default_factory=BrokerConfig)
    budget_usd_per_run: float | None = None
    budget_usd_per_day: float | None = None


def _coerce(raw: str) -> object:
    """Coerce an env string to int/float/bool where possible."""
    low = raw.lower()
    if low in {"true", "false"}:
        return low == "true"
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw


def _apply_env_overrides(data: dict[str, object]) -> dict[str, object]:
    """Apply DEV_HARNESS_<SECTION>__<KEY> overrides on top of TOML values.

    ``DEV_HARNESS_ANTHROPIC__RPM=10`` overrides ``providers.anthropic.rpm``.
    ``DEV_HARNESS_BROKER__ALLOW_UNBROKERED=true`` overrides ``broker.allow_unbrokered``.
    ``DEV_HARNESS_WORKSPACE_PATH`` overrides a top-level scalar.
    """
    providers = _nested(data, "providers")
    for key, raw in os.environ.items():
        if not key.startswith("DEV_HARNESS_"):
            continue
        rest = key[len("DEV_HARNESS_") :]
        value: object = _coerce(raw)
        if "__" in rest:
            section, field = rest.split("__", 1)
            section_l = section.lower()
            field_l = field.lower()
            if section_l == "broker":
                _nested(data, "broker")[field_l] = value
            else:
                _nested(providers, section_l)[field_l] = value
        else:
            # top-level scalar, lowercased (e.g. WORKSPACE_PATH)
            data[rest.lower()] = value
    return data


def _nested(data: dict[str, object], key: str) -> dict[str, object]:
    """Return the nested dict under ``key``, creating it if absent."""
    existing = data.get(key)
    if not isinstance(existing, dict):
        existing = {}
        data[key] = existing
    return existing


def load_config(path: str | Path | None = None) -> HarnessConfig:
    """Load configuration from TOML (or default) plus env overrides."""
    cfg_path = Path(path) if path else Path.cwd() / "dev-harness.toml"
    if cfg_path.exists():
        try:
            with cfg_path.open("rb") as f:
                data = tomllib.load(f)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(
                f"invalid TOML in {cfg_path}: {exc}",
                remediation="Fix the TOML syntax and retry.",
            ) from exc
    else:
        data = {}
    data = _apply_env_overrides(data)
    try:
        return HarnessConfig.model_validate(data)
    except ValidationError as exc:
        missing = [e["loc"] for e in exc.errors()]
        raise ConfigError(
            f"invalid configuration; missing or bad keys: {missing}",
            remediation="Fix the offending config key and retry.",
        ) from exc
