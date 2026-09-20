"""EngineDaemon: workspace-scoped socket daemon (V11 5.1).

The engine daemon is the process that hosts the graph, owns the workspace
socket (ADR-0002: workspace-scoped, unlike the host-scoped broker), and
serves attached clients. This task delivers the skeleton: bind the derived
socket, own the accept loop, install signal handlers, and drain cleanly.

The command surface (5.3), fan-out (5.4), provider gateway (5.5), and state
broadcast (5.8) plug in through the ``handler`` callable; the daemon itself
only owns the socket lifecycle.
"""

from __future__ import annotations

import signal
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dev_harness.contracts.errors import HarnessError
from dev_harness.contracts.events import Envelope
from dev_harness.ipc.server import IpcServer
from dev_harness.paths import derive_paths

# The engine speaks the envelope vocabulary over the same framing as the
# broker, but on a workspace-scoped socket (ADR-0002).
Handler = Callable[[Envelope], Envelope | None]


class EngineDaemonError(HarnessError):
    """Base for engine daemon lifecycle failures."""

    remediation = "Check the engine daemon log and restart if needed."


class EngineDaemon:
    """The workspace-scoped engine daemon.

    Owns the socket lifecycle and the accept loop. The ``handler`` receives
    every decoded envelope from any attached client and may return a reply
    envelope (request/response for commands).
    """

    def __init__(
        self,
        workspace: str | Path,
        *,
        handler: Handler | None = None,
        socket_factory: Callable[[int, int], Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.workspace = Path(workspace)
        self.paths = derive_paths(self.workspace)
        self.socket_path = self.paths.socket_path
        self.handler = handler
        self._socket_factory = socket_factory
        self._clock = clock
        self._server: IpcServer | None = None
        self._running = False
        self._draining = False
        self._exit_code = 0
        self._signal_received: int | None = None
        self._signal_lock = threading.Lock()
        self._original_handlers: dict[int, Any] = {}

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        """Bind the workspace socket and begin accepting connections."""
        self._server = IpcServer(
            self.socket_path,
            handler=self.handler,
            socket_factory=self._socket_factory,
        )
        self._server.start()
        self._running = True

    def run(self) -> int:
        """Own the loop: install signal handlers and block until drained.

        Returns the process exit code (0 on clean shutdown).
        """
        self._install_signal_handlers()
        try:
            while self._running:
                time.sleep(0.1)
        finally:
            self._restore_signal_handlers()
        return self._exit_code

    def request_shutdown(self, exit_code: int = 0) -> None:
        """Request a graceful shutdown from any thread (signal-safe)."""
        with self._signal_lock:
            self._exit_code = exit_code
            self._running = False

    def drain(self, timeout: float = 5.0) -> None:
        """Graceful drain: stop accepting, close the socket, unlink it."""
        self._draining = True
        self._running = False
        if self._server is not None:
            self._server.stop()
        self._server = None
        self._draining = False

    def stop(self) -> None:
        """Stop the daemon immediately (no drain)."""
        self._running = False
        if self._server is not None:
            self._server.stop()
        self._server = None

    # -- signal handling -----------------------------------------------------

    def _install_signal_handlers(self) -> None:
        """Install SIGINT/SIGTERM handlers that request a clean shutdown."""
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                self._original_handlers[sig] = signal.getsignal(sig)
            except (ValueError, OSError):
                continue
            try:
                signal.signal(sig, self._make_signal_handler(sig))
            except (ValueError, OSError):
                # Not the main thread or unsupported signal: skip.
                continue

    def _restore_signal_handlers(self) -> None:
        for sig, original in self._original_handlers.items():
            try:
                signal.signal(sig, original)
            except (ValueError, OSError):
                continue
        self._original_handlers.clear()

    def _make_signal_handler(self, sig: int) -> Callable[[int, Any], None]:
        def _handler(signum: int, frame: Any) -> None:
            self._signal_received = signum
            self.request_shutdown(exit_code=0)

        return _handler

    # -- introspection -------------------------------------------------------

    @property
    def running(self) -> bool:
        """True while the daemon is accepting connections."""
        return self._running

    @property
    def draining(self) -> bool:
        """True while the daemon is draining."""
        return self._draining

    @property
    def signal_received(self) -> int | None:
        """The signal that triggered shutdown, if any."""
        return self._signal_received