"""Pause seal tests (V11 6.6).

Validation matrix: ``is_paused=True``; timestamp within 1s; hash ==
``git rev-parse HEAD``. The hash provider and clock are injected for the unit
tests; the integration test seals against a real git workspace.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from dev_harness.contracts.errors import VcsError
from dev_harness.contracts.state import HarnessState
from dev_harness.core import pause_seal as pause_seal_module
from dev_harness.core.pause_seal import PauseSeal, PauseSealer
from tests.support.clock import FrozenClock


def _state(workspace: Path) -> HarnessState:
    return HarnessState(project_id="p1", workspace_path=str(workspace), thread_id="t1")


@pytest.mark.unit
def test_seal_records_paused_timestamp_and_hash(frozen_clock: FrozenClock) -> None:
    """A seal carries is_paused=True, the clock timestamp, and the bound hash."""
    sealer = PauseSealer(
        "C:/ws",
        clock=frozen_clock.time,
        hash_provider=lambda: "deadbeef",
    )

    seal = sealer.seal()

    assert seal == PauseSeal(
        is_paused=True,
        timestamp=frozen_clock.time(),
        checkpoint_hash="deadbeef",
    )


@pytest.mark.unit
def test_apply_marks_state_paused_without_mutating_input() -> None:
    """apply() returns a paused copy bound to the seal hash; input is untouched."""
    sealer = PauseSealer("C:/ws", clock=lambda: 42.0, hash_provider=lambda: "abc123")
    original = _state(Path("C:/ws"))

    updated = sealer.apply(original)

    assert updated.tui_state.is_paused is True
    assert updated.git_state.last_checkpoint_commit == "abc123"
    # The original state is not mutated.
    assert original.tui_state.is_paused is False
    assert original.git_state.last_checkpoint_commit is None


@pytest.mark.unit
def test_apply_seal_uses_explicit_seal() -> None:
    """apply_seal() applies a caller-supplied seal to a state copy."""
    original = _state(Path("C:/ws"))
    seal = PauseSeal(is_paused=True, timestamp=7.0, checkpoint_hash="cafe")

    updated = PauseSealer.apply_seal(original, seal)

    assert updated.tui_state.is_paused is True
    assert updated.git_state.last_checkpoint_commit == "cafe"
    assert original.git_state.last_checkpoint_commit is None


@pytest.mark.unit
def test_default_hash_provider_reads_git_head(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no injected provider, the seal reads HEAD via GitAdapter."""

    class _FakeGit:
        def __init__(self, repo: object) -> None:
            self.repo = repo

        def head_sha(self) -> str:
            return "headsha"

    monkeypatch.setattr(pause_seal_module, "GitAdapter", _FakeGit)
    sealer = PauseSealer("C:/ws", clock=lambda: 1.0)

    assert sealer.seal().checkpoint_hash == "headsha"


@pytest.mark.unit
def test_default_hash_provider_on_non_repo_raises(tmp_path: Path) -> None:
    """A workspace that is not a git repo surfaces a VcsError."""
    sealer = PauseSealer(tmp_path, clock=lambda: 1.0)

    with pytest.raises(VcsError):
        sealer.seal()


@pytest.mark.integration
def test_seal_hash_equals_git_head(tmp_workspace: Path) -> None:
    """Integration: the seal hash equals ``git rev-parse HEAD`` and is fresh."""
    head = subprocess.run(
        ["git", "-C", str(tmp_workspace), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    before = time.time()
    seal = PauseSealer(tmp_workspace).seal()
    after = time.time()

    assert seal.is_paused is True
    assert seal.checkpoint_hash == head
    # Timestamp is within 1s of wall-clock time.
    assert before - 1.0 <= seal.timestamp <= after + 1.0
