"""Subprocess group manager (V11 6.4).

Every runner subprocess is spawned with ``start_new_session=True`` so it
becomes the leader of its own process group (PGID == its PID). The manager
keeps a registry of those PGIDs so an interrupt (6.5) can signal the whole
tree — the runner and every grandchild — with one ``killpg`` call.

POSIX-only: ``start_new_session`` and ``os.killpg`` do not exist on Windows.
The module imports everywhere; the POSIX paths are guarded by
:func:`is_posix` and are testable on Windows via fakes.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from typing import IO, Any

from dev_harness.contracts.errors import HarnessError


class ProcessGroupError(HarnessError):
    """A subprocess group could not be created or tracked."""

    remediation = "Check the runner command and process permissions, then retry."


def is_posix() -> bool:
    """True when the platform supports process groups (``os.killpg``)."""
    return os.name == "posix" and sys.platform != "win32" and hasattr(os, "killpg")


class ProcessGroupManager:
    """Spawns runners in their own session and tracks their PGIDs.

    ``spawn`` starts ``argv`` with ``start_new_session=True`` and records the
    child's PGID (its PID, as the session leader). ``pgids`` exposes the
    registry; ``forget`` drops a PGID once its group is gone.
    """

    def __init__(self) -> None:
        self._pgids: set[int] = set()

    @property
    def pgids(self) -> frozenset[int]:
        """The PGIDs of live runner groups."""
        return frozenset(self._pgids)

    def spawn(
        self,
        argv: list[str],
        *,
        cwd: str | None = None,
        stdout: int | IO[Any] | None = None,
        stderr: int | IO[Any] | None = None,
    ) -> subprocess.Popen[bytes]:
        """Start ``argv`` as a new session leader and register its PGID.

        Returns the :class:`subprocess.Popen` handle. On non-POSIX platforms
        the child is spawned without a new session and no PGID is registered
        (the caller must not rely on group signalling there).
        """
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            stdout=stdout,
            stderr=stderr,
            start_new_session=is_posix(),
        )
        if is_posix():
            self._pgids.add(proc.pid)
        return proc

    def pgid_of(self, proc: subprocess.Popen[bytes]) -> int | None:
        """The PGID of ``proc``, or None when the group is not tracked.

        The PGID equals the child PID because the child is a session leader.
        """
        if proc.pid in self._pgids:
            return proc.pid
        return None

    def forget(self, pgid: int) -> None:
        """Drop ``pgid`` from the registry (no-op if absent)."""
        self._pgids.discard(pgid)

    def forget_all(self) -> None:
        """Drop every tracked PGID."""
        self._pgids.clear()

    def __len__(self) -> int:
        """Number of tracked runner groups."""
        return len(self._pgids)

    def __iter__(self) -> Iterator[int]:
        """Iterate the tracked PGIDs."""
        return iter(self._pgids)
