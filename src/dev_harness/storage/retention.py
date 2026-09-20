"""Retention: keep last N per thread + all paused seals; prune + VACUUM (V11 1.7)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from dev_harness.storage.connection import connect
from dev_harness.storage.sqlite_saver import Scope


class RetentionPolicy:
    """Prune checkpoints beyond the newest N per thread, preserving seals."""

    def __init__(self, keep: int = 5) -> None:
        self.keep = keep

    def prune(self, conn: sqlite3.Connection, scope: Scope) -> int:
        """Delete non-seal rows beyond the newest ``keep`` for the scope.

        Returns the number of rows deleted. Seals (is_paused=1) are never pruned.
        """
        # The (keep+1)-th newest non-seal checkpoint: everything older is pruned.
        row = conn.execute(
            "SELECT checkpoint_id FROM checkpoints "
            "WHERE project_id=? AND thread_id=? AND is_paused=0 "
            "ORDER BY created_at DESC, checkpoint_id DESC LIMIT 1 OFFSET ?",
            (scope.project_id, scope.thread_id, self.keep),
        ).fetchone()
        if row is None:
            # Fewer than ``keep`` non-seal rows: nothing to prune.
            return 0
        deleted = conn.execute(
            "DELETE FROM checkpoints "
            "WHERE project_id=? AND thread_id=? AND is_paused=0 "
            "AND checkpoint_id NOT IN ("
            "  SELECT checkpoint_id FROM checkpoints "
            "  WHERE project_id=? AND thread_id=? AND is_paused=0 "
            "  ORDER BY created_at DESC, checkpoint_id DESC LIMIT ?"
            ")",
            (scope.project_id, scope.thread_id, scope.project_id, scope.thread_id, self.keep),
        ).rowcount
        conn.commit()
        return deleted


def vacuum_with_retry(conn: sqlite3.Connection, *, attempts: int = 3) -> None:
    """VACUUM honoring busy_timeout; retry on SQLITE_BUSY, never raise."""
    for _ in range(attempts):
        try:
            conn.execute("VACUUM")
            conn.commit()
            return
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                continue
            raise
    # Exhausted retries: leave the file un-vacuumed rather than raising.
    return


def prune_and_vacuum(db_path: str | Path, scope: Scope, *, keep: int = 5) -> int:
    """Prune a scope then VACUUM, returning rows deleted."""
    conn = connect(db_path)
    policy = RetentionPolicy(keep)
    deleted = policy.prune(conn, scope)
    vacuum_with_retry(conn)
    conn.close()
    return deleted
