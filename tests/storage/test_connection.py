"""Connection factory PRAGMA tests (V11 1.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dev_harness.storage.connection import connect

pytestmark = pytest.mark.unit


def test_wal_journal_mode(tmp_path: Path) -> None:
    conn = connect(tmp_path / "db.sqlite")
    row = conn.execute("PRAGMA journal_mode;").fetchone()
    assert row[0].lower() == "wal"
    conn.close()


def test_synchronous_normal(tmp_path: Path) -> None:
    conn = connect(tmp_path / "db.sqlite")
    assert conn.execute("PRAGMA synchronous;").fetchone()[0] == 1  # NORMAL
    conn.close()


def test_busy_timeout(tmp_path: Path) -> None:
    conn = connect(tmp_path / "db.sqlite")
    assert conn.execute("PRAGMA busy_timeout;").fetchone()[0] == 10000
    conn.close()


def test_foreign_keys_on(tmp_path: Path) -> None:
    conn = connect(tmp_path / "db.sqlite")
    assert conn.execute("PRAGMA foreign_keys;").fetchone()[0] == 1
    conn.close()
