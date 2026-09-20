"""SqliteSaver read conformance tests (V11 1.5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dev_harness.contracts.state import HarnessState
from dev_harness.storage.sqlite_saver import Scope, SqliteSaver

pytestmark = pytest.mark.unit


def _state(project: str, thread: str, raw: str = "x") -> HarnessState:
    return HarnessState(project_id=project, workspace_path="/w", thread_id=thread, raw_input=raw)


def test_get_tuple_equals_golden(tmp_path: Path) -> None:
    saver = SqliteSaver(tmp_path / "db.sqlite")
    state = _state("p1", "t1")
    cid = saver.put(Scope("p1", "t1"), state)
    got = saver.get_tuple(Scope("p1", "t1"), cid)
    assert got is not None
    # Round-trip the stored JSON back to a model and compare field-for-field.
    parsed = HarnessState.model_validate_json(got["state_json"])
    assert parsed.model_dump(mode="json") == state.model_dump(mode="json")
    saver.close()


def test_list_newest_first(tmp_path: Path) -> None:
    saver = SqliteSaver(tmp_path / "db.sqlite")
    for i in range(5):
        saver.put(Scope("p1", "t1"), _state("p1", "t1", f"v{i}"), created_at=i, checkpoint_id=f"c{i}")
    rows = saver.list(Scope("p1", "t1"), limit=3)
    # created_at DESC -> c4, c3, c2
    assert [r["checkpoint_id"] for r in rows] == ["c4", "c3", "c2"]
    saver.close()


def test_paging_disjoint_sets(tmp_path: Path) -> None:
    saver = SqliteSaver(tmp_path / "db.sqlite")
    for i in range(6):
        saver.put(Scope("p1", "t1"), _state("p1", "t1", f"v{i}"), created_at=i, checkpoint_id=f"c{i}")
    page1 = saver.list(Scope("p1", "t1"), limit=3, offset=0)
    page2 = saver.list(Scope("p1", "t1"), limit=3, offset=3)
    ids1 = {r["checkpoint_id"] for r in page1}
    ids2 = {r["checkpoint_id"] for r in page2}
    assert ids1.isdisjoint(ids2)
    assert len(ids1) == 3 and len(ids2) == 3
    saver.close()


def test_get_missing_returns_none(tmp_path: Path) -> None:
    saver = SqliteSaver(tmp_path / "db.sqlite")
    assert saver.get_tuple(Scope("p1", "t1"), "nope") is None
    saver.close()
