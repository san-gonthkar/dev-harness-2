"""Branch-coverage completion tests for vcs (V11 1.x)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dev_harness.contracts.errors import VcsError
from dev_harness.vcs.git import GitAdapter
from dev_harness.vcs.restore import Restorer
from dev_harness.vcs.worktree import WorktreeManager

pytestmark = pytest.mark.unit


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


class TestGitBranches:
    def test_head_sha_other_error_reraises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _git(tmp_path, "init", "-b", "main")
        (tmp_path / ".gitkeep").write_text("", encoding="utf-8")
        _git(tmp_path, "add", ".")
        _git(tmp_path, "commit", "-m", "init")
        adapter = GitAdapter(tmp_path)

        def boom(*args: str) -> str:
            raise VcsError("some other git failure", remediation="x")

        monkeypatch.setattr(adapter, "_git", boom)
        with pytest.raises(VcsError):
            adapter.head_sha()


class TestRestoreBranches:
    def test_autostash_with_tracked_dirty_file(self, tmp_workspace: Path) -> None:
        """A tracked modified file is stashed and popped (covers stash pop)."""
        # f.txt exists in the current commit; restore to the same HEAD so the
        # stash base matches and pop applies cleanly.
        (tmp_workspace / "f.txt").write_text("v1", encoding="utf-8")
        _git(tmp_workspace, "add", ".")
        _git(tmp_workspace, "commit", "-m", "second")
        head = _git(tmp_workspace, "rev-parse", "HEAD")
        # Modify the tracked file (stashable).
        (tmp_workspace / "f.txt").write_text("modified", encoding="utf-8")
        restorer = Restorer(tmp_workspace)
        new_head = restorer.restore(head, autostash=True)
        assert new_head == head
        # The tracked modification is restored after stash pop.
        assert (tmp_workspace / "f.txt").read_text(encoding="utf-8") == "modified"
        assert _git(tmp_workspace, "stash", "list") == ""


class TestWorktreeBranches:
    def test_destroy_outside_root_raises(self, tmp_workspace: Path) -> None:
        mgr = WorktreeManager(tmp_workspace)
        # A worker_id with .. escapes the harness root -> guard triggers.
        with pytest.raises(VcsError):
            mgr.destroy("../escape")

    def test_destroy_existing_path(self, tmp_workspace: Path) -> None:
        mgr = WorktreeManager(tmp_workspace)
        mgr.create("w1")
        mgr.destroy("w1")  # path exists -> git worktree remove
        assert mgr.list() == []

    def test_destroy_nonexistent_path_noop(self, tmp_workspace: Path) -> None:
        """destroy on a path that does not exist is a no-op (path.exists() False)."""
        mgr = WorktreeManager(tmp_workspace)
        mgr.destroy("ghost")  # no worktree created -> path.exists() is False
        assert mgr.list() == []

    def test_git_error_raises_vcs_error(self, tmp_path: Path) -> None:
        """A failing git command raises VcsError (covers _git error branch)."""
        # tmp_path is NOT a git repo, so git fails.
        mgr = WorktreeManager(tmp_path)
        with pytest.raises(VcsError):
            mgr.create("w1")

    def test_restore_git_error_raises_vcs_error(self, tmp_path: Path) -> None:
        """A failing git command in Restorer raises VcsError."""
        restorer = Restorer(tmp_path)
        with pytest.raises(VcsError):
            restorer.restore("deadbeef")
