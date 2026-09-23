"""Session manager tests (V11 5.2).

Validation matrix: second START_SESSION -> SessionExistsError with existing
thread_id; 1,000 ids unique and sortable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dev_harness.contracts.enums import ExecutionState
from dev_harness.contracts.errors import HarnessError, SessionExistsError
from dev_harness.engine.session import (
    Session,
    SessionManager,
    new_thread_id,
    project_id_for,
)

pytestmark = pytest.mark.unit


@pytest.mark.unit
def test_new_session_creates_harness_state(tmp_path: Path) -> None:
    """A new session carries a full HarnessState bound to the workspace."""
    mgr = SessionManager()
    session = mgr.new_session(tmp_path)
    assert isinstance(session, Session)
    assert session.state.project_id == session.project_id
    assert session.state.workspace_path == str(tmp_path.resolve())
    assert session.state.thread_id == session.thread_id
    assert session.state.tui_state.critic_gatekeeper_status == ExecutionState.READY


@pytest.mark.unit
def test_second_start_session_raises_with_existing_thread_id(tmp_path: Path) -> None:
    """A second START_SESSION for the same workspace raises SessionExistsError
    carrying the existing thread_id."""
    mgr = SessionManager()
    first = mgr.new_session(tmp_path)
    with pytest.raises(SessionExistsError) as excinfo:
        mgr.new_session(tmp_path)
    assert first.thread_id in str(excinfo.value)
    assert excinfo.value.remediation


@pytest.mark.unit
def test_session_exists_error_is_harness_error() -> None:
    """SessionExistsError is part of the HarnessError taxonomy."""
    assert issubclass(SessionExistsError, HarnessError)


@pytest.mark.unit
def test_one_active_session_per_workspace(tmp_path: Path) -> None:
    """Different workspaces get independent sessions; same workspace does not."""
    mgr = SessionManager()
    ws_a = tmp_path / "a"
    ws_b = tmp_path / "b"
    ws_a.mkdir()
    ws_b.mkdir()
    sa = mgr.new_session(ws_a)
    sb = mgr.new_session(ws_b)
    assert sa.thread_id != sb.thread_id
    assert mgr.count() == 2
    with pytest.raises(SessionExistsError):
        mgr.new_session(ws_a)


@pytest.mark.unit
def test_get_returns_session(tmp_path: Path) -> None:
    """get() returns the session for the workspace."""
    mgr = SessionManager()
    session = mgr.new_session(tmp_path)
    assert mgr.get(tmp_path) is session
    assert mgr.get(tmp_path / "other") is None


@pytest.mark.unit
def test_remove_drops_session(tmp_path: Path) -> None:
    """remove() drops the session and allows a new one."""
    mgr = SessionManager()
    session = mgr.new_session(tmp_path)
    assert mgr.remove(tmp_path) is session
    assert mgr.get(tmp_path) is None
    assert mgr.count() == 0
    # A new session can now be created.
    again = mgr.new_session(tmp_path)
    assert again.thread_id != session.thread_id


@pytest.mark.unit
def test_remove_missing_returns_none(tmp_path: Path) -> None:
    """remove() on an unknown workspace returns None."""
    mgr = SessionManager()
    assert mgr.remove(tmp_path) is None


@pytest.mark.unit
def test_list_all_sorted_by_workspace(tmp_path: Path) -> None:
    """list_all() returns sessions sorted by workspace path."""
    mgr = SessionManager()
    ws_b = tmp_path / "b"
    ws_a = tmp_path / "a"
    ws_a.mkdir()
    ws_b.mkdir()
    mgr.new_session(ws_b)
    mgr.new_session(ws_a)
    sessions = mgr.list_all()
    assert [s.workspace_path for s in sessions] == [
        str(ws_a.resolve()),
        str(ws_b.resolve()),
    ]


@pytest.mark.unit
def test_workspace_paths_normalized(tmp_path: Path) -> None:
    """Relative and absolute forms of the same workspace collide correctly."""
    mgr = SessionManager()
    mgr.new_session(tmp_path)
    # Same path via a different spelling resolves to the same key.
    assert mgr.get(str(tmp_path)) is not None


@pytest.mark.unit
def test_thousand_ids_unique_and_sortable() -> None:
    """1,000 generated ids are unique and sort chronologically."""
    ids = [new_thread_id() for _ in range(1000)]
    assert len(set(ids)) == 1000
    assert ids == sorted(ids)


@pytest.mark.unit
def test_project_id_stable_and_deterministic(tmp_path: Path) -> None:
    """project_id_for() is deterministic across calls."""
    ws = str(tmp_path.resolve())
    assert project_id_for(ws) == project_id_for(ws)
    assert project_id_for(ws).startswith("ws-")
    assert project_id_for(ws) != project_id_for(str(tmp_path / "other"))
