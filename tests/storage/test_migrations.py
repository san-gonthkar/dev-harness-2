"""Migration runner up/down idempotence tests (V11 1.2)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dev_harness.storage.connection import connect
from dev_harness.storage.migrate import migrate_down, migrate_up

pytestmark = pytest.mark.unit


def _sqlite_master_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchone()[0]


def test_migrate_up_applies_0001(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    versions = migrate_up(db)
    assert versions == ["0001"]
    conn = connect(db)
    row = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()
    assert row[0] == 1
    assert _sqlite_master_count(conn) == 2  # schema_migrations + checkpoints
    conn.close()


def test_reapply_is_noop(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    migrate_up(db)
    second = migrate_up(db)
    assert second == []  # no-op on re-apply
    conn = connect(db)
    assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 1
    conn.close()


def test_rollback_returns_to_baseline(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    migrate_up(db)
    before = _sqlite_master_count(connect(db))
    conn_before = connect(db)
    conn_before.close()
    rolled = migrate_down(db)
    assert rolled == ["0001"]
    conn = connect(db)
    assert _sqlite_master_count(conn) == 1  # only schema_migrations remains
    conn.close()
