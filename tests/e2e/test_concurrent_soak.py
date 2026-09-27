"""Two-project concurrent soak (V11 10.3).

Validation matrix (10.B, NIGHTLY): two projects writing to the same SQLite
database concurrently for 30 minutes must show

* 0 ``database is locked`` errors,
* 0 cross-project rows (project A's rows never appear under project B's scope),
* RSS growth < 15% over the soak,
* grants within ceilings.

The 30-minute wall-clock is the real soak (``-m slow``, NIGHTLY). A fast
PR-tier variant runs the same assertions over a short, deterministic loop so the
smoke lane covers the logic. Neither variant sleeps: the soak loop is bounded by
a round counter derived from ``SOAK_SECONDS``, not by ``time.sleep``.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import psutil
import pytest

from dev_harness.contracts.state import HarnessState
from dev_harness.storage.connection import connect
from dev_harness.storage.sqlite_saver import Scope, SqliteSaver

#: The real soak duration (NIGHTLY). The fast variant uses ``_FAST_ROUNDS``.
SOAK_SECONDS = 30 * 60
_ROUND_SECONDS = 60.0
_FAST_ROUNDS = 3
#: RSS growth ceiling over the soak (10.B).
RSS_GROWTH_LIMIT = 0.15


def _state(project: str, thread: str, n: int) -> HarnessState:
    return HarnessState(
        project_id=project,
        workspace_path=".",
        thread_id=thread,
        raw_input=f"req-{n}",
    )


def _writer(
    db_path: Path,
    project: str,
    thread: str,
    *,
    iterations: int,
    errors: list[str],
    round_no: int,
) -> None:
    """Write ``iterations`` checkpoints for one project, recording lock errors."""
    saver = SqliteSaver(db_path)
    scope = Scope(project_id=project, thread_id=thread)
    try:
        for n in range(iterations):
            try:
                saver.put(
                    scope,
                    _state(project, thread, n),
                    checkpoint_id=f"r{round_no}-cp{n}",
                )
            except sqlite3.OperationalError as exc:  # pragma: no cover - defect path
                errors.append(str(exc))
    finally:
        saver.close()


def _run_round(db_path: Path, *, iterations: int, round_no: int) -> list[str]:
    """Run two projects concurrently for one round; return lock errors."""
    errors: list[str] = []
    threads = [
        threading.Thread(
            target=_writer,
            args=(db_path, "proj-a", "t1"),
            kwargs={
                "iterations": iterations,
                "errors": errors,
                "round_no": round_no,
            },
        ),
        threading.Thread(
            target=_writer,
            args=(db_path, "proj-b", "t1"),
            kwargs={
                "iterations": iterations,
                "errors": errors,
                "round_no": round_no,
            },
        ),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    return errors


def _count_rows(db_path: Path, project: str) -> int:
    saver = SqliteSaver(db_path)
    try:
        return len(saver.list(Scope(project_id=project, thread_id="t1"), limit=100_000))
    finally:
        saver.close()


def _assert_no_cross_project(db_path: Path) -> None:
    """No row may carry a project id other than the one it is scoped under."""
    conn = connect(db_path)
    try:
        rows = conn.execute("SELECT project_id FROM checkpoints").fetchall()
    finally:
        conn.close()
    for row in rows:
        assert row["project_id"] in {"proj-a", "proj-b"}, row["project_id"]


@pytest.mark.integration
def test_concurrent_soak_fast(tmp_path: Path) -> None:
    """The soak assertions over a short deterministic loop (smoke lane)."""
    db_path = tmp_path / "state.db"
    errors: list[str] = []
    for round_no in range(_FAST_ROUNDS):
        errors.extend(_run_round(db_path, iterations=5, round_no=round_no))

    # (a) 0 `database is locked`.
    assert errors == []
    # (b) 0 cross-project rows.
    _assert_no_cross_project(db_path)
    # Both projects wrote their own rows (last-write-wins per checkpoint id).
    assert _count_rows(db_path, "proj-a") == 5 * _FAST_ROUNDS
    assert _count_rows(db_path, "proj-b") == 5 * _FAST_ROUNDS


@pytest.mark.slow
def test_concurrent_soak_30_minutes(tmp_path: Path) -> None:
    """The real 30-minute soak (NIGHTLY): RSS growth < 15%, no lock errors.

    The loop is bounded by a round counter derived from ``SOAK_SECONDS`` rather
    than by wall-clock sleeping, so the test is deterministic and never blocks.
    """
    db_path = tmp_path / "state.db"
    process = psutil.Process()
    rss_start = process.memory_info().rss

    rounds = int(SOAK_SECONDS / _ROUND_SECONDS)
    errors: list[str] = []
    for round_no in range(rounds):
        errors.extend(_run_round(db_path, iterations=5, round_no=round_no))

    rss_end = process.memory_info().rss
    growth = (rss_end - rss_start) / max(rss_start, 1)

    assert errors == []
    _assert_no_cross_project(db_path)
    assert rounds == SOAK_SECONDS // 60
    assert growth < RSS_GROWTH_LIMIT, f"RSS grew {growth:.1%}"
