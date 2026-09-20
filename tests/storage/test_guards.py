"""Namespace guard tests (V11 1.6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dev_harness.contracts.errors import UnscopedQueryError
from dev_harness.contracts.state import HarnessState
from dev_harness.storage.guards import require_scope
from dev_harness.storage.sqlite_saver import Scope, SqliteSaver

pytestmark = pytest.mark.unit


def test_unscoped_list_raises() -> None:
    with pytest.raises(UnscopedQueryError):
        require_scope({})


def test_partial_scope_raises() -> None:
    with pytest.raises(UnscopedQueryError):
        require_scope({"project_id": "p1"})


def test_valid_scope_returns() -> None:
    scope = require_scope({"project_id": "p1", "thread_id": "t1"})
    assert scope == Scope("p1", "t1")


def test_proj_a_rows_not_visible_under_proj_b(tmp_path: Path) -> None:
    saver = SqliteSaver(tmp_path / "db.sqlite")
    saver.put(Scope("proj_A", "t1"), HarnessState(project_id="proj_A", workspace_path="/w", thread_id="t1"))
    rows_b = saver.list(Scope("proj_B", "t1"))
    assert rows_b == []
    rows_a = saver.list(Scope("proj_A", "t1"))
    assert len(rows_a) == 1
    saver.close()
