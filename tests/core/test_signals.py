"""Escalating process-group interrupt tests (V11 6.5).

Validation matrix: a runner with 3 grandchildren that ignore SIGINT is gone
(``pgrep -g {pgid}`` empty) within 4.0s and leaves 0 zombies. The live POSIX
path is exercised by the ``skipif`` test; every escalation branch is covered
on any platform via injected fakes:

1. grace expiry - SIGINT ignored, grace elapses -> SIGKILL sent
2. early exit   - group dies during grace -> SIGKILL NOT sent
3. already-dead - ``killpg`` raises ``ProcessLookupError`` -> success, no crash
4. reap failure - ``waitpid`` raises ``ChildProcessError`` -> reported
"""

from __future__ import annotations

import os
import signal
import sys
import time

import pytest

from dev_harness.contracts.errors import UnsupportedPlatformError
from dev_harness.core.process_group import ProcessGroupManager, is_posix
from dev_harness.core.signals import (
    EscalatingInterrupt,
    InterruptResult,
    sigkill,
    wnohang,
)

pytestmark = pytest.mark.unit


class _FakeClock:
    """A monotonic clock that advances only when the fake sleep is called."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self._now = start

    def monotonic(self) -> float:
        return self._now

    def sleep(self, seconds: float) -> None:
        self._now += seconds


class _KillRecorder:
    """Records ``killpg`` calls; optionally raises for a chosen signal."""

    def __init__(self, *, raise_on: int | None = None) -> None:
        self.calls: list[tuple[int, int]] = []
        self._raise_on = raise_on

    def __call__(self, pgid: int, sig: int) -> None:
        self.calls.append((pgid, sig))
        if self._raise_on is not None and sig == self._raise_on:
            raise ProcessLookupError(pgid)


class _WaitpidRecorder:
    """Returns queued results, then ``ChildProcessError``; or raises at once."""

    def __init__(
        self,
        results: list[tuple[int, int]] | None = None,
        *,
        raise_immediately: bool = False,
    ) -> None:
        self._results = list(results or [])
        self._raise_immediately = raise_immediately
        self.calls: list[tuple[int, int]] = []

    def __call__(self, pid: int, options: int) -> tuple[int, int]:
        self.calls.append((pid, options))
        if self._raise_immediately:
            raise ChildProcessError(pid)
        if self._results:
            return self._results.pop(0)
        raise ChildProcessError(pid)


def _manager_with(pgid: int) -> ProcessGroupManager:
    mgr = ProcessGroupManager()
    mgr._pgids.add(pgid)
    return mgr


# --- branch 1: grace expiry -> SIGKILL --------------------------------------


def test_grace_expiry_sends_sigkill() -> None:
    pgid = 4242
    mgr = _manager_with(pgid)
    clock = _FakeClock()
    kill = _KillRecorder()
    wait = _WaitpidRecorder([(pgid, 0)])
    interrupt = EscalatingInterrupt(
        mgr,
        grace=3.0,
        clock=clock.monotonic,
        sleep=clock.sleep,
        killpg=kill,
        waitpid=wait,
        alive=lambda _pgid: True,  # never exits during grace
    )

    result = interrupt.interrupt(pgid)

    assert result == InterruptResult(
        pgid=pgid,
        sigint_sent=True,
        sigkill_sent=True,
        already_dead=False,
        reaped=1,
        reap_failed=False,
    )
    assert kill.calls == [(pgid, signal.SIGINT), (pgid, sigkill())]
    assert mgr.pgids == frozenset()


def test_grace_window_is_respected() -> None:
    """The SIGKILL is sent only after the full grace window elapses."""
    pgid = 7
    mgr = _manager_with(pgid)
    clock = _FakeClock()
    kill = _KillRecorder()
    interrupt = EscalatingInterrupt(
        mgr,
        grace=3.0,
        poll_interval=1.0,
        clock=clock.monotonic,
        sleep=clock.sleep,
        killpg=kill,
        waitpid=_WaitpidRecorder([(pgid, 0)]),
        alive=lambda _pgid: True,
    )

    interrupt.interrupt(pgid)

    # 3 polls of 1.0s each: the clock advanced by exactly the grace window.
    assert clock.monotonic() == 1_000_003.0
    assert kill.calls[-1] == (pgid, sigkill())


# --- branch 2: early exit -> no SIGKILL -------------------------------------


def test_early_exit_skips_sigkill() -> None:
    pgid = 99
    mgr = _manager_with(pgid)
    clock = _FakeClock()
    kill = _KillRecorder()
    wait = _WaitpidRecorder([(pgid, 0)])
    interrupt = EscalatingInterrupt(
        mgr,
        grace=3.0,
        clock=clock.monotonic,
        sleep=clock.sleep,
        killpg=kill,
        waitpid=wait,
        alive=lambda _pgid: False,  # exits immediately after SIGINT
    )

    result = interrupt.interrupt(pgid)

    assert result == InterruptResult(
        pgid=pgid,
        sigint_sent=True,
        sigkill_sent=False,
        already_dead=False,
        reaped=1,
        reap_failed=False,
    )
    assert kill.calls == [(pgid, signal.SIGINT)]
    assert mgr.pgids == frozenset()


# --- branch 3: already-dead PGID --------------------------------------------


def test_already_dead_pgid_is_success() -> None:
    pgid = 555
    mgr = _manager_with(pgid)
    clock = _FakeClock()
    kill = _KillRecorder(raise_on=signal.SIGINT)
    wait = _WaitpidRecorder(raise_immediately=True)
    interrupt = EscalatingInterrupt(
        mgr,
        clock=clock.monotonic,
        sleep=clock.sleep,
        killpg=kill,
        waitpid=wait,
        alive=lambda _pgid: True,
    )

    result = interrupt.interrupt(pgid)

    assert result == InterruptResult(
        pgid=pgid,
        sigint_sent=False,
        sigkill_sent=False,
        already_dead=True,
        reaped=0,
        reap_failed=True,
    )
    # Only the SIGINT attempt was made; no SIGKILL, no grace wait.
    assert kill.calls == [(pgid, signal.SIGINT)]
    assert mgr.pgids == frozenset()


# --- branch 4: reap failure -------------------------------------------------


def test_reap_failure_is_reported() -> None:
    pgid = 321
    mgr = _manager_with(pgid)
    clock = _FakeClock()
    wait = _WaitpidRecorder(raise_immediately=True)
    interrupt = EscalatingInterrupt(
        mgr,
        clock=clock.monotonic,
        sleep=clock.sleep,
        killpg=_KillRecorder(),
        waitpid=wait,
        alive=lambda _pgid: False,
    )

    result = interrupt.interrupt(pgid)

    assert result.reap_failed is True
    assert result.reaped == 0
    assert result.sigkill_sent is False
    assert wait.calls == [(pgid, wnohang())]


def test_reap_loops_until_no_children() -> None:
    """WNOHANG reaping drains every child, then stops on ChildProcessError."""
    pgid = 12
    mgr = _manager_with(pgid)
    clock = _FakeClock()
    wait = _WaitpidRecorder([(pgid, 0), (pgid, 0), (pgid, 0)])
    interrupt = EscalatingInterrupt(
        mgr,
        clock=clock.monotonic,
        sleep=clock.sleep,
        killpg=_KillRecorder(),
        waitpid=wait,
        alive=lambda _pgid: False,
    )

    result = interrupt.interrupt(pgid)

    assert result.reaped == 3
    assert result.reap_failed is False
    assert len(wait.calls) == 4  # 3 reaps + the terminating ChildProcessError


# --- interrupt_all + registry -----------------------------------------------


def test_interrupt_all_covers_every_group_and_clears_registry() -> None:
    mgr = ProcessGroupManager()
    mgr._pgids.update({10, 20})
    clock = _FakeClock()
    interrupt = EscalatingInterrupt(
        mgr,
        clock=clock.monotonic,
        sleep=clock.sleep,
        killpg=_KillRecorder(),
        waitpid=_WaitpidRecorder([(10, 0), (20, 0)]),
        alive=lambda _pgid: False,
    )

    results = interrupt.interrupt_all()

    assert [r.pgid for r in results] == [10, 20]
    assert all(r.sigint_sent for r in results)
    assert mgr.pgids == frozenset()


def test_grace_property_exposes_window() -> None:
    interrupt = EscalatingInterrupt(ProcessGroupManager(), grace=1.5)
    assert interrupt.grace == 1.5


# --- platform helpers -------------------------------------------------------


def test_wnohang_and_sigkill_are_ints() -> None:
    assert isinstance(wnohang(), int)
    assert isinstance(sigkill(), int)
    assert sigkill() == 9


def test_non_posix_live_path_raises_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without injected syscalls the non-POSIX live path fails closed."""
    monkeypatch.setattr("dev_harness.core.signals.is_posix", lambda: False)
    interrupt = EscalatingInterrupt(ProcessGroupManager())
    with pytest.raises(UnsupportedPlatformError) as excinfo:
        interrupt.interrupt(1)
    assert "POSIX" in str(excinfo.value)
    assert excinfo.value.remediation != ""


def test_probe_alive_permission_error_means_alive() -> None:
    """A signal-0 probe that hits EPERM still counts as alive."""
    pgid = 8
    mgr = _manager_with(pgid)

    def _killpg(_pgid: int, sig: int) -> None:
        if sig == 0:
            raise PermissionError(_pgid)

    clock = _FakeClock()
    interrupt = EscalatingInterrupt(
        mgr,
        grace=1.0,
        poll_interval=1.0,
        clock=clock.monotonic,
        sleep=clock.sleep,
        killpg=_killpg,
        waitpid=_WaitpidRecorder([(pgid, 0)]),
    )

    result = interrupt.interrupt(pgid)

    assert result.sigkill_sent is True  # never observed as dead -> escalated


def test_probe_alive_process_lookup_means_dead() -> None:
    """A signal-0 probe that hits ESRCH reports the group as dead."""
    pgid = 9
    mgr = _manager_with(pgid)

    def _killpg(_pgid: int, sig: int) -> None:
        if sig == 0:
            raise ProcessLookupError(_pgid)

    clock = _FakeClock()
    interrupt = EscalatingInterrupt(
        mgr,
        grace=3.0,
        clock=clock.monotonic,
        sleep=clock.sleep,
        killpg=_killpg,
        waitpid=_WaitpidRecorder([(pgid, 0)]),
    )

    result = interrupt.interrupt(pgid)

    assert result.sigkill_sent is False  # observed dead during grace
    assert result.reaped == 1


def test_probe_alive_success_means_alive() -> None:
    """A signal-0 probe that returns cleanly reports the group as alive."""
    pgid = 11
    mgr = _manager_with(pgid)
    clock = _FakeClock()
    interrupt = EscalatingInterrupt(
        mgr,
        grace=1.0,
        poll_interval=1.0,
        clock=clock.monotonic,
        sleep=clock.sleep,
        killpg=_KillRecorder(),  # signal-0 probe never raises
        waitpid=_WaitpidRecorder([(pgid, 0)]),
    )

    result = interrupt.interrupt(pgid)

    assert result.sigkill_sent is True


def test_reap_stops_when_no_child_ready() -> None:
    """A WNOHANG reap returning pid 0 stops the loop without failure."""
    pgid = 13
    mgr = _manager_with(pgid)
    clock = _FakeClock()
    wait = _WaitpidRecorder([(0, 0)])
    interrupt = EscalatingInterrupt(
        mgr,
        clock=clock.monotonic,
        sleep=clock.sleep,
        killpg=_KillRecorder(),
        waitpid=wait,
        alive=lambda _pgid: False,
    )

    result = interrupt.interrupt(pgid)

    assert result.reaped == 0
    assert result.reap_failed is False
    assert wait.calls == [(pgid, wnohang())]


def test_live_killpg_resolution_on_posix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no injected killpg the POSIX branch resolves ``os.killpg``."""
    pgid = 14
    mgr = _manager_with(pgid)
    calls: list[tuple[int, int]] = []

    def _fake_killpg(_pgid: int, sig: int) -> None:
        calls.append((_pgid, sig))

    monkeypatch.setattr("dev_harness.core.signals.is_posix", lambda: True)
    monkeypatch.setattr(os, "killpg", _fake_killpg, raising=False)
    clock = _FakeClock()
    interrupt = EscalatingInterrupt(
        mgr,
        clock=clock.monotonic,
        sleep=clock.sleep,
        waitpid=_WaitpidRecorder([(pgid, 0)]),
        alive=lambda _pgid: False,
    )

    result = interrupt.interrupt(pgid)

    assert result.sigint_sent is True
    assert calls == [(pgid, signal.SIGINT)]


def test_non_posix_waitpid_resolution_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without an injected waitpid the non-POSIX reap path fails closed."""
    pgid = 15
    mgr = _manager_with(pgid)
    monkeypatch.setattr("dev_harness.core.signals.is_posix", lambda: False)
    clock = _FakeClock()
    interrupt = EscalatingInterrupt(
        mgr,
        clock=clock.monotonic,
        sleep=clock.sleep,
        killpg=_KillRecorder(),
        alive=lambda _pgid: False,
    )

    with pytest.raises(UnsupportedPlatformError) as excinfo:
        interrupt.interrupt(pgid)
    assert "reaping" in str(excinfo.value)


def test_live_waitpid_resolution_on_posix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no injected waitpid the POSIX branch resolves ``os.waitpid``."""
    pgid = 16
    mgr = _manager_with(pgid)
    calls: list[tuple[int, int]] = []

    def _fake_waitpid(_pid: int, options: int) -> tuple[int, int]:
        calls.append((_pid, options))
        if len(calls) == 1:
            return (_pid, 0)
        # No child remains: terminate the WNOHANG drain loop.
        raise ChildProcessError(_pid)

    monkeypatch.setattr("dev_harness.core.signals.is_posix", lambda: True)
    monkeypatch.setattr(os, "waitpid", _fake_waitpid, raising=False)
    clock = _FakeClock()
    interrupt = EscalatingInterrupt(
        mgr,
        clock=clock.monotonic,
        sleep=clock.sleep,
        killpg=_KillRecorder(),
        alive=lambda _pgid: False,
    )

    result = interrupt.interrupt(pgid)

    assert result.reaped == 1
    # One successful reap, then the terminating ChildProcessError.
    assert calls == [(pgid, wnohang()), (pgid, wnohang())]


# --- 6.B row: live POSIX orphan elimination ---------------------------------

_LIVE_RUNNER = (
    "import os, signal, subprocess, sys, time\n"
    "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
    "for _ in range(3):\n"
    "    subprocess.Popen([sys.executable, '-c',\n"
    "        'import signal, time; signal.signal(signal.SIGINT, signal.SIG_IGN);"
    " time.sleep(30)'])\n"
    "while True:\n"
    "    time.sleep(0.1)\n"
)


def _group_alive(pgid: int) -> bool:
    """True while any process remains in ``pgid`` (``pgrep -g`` equivalent)."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.skipif(not is_posix(), reason="process groups are POSIX-only")
def test_live_orphan_elimination() -> None:
    """A SIGINT-ignoring tree with 3 grandchildren is gone within 4.0s."""
    mgr = ProcessGroupManager()
    proc = mgr.spawn([sys.executable, "-c", _LIVE_RUNNER])
    pgid = proc.pid
    try:
        time.sleep(0.5)  # let the grandchildren spawn
        interrupt = EscalatingInterrupt(mgr, grace=3.0)
        started = time.monotonic()
        result = interrupt.interrupt(pgid)
        elapsed = time.monotonic() - started

        assert result.sigkill_sent is True
        assert elapsed < 4.0
        assert _group_alive(pgid) is False
        assert mgr.pgids == frozenset()
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)
