"""Additional vcs coverage: detached head, autostash edge, worktree list."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dev_harness.vcs.git import GitAdapter
from dev_harness.vcs.restore import Restorer
from dev_harness.vcs.worktree import WorktreeManager

pytestmark = pytest.mark.unit


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def test_detached_head_reports_detached(tmp_workspace: Path) -> None:
    adapter = GitAdapter(tmp_workspace)
    sha = adapter.head_sha()
    _git(tmp_workspace, "checkout", "--detach", sha)
    assert adapter.active_branch() == "(detached)"


def test_autostash_no_stash_when_clean(tmp_workspace: Path) -> None:
    """autostash=True on a clean tree must not crash when no stash exists."""
    (tmp_workspace / "f.txt").write_text("v1", encoding="utf-8")
    _git(tmp_workspace, "add", ".")
    _git(tmp_workspace, "commit", "-m", "second")
    first = _git(tmp_workspace, "rev-parse", "HEAD~1")
    restorer = Restorer(tmp_workspace)
    new_head = restorer.restore(first, autostash=True)
    assert new_head == first
    assert _git(tmp_workspace, "stash", "list") == ""


def test_worktree_list_empty_when_root_missing(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-b", "main")
    (tmp_path / ".gitkeep").write_text("", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "init")
    mgr = WorktreeManager(tmp_path)
    assert mgr.list() == []


def test_worktree_list_after_create(tmp_workspace: Path) -> None:
    mgr = WorktreeManager(tmp_workspace)
    mgr.create("w1")
    paths = mgr.list()
    assert len(paths) == 1
    assert paths[0].name == "w1"
    mgr.destroy("w1")
    assert mgr.list() == []
