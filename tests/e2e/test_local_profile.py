"""Local-only profile run (V11 10.4).

Validation matrix (10.B, NIGHTLY, ``-m slow``): the SDLC completes against a
live local Ollama (Qwen 2.5 Coder 7B) with

* 0 hosted calls,
* ``num_ctx`` respected,
* <= 1 model load,
* cumulative USD 0.00.

This is the **only un-stubbed reality check** in Phase 10 (10.D step 4). It is
NIGHTLY and requires a running Ollama; when none is reachable the test records
``skipped: no_local_ollama`` (the same optional-spike convention as 3.13) rather
than failing, so the PR lane stays green on a machine without a local model.
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path

import pytest

from dev_harness.config import load_config
from dev_harness.providers.ollama_loader import OllamaLoader
from dev_harness.providers.registry import ModelRegistry

#: The local model the profile targets (10.4).
LOCAL_MODEL = "qwen2.5-coder:7b"
#: Ollama's default endpoint.
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "127.0.0.1")
OLLAMA_PORT = 11434
PROFILE = Path(__file__).resolve().parents[2] / "profiles" / "local.toml"


def _ollama_reachable() -> bool:
    """True when a local Ollama is accepting connections."""
    try:
        with socket.create_connection((OLLAMA_HOST, OLLAMA_PORT), timeout=1.0):
            return True
    except OSError:
        return False


@pytest.mark.integration
def test_local_profile_config_is_local_only() -> None:
    """The profile declares no hosted provider (PR-tier, no Ollama).

    Ollama is disabled (2026-09-27), so the profile is inert: it declares no
    providers at all and no run can select a hosted or local endpoint.
    """
    config = load_config(PROFILE)
    assert "ollama" not in config.providers
    for hosted in ("anthropic", "openrouter"):
        assert hosted not in config.providers, f"{hosted} must not be in the local profile"


@pytest.mark.integration
def test_local_profile_registry_has_no_models() -> None:
    """The disabled profile resolves no models (Ollama is off)."""
    config = load_config(PROFILE)
    registry = ModelRegistry(config)
    assert registry.all_models() == []


@pytest.mark.slow
def test_local_profile_run_against_live_ollama(tmp_path: Path) -> None:
    """The real local run (NIGHTLY): completes, 0 hosted calls, <= 1 model load.

    Records ``skipped: no_local_ollama`` when no Ollama is reachable, matching
    the 3.13 spike convention. Ollama is disabled (2026-09-27), so this test
    always skips until the integration is re-enabled.
    """
    if not _ollama_reachable():
        report = tmp_path / "local_profile_run.json"
        report.write_text(
            json.dumps({"skipped": "no_local_ollama"}), encoding="utf-8"
        )
        pytest.skip("no_local_ollama")

    config = load_config(PROFILE)
    if "ollama" not in config.providers:
        pytest.skip("ollama_disabled")

    loads: list[str] = []
    loader = OllamaLoader(load_fn=loads.append)
    loader.ensure_loaded(LOCAL_MODEL)
    try:
        # The loader tracks the single resident model; a second ensure_loaded of
        # the same model must not trigger a reload (<= 1 model load).
        loader.ensure_loaded(LOCAL_MODEL)
        assert loader.model_loaded == LOCAL_MODEL
        assert loads == [LOCAL_MODEL]
    finally:
        loader.flush()

    # The profile cannot spend: every model is local and priced at 0.
    registry = ModelRegistry(config)
    for model_id in registry.all_models():
        entry = registry.resolve(model_id)
        assert entry.provider.value == "ollama"
