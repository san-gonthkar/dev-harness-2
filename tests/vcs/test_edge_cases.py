"""Git edge-case tests (V11 9.6).

Real temp git repositories; no network, no sleeps. Acceptance is exact:
unborn branch -> NoCommitsError; detached restore succeeds and reports
detached; duplicate worktree -> WorktreeExistsError.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dev_harness.contracts.errors import NoCommitsError, WorktreeExistsError
from dev_harness.vcs.edge_cases import (
    describe_repo_state,
    guard_worktree,
    require_commits,
    restore_detached,
)
from dev_harness.vcs.worktree import WorktreeManager


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=False
    )


def _init_unborn(root: Path) -> Path:
    """A git repo with no commits (unborn branch)."""
    repo = root / "unborn"
    repo.mkdir()
    assert _git(repo, "init", "-b", "main").returncode == 0
    return repo


@pytest.mark.negative
def test_unborn_branch_raises_no_commits(tmp_path: Path) -> None:
    repo = _init_unborn(tmp_path)
    with pytest.raises(NoCommitsError):
        require_commits(repo)


@pytest.mark.unit
def test_unborn_branch_state_normalized(tmp_path: Path) -> None:
    repo = _init_unborn(tmp_path)
    state = describe_repo_state(repo)
    assert state.is_repository is True
    assert state.has_commits is False
    assert state.head_sha is None
    assert state.detached is False
    assert state.branch == "main"


@pytest.mark.integration
def test_detached_head_restore_succeeds_and_reports_detached(
    tmp_workspace: Path,
) -> None:
    (tmp_workspace / "f.txt").write_text("v1", encoding="utf-8")
    assert _git(tmp_workspace, "add", ".").returncode == 0
    assert _git(tmp_workspace, "commit", "-m", "second").returncode == 0
    first = _git(tmp_workspace, "rev-parse", "HEAD~1").stdout.strip()

    state = restore_detached(tmp_workspace, first)

    assert state.head_sha == first
    assert state.detached is True
    assert state.branch is None


@pytest.mark.negative
def test_duplicate_worktree_raises(tmp_workspace: Path) -> None:
    guard_worktree(tmp_workspace, "w1")
    with pytest.raises(WorktreeExistsError):
        guard_worktree(tmp_workspace, "w1")


@pytest.mark.integration
def test_pre_existing_worktree_detected(tmp_workspace: Path) -> None:
    mgr = WorktreeManager(tmp_workspace)
    created = mgr.create("w1")
    state = describe_repo_state(tmp_workspace)
    assert created.resolve() in {Path(p).resolve() for p in state.worktrees}
    with pytest.raises(WorktreeExistsError):
        guard_worktree(tmp_workspace, "w1")


@pytest.mark.unit
def test_non_repository_state(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    state = describe_repo_state(plain)
    assert state.is_repository is False
    assert state.has_commits is False
    assert state.worktrees == []


@pytest.mark.integration
def test_submodule_detected(tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    assert _git(sub, "init", "-b", "main").returncode == 0
    _git(sub, "config", "user.email", "harness@test.local")
    _git(sub, "config", "user.name", "Harness Test")
    (sub / "s.txt").write_text("s", encoding="utf-8")
    _git(sub, "add", "-A")
    assert _git(sub, "commit", "-m", "sub initial").returncode == 0

    repo = tmp_path / "main"
    repo.mkdir()
    assert _git(repo, "init", "-b", "main").returncode == 0
    _git(repo, "config", "user.email", "harness@test.local")
    _git(repo, "config", "user.name", "Harness Test")
    (repo / ".gitkeep").write_text("", encoding="utf-8")
    _git(repo, "add", "-A")
    assert _git(repo, "commit", "-m", "initial").returncode == 0

    added = _git(
        repo,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        str(sub),
        "sub",
    )
    if added.returncode != 0:
        pytest.skip(f"cannot create a local submodule offline: {added.stderr.strip()}")

    state = describe_repo_state(repo)
    assert state.submodules == ["sub"]
