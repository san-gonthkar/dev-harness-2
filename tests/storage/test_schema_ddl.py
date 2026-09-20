"""Schema DDL constraints and index tests (V11 1.3)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dev_harness.storage.connection import connect
from dev_harness.storage.migrate import migrate_up

pytestmark = pytest.mark.unit


def _setup(tmp_path: Path) -> sqlite3.Connection:
    migrate_up(tmp_path / "state.db")
    return connect(tmp_path / "state.db")


def test_duplicate_composite_pk_rejected(tmp_path: Path) -> None:
    conn = _setup(tmp_path)
    conn.execute(
        "INSERT INTO checkpoints VALUES ('p','t','c1','{}','sha','hash',0,1)"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO checkpoints VALUES ('p','t','c1','{}','sha2','hash2',0,2)"
        )
    conn.close()


def test_scope_index_exists(tmp_path: Path) -> None:
    """The scope index on (project_id, thread_id, created_at DESC) must exist."""
    conn = _setup(tmp_path)
    rows = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='index' AND name='idx_checkpoints_scope'"
    ).fetchall()
    assert len(rows) == 1
    sql = rows[0]["sql"]
    assert "created_at DESC" in sql
    conn.close()


def test_columns_present(tmp_path: Path) -> None:
    conn = _setup(tmp_path)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(checkpoints)")}
    assert {
        "project_id", "thread_id", "checkpoint_id", "state_json",
        "state_sha256", "git_commit_hash", "is_paused", "created_at",
    } <= cols
    conn.close()
