"""Crash recovery: detect an un-finalized session and resume it (V11 9.1).

A crash (SIGKILL, power loss) leaves a session with checkpoints but no
clean-shutdown marker. On the next start the harness must:

1. **Detect** the un-finalized session - the newest checkpoint is not the seal
   written by :class:`engine.shutdown.GracefulShutdown` (``checkpoint_id ==
   "shutdown"``, ``is_paused=True``).
2. **Offer** resume-from-checkpoint - :func:`detect_unfinalized_session` returns
   a :class:`RecoveryPlan` (or ``None`` when the session ended cleanly), so the
   caller can prompt the user before acting.
3. **Restore** each worker worktree to its recorded HEAD + diff (8.21b) and
   return the state so it equals the last seal **field-for-field**.

Reuse, not reimplementation:

* :func:`storage.integrity.get_verified_tuple` - the verified read path, so a
  corrupt newest checkpoint is quarantined and the prior valid one is served.
* :meth:`engine.worker_workspace.WorkerWorkspace.restore` - replays the captured
  ``{head, diff}`` snapshot (8.21b).
* :func:`recovery.reclaim.reclaim` - clears stale sockets/locks/worktrees left by
  the dead process before the worktrees are recreated.

``recovery/`` is in the 9.C coverage set (90/85); every branch is exercised by
``tests/recovery/test_session_recovery.py``. No ``time.sleep``, no network, no
``tui/`` import.
"""

from __future__ import annotations

import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path

from dev_harness.contracts.errors import RecoveryError
from dev_harness.contracts.state import Chunk, HarnessState
from dev_harness.engine.worker_workspace import WorkerWorkspace, chunk_branch
from dev_harness.paths import derive_paths
from dev_harness.recovery.reclaim import reclaim
from dev_harness.storage.checkpoint_binding import deserialize_worktree_state
from dev_harness.storage.connection import connect
from dev_harness.storage.integrity import get_verified_tuple
from dev_harness.storage.sqlite_saver import Scope, SqliteSaver

#: The checkpoint id written by the graceful-shutdown seal
#: (``engine/shutdown.py``). Its presence as the newest checkpoint is the
#: clean-shutdown marker; anything else means the session was un-finalized.
SEAL_CHECKPOINT_ID = "shutdown"


@dataclass(frozen=True)
class RecoveryPlan:
    """An un-finalized session and everything needed to resume it.

    Returned by :func:`detect_unfinalized_session`; the caller *offers* it to the
    user (resume-from-checkpoint) before calling :func:`resume`.
    """

    scope: Scope
    checkpoint_id: str
    state: HarnessState
    worktrees: dict[str, dict[str, str]]


def _newest_checkpoint(db_path: Path) -> tuple[Scope, str] | None:
    """The newest checkpoint across all scopes, or ``None`` if there is none.

    Uses the same ordering as :meth:`SqliteSaver.list` (``created_at DESC,
    checkpoint_id DESC``) so "newest" is consistent with the rest of storage.
    """
    if not db_path.exists():
        return None
    conn = connect(db_path)
    try:
        try:
            row = conn.execute(
                "SELECT project_id, thread_id, checkpoint_id FROM checkpoints "
                "ORDER BY created_at DESC, checkpoint_id DESC LIMIT 1"
            ).fetchone()
        except sqlite3.OperationalError:
            # No schema yet (fresh/empty DB): nothing to recover.
            return None
    finally:
        conn.close()
    if row is None:
        return None
    return (
        Scope(str(row["project_id"]), str(row["thread_id"])),
        str(row["checkpoint_id"]),
    )


def detect_unfinalized_session(workspace: str | Path) -> RecoveryPlan | None:
    """Detect an un-finalized session and return a resume plan.

    Returns ``None`` when the workspace has no checkpoints or the newest
    checkpoint is the clean-shutdown seal. A corrupt newest checkpoint is
    quarantined by the verified read; if the prior valid checkpoint is the seal,
    the session is treated as finalized (``None``).
    """
    paths = derive_paths(workspace)
    newest = _newest_checkpoint(paths.state_db)
    if newest is None:
        return None
    scope, checkpoint_id = newest
    if checkpoint_id == SEAL_CHECKPOINT_ID:
        return None
    saver = SqliteSaver(paths.state_db)
    try:
        row = get_verified_tuple(saver, scope, checkpoint_id)
    finally:
        saver.close()
    if row is None:
        return None
    served_id = str(row["checkpoint_id"])
    if served_id == SEAL_CHECKPOINT_ID:
        # The newest row was corrupt and the prior valid checkpoint is the seal.
        return None
    state = HarnessState.model_validate_json(str(row["state_json"]))
    return RecoveryPlan(
        scope=scope,
        checkpoint_id=served_id,
        state=state,
        worktrees=deserialize_worktree_state(row),
    )


def _delete_branch(workspace: Path, branch: str) -> None:
    """Delete ``branch`` if it exists (best-effort; a missing branch is fine).

    ``reclaim`` removes a stale worktree but git keeps its branch, so a re-bind
    with ``-b`` would fail with "branch already exists". Deleting the orphaned
    branch first makes the re-bind idempotent. A branch that is still checked out
    elsewhere cannot be deleted; that is left for the caller's bind to surface.
    """
    subprocess.run(
        ["git", "-C", str(workspace), "branch", "-D", branch],
        capture_output=True,
        text=True,
        check=False,
    )


def resume(
    plan: RecoveryPlan,
    workspace: str | Path,
    *,
    worker_workspace: WorkerWorkspace | None = None,
) -> HarnessState:
    """Resume ``plan``: reclaim stale artifacts, restore worktrees, return state.

    Stale sockets/locks/worktrees left by the dead process are reclaimed first
    (9.2), then each recorded worker worktree is recreated and restored to its
    captured HEAD + diff (8.21b). The returned :class:`HarnessState` is the
    verified checkpoint state, so it equals the last seal field-for-field.

    Raises :class:`RecoveryError` when a worktree cannot be restored.
    """
    reclaim(workspace)
    ws = worker_workspace or WorkerWorkspace(workspace)
    for worker_id, snapshot in plan.worktrees.items():
        try:
            _delete_branch(Path(workspace), chunk_branch(worker_id))
            ws.bind(worker_id, Chunk(chunk_id=worker_id, title=worker_id))
            ws.restore(worker_id, snapshot)
        except RecoveryError:
            raise
        except Exception as exc:
            raise RecoveryError(
                f"failed to restore worktree '{worker_id}' during resume: {exc}",
                remediation=(
                    "Inspect the worktree and re-apply the captured diff, or "
                    "resume from an earlier checkpoint."
                ),
            ) from exc
    return plan.state
