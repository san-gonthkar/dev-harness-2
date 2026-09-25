"""Git edge-case detection and normalization (V11 9.6).

Detects and normalizes the awkward repository states the harness must survive:
an unborn branch (``git init`` with no commits), a detached HEAD, duplicate or
pre-existing worktrees, and submodules. This module *reuses* ``GitAdapter``,
``WorktreeManager`` and ``Restorer`` rather than duplicating them; it only adds
the detection/normalization layer and raises the canonical error classes.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from dev_harness.contracts.errors import NoCommitsError, VcsError, WorktreeExistsError
from dev_harness.vcs.git import GitAdapter
from dev_harness.vcs.restore import Restorer
from dev_harness.vcs.worktree import WorktreeManager

#: ``GitAdapter.active_branch`` sentinel for a detached HEAD.
_DETACHED = "(detached)"


class RepoState(BaseModel):
    """Normalized snapshot of a repository's edge-case-relevant state."""

    model_config = ConfigDict(frozen=True)

    is_repository: bool
    has_commits: bool
    detached: bool
    branch: str | None
    head_sha: str | None
    dirty: bool
    uncommitted_count: int
    submodules: list[str]
    worktrees: list[str]


def _git(repo: Path, *args: str) -> str:
    """Run a read-only git query, raising VcsError on failure."""
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise VcsError(
            f"git {' '.join(args)} failed: {proc.stderr.strip()}",
            remediation="Inspect the git repository state and retry the operation.",
        )
    return proc.stdout.strip()


def _submodule_paths(repo: Path) -> list[str]:
    """Paths of submodules registered in the repository (empty when none)."""
    paths: list[str] = []
    for line in _git(repo, "submodule", "status").splitlines():
        parts = line.strip().split()
        if len(parts) >= 2:
            paths.append(parts[1])
    return paths


def _worktree_paths(repo: Path) -> list[str]:
    """Absolute paths of every worktree git knows about, primary included."""
    prefix = "worktree "
    return [
        line[len(prefix) :]
        for line in _git(repo, "worktree", "list", "--porcelain").splitlines()
        if line.startswith(prefix)
    ]


def describe_repo_state(repo: str | Path) -> RepoState:
    """Detect and normalize the repository state at ``repo``.

    Never raises for a non-repository path or an unborn branch: those are
    reported through the returned state (``is_repository`` / ``has_commits``).
    """
    path = Path(repo)
    git = GitAdapter(path)
    if not git.is_repository():
        return RepoState(
            is_repository=False,
            has_commits=False,
            detached=False,
            branch=None,
            head_sha=None,
            dirty=False,
            uncommitted_count=0,
            submodules=[],
            worktrees=[],
        )

    try:
        head_sha: str | None = git.head_sha()
        has_commits = True
    except NoCommitsError:
        head_sha = None
        has_commits = False

    if has_commits:
        active = git.active_branch()
        detached = active == _DETACHED
        branch: str | None = None if detached else active
    else:
        # An unborn branch has no HEAD commit; symbolic-ref still names it.
        detached = False
        branch = _git(path, "symbolic-ref", "--short", "HEAD")

    return RepoState(
        is_repository=True,
        has_commits=has_commits,
        detached=detached,
        branch=branch,
        head_sha=head_sha,
        dirty=git.is_dirty(),
        uncommitted_count=git.uncommitted_count(),
        submodules=_submodule_paths(path),
        worktrees=_worktree_paths(path),
    )


def require_commits(repo: str | Path) -> str:
    """Return the HEAD sha, raising NoCommitsError on an unborn branch."""
    return GitAdapter(repo).head_sha()


def restore_detached(
    repo: str | Path, sha: str, *, autostash: bool = False
) -> RepoState:
    """Restore to ``sha`` (detaching HEAD) and return the resulting state.

    The returned state reports ``detached=True`` and ``branch=None``.
    """
    Restorer(repo).restore(sha, autostash=autostash)
    return describe_repo_state(repo)


def guard_worktree(repo: str | Path, worker_id: str) -> Path:
    """Create a harness worktree, raising WorktreeExistsError on a duplicate.

    Detects both an existing directory and a worktree still registered with git
    (e.g. its directory was deleted), normalizing the latter to the canonical
    ``WorktreeExistsError`` instead of a raw git failure.
    """
    mgr = WorktreeManager(repo)
    target = mgr.worktrees_root / worker_id
    registered = {Path(p).resolve() for p in _worktree_paths(Path(repo))}
    if target.exists() or target.resolve() in registered:
        raise WorktreeExistsError(f"worktree already exists at {target}")
    return mgr.create(worker_id)
