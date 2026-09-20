"""Secrets provider containment tests (V11 0.9)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from dev_harness.contracts.errors import InsecureKeyFileError
from dev_harness.contracts.state import HarnessState
from dev_harness.secrets import Secret, SecretsProvider, is_insecure_mode

pytestmark = pytest.mark.unit

API_KEY = "sk-ant-api03-abcdef1234567890"


def test_repr_masked() -> None:
    s = Secret(API_KEY)
    assert "***" in repr(s)
    assert API_KEY not in repr(s)
    assert API_KEY not in str(s)


def test_state_dump_contains_no_key() -> None:
    state = HarnessState(project_id="p", workspace_path="/w", thread_id="t")
    dump = state.model_dump_json()
    assert API_KEY not in dump


def test_env_priority(tmp_path: Path) -> None:
    prov = SecretsProvider(env={"DEV_HARNESS_ANTHROPIC_API_KEY": API_KEY})
    secret = prov.get("ANTHROPIC_API_KEY")
    assert secret.reveal() == API_KEY


def test_missing_raises_keyerror() -> None:
    prov = SecretsProvider(env={})
    with pytest.raises(KeyError):
        prov.get("ANTHROPIC_API_KEY")


def test_insecure_mode_detection() -> None:
    assert is_insecure_mode(0o644) is True
    assert is_insecure_mode(0o600) is False
    assert is_insecure_mode(0o700) is False
    assert is_insecure_mode(0o666) is True


def test_insecure_key_file_raises(tmp_path: Path) -> None:
    key_file = tmp_path / "keyfile"
    key_file.write_text(API_KEY, encoding="utf-8")
    os.chmod(key_file, 0o644)
    prov = SecretsProvider(key_file=key_file, env={})
    if os.name == "posix":
        with pytest.raises(InsecureKeyFileError):
            prov.get("ANTHROPIC_API_KEY")
    else:
        # Windows reports 0o666 regardless; the POSIX enforcement path is
        # covered by is_insecure_mode above.
        assert prov.get("ANTHROPIC_API_KEY").reveal() == API_KEY


def test_secure_key_file_reads(tmp_path: Path) -> None:
    key_file = tmp_path / "keyfile"
    key_file.write_text(API_KEY, encoding="utf-8")
    os.chmod(key_file, 0o600)
    prov = SecretsProvider(key_file=key_file, env={})
    secret = prov.get("ANTHROPIC_API_KEY")
    assert secret.reveal() == API_KEY
