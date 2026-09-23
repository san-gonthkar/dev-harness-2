"""Restore: git checkout {sha} with dirty-tree guard (V11 1.12)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from dev_harness.contracts.errors import DirtyWorktreeError, VcsError
from dev_harness.vcs.git import GitAdapter


class Restorer:
    """Checks out a stored commit, refusing or autostashing a dirty tree."""

    def __init__(self, repo: str | Path) -> None:
        self.repo = Path(repo)
        self.git = GitAdapter(repo)

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

    def restore(self, sha: str, *, autostash: bool = False) -> str:
        """Check out ``sha``. Returns the new HEAD.

        Raises DirtyWorktreeError if the tree is dirty and autostash is False.
        With autostash=True, uncommitted changes are stashed, the checkout runs,
        and the stash is reapplied.
        """
        if self.git.is_dirty():
            if not autostash:
                raise DirtyWorktreeError(
                    f"refusing to restore {sha} on a dirty tree",
                    remediation="Commit, stash, or autostash the uncommitted changes before restoring.",
                )
            self._git("stash", "push", "-m", "harness-autostash")
        try:
            self._git("checkout", sha)
        finally:
            if autostash and self.git.is_dirty() is False:
                # Reapply the stash if it exists.
                stash_list = self._git("stash", "list")
                if stash_list:
                    self._git("stash", "pop")
        return self.git.head_sha()
