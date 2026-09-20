"""Restore with dirty-tree guard tests (V11 1.12)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dev_harness.contracts.errors import DirtyWorktreeError
from dev_harness.vcs.restore import Restorer

pytestmark = pytest.mark.unit


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _porcelain(cwd: Path) -> str:
    return _git(cwd, "status", "--porcelain")


def test_dirty_refusal_leaves_tree_unchanged(tmp_workspace: Path) -> None:
    # Create a second commit to restore to.
    (tmp_workspace / "f.txt").write_text("v1", encoding="utf-8")
    _git(tmp_workspace, "add", ".")
    _git(tmp_workspace, "commit", "-m", "second")
    first = _git(tmp_workspace, "rev-parse", "HEAD~1")
    # Dirty the tree.
    (tmp_workspace / "dirty.txt").write_text("x", encoding="utf-8")
    before = _porcelain(tmp_workspace)
    restorer = Restorer(tmp_workspace)
    with pytest.raises(DirtyWorktreeError):
        restorer.restore(first)
    after = _porcelain(tmp_workspace)
    assert before == after  # byte-identical


def test_autostash_reapplies(tmp_workspace: Path) -> None:
    (tmp_workspace / "f.txt").write_text("v1", encoding="utf-8")
    _git(tmp_workspace, "add", ".")
    _git(tmp_workspace, "commit", "-m", "second")
    first = _git(tmp_workspace, "rev-parse", "HEAD~1")
    (tmp_workspace / "dirty.txt").write_text("x", encoding="utf-8")
    restorer = Restorer(tmp_workspace)
    new_head = restorer.restore(first, autostash=True)
    assert new_head == first
    # The dirty file is back after stash pop.
    assert (tmp_workspace / "dirty.txt").read_text(encoding="utf-8") == "x"
    # Stash list is empty.
    assert _git(tmp_workspace, "stash", "list") == ""


def test_clean_restore(tmp_workspace: Path) -> None:
    (tmp_workspace / "f.txt").write_text("v1", encoding="utf-8")
    _git(tmp_workspace, "add", ".")
    _git(tmp_workspace, "commit", "-m", "second")
    first = _git(tmp_workspace, "rev-parse", "HEAD~1")
    restorer = Restorer(tmp_workspace)
    assert restorer.restore(first) == first
