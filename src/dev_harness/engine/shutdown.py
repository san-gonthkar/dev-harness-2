"""Graceful shutdown: drain, seal checkpoint, unlink socket (V11 5.7).

The shutdown sequence is the inverse of startup: stop accepting new work,
drain in-flight envelopes, seal a final checkpoint with ``is_paused=True``
so a later resume restores the exact state, then unlink the workspace
socket. The daemon owns the socket lifecycle; this module owns the
*ordering* of the drain so a crash mid-shutdown never leaves a live socket
with a stale checkpoint.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dev_harness.contracts.enums import ExecutionState
from dev_harness.contracts.errors import HarnessError
from dev_harness.engine.session import SessionManager
from dev_harness.storage.checkpoint_binding import CheckpointBinding
from dev_harness.storage.sqlite_saver import Scope

DRAIN_TIMEOUT = 5.0  # 5.B: in-flight work is drained before the socket goes.


class ShutdownError(HarnessError):
    """A graceful shutdown could not complete cleanly."""

    remediation = (
        "The daemon stopped without a sealed checkpoint; restart it and "
        "shut down again, or recover from the prior valid checkpoint."
    )


class GracefulShutdown:
    """Orchestrates the graceful shutdown sequence.

    The sequence is: (1) stop accepting new work, (2) drain in-flight
    envelopes until the fanout queues are empty or the timeout elapses,
    (3) seal a final checkpoint with ``is_paused=True``, (4) unlink the
    socket. Each step is idempotent so a repeated shutdown is a no-op.
    """

    def __init__(
        self,
        workspace: str | Path,
        *,
        sessions: SessionManager,
        fanout: Any,
        unlink: Callable[[], None],
        binding: CheckpointBinding | None = None,
        clock: Callable[[], float] = time.monotonic,
        drain_timeout: float = DRAIN_TIMEOUT,
    ) -> None:
        self.workspace = Path(workspace)
        self._sessions = sessions
        self._fanout = fanout
        self._unlink_socket = unlink
        self._binding = binding or CheckpointBinding(self.workspace)
        self._clock = clock
        self.drain_timeout = drain_timeout
        self._lock = threading.Lock()
        self._finished = False

    # -- public API ----------------------------------------------------------

    def shutdown(self) -> None:
        """Run the graceful shutdown sequence exactly once.

        Safe to call from any thread (the daemon's signal handler, the
        SHUTDOWN command handler, or a test). A second call is a no-op.
        """
        with self._lock:
            if self._finished:
                return
            self._finished = True
        try:
            self._drain()
            self._seal_checkpoint()
        finally:
            # The socket is always unlinked: even on a drain timeout no new
            # work may arrive at a dying daemon. The checkpoint is only
            # sealed when the drain succeeded (state is consistent).
            self._unlink_socket_file()

    @property
    def done(self) -> bool:
        """True once the shutdown sequence has run."""
        return self._finished

    # -- sequence ------------------------------------------------------------

    def _drain(self) -> None:
        """Wait for in-flight envelopes to leave the fanout queues."""
        deadline = self._clock() + self.drain_timeout
        while self._clock() < deadline:
            if self._fanout_pending() == 0:
                return
            time.sleep(0.01)
        # Timeout: the queues are still non-empty. The socket is unlinked
        # anyway so no new work arrives; the checkpoint below still seals
        # the state we have.
        raise ShutdownError(
            f"in-flight work did not drain within {self.drain_timeout}s",
            remediation=(
                "The daemon stopped without a sealed checkpoint; restart it "
                "and shut down again, or recover from the prior valid checkpoint."
            ),
        )

    def _fanout_pending(self) -> int:
        """Total pending envelopes across all attached clients."""
        total = 0
        for client_id in self._fanout.clients():
            total += self._fanout.pending(client_id)
        return total

    def _seal_checkpoint(self) -> None:
        """Write a final checkpoint with ``is_paused=True`` for the session."""
        session = self._sessions.get(self.workspace)
        if session is None:
            # No session was ever started: nothing to seal.
            return
        sealed = session.state.model_copy(
            update={
                "tui_state": session.state.tui_state.model_copy(
                    update={
                        "is_paused": True,
                        "critic_gatekeeper_status": ExecutionState.PAUSED,
                    }
                )
            }
        )
        scope = Scope(
            project_id=session.project_id,
            thread_id=session.thread_id,
        )
        self._binding.put_bound(
            scope,
            sealed,
            checkpoint_id="shutdown",
            is_paused=True,
        )

    def _unlink_socket_file(self) -> None:
        """Unlink the workspace socket so no client can connect."""
        self._unlink_socket()
