"""Workspace lock via portalocker with stale detection (V11 1.8).

10s timeout; PID+hostname recorded for stale detection. A dead-PID lock is
reclaimed in one attempt.
"""

from __future__ import annotations

import os
import socket
import time
from pathlib import Path

import portalocker

from dev_harness.contracts.errors import LockTimeout


class WorkspaceLock:
    """A portalocker-based exclusive lock on a workspace."""

    def __init__(self, lock_path: str | Path, *, timeout: float = 10.0) -> None:
        self.lock_path = Path(lock_path)
        self.timeout = timeout
        self._fh = None

    def _write_owner(self) -> None:
        owner = f"{os.getpid()}:{socket.gethostname()}"
        self.lock_path.write_text(owner, encoding="utf-8")

    def acquire(self) -> None:
        """Acquire the lock, waiting up to ``timeout`` seconds."""
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self._fh = open(self.lock_path, "a+", encoding="utf-8")
                portalocker.lock(self._fh, portalocker.LOCK_EX | portalocker.LOCK_NB)
                self._write_owner()
                return
            except portalocker.exceptions.LockException:
                if self._fh is not None:
                    self._fh.close()
                    self._fh = None
                # Stale detection: if the recorded PID is dead, reclaim.
                if self._reclaim_stale():
                    continue
                if time.monotonic() >= deadline:
                    raise LockTimeout(
                        f"could not acquire lock {self.lock_path} within {self.timeout}s",
                        remediation="Another process holds the workspace lock; wait for it to exit or reclaim a stale lock (dead PID).",
                    )
                time.sleep(0.05)

    def _reclaim_stale(self) -> bool:
        """Reclaim the lock if the recorded owner PID is dead."""
        try:
            content = self.lock_path.read_text(encoding="utf-8").strip()
            pid_str = content.split(":")[0]
            pid = int(pid_str)
            if not _pid_alive(pid):
                self.lock_path.unlink(missing_ok=True)
                return True
        except (ValueError, OSError):
            return False
        return False

    def release(self) -> None:
        if self._fh is not None:
            try:
                portalocker.unlock(self._fh)
            finally:
                self._fh.close()
                self._fh = None
        self.lock_path.unlink(missing_ok=True)

    def __enter__(self) -> WorkspaceLock:
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()


def _pid_alive(pid: int) -> bool:
    """True if a process with the given PID is alive."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
