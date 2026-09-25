"""Stale artifact reclamation tests (V11 9.2).

Acceptance is exact: (a) a dead-PID socket, lock and worktree are removed;
(b) a fresh session starts in under two seconds; (c) live-PID artifacts are
untouched. AF_UNIX is POSIX-only, so the socket path is exercised as a plain
file (reclamation only needs the path plus liveness).
"""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from dev_harness.contracts.errors import RecoveryError
from dev_harness.paths import derive_paths
from dev_harness.recovery.reclaim import reclaim, socket_pid_path
from dev_harness.vcs.worktree import WorktreeManager

_DEAD_PID = 99999999


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_worktree(ws: Path, worker_id: str, pid: int) -> Path:
    path = WorktreeManager(ws).create(worker_id)
    _write(path / ".pid", str(pid))
    return path


@pytest.fixture
def live_pid() -> Iterator[int]:
    """A real, live PID (an isolated child process).

    ``os.kill(pid, 0)`` on Windows calls ``TerminateProcess``, so the current
    process's own PID must never be probed; a child process is safe. The child
    is placed in its own process group with detached streams so console events
    cannot reach it.
    """
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
    try:
        yield proc.pid
    finally:
        proc.kill()
        proc.wait()


# --- (a) dead-PID artifacts are removed -------------------------------------


@pytest.mark.integration
def test_dead_pid_socket_lock_and_worktree_removed(tmp_workspace: Path) -> None:
    paths = derive_paths(tmp_workspace)
    _write(paths.socket_path, "socket")
    _write(socket_pid_path(paths.socket_path), str(_DEAD_PID))
    _write(paths.lock_path, f"{_DEAD_PID}:somehost")
    worktree = _make_worktree(tmp_workspace, "w1", _DEAD_PID)

    report = reclaim(tmp_workspace)

    assert not paths.socket_path.exists()
    assert not socket_pid_path(paths.socket_path).exists()
    assert not paths.lock_path.exists()
    assert not worktree.exists()
    assert report.socket_removed is True
    assert report.lock_removed is True
    assert report.worktrees_removed == ("w1",)
    assert report.reclaimed_any is True


# --- (b) a fresh session starts in under two seconds ------------------------


@pytest.mark.integration
def test_fresh_session_starts_under_two_seconds(tmp_workspace: Path) -> None:
    paths = derive_paths(tmp_workspace)
    _write(paths.socket_path, "socket")
    _write(socket_pid_path(paths.socket_path), str(_DEAD_PID))
    _write(paths.lock_path, f"{_DEAD_PID}:somehost")
    _make_worktree(tmp_workspace, "w1", _DEAD_PID)

    start = time.monotonic()
    report = reclaim(tmp_workspace)
    elapsed = time.monotonic() - start

    assert elapsed < 2.0
    assert report.elapsed_seconds < 2.0
    assert report.reclaimed_any is True


# --- (c) live-PID artifacts are untouched -----------------------------------


@pytest.mark.integration
def test_live_pid_artifacts_untouched(tmp_workspace: Path, live_pid: int) -> None:
    paths = derive_paths(tmp_workspace)
    _write(paths.socket_path, "socket")
    _write(socket_pid_path(paths.socket_path), str(live_pid))
    _write(paths.lock_path, f"{live_pid}:somehost")
    worktree = _make_worktree(tmp_workspace, "w2", live_pid)

    report = reclaim(tmp_workspace)

    assert paths.socket_path.exists()
    assert paths.lock_path.exists()
    assert worktree.exists()
    assert report.socket_removed is False
    assert report.lock_removed is False
    assert report.worktrees_removed == ()
    assert report.reclaimed_any is False
    assert set(report.live_artifacts) == {
        str(paths.socket_path),
        str(paths.lock_path),
        str(worktree),
    }


# --- unit: liveness edge cases ----------------------------------------------


@pytest.mark.unit
def test_clean_workspace_reclaims_nothing(tmp_workspace: Path) -> None:
    report = reclaim(tmp_workspace)

    assert report.reclaimed_any is False
    assert report.socket_removed is False
    assert report.lock_removed is False
    assert report.worktrees_removed == ()
    assert report.live_artifacts == ()


@pytest.mark.unit
def test_missing_pid_sidecar_is_stale(tmp_workspace: Path) -> None:
    paths = derive_paths(tmp_workspace)
    _write(paths.socket_path, "socket")  # no sidecar
    _write(paths.lock_path, "")  # empty owner record

    report = reclaim(tmp_workspace)

    assert not paths.socket_path.exists()
    assert not paths.lock_path.exists()
    assert report.socket_removed is True
    assert report.lock_removed is True


@pytest.mark.unit
def test_unparseable_pid_is_stale(tmp_workspace: Path) -> None:
    paths = derive_paths(tmp_workspace)
    _write(paths.socket_path, "socket")
    _write(socket_pid_path(paths.socket_path), "not-a-pid")
    _write(paths.lock_path, "garbage")

    report = reclaim(tmp_workspace)

    assert not paths.socket_path.exists()
    assert not paths.lock_path.exists()
    assert report.socket_removed is True
    assert report.lock_removed is True


@pytest.mark.unit
def test_socket_pid_path_is_sidecar(tmp_path: Path) -> None:
    sock = tmp_path / "harness.sock"
    assert socket_pid_path(sock) == Path(f"{sock}.pid")


# --- negative: failure handling ---------------------------------------------


@pytest.mark.negative
def test_reclaim_raises_recovery_error_on_oserror(
    tmp_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(_workspace: object) -> object:
        raise OSError("cannot inspect workspace")

    monkeypatch.setattr("dev_harness.recovery.reclaim.derive_paths", _boom)

    with pytest.raises(RecoveryError) as excinfo:
        reclaim(tmp_workspace)

    assert excinfo.value.remediation


@pytest.mark.negative
def test_unregistered_worktree_dir_is_removed(tmp_workspace: Path) -> None:
    manager = WorktreeManager(tmp_workspace)
    orphan = manager.worktrees_root / "orphan"
    _write(orphan / ".pid", str(_DEAD_PID))

    report = reclaim(tmp_workspace)

    assert not orphan.exists()
    assert report.worktrees_removed == ("orphan",)
