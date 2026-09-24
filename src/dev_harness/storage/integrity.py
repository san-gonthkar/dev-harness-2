"""Corrupt-checkpoint detection, quarantine, and verified reads (V11 9.4).

The last line of defence against serving corrupt state. ``SqliteSaver.get_tuple``
stores a ``state_sha256`` digest but does **not** verify it on read; this module
provides the verified read path:

1. Recompute ``sha256(state_json)`` independently of the writer's helper and
   compare it to the stored ``state_sha256`` (a single byte flip fails).
2. On mismatch, *quarantine* the row instead of deleting it: the full original
   (corrupt) row is copied into the ``checkpoint_quarantine`` side table and the
   row is removed from ``checkpoints``. Nothing is lost — the evidence needed to
   investigate the corruption is preserved.
3. Serve the newest remaining *valid* checkpoint; if none exists, raise
   :class:`CorruptCheckpointError`.

Quarantine is a move-aside, not a delete: the side table's primary key mirrors
the source ``(project_id, thread_id, checkpoint_id)`` so re-quarantining the same
row is idempotent (``INSERT OR REPLACE``).
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from dataclasses import dataclass

from dev_harness.contracts.errors import CorruptCheckpointError
from dev_harness.storage.connection import connect
from dev_harness.storage.sqlite_saver import Scope, SqliteSaver

#: Side table that holds quarantined (corrupt) checkpoints. Created lazily by
#: this module so no migration-ledger entry is required.
QUARANTINE_TABLE = "checkpoint_quarantine"

#: Recorded on every quarantined row, explaining why it was moved aside.
CORRUPTION_REASON = (
    "state_sha256 mismatch: recomputed digest of state_json does not match the "
    "stored digest"
)

_QUARANTINE_DDL = (
    "CREATE TABLE IF NOT EXISTS checkpoint_quarantine ("
    "project_id TEXT NOT NULL,"
    "thread_id TEXT NOT NULL,"
    "checkpoint_id TEXT NOT NULL,"
    "state_json TEXT,"
    "state_sha256 TEXT,"
    "reason TEXT NOT NULL,"
    "quarantined_at INTEGER NOT NULL,"
    "PRIMARY KEY (project_id, thread_id, checkpoint_id))"
)


@dataclass(frozen=True)
class _RawRow:
    """The subset of a checkpoint row needed for verification/quarantine."""

    project_id: str
    thread_id: str
    checkpoint_id: str
    state_json: str
    state_sha256: str


def verify_row(state_json: str, state_sha256: str) -> bool:
    """Return True iff the recomputed digest of ``state_json`` matches.

    The digest is computed here, not via ``SqliteSaver._sha256``, so a bug in the
    writer's helper cannot mask corruption; the two are asserted equal in tests.
    """
    return hashlib.sha256(state_json.encode("utf-8")).hexdigest() == state_sha256


def _fetch_rows(conn: sqlite3.Connection, scope: Scope) -> list[_RawRow]:
    rows = conn.execute(
        "SELECT project_id, thread_id, checkpoint_id, state_json, state_sha256 "
        "FROM checkpoints WHERE project_id=? AND thread_id=? "
        "ORDER BY created_at DESC, checkpoint_id DESC",
        (scope.project_id, scope.thread_id),
    ).fetchall()
    return [
        _RawRow(
            project_id=str(r["project_id"]),
            thread_id=str(r["thread_id"]),
            checkpoint_id=str(r["checkpoint_id"]),
            state_json=str(r["state_json"]),
            state_sha256=str(r["state_sha256"]),
        )
        for r in rows
    ]


def _quarantine(conn: sqlite3.Connection, row: _RawRow, *, at: int) -> None:
    """Move ``row`` into the quarantine table (copy aside, then remove)."""
    conn.execute(_QUARANTINE_DDL)
    conn.execute(
        "INSERT OR REPLACE INTO checkpoint_quarantine "
        "(project_id, thread_id, checkpoint_id, state_json, state_sha256, reason, quarantined_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            row.project_id,
            row.thread_id,
            row.checkpoint_id,
            row.state_json,
            row.state_sha256,
            CORRUPTION_REASON,
            at,
        ),
    )
    conn.execute(
        "DELETE FROM checkpoints WHERE project_id=? AND thread_id=? AND checkpoint_id=?",
        (row.project_id, row.thread_id, row.checkpoint_id),
    )


def get_verified_tuple(
    saver: SqliteSaver, scope: Scope, checkpoint_id: str
) -> dict[str, object] | None:
    """Verified read of one checkpoint with quarantine + rollback.

    Returns the checkpoint when its digest verifies. If the requested row is
    corrupt, it is quarantined and the newest remaining *valid* checkpoint is
    returned. A missing checkpoint returns ``None`` (no error). If the requested
    row is corrupt and nothing valid remains, raises
    :class:`CorruptCheckpointError`.
    """
    conn = connect(saver.db_path)
    try:
        rows = _fetch_rows(conn, scope)
        target = next((r for r in rows if r.checkpoint_id == checkpoint_id), None)
        if target is None:
            return None
        if verify_row(target.state_json, target.state_sha256):
            return saver.get_tuple(scope, checkpoint_id)
        _quarantine(conn, target, at=int(time.time()))
        conn.commit()
        fallback = next(
            (
                r
                for r in rows
                if r.checkpoint_id != checkpoint_id
                and verify_row(r.state_json, r.state_sha256)
            ),
            None,
        )
        if fallback is None:
            raise CorruptCheckpointError(
                f"checkpoint {checkpoint_id!r} failed its state_sha256 digest and "
                "no prior valid checkpoint exists",
                remediation=(
                    "Restore a known-good checkpoint from backup before resuming."
                ),
            )
        return saver.get_tuple(scope, fallback.checkpoint_id)
    finally:
        conn.close()


def quarantine_corrupt(saver: SqliteSaver, scope: Scope) -> list[str]:
    """Scan a scope, quarantine every corrupt row, and return their ids.

    Newest-first order; the checkpoints table is left containing only rows whose
    digest verifies.
    """
    conn = connect(saver.db_path)
    try:
        quarantined: list[str] = []
        for row in _fetch_rows(conn, scope):
            if not verify_row(row.state_json, row.state_sha256):
                _quarantine(conn, row, at=int(time.time()))
                quarantined.append(row.checkpoint_id)
        if quarantined:
            conn.commit()
        return quarantined
    finally:
        conn.close()
