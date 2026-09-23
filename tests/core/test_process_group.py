"""Subprocess group manager tests (V11 6.4).

Validation matrix: a spawned child has ``getpgid(child) != getpgid(0)``
(its own session) and exactly one PGID is registered per runner. Also
covers the registry bookkeeping (forget, forget_all, iteration) and the
Windows fallback path (no new session, no PGID registered).
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from dev_harness.core.process_group import ProcessGroupManager, is_posix

pytestmark = pytest.mark.unit

_SLEEP = [sys.executable, "-c", "import time; time.sleep(30)"]


def _cleanup(proc: subprocess.Popen[bytes]) -> None:
    """Kill and reap a leftover child so no process leaks between tests."""
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=5)


# --- 6.B row: getpgid(child) != getpgid(0); one PGID per runner -------------


@pytest.mark.skipif(not is_posix(), reason="process groups are POSIX-only")
def test_spawn_creates_new_session_with_distinct_pgid() -> None:
    mgr = ProcessGroupManager()
    proc = mgr.spawn(_SLEEP)
    try:
        child_pgid = os.getpgid(proc.pid)
        assert child_pgid != os.getpgid(0)
        assert child_pgid == proc.pid  # session leader: PGID == PID
        assert mgr.pgid_of(proc) == proc.pid
        assert mgr.pgids == frozenset({proc.pid})
        assert len(mgr) == 1
    finally:
        _cleanup(proc)


@pytest.mark.skipif(not is_posix(), reason="process groups are POSIX-only")
def test_exactly_one_pgid_per_runner() -> None:
    mgr = ProcessGroupManager()
    procs = [mgr.spawn(_SLEEP) for _ in range(3)]
    try:
        # One PGID per runner, all distinct.
        assert len(mgr.pgids) == 3
        assert len(mgr.pgids) == len(procs)
        for proc in procs:
            assert mgr.pgid_of(proc) == proc.pid
    finally:
        for proc in procs:
            _cleanup(proc)


# --- registry bookkeeping ---------------------------------------------------


def test_forget_drops_pgid() -> None:
    mgr = ProcessGroupManager()
    mgr._pgids.add(42)
    mgr.forget(42)
    assert mgr.pgids == frozenset()
    # Forgetting an absent PGID is a no-op.
    mgr.forget(42)


def test_forget_all_clears_registry() -> None:
    mgr = ProcessGroupManager()
    mgr._pgids.update({1, 2, 3})
    mgr.forget_all()
    assert mgr.pgids == frozenset()
    assert len(mgr) == 0


def test_iteration_and_len() -> None:
    mgr = ProcessGroupManager()
    mgr._pgids.update({7, 8})
    assert sorted(mgr) == [7, 8]
    assert len(mgr) == 2


def test_pgid_of_untracked_proc_is_none() -> None:
    mgr = ProcessGroupManager()
    proc = subprocess.Popen(_SLEEP)
    try:
        assert mgr.pgid_of(proc) is None
    finally:
        _cleanup(proc)


# --- Windows fallback: no new session, no PGID registered -------------------


def test_spawn_without_posix_registers_no_pgid(monkeypatch: pytest.MonkeyPatch) -> None:
    """On non-POSIX the child is spawned plainly and nothing is tracked."""
    monkeypatch.setattr("dev_harness.core.process_group.is_posix", lambda: False)
    mgr = ProcessGroupManager()
    proc = mgr.spawn(_SLEEP)
    try:
        assert mgr.pgids == frozenset()
        assert mgr.pgid_of(proc) is None
    finally:
        _cleanup(proc)


def test_is_posix_reflects_platform() -> None:
    assert isinstance(is_posix(), bool)


# --- POSIX branch coverage via fakes (runs on Windows too) ------------------


class _FakePopen:
    """A stand-in for subprocess.Popen with a pid and no real process."""

    def __init__(self, pid: int = 4242) -> None:
        self.pid = pid


def test_posix_spawn_path_registers_pgid(monkeypatch: pytest.MonkeyPatch) -> None:
    """The POSIX branch (start_new_session + registry add) via a fake Popen."""
    captured: dict[str, object] = {}

    def _fake_popen(argv: list[str], **kwargs: object) -> _FakePopen:
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return _FakePopen(pid=4242)

    monkeypatch.setattr("dev_harness.core.process_group.is_posix", lambda: True)
    monkeypatch.setattr("dev_harness.core.process_group.subprocess.Popen", _fake_popen)
    mgr = ProcessGroupManager()
    proc = mgr.spawn(["runner"])
    assert captured["kwargs"]["start_new_session"] is True
    assert mgr.pgids == frozenset({4242})
    assert mgr.pgid_of(proc) == 4242  # type: ignore[arg-type]
