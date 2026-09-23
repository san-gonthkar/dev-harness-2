"""Two-workspace concurrency isolation tests (V11 1.13)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from dev_harness.contracts.state import HarnessState
from dev_harness.storage.sqlite_saver import Scope, SqliteSaver
from tests.support.workspace import make_workspace

pytestmark = pytest.mark.slow


def _write_loop(db: Path, project: str, thread: str, count: int, errors: list) -> None:
    try:
        saver = SqliteSaver(db)
        for i in range(count):
            state = HarnessState(
                project_id=project,
                workspace_path=str(db),
                thread_id=thread,
                raw_input=f"{project}-{i}",
            )
            saver.put(Scope(project, thread), state, checkpoint_id=f"c{i}")
        saver.close()
    except Exception as exc:  # noqa: BLE001 - collect any error for the assertion
        errors.append(exc)


def test_two_workspaces_no_cross_project_rows(tmp_path: Path) -> None:
    ws_a = make_workspace(tmp_path, name="wA")
    ws_b = make_workspace(tmp_path, name="wB")
    db_a = ws_a / ".dev-harness" / "state.db"
    db_b = ws_b / ".dev-harness" / "state.db"

    errors: list[Exception] = []
    threads = [
        threading.Thread(target=_write_loop, args=(db_a, "proj_A", "t1", 50, errors)),
        threading.Thread(target=_write_loop, args=(db_b, "proj_B", "t1", 50, errors)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert errors == [], f"errors: {errors}"
    saver_a = SqliteSaver(db_a)
    saver_b = SqliteSaver(db_b)
    assert len(saver_a.list(Scope("proj_A", "t1"), limit=100)) == 50
    assert len(saver_a.list(Scope("proj_B", "t1"), limit=100)) == 0  # no cross-project
    assert len(saver_b.list(Scope("proj_B", "t1"), limit=100)) == 50
    saver_a.close()
    saver_b.close()
