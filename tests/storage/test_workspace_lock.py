"""Workspace lock tests (V11 1.8)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from dev_harness.contracts.errors import LockTimeout
from dev_harness.storage.workspace_lock import WorkspaceLock, _pid_alive

pytestmark = pytest.mark.timing


def test_lock_acquire_release(tmp_path: Path) -> None:
    lock = WorkspaceLock(tmp_path / "ws.lock", timeout=2.0)
    lock.acquire()
    assert lock.lock_path.exists()
    lock.release()
    assert not lock.lock_path.exists()


def test_lock_contention_times_out(tmp_path: Path) -> None:
    lock1 = WorkspaceLock(tmp_path / "ws.lock", timeout=10.0)
    lock1.acquire()
    start = time.monotonic()
    lock2 = WorkspaceLock(tmp_path / "ws.lock", timeout=0.5)
    with pytest.raises(LockTimeout):
        lock2.acquire()
    elapsed = time.monotonic() - start
    assert 0.4 <= elapsed <= 1.5
    lock1.release()


def test_dead_pid_lock_reclaimed(tmp_path: Path) -> None:
    """A lock whose recorded PID is dead is reclaimed in one attempt."""
    lock_path = tmp_path / "ws.lock"
    # Record a dead PID (999999 is very unlikely to exist).
    lock_path.write_text("999999:somehost", encoding="utf-8")
    lock = WorkspaceLock(lock_path, timeout=1.0)
    lock.acquire()
    assert lock.lock_path.exists()
    lock.release()


def test_pid_alive() -> None:
    assert _pid_alive(os.getpid()) is True
    assert _pid_alive(99999999) is False
