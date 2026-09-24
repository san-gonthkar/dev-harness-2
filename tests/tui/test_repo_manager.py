"""Repo-manager panel tests (V11 task 7.2).

Unit tests drive the handlers directly; the integration test mounts the panel
under a real ``HermesApp``, feeds a ``GIT_STATUS_UPDATE`` through a ``Bridge``
backed by a ``BackpressureQueue``, and asserts the displayed cells. No
``time.sleep`` — the update is asserted after a single ``pilot.pause``.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from textual.widgets import DataTable, DirectoryTree

from dev_harness.contracts.enums import EventType
from dev_harness.contracts.events import (
    Envelope,
    FileChangePayload,
    GitStatusUpdatePayload,
)
from dev_harness.ipc.queue import BackpressureQueue
from dev_harness.tui.app import REPO_MANAGER_ID, HermesApp
from dev_harness.tui.bridge import Bridge
from dev_harness.tui.panels.repo_manager import (
    GIT_STATUS_ID,
    REPO_TREE_ID,
    RepoManager,
)

SIZE = (100, 30)
#: Bounded wait for the reader thread to drain the queue.
DRAIN_DEADLINE_S = 30.0


def git_env(seq: int, branch: str, dirty: int) -> Envelope:
    return Envelope(
        type=EventType.GIT_STATUS_UPDATE,
        seq=seq,
        payload=GitStatusUpdatePayload(
            type="GIT_STATUS_UPDATE", branch=branch, dirty_count=dirty
        ),
    )


def file_env(seq: int, path: str, change_type: str = "modified") -> Envelope:
    return Envelope(
        type=EventType.FILE_CHANGE,
        seq=seq,
        payload=FileChangePayload(type="FILE_CHANGE", path=path, change_type=change_type),
    )


# --- unit -------------------------------------------------------------------


@pytest.mark.unit
def test_on_git_status_sets_branch_and_dirty_count() -> None:
    """``on_git_status`` records the branch and dirty count."""
    panel = RepoManager()
    panel.on_git_status(
        GitStatusUpdatePayload(type="GIT_STATUS_UPDATE", branch="feat/x", dirty_count=3)
    )
    assert panel.branch == "feat/x"
    assert panel.dirty_count == 3


@pytest.mark.unit
def test_on_file_change_records_path() -> None:
    """``on_file_change`` records the path with its latest change type."""
    panel = RepoManager()
    panel.on_file_change(
        FileChangePayload(type="FILE_CHANGE", path="src/a.py", change_type="created")
    )
    assert panel.changed_paths == {"src/a.py": "created"}


@pytest.mark.unit
def test_repeated_file_change_is_last_write_wins() -> None:
    """A repeated path updates in place rather than adding a second entry."""
    panel = RepoManager()
    panel.on_file_change(
        FileChangePayload(type="FILE_CHANGE", path="src/a.py", change_type="created")
    )
    panel.on_file_change(
        FileChangePayload(type="FILE_CHANGE", path="src/a.py", change_type="modified")
    )
    assert panel.changed_paths == {"src/a.py": "modified"}


# --- negative ---------------------------------------------------------------


@pytest.mark.negative
def test_zero_dirty_count_is_recorded_not_blank() -> None:
    """``dirty_count == 0`` is a real value, not a blank/absent cell."""
    panel = RepoManager()
    panel.on_git_status(
        GitStatusUpdatePayload(type="GIT_STATUS_UPDATE", branch="main", dirty_count=0)
    )
    assert panel.dirty_count == 0
    assert panel.branch == "main"


@pytest.mark.negative
def test_file_change_outside_tree_does_not_raise() -> None:
    """A path outside the tree root is recorded without raising."""
    panel = RepoManager(path=".")
    panel.on_file_change(
        FileChangePayload(
            type="FILE_CHANGE", path="../outside/elsewhere.py", change_type="deleted"
        )
    )
    assert panel.changed_paths == {"../outside/elsewhere.py": "deleted"}


# --- integration ------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_git_status_update_renders_cells(tmp_path: Path) -> None:
    """A bridged ``GIT_STATUS_UPDATE`` lands in the displayed cells after one pause."""
    workspace = str(tmp_path)
    app = HermesApp(workspace=workspace)
    queue = BackpressureQueue()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        panel = app.query_one(REPO_MANAGER_ID, RepoManager)
        assert app.query_one(REPO_TREE_ID, DirectoryTree) is not None
        assert app.query_one(GIT_STATUS_ID, DataTable) is not None

        bridge = Bridge(app, queue=queue)
        panel.bind(bridge)
        bridge.start()

        queue.put(git_env(1, "feat/x", 3))

        deadline = time.monotonic() + DRAIN_DEADLINE_S
        while bridge.applied < 1 and time.monotonic() < deadline:
            await pilot.pause()

        bridge.stop(timeout=2.0)

        assert bridge.applied == 1
        assert panel.branch == "feat/x"
        assert panel.dirty_count == 3
        assert panel.displayed_branch == "feat/x"
        assert panel.displayed_dirty_count == "3"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_repeated_updates_do_not_duplicate_rows(tmp_path: Path) -> None:
    """Repeated ``GIT_STATUS_UPDATE``/``FILE_CHANGE`` keep a fixed row set."""
    workspace = str(tmp_path)
    app = HermesApp(workspace=workspace)
    queue = BackpressureQueue()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        panel = app.query_one(REPO_MANAGER_ID, RepoManager)
        bridge = Bridge(app, queue=queue)
        panel.bind(bridge)
        bridge.start()

        for i in range(5):
            queue.put(git_env(i, "feat/x", i))
            queue.put(file_env(100 + i, "src/a.py", "modified"))

        deadline = time.monotonic() + DRAIN_DEADLINE_S
        while bridge.applied < 10 and time.monotonic() < deadline:
            await pilot.pause()

        bridge.stop(timeout=2.0)

        assert bridge.applied == 10
        # branch + dirty + one distinct changed path == 3 rows, no duplicates.
        assert panel.row_count == 3
        assert panel.displayed_dirty_count == "4"
