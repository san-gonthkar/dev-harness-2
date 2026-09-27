"""SQLite connection factory with required PRAGMAs (V11 1.1)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

PRAGMAS = (
    "PRAGMA journal_mode=WAL;",
    "PRAGMA synchronous=NORMAL;",
    "PRAGMA foreign_keys=ON;",
)

#: Seconds a connection waits on a locked database before raising SQLITE_BUSY.
BUSY_TIMEOUT_SECONDS = 10.0


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection with the mandated WAL/NORMAL/busy pragmas.

    The busy timeout is set via ``sqlite3.connect(timeout=...)`` rather than a
    ``PRAGMA busy_timeout``: the C-level timeout is in force *before* the first
    statement, so the ``journal_mode=WAL`` switch (which needs a brief exclusive
    lock) waits instead of failing immediately when another connection holds the
    database - the "database is locked" the concurrent soak (10.3) exercises.
    ``isolation_level=None`` keeps the connection in autocommit so no implicit
    transaction is held open across the pragmas.
    """
    conn = sqlite3.connect(
        str(db_path), timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None
    )
    conn.row_factory = sqlite3.Row
    for pragma in PRAGMAS:
        conn.execute(pragma)
    return conn
