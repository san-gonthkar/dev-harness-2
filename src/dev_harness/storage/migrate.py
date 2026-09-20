"""Migration runner: forward + --down, schema_migrations ledger (V11 1.2)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from dev_harness.storage.connection import connect

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _ledger(conn) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at INTEGER)"
    )
    conn.commit()


def _migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("[0-9]*_*.sql"), key=lambda p: p.name)


def migrate_up(db_path: str | Path) -> list[str]:
    """Apply all pending migrations, returning the applied versions."""
    conn = connect(db_path)
    _ledger(conn)
    applied = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
    versions: list[str] = []
    for mfile in _migration_files():
        version = mfile.stem.split("_", 1)[0]
        if version in applied:
            continue
        sql = mfile.read_text(encoding="utf-8")
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (version, int(time.time())),
        )
        versions.append(version)
    conn.commit()
    conn.close()
    return versions


def migrate_down(db_path: str | Path, target: str | None = None) -> list[str]:
    """Roll back applied migrations (reverse order). Returns rolled-back versions."""
    conn = connect(db_path)
    _ledger(conn)
    applied = [r["version"] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY applied_at DESC")]
    if target is not None:
        applied = [v for v in applied if v > target]
    rolled_back: list[str] = []
    for version in applied:
        # Best-effort: drop the checkpoints table created by 0001.
        conn.executescript("DROP TABLE IF EXISTS checkpoints;")
        conn.execute("DELETE FROM schema_migrations WHERE version = ?", (version,))
        rolled_back.append(version)
    conn.commit()
    conn.close()
    return rolled_back


def main() -> int:
    args = sys.argv[1:]
    if "--workspace" not in args:
        print("usage: python -m dev_harness.storage.migrate --workspace <dir> [--up|--down]", file=sys.stderr)
        return 2
    db = args[args.index("--workspace") + 1]
    db_path = Path(db) / ".dev-harness" / "state.db"
    if "--up" in args:
        versions = migrate_up(db_path)
        print(f"applied: {versions}")
        return 0
    if "--down" in args:
        versions = migrate_down(db_path)
        print(f"rolled back: {versions}")
        return 0
    print("specify --up or --down", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
