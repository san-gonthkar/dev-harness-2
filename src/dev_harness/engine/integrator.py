"""Sequential integration merge of chunk branches into the primary branch (V11 8.9).

After the worker pool finishes, each chunk's work lives on its own branch
``chunk/{chunk_id}`` (see :mod:`dev_harness.engine.worker_workspace`). This
module merges those branches back into the primary branch **one at a time, in
topological order**, fast-forwarding when possible and creating a merge commit
otherwise.

Integration contract (8.B acceptance, exact):

* (a) NON-overlapping chunk branches merge cleanly - every chunk's changes are
  present on the primary branch afterwards;
* (b) an OVERLAPPING edit (two chunks touch the same file/region) raises
  :class:`~dev_harness.contracts.errors.IntegrationConflict` whose message names
  the conflicting file **and both chunk ids**;
* (c) on conflict the primary branch is left UNMODIFIED - the in-progress merge
  is aborted (``git merge --abort``) so HEAD and ``git status`` are unchanged.

``engine/integrator.py`` is in the 8.C high-coverage set (95/90) and the
mutation focus set (>=80%), so every branch is exercised by the tests. The
module holds no clock and no randomness.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable
from pathlib import Path

from dev_harness.contracts.errors import IntegrationConflict, VcsError
from dev_harness.contracts.state import Chunk
from dev_harness.engine.worker_workspace import chunk_branch

_GIT_REMEDIATION = "Inspect the git repository state and retry the operation."
_ABORT_REMEDIATION = "Resolve the conflict manually, then re-run integration."


class Integrator:
    """Merges chunk branches into the primary branch, sequentially.

    :param repo: the primary repository root (the workspace).
    """

    def __init__(self, repo: str | Path) -> None:
        self.repo = Path(repo)

    # -- git plumbing -------------------------------------------------------

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        """Run ``git -C repo <args>`` without raising on a non-zero exit."""
        return subprocess.run(
            ["git", "-C", str(self.repo), *args],
            capture_output=True,
            text=True,
            check=False,
        )

    def _git(self, *args: str) -> str:
        """Run git and raise :class:`VcsError` on a non-zero exit."""
        proc = self._run(*args)
        if proc.returncode != 0:
            raise VcsError(
                f"git {' '.join(args)} failed: {proc.stderr.strip()}",
                remediation=_GIT_REMEDIATION,
            )
        return proc.stdout.strip()

    @property
    def primary_branch(self) -> str:
        """The branch currently checked out in the primary repository."""
        return self._git("rev-parse", "--abbrev-ref", "HEAD")

    def _changed_files(self, branch: str) -> set[str]:
        """Files ``branch`` changed relative to its merge base with the primary.

        Raises :class:`VcsError` when ``branch`` does not exist.
        """
        base = self._git("merge-base", self.primary_branch, branch)
        out = self._git("diff", "--name-only", base, branch)
        return set(out.splitlines())

    def _conflicting_files(self) -> set[str]:
        """Unmerged (conflicted) paths in the current merge, if any."""
        proc = self._run("diff", "--name-only", "--diff-filter=U")
        if proc.returncode != 0:
            return set()
        return set(proc.stdout.splitlines())

    def _abort_merge(self, start_head: str) -> None:
        """Abort the in-progress merge and roll the primary back to ``start_head``.

        Integration is atomic: a conflict on any chunk leaves the primary branch
        exactly as it was before :meth:`integrate` began, discarding the merges
        that already succeeded in this run.
        """
        proc = self._run("merge", "--abort")
        if proc.returncode != 0:
            raise VcsError(
                f"git merge --abort failed: {proc.stderr.strip()}",
                remediation=_ABORT_REMEDIATION,
            )
        reset = self._run("reset", "--hard", start_head)
        if reset.returncode != 0:
            raise VcsError(
                f"git reset --hard {start_head} failed: {reset.stderr.strip()}",
                remediation=_ABORT_REMEDIATION,
            )

    @staticmethod
    def _other_chunk(
        conflict_file: str,
        touched: list[tuple[str, set[str]]],
        fallback: str,
    ) -> str:
        """The already-merged chunk that last touched ``conflict_file``.

        Falls back to ``fallback`` (the primary branch name) when no merged
        chunk is recorded as having touched the file.
        """
        for chunk_id, files in touched:
            if conflict_file in files:
                return chunk_id
        return fallback

    # -- integration --------------------------------------------------------

    def integrate(self, chunks: Iterable[Chunk]) -> list[str]:
        """Merge each chunk branch into the primary branch, in the given order.

        ``chunks`` must already be in topological order (e.g. from
        :meth:`~dev_harness.engine.dag.ChunkDAG.topological_order`). Returns the
        ids of the chunks merged, in order. Raises
        :class:`IntegrationConflict` on the first conflict, leaving the primary
        branch unmodified.
        """
        merged: list[str] = []
        touched: list[tuple[str, set[str]]] = []
        start_head = self._git("rev-parse", "HEAD")
        for chunk in chunks:
            branch = chunk_branch(chunk.chunk_id)
            changed = self._changed_files(branch)
            proc = self._run("merge", "--no-edit", branch)
            if proc.returncode != 0:
                conflicts = self._conflicting_files()
                if not conflicts:
                    raise VcsError(
                        f"git merge {branch} failed: {proc.stderr.strip()}",
                        remediation=_GIT_REMEDIATION,
                    )
                conflict_file = min(conflicts)
                other = self._other_chunk(conflict_file, touched, self.primary_branch)
                self._abort_merge(start_head)
                raise IntegrationConflict(
                    f"Integration conflict on '{conflict_file}' between chunks "
                    f"'{other}' and '{chunk.chunk_id}'.",
                )
            merged.append(chunk.chunk_id)
            touched.append((chunk.chunk_id, changed))
        return merged
