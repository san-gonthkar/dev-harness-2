"""Escalating process-group interrupt (V11 6.5).

An interrupt must reliably kill a hostile process tree - the runner and every
grandchild it spawned. The escalation is:

1. ``killpg(pgid, SIGINT)`` - ask the group to stop cooperatively.
2. Wait up to ``grace`` seconds (default 3.0) for the group to exit.
3. If it is still alive, ``killpg(pgid, SIGKILL)`` - no cooperation needed.
4. Reap the group leader with ``waitpid(..., WNOHANG)`` until no child
   remains, so no zombie is left behind.

POSIX-only: ``os.killpg``, ``signal.SIGKILL`` and ``os.WNOHANG`` do not exist
on Windows. The module imports everywhere; the syscalls are injected
(constructor parameters with real defaults) so every escalation branch is
testable on Windows with fakes. The live path is guarded by :func:`is_posix`.
"""

from __future__ import annotations

import os
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from dev_harness.contracts.errors import UnsupportedPlatformError
from dev_harness.core.process_group import ProcessGroupManager, is_posix

KillpgFn = Callable[[int, int], None]
WaitpidFn = Callable[[int, int], tuple[int, int]]
AliveFn = Callable[[int], bool]

_DEFAULT_GRACE = 3.0
_DEFAULT_POLL_INTERVAL = 0.05
_KILLPG_ATTR = "killpg"


def wnohang() -> int:
    """The platform's ``WNOHANG`` flag (1 where the constant is absent)."""
    return cast(int, getattr(os, "WNOHANG", 1))


def sigkill() -> int:
    """The platform's ``SIGKILL`` number (9 where the constant is absent)."""
    return cast(int, getattr(signal, "SIGKILL", 9))


@dataclass(frozen=True, slots=True)
class InterruptResult:
    """Outcome of one escalating interrupt.

    ``already_dead`` is True when the group was gone before SIGINT was sent.
    ``reap_failed`` is True when the reap loop found no child to reap at all
    (``ChildProcessError`` on the first ``waitpid``).
    """

    pgid: int
    sigint_sent: bool
    sigkill_sent: bool
    already_dead: bool
    reaped: int
    reap_failed: bool


class EscalatingInterrupt:
    """Escalates SIGINT -> grace -> SIGKILL over a process group, then reaps.

    The grace window, clock, sleep and the syscall surface (``killpg``,
    ``waitpid``, and the signal-0 liveness probe) are all injectable so every
    branch is reachable with fakes on any platform.
    """

    def __init__(
        self,
        manager: ProcessGroupManager,
        *,
        grace: float = _DEFAULT_GRACE,
        poll_interval: float = _DEFAULT_POLL_INTERVAL,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
        killpg: KillpgFn | None = None,
        waitpid: WaitpidFn | None = None,
        alive: AliveFn | None = None,
    ) -> None:
        self._manager = manager
        self._grace = grace
        self._poll_interval = poll_interval
        self._clock = clock if clock is not None else time.monotonic
        self._sleep = sleep if sleep is not None else time.sleep
        self._killpg = killpg
        self._waitpid = waitpid
        self._alive = alive

    @property
    def grace(self) -> float:
        """The cooperative grace window in seconds."""
        return self._grace

    def interrupt(self, pgid: int) -> InterruptResult:
        """Escalate over ``pgid`` and reap it; never raises on a dead group."""
        if not self._signal(pgid, signal.SIGINT):
            reaped, reap_failed = self._reap(pgid)
            self._manager.forget(pgid)
            return InterruptResult(
                pgid=pgid,
                sigint_sent=False,
                sigkill_sent=False,
                already_dead=True,
                reaped=reaped,
                reap_failed=reap_failed,
            )
        if self._wait_for_exit(pgid, self._grace):
            reaped, reap_failed = self._reap(pgid)
            self._manager.forget(pgid)
            return InterruptResult(
                pgid=pgid,
                sigint_sent=True,
                sigkill_sent=False,
                already_dead=False,
                reaped=reaped,
                reap_failed=reap_failed,
            )
        self._signal(pgid, sigkill())
        reaped, reap_failed = self._reap(pgid)
        self._manager.forget(pgid)
        return InterruptResult(
            pgid=pgid,
            sigint_sent=True,
            sigkill_sent=True,
            already_dead=False,
            reaped=reaped,
            reap_failed=reap_failed,
        )

    def interrupt_all(self) -> list[InterruptResult]:
        """Interrupt every tracked group, then clear the registry."""
        results = [self.interrupt(pgid) for pgid in sorted(self._manager.pgids)]
        self._manager.forget_all()
        return results

    def _signal(self, pgid: int, sig: int) -> bool:
        """Send ``sig`` to ``pgid``; False when the group is already gone."""
        try:
            self._resolve_killpg()(pgid, sig)
        except ProcessLookupError:
            return False
        return True

    def _probe_alive(self, pgid: int) -> bool:
        """True while ``pgid`` still exists (signal-0 probe)."""
        if self._alive is not None:
            return self._alive(pgid)
        try:
            self._resolve_killpg()(pgid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _wait_for_exit(self, pgid: int, grace: float) -> bool:
        """True if the group exited within ``grace`` seconds."""
        deadline = self._clock() + grace
        while True:
            if not self._probe_alive(pgid):
                return True
            if self._clock() >= deadline:
                return False
            self._sleep(self._poll_interval)

    def _reap(self, pgid: int) -> tuple[int, bool]:
        """Reap ``pgid``'s leader with WNOHANG until no child remains.

        Returns ``(reaped_count, reap_failed)``. ``reap_failed`` is True only
        when the first ``waitpid`` found no child at all.
        """
        waitpid = self._resolve_waitpid()
        reaped = 0
        while True:
            try:
                pid, _status = waitpid(pgid, wnohang())
            except ChildProcessError:
                return reaped, reaped == 0
            if pid == 0:
                return reaped, False
            reaped += 1

    def _resolve_killpg(self) -> KillpgFn:
        """The injected ``killpg``, or ``os.killpg`` on POSIX."""
        if self._killpg is not None:
            return self._killpg
        if not is_posix():
            raise UnsupportedPlatformError(
                "process-group signalling requires a POSIX platform"
            )
        return cast(KillpgFn, getattr(os, _KILLPG_ATTR))

    def _resolve_waitpid(self) -> WaitpidFn:
        """The injected ``waitpid``, or ``os.waitpid`` on POSIX."""
        if self._waitpid is not None:
            return self._waitpid
        if not is_posix():
            raise UnsupportedPlatformError("child reaping requires a POSIX platform")
        return os.waitpid
