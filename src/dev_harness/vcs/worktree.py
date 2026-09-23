"""Worktree manager: create/destroy .dev-harness/worktrees/{worker_id} (V11 1.10)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from dev_harness.contracts.errors import VcsError, WorktreeExistsError


class WorktreeManager:
    """Create and destroy harness-owned worktrees bound to branches."""

    def __init__(self, repo: str | Path) -> None:
        self.repo = Path(repo)
        self.worktrees_root = self.repo / ".dev-harness" / "worktrees"

    def _git(self, *args: str) -> str:
        proc = subprocess.run(
            ["git", "-C", str(self.repo), *args],
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

    def create(self, worker_id: str, *, branch: str | None = None) -> Path:
        """Create a worktree at .dev-harness/worktrees/{worker_id} on a branch.

        The branch defaults to ``worker/{worker_id}``. Raises WorktreeExistsError
        if the worktree path already exists.
        """
        path = self.worktrees_root / worker_id
        if path.exists():
            raise WorktreeExistsError(
                f"worktree already exists at {path}",
                remediation="Remove the existing worktree or choose a different worker id.",
            )
        branch = branch or f"worker/{worker_id}"
        self.worktrees_root.mkdir(parents=True, exist_ok=True)
        self._git("worktree", "add", "-b", branch, str(path))
        return path

    def destroy(self, worker_id: str) -> None:
        """Remove a harness-created worktree. Refuses paths outside the harness root."""
        path = self.worktrees_root / worker_id
        # Safety: only remove worktrees under .dev-harness/worktrees/.
        if not str(path.resolve()).startswith(str(self.worktrees_root.resolve())):
            raise VcsError(
                f"refusing to remove worktree outside harness root: {path}",
                remediation="Only harness-created worktrees may be removed.",
            )
        if path.exists():
            self._git("worktree", "remove", "--force", str(path))

    def list(self) -> list[Path]:
        """List existing harness worktree paths."""
        if not self.worktrees_root.exists():
            return []
        return [p for p in sorted(self.worktrees_root.iterdir()) if p.is_dir()]
