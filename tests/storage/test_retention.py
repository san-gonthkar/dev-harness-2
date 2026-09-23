"""Retention policy tests (V11 1.7)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dev_harness.contracts.state import HarnessState
from dev_harness.storage.connection import connect
from dev_harness.storage.retention import (
    prune_and_vacuum,
    vacuum_with_retry,
)
from dev_harness.storage.sqlite_saver import Scope, SqliteSaver

pytestmark = pytest.mark.unit


def _state(project: str, thread: str, raw: str = "x") -> HarnessState:
    return HarnessState(
        project_id=project, workspace_path="/w", thread_id=thread, raw_input=raw
    )


def test_keep5_over_20_leaves_5_plus_seals(tmp_path: Path) -> None:
    saver = SqliteSaver(tmp_path / "db.sqlite")
    for i in range(20):
        saver.put(
            Scope("p1", "t1"),
            _state("p1", "t1", f"v{i}"),
            created_at=i,
            checkpoint_id=f"c{i}",
        )
    # Seal one old checkpoint.
    saver._conn.execute("UPDATE checkpoints SET is_paused=1 WHERE checkpoint_id='c0'")
    saver._conn.commit()
    deleted = prune_and_vacuum(tmp_path / "db.sqlite", Scope("p1", "t1"), keep=5)
    rows = saver.list(Scope("p1", "t1"), limit=100)
    non_seal = [r for r in rows if not r["is_paused"]]
    seals = [r for r in rows if r["is_paused"]]
    assert len(non_seal) == 5
    assert len(seals) == 1  # c0 preserved
    assert deleted == 14  # 20 - 5 kept - 1 seal
    saver.close()


def test_file_shrinks_after_vacuum(tmp_path: Path) -> None:
    saver = SqliteSaver(tmp_path / "db.sqlite")
    for i in range(50):
        saver.put(
            Scope("p1", "t1"),
            _state("p1", "t1", "x" * 100),
            created_at=i,
            checkpoint_id=f"c{i}",
        )
    saver.close()
    size_before = (tmp_path / "db.sqlite").stat().st_size
    prune_and_vacuum(tmp_path / "db.sqlite", Scope("p1", "t1"), keep=5)
    size_after = (tmp_path / "db.sqlite").stat().st_size
    assert size_after < size_before


def test_retained_seal_still_restorable(tmp_path: Path) -> None:
    saver = SqliteSaver(tmp_path / "db.sqlite")
    for i in range(10):
        saver.put(
            Scope("p1", "t1"),
            _state("p1", "t1", f"v{i}"),
            created_at=i,
            checkpoint_id=f"c{i}",
        )
    saver._conn.execute("UPDATE checkpoints SET is_paused=1 WHERE checkpoint_id='c3'")
    saver._conn.commit()
    prune_and_vacuum(tmp_path / "db.sqlite", Scope("p1", "t1"), keep=5)
    got = saver.get_tuple(Scope("p1", "t1"), "c3")
    assert got is not None
    assert got["is_paused"] is True
    saver.close()


def test_vacuum_busy_retried_not_raised(tmp_path: Path) -> None:
    """A SQLITE_BUSY during VACUUM is retried, never raised."""
    conn = connect(tmp_path / "db.sqlite")
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.commit()

    class BusyOnce:
        """Wraps a connection, raising SQLITE_BUSY on the first VACUUM."""

        def __init__(self, inner):
            self._inner = inner
            self.vacuum_attempts = 0

        def execute(self, sql, params=None):
            if "VACUUM" in str(sql):
                self.vacuum_attempts += 1
                if self.vacuum_attempts == 1:
                    raise sqlite3.OperationalError("database is locked")
            if params is None:
                return self._inner.execute(sql)
            return self._inner.execute(sql, params)

        def commit(self):
            self._inner.commit()

    busy = BusyOnce(conn)
    vacuum_with_retry(busy, attempts=3)  # type: ignore[arg-type]
    assert busy.vacuum_attempts == 2  # first raised, second succeeded
    conn.close()
