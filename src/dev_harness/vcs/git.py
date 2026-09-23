"""Git adapter: head_sha, active_branch, is_dirty, uncommitted_count (V11 1.9)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from dev_harness.contracts.errors import NoCommitsError, VcsError


class GitAdapter:
    """Thin typed wrapper over git for a workspace."""

    def __init__(self, repo: str | Path) -> None:
        self.repo = Path(repo)

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

    def head_sha(self) -> str:
        """The current HEAD commit sha (40 hex chars)."""
        try:
            return self._git("rev-parse", "HEAD")
        except VcsError as exc:
            if "unknown revision" in str(exc) or "ambiguous argument" in str(exc):
                raise NoCommitsError(
                    "the repository has no commits yet",
                    remediation="Create an initial commit before running the harness.",
                ) from exc
            raise

    def active_branch(self) -> str:
        """The current branch name, or '(detached)' when detached."""
        out = self._git("rev-parse", "--abbrev-ref", "HEAD")
        return out if out != "HEAD" else "(detached)"

    def is_dirty(self) -> bool:
        """True if the working tree has uncommitted changes."""
        out = self._git("status", "--porcelain")
        return bool(out)

    def uncommitted_count(self) -> int:
        """Number of uncommitted changes (files)."""
        out = self._git("status", "--porcelain")
        return len([l for l in out.splitlines() if l.strip()])
