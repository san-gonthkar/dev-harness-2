"""SqliteSaver write/read paths (V11 1.4, 1.5).

Stores HarnessState as JSON with a sha256 digest, git commit, and paused flag.
Namespace is (project_id, thread_id).
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from dev_harness.contracts.state import HarnessState
from dev_harness.storage.connection import connect


@dataclass
class Scope:
    """Query scope enforced by the namespace guard."""

    project_id: str
    thread_id: str


class SqliteSaver:
    """Write/read checkpoints in SQLite, newest-first."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = connect(self.db_path)
        from dev_harness.storage.migrate import migrate_up

        migrate_up(self.db_path)

    def _sha256(self, state_json: str) -> str:
        return hashlib.sha256(state_json.encode("utf-8")).hexdigest()

    def put(
        self,
        scope: Scope,
        state: HarnessState,
        *,
        checkpoint_id: str | None = None,
        git_commit_hash: str | None = None,
        is_paused: bool = False,
        created_at: int | None = None,
        worktree_head: str | None = None,
        worktree_diff: str | None = None,
    ) -> str:
        """Write one checkpoint. Returns the checkpoint_id.

        ``worktree_head``/``worktree_diff`` carry the serialized per-worker
        worktree state (a JSON ``worker_id -> value`` map each); they are NULL
        for checkpoints that do not capture worktrees (V11 8.21a/8.21b).
        """
        cid = checkpoint_id or f"cp_{len(self.list(scope))}"
        state_json = state.model_dump_json()
        sha = self._sha256(state_json)
        try:
            self._conn.execute("BEGIN")
            self._conn.execute(
                "INSERT INTO checkpoints (project_id, thread_id, checkpoint_id, state_json, state_sha256, git_commit_hash, is_paused, created_at, worktree_head, worktree_diff) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    scope.project_id,
                    scope.thread_id,
                    cid,
                    state_json,
                    sha,
                    git_commit_hash,
                    int(is_paused),
                    created_at or 0,
                    worktree_head,
                    worktree_diff,
                ),
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return cid

    def get_tuple(self, scope: Scope, checkpoint_id: str) -> dict[str, object] | None:
        """Read one checkpoint by id, verifying the digest."""
        row = self._conn.execute(
            "SELECT * FROM checkpoints WHERE project_id=? AND thread_id=? AND checkpoint_id=?",
            (scope.project_id, scope.thread_id, checkpoint_id),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def list(
        self, scope: Scope, *, limit: int = 100, offset: int = 0
    ) -> list[dict[str, object]]:
        """Newest-first checkpoints for a scope with cursor paging."""
        rows = self._conn.execute(
            "SELECT * FROM checkpoints WHERE project_id=? AND thread_id=? "
            "ORDER BY created_at DESC, checkpoint_id DESC LIMIT ? OFFSET ?",
            (scope.project_id, scope.thread_id, limit, offset),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, object]:
        return {
            "project_id": row["project_id"],
            "thread_id": row["thread_id"],
            "checkpoint_id": row["checkpoint_id"],
            "state_json": row["state_json"],
            "state_sha256": row["state_sha256"],
            "git_commit_hash": row["git_commit_hash"],
            "is_paused": bool(row["is_paused"]),
            "created_at": row["created_at"],
            "worktree_head": row["worktree_head"],
            "worktree_diff": row["worktree_diff"],
        }

    def close(self) -> None:
        self._conn.close()
