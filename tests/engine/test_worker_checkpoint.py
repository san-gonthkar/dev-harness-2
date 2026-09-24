"""Worktree state capture + restore tests (V11 8.21b).

Validation matrix (8.B, acceptance is exact) against a REAL temp git repo:

(a) the checkpoint RECORDS each worktree's HEAD + diff - the stored
    ``worktree_head``/``worktree_diff`` columns decode to the captured values;
(b) RESTORE replays them - the restored worktree matches the snapshot;
(c) a worktree with UNCOMMITTED work (a modified tracked file + an untracked
    file) is recoverable FIELD-FOR-FIELD.

``engine/worker_workspace.py`` is in the 8.C high-coverage set (95/90) and the
mutation focus set (>=80%), so every branch is exercised. No ``time.sleep``, no
network, no ``tui/`` import.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dev_harness.contracts.errors import EngineError, StorageError
from dev_harness.contracts.state import Chunk, HarnessState
from dev_harness.engine.worker_workspace import WorkerWorkspace
from dev_harness.storage.checkpoint_binding import (
    CheckpointBinding,
    deserialize_worktree_state,
    serialize_worktree_state,
)
from dev_harness.storage.sqlite_saver import Scope, SqliteSaver


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    ).stdout


def _chunk(chunk_id: str, worker_id: str | None) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        title=f"chunk {chunk_id}",
        assigned_worker_id=worker_id,
    )


class FakeWorktreeManager:
    """A git-free worktree manager creating real dirs under ``root``."""

    def __init__(self, root: Path) -> None:
        self.worktrees_root = root
        self.created: list[str] = []
        self.destroyed: list[str] = []

    def create(self, worker_id: str, *, branch: str | None = None) -> Path:
        self.created.append(worker_id)
        path = self.worktrees_root / worker_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def destroy(self, worker_id: str) -> None:
        self.destroyed.append(worker_id)


# --- unit: serialization + unbound guards -----------------------------------


@pytest.mark.unit
def test_serialize_round_trip() -> None:
    state = {"worker-1": {"head": "abc123", "diff": "diff --git a/x b/x\n+hi\n"}}
    head_col, diff_col = serialize_worktree_state(state)
    assert head_col is not None and diff_col is not None
    decoded = deserialize_worktree_state(
        {"worktree_head": head_col, "worktree_diff": diff_col}
    )
    assert decoded == state


@pytest.mark.unit
def test_serialize_empty_is_none() -> None:
    assert serialize_worktree_state(None) == (None, None)
    assert serialize_worktree_state({}) == (None, None)
    assert (
        deserialize_worktree_state({"worktree_head": None, "worktree_diff": None}) == {}
    )


@pytest.mark.unit
def test_capture_unbound_raises(tmp_path: Path) -> None:
    ws = WorkerWorkspace(tmp_path, worktrees=FakeWorktreeManager(tmp_path))
    with pytest.raises(EngineError):
        ws.capture("worker-9")


# --- integration: real git capture + restore --------------------------------


@pytest.mark.integration
def test_checkpoint_records_and_restore_replays_uncommitted_work(
    tmp_workspace: Path,
) -> None:
    ws = WorkerWorkspace(tmp_workspace)
    chunk = _chunk("c1", "worker-1")
    root = ws.bind("worker-1", chunk)

    # Uncommitted work: modify a tracked file AND add an untracked file.
    (root / "tracked.txt").write_text("tracked-v1\n", encoding="utf-8")
    _git(root, "add", "tracked.txt")
    _git(root, "commit", "-m", "add tracked")
    (root / "tracked.txt").write_text("tracked-v2-uncommitted\n", encoding="utf-8")
    (root / "new.txt").write_text("untracked work\n", encoding="utf-8")

    snapshot = ws.capture("worker-1")
    head = _git(root, "rev-parse", "HEAD").strip()
    assert snapshot["head"] == head
    assert "tracked-v2-uncommitted" in snapshot["diff"]
    assert "new.txt" in snapshot["diff"]

    # (a) the checkpoint RECORDS each worktree's HEAD + diff.
    state = HarnessState(
        project_id="p1", workspace_path=str(tmp_workspace), thread_id="t1"
    )
    binding = CheckpointBinding(tmp_workspace)
    cid = binding.put_bound(
        Scope("p1", "t1"),
        state,
        worktree_state=ws.capture_map(["worker-1"]),
    )
    saver = SqliteSaver(tmp_workspace / ".dev-harness" / "state.db")
    row = saver.get_tuple(Scope("p1", "t1"), cid)
    assert row is not None
    recorded = deserialize_worktree_state(row)
    assert recorded == {"worker-1": snapshot}
    saver.close()

    # Simulate resume: the worktree is lost back to a clean HEAD.
    _git(root, "reset", "--hard")
    _git(root, "clean", "-fd")
    assert not (root / "new.txt").exists()
    assert (root / "tracked.txt").read_text(encoding="utf-8") == "tracked-v1\n"

    # (b)/(c) restore replays the captured HEAD + uncommitted diff exactly.
    ws.restore("worker-1", recorded["worker-1"])
    assert _git(root, "rev-parse", "HEAD").strip() == snapshot["head"]
    assert (root / "tracked.txt").read_text(encoding="utf-8") == (
        "tracked-v2-uncommitted\n"
    )
    assert (root / "new.txt").read_text(encoding="utf-8") == "untracked work\n"


@pytest.mark.integration
def test_capture_map_records_each_worktree(tmp_workspace: Path) -> None:
    ws = WorkerWorkspace(tmp_workspace)
    for i in (1, 2):
        ws.bind(f"worker-{i}", _chunk(f"c{i}", f"worker-{i}"))
    for i in (1, 2):
        root = ws.root_for(_chunk(f"c{i}", f"worker-{i}"))
        (root / "out.txt").write_text(f"worker {i}\n", encoding="utf-8")

    captured = ws.capture_map(["worker-1", "worker-2"])
    assert set(captured) == {"worker-1", "worker-2"}
    assert captured["worker-1"]["head"] != ""
    assert "worker 1" in captured["worker-1"]["diff"]
    assert "worker 2" in captured["worker-2"]["diff"]


@pytest.mark.integration
def test_restore_no_diff_is_noop(tmp_workspace: Path) -> None:
    ws = WorkerWorkspace(tmp_workspace)
    root = ws.bind("worker-1", _chunk("c1", "worker-1"))
    (root / "keep.txt").write_text("keep\n", encoding="utf-8")
    ws.restore(
        "worker-1", {"head": _git(root, "rev-parse", "HEAD").strip(), "diff": ""}
    )
    assert (root / "keep.txt").read_text(encoding="utf-8") == "keep\n"


@pytest.mark.integration
def test_restore_resets_head_when_it_differs(tmp_workspace: Path) -> None:
    ws = WorkerWorkspace(tmp_workspace)
    root = ws.bind("worker-1", _chunk("c1", "worker-1"))
    (root / "f.txt").write_text("v1\n", encoding="utf-8")
    _git(root, "add", "f.txt")
    _git(root, "commit", "-m", "v1")
    snapshot = ws.capture("worker-1")
    # The worktree advances past the captured HEAD; restore must rewind it.
    (root / "f.txt").write_text("v2\n", encoding="utf-8")
    _git(root, "add", "f.txt")
    _git(root, "commit", "-m", "v2")
    assert _git(root, "rev-parse", "HEAD").strip() != snapshot["head"]

    ws.restore("worker-1", snapshot)
    assert _git(root, "rev-parse", "HEAD").strip() == snapshot["head"]
    assert (root / "f.txt").read_text(encoding="utf-8") == "v1\n"


# --- negative ---------------------------------------------------------------


@pytest.mark.negative
def test_capture_non_git_root_raises(tmp_path: Path) -> None:
    """A bound but non-git root makes the underlying git call fail."""
    ws = WorkerWorkspace(tmp_path, worktrees=FakeWorktreeManager(tmp_path))
    ws.bind("worker-1", _chunk("c1", "worker-1"))
    with pytest.raises(StorageError):
        ws.capture("worker-1")


@pytest.mark.negative
def test_restore_unbound_raises(tmp_path: Path) -> None:
    ws = WorkerWorkspace(tmp_path, worktrees=FakeWorktreeManager(tmp_path))
    with pytest.raises(EngineError):
        ws.restore("worker-9", {"head": "abc", "diff": "x"})


@pytest.mark.negative
def test_restore_inapplicable_diff_raises(tmp_workspace: Path) -> None:
    ws = WorkerWorkspace(tmp_workspace)
    root = ws.bind("worker-1", _chunk("c1", "worker-1"))
    (root / "f.txt").write_text("real\n", encoding="utf-8")
    _git(root, "add", "f.txt")
    _git(root, "commit", "-m", "add f")
    bad_diff = (
        "diff --git a/f.txt b/f.txt\n"
        "--- a/f.txt\n"
        "+++ b/f.txt\n"
        "@@ -1 +1 @@\n"
        "-does-not-match\n"
        "+patched\n"
    )
    with pytest.raises(StorageError):
        ws.restore("worker-1", {"head": "", "diff": bad_diff})
