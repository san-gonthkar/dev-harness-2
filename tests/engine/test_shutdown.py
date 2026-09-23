"""Graceful shutdown tests (V11 5.7).

Validation matrix: in-flight work is drained; the final checkpoint has
``is_paused=True``; the socket is unlinked. The binding and fanout are
faked so the sequence is deterministic and fast (PR tier).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dev_harness.contracts.enums import ExecutionState
from dev_harness.contracts.errors import HarnessError
from dev_harness.engine.session import SessionManager
from dev_harness.engine.shutdown import GracefulShutdown, ShutdownError
from dev_harness.storage.sqlite_saver import Scope

pytestmark = pytest.mark.unit


class FakeFanout:
    """A fanout whose pending count is scripted."""

    def __init__(self, pending: int = 0) -> None:
        self._pending = pending
        self._clients = ["c1"]

    def clients(self) -> list[str]:
        return self._clients

    def pending(self, client_id: str) -> int:
        return self._pending


class FakeBinding:
    """Records the sealed checkpoint instead of touching SQLite/git."""

    def __init__(self) -> None:
        self.writes: list[tuple[Scope, Any, dict[str, Any]]] = []

    def put_bound(
        self,
        scope: Scope,
        state: Any,
        *,
        checkpoint_id: str | None = None,
        is_paused: bool = False,
    ) -> str:
        self.writes.append(
            (scope, state, {"checkpoint_id": checkpoint_id, "is_paused": is_paused})
        )
        return checkpoint_id or "cp_0"


class FakeClock:
    """A monotonic clock that advances by a fixed step per call."""

    def __init__(self, step: float = 0.01) -> None:
        self._now = 0.0
        self._step = step

    def __call__(self) -> float:
        self._now += self._step
        return self._now


def _make(
    tmp_path: Path,
    *,
    pending: int = 0,
    clock: FakeClock | None = None,
    binding: FakeBinding | None = None,
) -> tuple[GracefulShutdown, FakeBinding, list[bool]]:
    sessions = SessionManager()
    sessions.new_session(tmp_path)
    fanout = FakeFanout(pending=pending)
    unlinked: list[bool] = []

    def _unlink() -> None:
        unlinked.append(True)

    shutdown = GracefulShutdown(
        tmp_path,
        sessions=sessions,
        fanout=fanout,
        unlink=_unlink,
        binding=binding or FakeBinding(),
        clock=clock or FakeClock(),
        drain_timeout=1.0,
    )
    return shutdown, binding or shutdown._binding, unlinked  # type: ignore[arg-type]


@pytest.mark.unit
def test_shutdown_drains_seals_and_unlinks(tmp_path: Path) -> None:
    """The full sequence runs: drain, seal is_paused=True, unlink socket."""
    shutdown, binding, unlinked = _make(tmp_path)
    shutdown.shutdown()

    assert unlinked == [True]
    assert shutdown.done is True
    assert len(binding.writes) == 1
    scope, state, kwargs = binding.writes[0]
    assert isinstance(scope, Scope)
    assert kwargs["is_paused"] is True
    assert kwargs["checkpoint_id"] == "shutdown"
    assert state.tui_state.is_paused is True
    assert state.tui_state.critic_gatekeeper_status == ExecutionState.PAUSED


@pytest.mark.unit
def test_shutdown_is_idempotent(tmp_path: Path) -> None:
    """A second shutdown call is a no-op: one checkpoint, one unlink."""
    shutdown, binding, unlinked = _make(tmp_path)
    shutdown.shutdown()
    shutdown.shutdown()

    assert len(binding.writes) == 1
    assert unlinked == [True]


@pytest.mark.unit
def test_shutdown_without_session_seals_nothing(tmp_path: Path) -> None:
    """No session -> no checkpoint is written, but the socket is unlinked."""
    sessions = SessionManager()
    fanout = FakeFanout()
    unlinked: list[bool] = []
    binding = FakeBinding()

    def _unlink() -> None:
        unlinked.append(True)

    shutdown = GracefulShutdown(
        tmp_path,
        sessions=sessions,
        fanout=fanout,
        unlink=_unlink,
        binding=binding,
        clock=FakeClock(),
        drain_timeout=1.0,
    )
    shutdown.shutdown()

    assert binding.writes == []
    assert unlinked == [True]


@pytest.mark.unit
def test_drain_timeout_raises_shutdown_error(tmp_path: Path) -> None:
    """In-flight work that never drains raises ShutdownError."""
    shutdown, binding, unlinked = _make(tmp_path, pending=5)
    with pytest.raises(ShutdownError):
        shutdown.shutdown()
    # The socket is still unlinked so no new work can arrive.
    assert unlinked == [True]
    assert binding.writes == []


@pytest.mark.unit
def test_shutdown_error_is_harness_error() -> None:
    """ShutdownError is part of the HarnessError taxonomy."""
    assert issubclass(ShutdownError, HarnessError)
    assert ShutdownError.remediation


@pytest.mark.unit
def test_drain_returns_when_queues_empty(tmp_path: Path) -> None:
    """Drain completes as soon as the fanout reports zero pending."""
    shutdown, _, _ = _make(tmp_path, pending=0)
    shutdown._drain()
    assert shutdown.done is False  # only the full sequence marks done
