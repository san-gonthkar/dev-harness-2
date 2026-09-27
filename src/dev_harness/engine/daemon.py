"""EngineDaemon: workspace-scoped dual-socket daemon (V11 5.1).

The engine daemon is the process that hosts the graph, owns the workspace
endpoints (ADR-0002: workspace-scoped, unlike the host-scoped broker), and
serves attached clients. It owns **two** sockets, each with one wire
vocabulary:

* the **streaming** socket — the envelope vocabulary (state broadcast, fan-out
  to TUI clients), served by :class:`~dev_harness.ipc.server.IpcServer`;
* the **control** socket — the command vocabulary (START_SESSION, ATTACH,
  DETACH, STATUS, SHUTDOWN), served by
  :class:`~dev_harness.ipc.control_server.ControlServer`.

Keeping them separate means a client can never send a frame the server cannot
decode. This mirrors the reference daemon in ``scripts/verify_phase_05.sh``.

The command surface (5.3), fan-out (5.4), provider gateway (5.5), and state
broadcast (5.8) plug in through the ``handler`` and ``command_handler``
callables; the daemon itself only owns the socket lifecycle.
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
from dev_harness.engine.commands import Command, CommandResponse
from dev_harness.ipc.control_server import ControlServer
from dev_harness.ipc.server import IpcServer
from dev_harness.ipc.transport import (
    EPHEMERAL_PORT,
    LOOPBACK,
    Endpoint,
    af_unix_available,
    parse_endpoint,
)
from dev_harness.paths import derive_paths

# The streaming socket speaks the envelope vocabulary (ADR-0002).
Handler = Callable[[Envelope], Envelope | None]
# The control socket speaks the command vocabulary (5.3).
CommandHandlerFn = Callable[[Command], CommandResponse]


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
        command_handler: CommandHandlerFn | None = None,
        socket_factory: Callable[[int, int], Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
        endpoint: Endpoint | None = None,
        control_endpoint: Endpoint | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.paths = derive_paths(self.workspace)
        self.socket_path = self.paths.socket_path
        self.control_socket_path = self.paths.control_socket_path
        self.handler = handler
        self.command_handler = command_handler
        self._socket_factory = socket_factory
        self._clock = clock
        self._endpoint = endpoint
        self._control_endpoint = control_endpoint
        self._server: IpcServer | None = None
        self._control_server: ControlServer | None = None
        #: The streaming endpoint actually bound (resolved after :meth:`start`).
        self.endpoint: Endpoint | None = None
        #: The control endpoint actually bound (resolved after :meth:`start`).
        self.control_endpoint: Endpoint | None = None
        self._running = False
        self._draining = False
        self._exit_code = 0
        self._signal_received: int | None = None
        self._signal_lock = threading.Lock()
        self._original_handlers: dict[int, Any] = {}

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        """Bind both workspace endpoints and begin accepting connections.

        AF_UNIX is used where available. On platforms without it (native
        Windows) each socket binds TCP loopback on an OS-assigned port and the
        resolved endpoint is published to ``<workspace>/.dev-harness/`` so
        clients can find it.
        """
        self._server = IpcServer(
            self.socket_path,
            handler=self.handler,
            socket_factory=self._socket_factory,
            endpoint=self._requested_endpoint(),
        )
        self._server.start()
        self.endpoint = self._server.endpoint

        self._control_server = ControlServer(
            self.control_socket_path,
            handler=self.command_handler or _unhandled_command,
            socket_factory=self._socket_factory,
            endpoint=self._requested_control_endpoint(),
        )
        self._control_server.start()
        self.control_endpoint = self._control_server.endpoint

        self._publish_endpoints()
        self._running = True

    def _requested_endpoint(self) -> Endpoint:
        """The streaming endpoint: AF_UNIX where available, else TCP loopback.

        An injected ``socket_factory`` is a test seam and implies AF_UNIX, so
        the daemon's socket lifecycle is testable on any platform.
        """
        if self._endpoint is not None:
            return self._endpoint
        if self._socket_factory is not None or af_unix_available():
            return Endpoint(kind="unix", address=str(self.socket_path))
        return Endpoint(kind="tcp", address=f"{LOOPBACK}:{EPHEMERAL_PORT}")

    def _requested_control_endpoint(self) -> Endpoint:
        """The control endpoint: AF_UNIX where available, else TCP loopback."""
        if self._control_endpoint is not None:
            return self._control_endpoint
        if self._socket_factory is not None or af_unix_available():
            return Endpoint(kind="unix", address=str(self.control_socket_path))
        return Endpoint(kind="tcp", address=f"{LOOPBACK}:{EPHEMERAL_PORT}")

    def _publish_endpoints(self) -> None:
        """Publish each bound endpoint to its file (TCP only).

        On POSIX the AF_UNIX paths are already deterministic, so no file is
        needed; on Windows the dynamic ports must be discoverable.
        """
        self._write_endpoint_file(self.endpoint, self.paths.endpoint_file)
        self._write_endpoint_file(
            self.control_endpoint, self.paths.control_endpoint_file
        )

    @staticmethod
    def _write_endpoint_file(endpoint: Endpoint | None, path: Path) -> None:
        """Write ``endpoint`` to ``path`` unless it is a deterministic unix path."""
        if endpoint is None or endpoint.kind == "unix":
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(endpoint), encoding="utf-8")

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
        """Graceful drain: stop accepting, close both sockets, unlink them."""
        self._draining = True
        self._running = False
        if self._server is not None:
            self._server.stop()
        self._server = None
        if self._control_server is not None:
            self._control_server.stop()
        self._control_server = None
        self._remove_endpoint_files()
        self._draining = False

    def stop(self) -> None:
        """Stop the daemon immediately (no drain)."""
        self._running = False
        if self._server is not None:
            self._server.stop()
        self._server = None
        if self._control_server is not None:
            self._control_server.stop()
        self._control_server = None
        self._remove_endpoint_files()

    def _remove_endpoint_files(self) -> None:
        """Remove the published endpoint files, if any."""
        for path in (self.paths.endpoint_file, self.paths.control_endpoint_file):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

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


def _unhandled_command(command: Command) -> CommandResponse:
    """Default control handler: report that no command surface is wired.

    The daemon owns the socket lifecycle; the command surface (5.3) is
    injected. Without one, a command gets a typed error rather than a dropped
    connection.
    """
    from dev_harness.engine.commands import ErrorResponse

    return ErrorResponse(
        error=f"no command handler wired for {command.command}",
        remediation="Start the daemon with a CommandHandler (see engine.bootstrap).",
    )


def main(argv: list[str] | None = None) -> int:
    """``dev-harness-engine`` daemon entry point.

    Binds both workspace endpoints and blocks until a signal requests
    shutdown. ``--endpoint``/``--control-endpoint`` override the transports
    (e.g. ``tcp:127.0.0.1:0``).
    """
    import argparse

    from dev_harness.engine.commands import CommandHandler
    from dev_harness.engine.session import SessionManager

    parser = argparse.ArgumentParser(prog="dev-harness-engine-daemon")
    parser.add_argument("--workspace", default=".", help="Workspace directory")
    parser.add_argument(
        "--endpoint",
        default=None,
        help="Streaming endpoint: unix:/path/to.sock or tcp:127.0.0.1:0",
    )
    parser.add_argument(
        "--control-endpoint",
        default=None,
        dest="control_endpoint",
        help="Control endpoint: unix:/path/to.sock or tcp:127.0.0.1:0",
    )
    args = parser.parse_args(argv)

    endpoint = parse_endpoint(args.endpoint) if args.endpoint else None
    control_endpoint = (
        parse_endpoint(args.control_endpoint) if args.control_endpoint else None
    )
    sessions = SessionManager()
    daemon = EngineDaemon(
        args.workspace,
        command_handler=CommandHandler(sessions).handle,
        endpoint=endpoint,
        control_endpoint=control_endpoint,
    )
    daemon.start()
    try:
        return daemon.run()
    finally:
        daemon.drain()


if __name__ == "__main__":
    raise SystemExit(main())
