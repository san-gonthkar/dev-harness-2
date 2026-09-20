"""Checkpoint<->Git binding tests (V11 1.11)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dev_harness.contracts.state import HarnessState
from dev_harness.storage.checkpoint_binding import CheckpointBinding
from dev_harness.storage.sqlite_saver import Scope

pytestmark = pytest.mark.slow


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def test_bound_hash_equals_head_at_write(tmp_workspace: Path) -> None:
    binding = CheckpointBinding(tmp_workspace)
    state = HarnessState(project_id="p1", workspace_path=str(tmp_workspace), thread_id="t1")
    cid = binding.put_bound(Scope("p1", "t1"), state)
    head = _git(tmp_workspace, "rev-parse", "HEAD")
    # The stored hash must equal HEAD at write time and pass git cat-file -e.
    from dev_harness.storage.sqlite_saver import SqliteSaver

    saver = SqliteSaver(tmp_workspace / ".dev-harness" / "state.db")
    row = saver.get_tuple(Scope("p1", "t1"), cid)
    assert row is not None
    assert row["git_commit_hash"] == head
    # git cat-file -e passes for the stored hash.
    proc = subprocess.run(
        ["git", "-C", str(tmp_workspace), "cat-file", "-e", f"{head}^{{commit}}"],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0
    saver.close()


def test_interleaved_writes_all_valid(tmp_workspace: Path) -> None:
    """50 interleaved writes: every hash passes git cat-file -e."""
    binding = CheckpointBinding(tmp_workspace)
    saver = None
    for i in range(50):
        state = HarnessState(project_id="p1", workspace_path=str(tmp_workspace), thread_id="t1", raw_input=f"v{i}")
        cid = binding.put_bound(Scope("p1", "t1"), state, checkpoint_id=f"c{i}")
        from dev_harness.storage.sqlite_saver import SqliteSaver

        saver = SqliteSaver(tmp_workspace / ".dev-harness" / "state.db")
        row = saver.get_tuple(Scope("p1", "t1"), cid)
        assert row is not None
        proc = subprocess.run(
            ["git", "-C", str(tmp_workspace), "cat-file", "-e", f"{row['git_commit_hash']}^{{commit}}"],
            capture_output=True, text=True, check=False,
        )
        assert proc.returncode == 0, f"hash {row['git_commit_hash']} invalid at write {i}"
        saver.close()
