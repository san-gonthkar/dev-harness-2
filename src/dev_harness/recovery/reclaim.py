"""Stale artifact reclamation: dead-PID sockets, locks and worktrees (V11 9.2).

A crashed session can leave behind an AF_UNIX socket, a workspace lock and
worker worktrees whose owning process is gone. :func:`reclaim` orchestrates the
existing stale-detection primitives (``storage.workspace_lock``'s ``_pid_alive``,
the socket-cleanup pattern from ``ipc.server``, and ``vcs.worktree``'s
list/destroy) into a single entry point so a fresh session starts in under two
seconds without manual cleanup.

Liveness is recorded as a PID next to each artifact:

* socket   -> ``<socket_path>.pid`` (sidecar; the socket path itself is opaque)
* lock     -> the lock file itself (``pid:hostname``, written by ``WorkspaceLock``)
* worktree -> ``<worktree>/.pid``

An artifact whose recorded PID is alive is left untouched. A missing, empty or
unparseable PID is treated as stale (an orphan) and reclaimed.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from dev_harness.contracts.errors import RecoveryError, VcsError
from dev_harness.paths import DerivedPaths, derive_paths
from dev_harness.storage.workspace_lock import _pid_alive
from dev_harness.vcs.worktree import WorktreeManager

_PID_FILE = ".pid"


@dataclass(frozen=True)
class ReclaimReport:
    """What :func:`reclaim` removed and what it deliberately left alone."""

    socket_removed: bool
    lock_removed: bool
    worktrees_removed: tuple[str, ...]
    live_artifacts: tuple[str, ...]
    elapsed_seconds: float

    @property
    def reclaimed_any(self) -> bool:
        """True if at least one stale artifact was removed."""
        return self.socket_removed or self.lock_removed or bool(self.worktrees_removed)


def socket_pid_path(socket_path: str | Path) -> Path:
    """The sidecar file recording the PID that owns ``socket_path``."""
    return Path(f"{socket_path}.pid")


def _read_pid(path: Path) -> int | None:
    """Read a PID from ``path`` (``pid`` or ``pid:hostname``); None if absent."""
    try:
        content = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not content:
        return None
    try:
        return int(content.split(":")[0])
    except ValueError:
        return None


def _is_live(path: Path) -> bool:
    """True if ``path`` records a PID that is still alive."""
    pid = _read_pid(path)
    return pid is not None and _pid_alive(pid)


def _reclaim_socket(socket_path: Path, live: list[str]) -> bool:
    if not socket_path.exists():
        return False
    if _is_live(socket_pid_path(socket_path)):
        live.append(str(socket_path))
        return False
    socket_path.unlink(missing_ok=True)
    socket_pid_path(socket_path).unlink(missing_ok=True)
    return True


def _reclaim_lock(lock_path: Path, live: list[str]) -> bool:
    if not lock_path.exists():
        return False
    if _is_live(lock_path):
        live.append(str(lock_path))
        return False
    lock_path.unlink(missing_ok=True)
    return True


def _destroy_worktree(manager: WorktreeManager, path: Path) -> None:
    try:
        manager.destroy(path.name)
    except VcsError:
        # Not a registered worktree (or git already forgot it): the directory is
        # harness-owned, so remove it directly.
        shutil.rmtree(path, ignore_errors=True)


def _reclaim_worktrees(manager: WorktreeManager, live: list[str]) -> tuple[str, ...]:
    removed: list[str] = []
    for path in manager.list():
        if _is_live(path / _PID_FILE):
            live.append(str(path))
            continue
        _destroy_worktree(manager, path)
        removed.append(path.name)
    return tuple(removed)


def reclaim(
    workspace: str | Path,
    *,
    worktree_manager: WorktreeManager | None = None,
) -> ReclaimReport:
    """Reclaim dead-PID sockets, locks and worktrees for ``workspace``.

    Returns a :class:`ReclaimReport`; live-PID artifacts are never touched.
    Raises :class:`RecoveryError` if the filesystem cannot be inspected.
    """
    start = time.monotonic()
    live: list[str] = []
    try:
        paths: DerivedPaths = derive_paths(workspace)
        manager = worktree_manager or WorktreeManager(paths.workspace)
        socket_removed = _reclaim_socket(paths.socket_path, live)
        lock_removed = _reclaim_lock(paths.lock_path, live)
        worktrees_removed = _reclaim_worktrees(manager, live)
    except OSError as exc:
        raise RecoveryError(
            f"stale artifact reclamation failed for {workspace}: {exc}",
            remediation=(
                "Inspect the workspace .dev-harness directory and remove stale "
                "artifacts manually, then retry."
            ),
        ) from exc
    return ReclaimReport(
        socket_removed=socket_removed,
        lock_removed=lock_removed,
        worktrees_removed=worktrees_removed,
        live_artifacts=tuple(live),
        elapsed_seconds=time.monotonic() - start,
    )
