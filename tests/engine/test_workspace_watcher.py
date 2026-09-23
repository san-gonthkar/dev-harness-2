"""Workspace watcher tests (V11 5.11).

Validation matrix: ``touch`` of a tracked file -> ``FILE_CHANGE`` within 1s;
branch switch -> ``GIT_STATUS_UPDATE`` with the correct branch/dirty count.
The git layer is faked for the unit tests so branch/dirty transitions are
scripted deterministically; an integration test exercises the real git
adapter against a git-backed workspace.
"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import pytest

from dev_harness.contracts.enums import EventType
from dev_harness.contracts.events import Envelope, FileChangePayload
from dev_harness.engine.workspace_watcher import WorkspaceWatcher
from dev_harness.vcs.git import GitAdapter


class FakeGit:
    """A scripted git adapter whose branch/dirty count the test controls."""

    def __init__(self, branch: str = "main", dirty: int = 0) -> None:
        self.branch = branch
        self.dirty = dirty

    def active_branch(self) -> str:
        return self.branch

    def uncommitted_count(self) -> int:
        return self.dirty


def _watcher(
    ws: Path, *, git: FakeGit | None = None, sink: list[Envelope] | None = None
) -> WorkspaceWatcher:
    events = sink if sink is not None else []
    return WorkspaceWatcher(
        ws,
        git=git or FakeGit(),  # type: ignore[arg-type]
        publish=events.append,
    )


@pytest.mark.unit
def test_first_scan_primes_and_emits_nothing(tmp_path: Path) -> None:
    """The priming scan records the baseline without emitting."""
    (tmp_path / "a.py").write_text("x = 1", encoding="utf-8")
    sink: list[Envelope] = []
    watcher = _watcher(tmp_path, sink=sink)
    assert watcher.scan_once() == []
    assert sink == []
    assert watcher.branch == "main"


@pytest.mark.unit
def test_touch_tracked_file_emits_file_change(tmp_path: Path) -> None:
    """Modifying a tracked file emits a FILE_CHANGE(modified) on next scan."""
    tracked = tmp_path / "a.py"
    tracked.write_text("x = 1", encoding="utf-8")
    watcher = _watcher(tmp_path)
    watcher.scan_once()  # prime

    tracked.write_text("x = 2", encoding="utf-8")
    events = watcher.scan_once()

    assert len(events) == 1
    env = events[0]
    assert env.type == EventType.FILE_CHANGE
    payload = env.payload
    assert isinstance(payload, FileChangePayload)
    assert payload.path == "a.py"
    assert payload.change_type == "modified"


@pytest.mark.unit
def test_new_and_deleted_files_emit_file_change(tmp_path: Path) -> None:
    """Creating then deleting a file emits created then deleted envelopes."""
    watcher = _watcher(tmp_path)
    watcher.scan_once()  # prime empty

    created = tmp_path / "new.py"
    created.write_text("y = 1", encoding="utf-8")
    created_events = watcher.scan_once()
    assert [p.change_type for p in (e.payload for e in created_events)] == ["created"]  # type: ignore[union-attr]

    created.unlink()
    deleted_events = watcher.scan_once()
    assert [p.change_type for p in (e.payload for e in deleted_events)] == ["deleted"]  # type: ignore[union-attr]


@pytest.mark.unit
def test_branch_switch_emits_git_status_update(tmp_path: Path) -> None:
    """A branch switch emits GIT_STATUS_UPDATE with the new branch/dirty count."""
    git = FakeGit(branch="main", dirty=0)
    watcher = _watcher(tmp_path, git=git)
    watcher.scan_once()  # prime on main

    git.branch = "feature/x"
    git.dirty = 3
    events = watcher.scan_once()

    assert len(events) == 1
    env = events[0]
    assert env.type == EventType.GIT_STATUS_UPDATE
    assert env.payload.branch == "feature/x"  # type: ignore[union-attr]
    assert env.payload.dirty_count == 3  # type: ignore[union-attr]


@pytest.mark.unit
def test_dirty_count_change_emits_git_status_update(tmp_path: Path) -> None:
    """A dirty-count change alone emits GIT_STATUS_UPDATE."""
    git = FakeGit(branch="main", dirty=0)
    watcher = _watcher(tmp_path, git=git)
    watcher.scan_once()

    git.dirty = 2
    events = watcher.scan_once()
    assert [e.type for e in events] == [EventType.GIT_STATUS_UPDATE]
    assert events[0].payload.dirty_count == 2  # type: ignore[union-attr]


@pytest.mark.unit
def test_no_change_emits_nothing(tmp_path: Path) -> None:
    """A scan with no filesystem or git change emits nothing."""
    (tmp_path / "a.py").write_text("x = 1", encoding="utf-8")
    watcher = _watcher(tmp_path)
    watcher.scan_once()
    assert watcher.scan_once() == []


@pytest.mark.unit
def test_git_internal_dirs_are_ignored(tmp_path: Path) -> None:
    """Files under .git/.dev-harness/__pycache__ are never watched."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")
    (tmp_path / "keep.py").write_text("z = 0", encoding="utf-8")
    watcher = _watcher(tmp_path)
    watcher.scan_once()

    (git_dir / "HEAD").write_text("ref: refs/heads/other", encoding="utf-8")
    assert watcher.scan_once() == []


@pytest.mark.unit
def test_seqs_increase_across_scans(tmp_path: Path) -> None:
    """Emitted envelopes carry strictly increasing sequence numbers."""
    watcher = _watcher(tmp_path)
    watcher.scan_once()
    (tmp_path / "a.py").write_text("1", encoding="utf-8")
    first = watcher.scan_once()
    (tmp_path / "b.py").write_text("2", encoding="utf-8")
    second = watcher.scan_once()
    assert first[0].seq == 0
    assert all(e.seq > first[0].seq for e in second)


@pytest.mark.integration
def test_background_watcher_detects_touch_within_one_second(tmp_path: Path) -> None:
    """A tracked-file touch is observed within 1s by the background poller."""
    tracked = tmp_path / "a.py"
    tracked.write_text("x = 1", encoding="utf-8")
    seen = threading.Event()
    events: list[Envelope] = []

    def sink(envelope: Envelope) -> None:
        events.append(envelope)
        if envelope.type == EventType.FILE_CHANGE:
            seen.set()

    watcher = WorkspaceWatcher(tmp_path, git=FakeGit(), publish=sink)  # type: ignore[arg-type]
    watcher.scan_once()  # prime before the poller starts
    watcher.start()
    try:
        tracked.write_text("x = 2", encoding="utf-8")
        assert seen.wait(timeout=1.0), "FILE_CHANGE not observed within 1s"
    finally:
        watcher.stop()
    assert any(e.type == EventType.FILE_CHANGE for e in events)


@pytest.mark.integration
def test_real_git_branch_switch_reports_correct_state(tmp_workspace: Path) -> None:
    """Against real git: branch switch yields the branch and dirty count."""
    git = GitAdapter(tmp_workspace)
    watcher = WorkspaceWatcher(tmp_workspace, git=git)
    watcher.scan_once()  # prime on main

    subprocess.run(
        ["git", "-C", str(tmp_workspace), "checkout", "-b", "feature/y"],
        check=True,
        capture_output=True,
        text=True,
    )
    (tmp_workspace / "dirty.txt").write_text("d", encoding="utf-8")

    events = watcher.scan_once()
    git_events = [e for e in events if e.type == EventType.GIT_STATUS_UPDATE]
    assert len(git_events) == 1
    assert git_events[0].payload.branch == "feature/y"  # type: ignore[union-attr]
    assert git_events[0].payload.dirty_count == 1  # type: ignore[union-attr]
