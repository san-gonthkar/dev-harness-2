"""Per-worker worktree binding: isolate each worker in its own worktree (V11 8.8).

Each worker executes inside ``.dev-harness/worktrees/{worker_id}`` on branch
``chunk/{chunk_id}`` (V8 ran parallel workers against one tree - a correctness
bug, not a style issue). This module binds a worker to a worktree, resolves a
chunk's worktree root, and hands out *safe* write paths that cannot escape the
worktree root.

Isolation contract (8.B acceptance, exact):

* three workers writing the same relative path produce three distinct contents;
* the primary worktree's ``git status --porcelain`` stays empty throughout.

The binding is a thin, deterministic layer over :class:`WorktreeManager`; it
holds no clock and no randomness. ``engine/worker_workspace.py`` is in the 8.C
high-coverage set (95/90) and the mutation focus set (>=80%, 0 survivors in the
worktree binding), so every branch is exercised by the tests.

8.21b adds worktree state capture + restore: :meth:`WorkerWorkspace.capture`
reads a worktree's HEAD + uncommitted diff (tracked changes *and* untracked
files, so an in-flight worktree is recoverable field-for-field) and
:meth:`WorkerWorkspace.restore` replays a captured snapshot. The diff is
``git apply``-compatible, so restore is exact: same file set, same contents.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from pathlib import Path

from dev_harness.contracts.errors import (
    EngineError,
    StorageError,
    WorkspaceEscapeError,
)
from dev_harness.contracts.state import Chunk
from dev_harness.vcs.worktree import WorktreeManager

_ESCAPE_REMEDIATION = "Constrain the worker to its worktree root."
_HARNESS_IGNORE_ENTRY = ".dev-harness/"
_UNBOUND_REMEDIATION = "Bind the worker to a worktree before capturing its state."


def chunk_branch(chunk_id: str) -> str:
    """The branch a chunk's worker worktree is bound to (``chunk/{chunk_id}``)."""
    return f"chunk/{chunk_id}"


class WorkerWorkspace:
    """Binds workers to isolated worktrees and resolves safe write paths.

    :param repo: the primary repository root (the workspace).
    :param worktrees: the worktree manager; defaults to one rooted at ``repo``.
        Injectable so the binding logic is unit-testable without git.
    """

    def __init__(
        self,
        repo: str | Path,
        *,
        worktrees: WorktreeManager | None = None,
    ) -> None:
        self.repo = Path(repo)
        self._worktrees = worktrees or WorktreeManager(self.repo)
        self._roots: dict[str, Path] = {}
        self._ensure_harness_ignored()

    def _ensure_harness_ignored(self) -> None:
        """Keep ``.dev-harness/`` out of the primary tree's status.

        Worktrees live under ``.dev-harness/worktrees/`` inside the repo, so an
        un-ignored harness dir would show as untracked and dirty the primary
        tree. The entry is written to ``.git/info/exclude`` (local, untracked),
        so the primary's ``git status --porcelain`` stays empty.
        """
        exclude = self.repo / ".git" / "info" / "exclude"
        if not exclude.parent.is_dir():
            return
        existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        if _HARNESS_IGNORE_ENTRY in existing.splitlines():
            return
        prefix = "" if not existing or existing.endswith("\n") else "\n"
        with exclude.open("a", encoding="utf-8") as handle:
            handle.write(f"{prefix}{_HARNESS_IGNORE_ENTRY}\n")

    # -- binding ------------------------------------------------------------

    def bind(self, worker_id: str, chunk: Chunk) -> Path:
        """Create (or reuse) the worktree for ``worker_id`` on ``chunk``'s branch.

        Returns the worktree root. Idempotent for a given worker id: a second
        bind returns the already-created root without touching git.
        """
        existing = self._roots.get(worker_id)
        if existing is not None:
            return existing
        root = self._worktrees.create(worker_id, branch=chunk_branch(chunk.chunk_id))
        self._roots[worker_id] = root
        return root

    def root_for(self, chunk: Chunk) -> Path:
        """The worktree root bound to ``chunk``'s assigned worker.

        Raises :class:`EngineError` when the chunk has no assigned worker or
        that worker has not been bound yet.
        """
        worker_id = chunk.assigned_worker_id
        if worker_id is None:
            raise EngineError(
                f"chunk '{chunk.chunk_id}' has no assigned worker.",
                remediation="Assign a worker id before resolving its worktree root.",
            )
        root = self._roots.get(worker_id)
        if root is None:
            raise EngineError(
                f"worker '{worker_id}' is not bound to a worktree.",
                remediation="Bind the worker to a worktree before resolving its root.",
            )
        return root

    def write_path(self, chunk: Chunk, relative_path: str) -> Path:
        """A safe absolute path for ``relative_path`` inside ``chunk``'s worktree.

        Rejects absolute paths and any path that escapes the worktree root with
        :class:`WorkspaceEscapeError` (the 8.10 developer node reuses this).
        """
        root = self.root_for(chunk).resolve()
        candidate = Path(relative_path)
        if candidate.is_absolute():
            raise WorkspaceEscapeError(
                f"absolute write path '{relative_path}' is not allowed.",
                remediation=_ESCAPE_REMEDIATION,
            )
        resolved = (root / candidate).resolve()
        if resolved != root and root not in resolved.parents:
            raise WorkspaceEscapeError(
                f"write path '{relative_path}' escapes the worktree root.",
                remediation=_ESCAPE_REMEDIATION,
            )
        return resolved

    def release(self, worker_id: str) -> None:
        """Destroy ``worker_id``'s worktree and forget its binding."""
        self._worktrees.destroy(worker_id)
        self._roots.pop(worker_id, None)

    # -- worktree state capture + restore (8.21b) ---------------------------

    def _git_in(self, root: Path, *args: str) -> str:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise StorageError(
                f"git {' '.join(args)} failed in {root}: {proc.stderr.strip()}",
                remediation="Inspect the worktree git state and retry the capture.",
            )
        return proc.stdout

    def capture(self, worker_id: str) -> dict[str, str]:
        """Capture ``worker_id``'s worktree HEAD + uncommitted diff.

        The diff includes tracked changes *and* untracked files (``git add -N``
        stages an intent-to-add so ``git diff`` records them) so a worktree with
        uncommitted work is recoverable field-for-field. Raises
        :class:`EngineError` when the worker is not bound.
        """
        root = self._roots.get(worker_id)
        if root is None:
            raise EngineError(
                f"worker '{worker_id}' is not bound to a worktree.",
                remediation=_UNBOUND_REMEDIATION,
            )
        head = self._git_in(root, "rev-parse", "HEAD").strip()
        # Intent-to-add: make untracked files visible to `git diff` without
        # committing them; `git reset` below restores the original index state.
        self._git_in(root, "add", "-N", "-A")
        diff = self._git_in(root, "diff", "--binary")
        self._git_in(root, "reset", "--quiet")
        return {"head": head, "diff": diff}

    def capture_map(self, worker_ids: list[str]) -> dict[str, dict[str, str]]:
        """Capture several workers into a ``worker_id -> {head, diff}`` map.

        The map is the shape :meth:`storage.checkpoint_binding.CheckpointBinding.
        put_bound` serializes into the ``worktree_head``/``worktree_diff``
        columns.
        """
        return {wid: self.capture(wid) for wid in worker_ids}

    def restore(self, worker_id: str, snapshot: Mapping[str, str]) -> None:
        """Replay a captured ``{head, diff}`` snapshot into ``worker_id``'s tree.

        Replays the recorded HEAD (``git reset --hard`` when it differs) and then
        applies the captured diff (``git apply``). The result matches the
        captured snapshot field-for-field: same HEAD, same file set, same
        contents. Raises :class:`EngineError` when the worker is not bound and
        :class:`StorageError` when the patch cannot be applied.
        """
        root = self._roots.get(worker_id)
        if root is None:
            raise EngineError(
                f"worker '{worker_id}' is not bound to a worktree.",
                remediation="Bind the worker to a worktree before restoring its state.",
            )
        head = snapshot.get("head", "")
        if head and self._git_in(root, "rev-parse", "HEAD").strip() != head:
            self._git_in(root, "reset", "--hard", head)
        diff = snapshot.get("diff", "")
        if not diff:
            return
        applied = subprocess.run(
            ["git", "-C", str(root), "apply", "-"],
            input=diff,
            capture_output=True,
            text=True,
            check=False,
        )
        if applied.returncode != 0:
            raise StorageError(
                f"failed to restore worktree '{worker_id}': {applied.stderr.strip()}",
                remediation="Inspect the worktree and re-apply the captured diff.",
            )
