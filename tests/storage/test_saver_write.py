"""SqliteSaver write conformance tests (V11 1.4)."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from dev_harness.contracts.state import HarnessState
from dev_harness.storage.sqlite_saver import Scope, SqliteSaver

pytestmark = pytest.mark.unit


def _state(project: str = "p1", thread: str = "t1") -> HarnessState:
    return HarnessState(project_id=project, workspace_path="/w", thread_id=thread)


def test_state_sha256_matches_recomputed(tmp_path: Path) -> None:
    saver = SqliteSaver(tmp_path / "db.sqlite")
    state = _state()
    cid = saver.put(Scope("p1", "t1"), state, git_commit_hash="abc")
    row = saver._conn.execute(
        "SELECT state_json, state_sha256 FROM checkpoints WHERE checkpoint_id=?", (cid,)
    ).fetchone()
    expected = hashlib.sha256(row["state_json"].encode("utf-8")).hexdigest()
    assert row["state_sha256"] == expected
    saver.close()


def test_put_returns_id_and_round_trips(tmp_path: Path) -> None:
    saver = SqliteSaver(tmp_path / "db.sqlite")
    state = _state()
    cid = saver.put(Scope("p1", "t1"), state)
    got = saver.get_tuple(Scope("p1", "t1"), cid)
    assert got is not None
    assert HarnessState.model_validate_json(got["state_json"]) == state
    saver.close()


def test_injected_mid_write_failure_leaves_no_partial(tmp_path: Path) -> None:
    """A failed write must not leave a partial row (transaction rollback).

    We simulate a failure by giving the saver a connection whose commit raises.
    The put() must roll back so no partial row persists.
    """

    from dev_harness.storage import sqlite_saver as mod

    real = sqlite3.connect(str(tmp_path / "db.sqlite"))
    # Use a fresh real connection, migrating the schema manually.
    for pragma in ("PRAGMA journal_mode=WAL;", "PRAGMA synchronous=NORMAL;"):
        real.execute(pragma)
    real.execute(
        "CREATE TABLE IF NOT EXISTS checkpoints (project_id TEXT, thread_id TEXT, "
        "checkpoint_id TEXT, state_json TEXT, state_sha256 TEXT, git_commit_hash TEXT, "
        "is_paused INTEGER, created_at INTEGER, worktree_head TEXT, worktree_diff TEXT, "
        "PRIMARY KEY (project_id, thread_id, checkpoint_id))"
    )
    real.commit()

    class FailingConn:
        def __init__(self, inner):
            self._inner = inner

        def execute(self, sql, params=None):
            if params is None:
                return self._inner.execute(sql)
            return self._inner.execute(sql, params)

        def commit(self):
            raise RuntimeError("simulated failure")

        def rollback(self):
            self._inner.rollback()

    saver = mod.SqliteSaver.__new__(mod.SqliteSaver)
    saver._conn = FailingConn(real)
    with pytest.raises(RuntimeError):
        saver.put(Scope("p1", "t1"), _state())
    count = real.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0]
    assert count == 0
    real.close()
