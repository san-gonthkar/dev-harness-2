"""Worktree manager tests (V11 1.10)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dev_harness.contracts.errors import WorktreeExistsError
from dev_harness.vcs.worktree import WorktreeManager

pytestmark = pytest.mark.unit


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def test_three_concurrent_worktrees_distinct(tmp_workspace: Path) -> None:
    mgr = WorktreeManager(tmp_workspace)
    paths = [mgr.create(f"w{i}") for i in range(3)]
    assert len(set(paths)) == 3
    branches = [_git(p, "rev-parse", "--abbrev-ref", "HEAD") for p in paths]
    assert len(set(branches)) == 3
    # All under the harness root.
    for p in paths:
        assert ".dev-harness" in str(p)
    # Cleanup
    for i in range(3):
        mgr.destroy(f"w{i}")
    assert mgr.list() == []


def test_destroy_leaves_only_primary(tmp_workspace: Path) -> None:
    mgr = WorktreeManager(tmp_workspace)
    mgr.create("w1")
    mgr.create("w2")
    mgr.destroy("w1")
    mgr.destroy("w2")
    out = _git(tmp_workspace, "worktree", "list")
    # Only the primary worktree remains.
    assert out.count("\n") == 0 or len(out.splitlines()) == 1


def test_file_in_a_absent_in_b(tmp_workspace: Path) -> None:
    mgr = WorktreeManager(tmp_workspace)
    a = mgr.create("wa")
    b = mgr.create("wb")
    (a / "only_a.txt").write_text("x", encoding="utf-8")
    assert (b / "only_a.txt").exists() is False
    mgr.destroy("wa")
    mgr.destroy("wb")


def test_duplicate_worktree_raises(tmp_workspace: Path) -> None:
    mgr = WorktreeManager(tmp_workspace)
    mgr.create("w1")
    with pytest.raises(WorktreeExistsError):
        mgr.create("w1")
    mgr.destroy("w1")
