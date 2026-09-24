"""Migration 0002 worktree-state schema: apply + per-version rollback (V11 8.21a)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dev_harness.contracts.errors import StorageError
from dev_harness.storage.connection import connect
from dev_harness.storage.migrate import (
    MIGRATIONS_DIR,
    _ledger,
    migrate_down,
    migrate_up,
)


def _object_count(conn: sqlite3.Connection) -> int:
    """Count user objects in sqlite_master (tables + indexes)."""
    return conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
    ).fetchone()[0]


def _columns(conn: sqlite3.Connection) -> set[str]:
    return {r["name"] for r in conn.execute("PRAGMA table_info(checkpoints)")}


def _baseline_after_0001(tmp_path: Path) -> int:
    """Object count after 0001 only - the rollback baseline for 0002.

    Mirrors migrate_up's ledger creation plus the 0001 DDL, without applying 0002.
    """
    db = tmp_path / "baseline.db"
    conn = connect(db)
    _ledger(conn)
    conn.executescript((MIGRATIONS_DIR / "0001_init.sql").read_text(encoding="utf-8"))
    conn.commit()
    count = _object_count(conn)
    conn.close()
    return count


@pytest.mark.unit
def test_0002_applies_cleanly_on_top_of_0001(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    assert migrate_up(db) == ["0001", "0002"]
    conn = connect(db)
    assert _columns(conn) >= {"worktree_head", "worktree_diff"}
    assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 2
    conn.close()


@pytest.mark.integration
def test_0002_rollback_returns_to_baseline(tmp_path: Path) -> None:
    baseline = _baseline_after_0001(tmp_path)
    db = tmp_path / "state.db"
    migrate_up(db)
    conn = connect(db)
    assert _object_count(conn) == baseline  # ALTER adds no sqlite_master objects
    conn.close()

    assert migrate_down(db, target="0001") == ["0002"]
    conn = connect(db)
    assert _object_count(conn) == baseline
    assert _columns(conn) == {
        "project_id",
        "thread_id",
        "checkpoint_id",
        "state_json",
        "state_sha256",
        "git_commit_hash",
        "is_paused",
        "created_at",
    }
    conn.close()


@pytest.mark.integration
def test_worktree_columns_capture_head_and_diff(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    migrate_up(db)
    conn = connect(db)
    conn.execute(
        "INSERT INTO checkpoints (project_id, thread_id, checkpoint_id, state_json, "
        "state_sha256, git_commit_hash, is_paused, created_at, worktree_head, worktree_diff) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("p", "t", "c1", "{}", "sha", "base", 0, 1, "deadbeef", "diff --git a/x b/x"),
    )
    conn.commit()
    row = conn.execute(
        "SELECT worktree_head, worktree_diff FROM checkpoints WHERE checkpoint_id='c1'"
    ).fetchone()
    assert row["worktree_head"] == "deadbeef"
    assert row["worktree_diff"] == "diff --git a/x b/x"
    conn.close()


@pytest.mark.negative
def test_rollback_0002_preserves_0001_table(tmp_path: Path) -> None:
    """Rolling back 0002 must not destroy 0001's checkpoints table."""
    db = tmp_path / "state.db"
    migrate_up(db)
    migrate_down(db, target="0001")
    conn = connect(db)
    exists = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='checkpoints'"
    ).fetchone()[0]
    assert exists == 1
    assert "git_commit_hash" in _columns(conn)
    conn.close()


@pytest.mark.negative
def test_unknown_version_rollback_raises(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    migrate_up(db)
    conn = connect(db)
    conn.execute(
        "INSERT INTO schema_migrations (version, applied_at) VALUES ('9999', 0)"
    )
    conn.commit()
    conn.close()
    with pytest.raises(StorageError):
        migrate_down(db)
