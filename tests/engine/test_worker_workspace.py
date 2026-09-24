"""Per-worker worktree binding tests (V11 8.8).

Validation matrix (8.B, acceptance is exact) against a REAL temp git repo:

(a) three workers each write the SAME relative path (``src/app.py``) in their
    own worktree -> three DISTINCT file contents;
(b) the PRIMARY worktree's ``git status --porcelain`` is EMPTY throughout.

The binding is also exercised without git (a fake manager) so the root
resolution and escape guards are unit-testable in isolation. No ``time.sleep``,
no network. ``engine/worker_workspace.py`` is in the 8.C high-coverage set
(95/90) and the mutation focus set (>=80%, 0 survivors in the worktree
binding), so every branch is covered.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dev_harness.contracts.errors import EngineError, WorkspaceEscapeError
from dev_harness.contracts.state import Chunk
from dev_harness.engine.worker_workspace import WorkerWorkspace, chunk_branch


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _porcelain(cwd: Path) -> str:
    return _git(cwd, "status", "--porcelain")


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
        self.created: list[tuple[str, str | None]] = []
        self.destroyed: list[str] = []

    def create(self, worker_id: str, *, branch: str | None = None) -> Path:
        self.created.append((worker_id, branch))
        path = self.worktrees_root / worker_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def destroy(self, worker_id: str) -> None:
        self.destroyed.append(worker_id)


# --- acceptance: real git isolation -----------------------------------------


@pytest.mark.integration
def test_three_workers_same_path_distinct_contents(tmp_workspace: Path) -> None:
    """(a): 3 workers writing src/app.py produce 3 distinct contents."""
    ws = WorkerWorkspace(tmp_workspace)
    chunks = [_chunk(f"c{i}", f"worker-{i}") for i in range(3)]
    roots = [ws.bind(f"worker-{i}", chunks[i]) for i in range(3)]
    assert len(set(roots)) == 3

    contents: list[str] = []
    for i, chunk in enumerate(chunks):
        path = ws.write_path(chunk, "src/app.py")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"content-{i}", encoding="utf-8")
        contents.append(path.read_text(encoding="utf-8"))

    assert contents == ["content-0", "content-1", "content-2"]
    assert len(set(contents)) == 3
    # Each worker's file lives only in its own worktree.
    for i, root in enumerate(roots):
        assert (root / "src" / "app.py").read_text(encoding="utf-8") == f"content-{i}"


@pytest.mark.integration
def test_primary_status_empty_throughout(tmp_workspace: Path) -> None:
    """(b): the primary worktree's porcelain stays empty throughout."""
    ws = WorkerWorkspace(tmp_workspace)
    assert _porcelain(tmp_workspace) == ""
    for i in range(3):
        chunk = _chunk(f"c{i}", f"worker-{i}")
        ws.bind(f"worker-{i}", chunk)
        assert _porcelain(tmp_workspace) == ""
        path = ws.write_path(chunk, "src/app.py")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"content-{i}", encoding="utf-8")
        assert _porcelain(tmp_workspace) == ""
    # No worker write leaked into the primary tree.
    assert (tmp_workspace / "src" / "app.py").exists() is False


@pytest.mark.integration
def test_bind_uses_chunk_branch(tmp_workspace: Path) -> None:
    """Each worker's worktree is bound to branch ``chunk/{chunk_id}``."""
    ws = WorkerWorkspace(tmp_workspace)
    chunk = _chunk("c7", "worker-1")
    root = ws.bind("worker-1", chunk)
    assert _git(root, "rev-parse", "--abbrev-ref", "HEAD") == "chunk/c7"


@pytest.mark.integration
def test_release_destroys_worktree(tmp_workspace: Path) -> None:
    """``release`` removes the worktree and forgets the binding."""
    ws = WorkerWorkspace(tmp_workspace)
    chunk = _chunk("c1", "worker-1")
    root = ws.bind("worker-1", chunk)
    assert root.exists()
    ws.release("worker-1")
    assert root.exists() is False
    with pytest.raises(EngineError):
        ws.root_for(chunk)


# --- unit: binding logic without git ----------------------------------------


@pytest.mark.unit
def test_bind_is_idempotent(tmp_path: Path) -> None:
    """A second bind returns the same root without re-creating the worktree."""
    fake = FakeWorktreeManager(tmp_path / "worktrees")
    ws = WorkerWorkspace(tmp_path, worktrees=fake)  # type: ignore[arg-type]
    chunk = _chunk("c1", "worker-1")
    first = ws.bind("worker-1", chunk)
    second = ws.bind("worker-1", chunk)
    assert first == second
    assert fake.created == [("worker-1", "chunk/c1")]


@pytest.mark.unit
def test_chunk_branch_format() -> None:
    assert chunk_branch("abc") == "chunk/abc"


@pytest.mark.unit
def test_write_path_inside_root(tmp_path: Path) -> None:
    fake = FakeWorktreeManager(tmp_path / "worktrees")
    ws = WorkerWorkspace(tmp_path, worktrees=fake)  # type: ignore[arg-type]
    chunk = _chunk("c1", "worker-1")
    root = ws.bind("worker-1", chunk)
    assert ws.write_path(chunk, "src/app.py") == (root / "src" / "app.py").resolve()


# --- negative: escape and unbound guards ------------------------------------


@pytest.mark.negative
def test_root_for_unassigned_worker_raises(tmp_path: Path) -> None:
    fake = FakeWorktreeManager(tmp_path / "worktrees")
    ws = WorkerWorkspace(tmp_path, worktrees=fake)  # type: ignore[arg-type]
    with pytest.raises(EngineError, match="no assigned worker"):
        ws.root_for(_chunk("c1", None))


@pytest.mark.negative
def test_root_for_unbound_worker_raises(tmp_path: Path) -> None:
    fake = FakeWorktreeManager(tmp_path / "worktrees")
    ws = WorkerWorkspace(tmp_path, worktrees=fake)  # type: ignore[arg-type]
    with pytest.raises(EngineError, match="not bound"):
        ws.root_for(_chunk("c1", "worker-9"))


@pytest.mark.negative
def test_write_path_rejects_absolute(tmp_path: Path) -> None:
    fake = FakeWorktreeManager(tmp_path / "worktrees")
    ws = WorkerWorkspace(tmp_path, worktrees=fake)  # type: ignore[arg-type]
    chunk = _chunk("c1", "worker-1")
    ws.bind("worker-1", chunk)
    with pytest.raises(WorkspaceEscapeError):
        ws.write_path(chunk, str(tmp_path / "outside.py"))


@pytest.mark.negative
def test_write_path_rejects_escape(tmp_path: Path) -> None:
    fake = FakeWorktreeManager(tmp_path / "worktrees")
    ws = WorkerWorkspace(tmp_path, worktrees=fake)  # type: ignore[arg-type]
    chunk = _chunk("c1", "worker-1")
    ws.bind("worker-1", chunk)
    with pytest.raises(WorkspaceEscapeError):
        ws.write_path(chunk, "../escape.py")
