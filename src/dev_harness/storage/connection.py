"""SQLite connection factory with required PRAGMAs (V11 1.1)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

PRAGMAS = (
    "PRAGMA journal_mode=WAL;",
    "PRAGMA synchronous=NORMAL;",
    "PRAGMA busy_timeout=10000;",
    "PRAGMA foreign_keys=ON;",
)


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection with the mandated WAL/NORMAL/busy pragmas."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    for pragma in PRAGMAS:
        conn.execute(pragma)
    return conn
