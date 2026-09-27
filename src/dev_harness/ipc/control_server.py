"""Control server: the command vocabulary over a socket (V11 5.3, ADR-0002).

The engine daemon owns two endpoints:

* the **streaming** socket (:class:`~dev_harness.ipc.server.IpcServer`) carrying
  the envelope vocabulary — state broadcast and fan-out to TUI clients;
* the **control** socket (this module) carrying the command vocabulary —
  START_SESSION, ATTACH, DETACH, STATUS, SHUTDOWN.

Keeping them separate means each socket has exactly one wire vocabulary, so a
client can never send a frame the server cannot decode. This mirrors the
reference daemon in ``scripts/verify_phase_05.sh``.

Transport is AF_UNIX where available, else TCP loopback (native Windows); see
:mod:`dev_harness.ipc.transport`.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dev_harness.engine.commands import (
    Command,
    CommandResponse,
    ErrorResponse,
    encode_response,
    read_command_frame,
)
from dev_harness.ipc.transport import (
    Endpoint,
    bind_server,
    bound_endpoint,
    cleanup,
    require_posix,
)

#: Dispatches one decoded command to its typed response.
CommandHandlerFn = Callable[[Command], CommandResponse]


class ControlServer:
    """A single socket server for the engine command vocabulary.

    Each accepted connection is handled on its own thread. A malformed frame
    ends that connection; an unknown command yields an ``ErrorResponse`` and
    the connection stays open (5.3).
    """

    def __init__(
        self,
        socket_path: str | Path,
        *,
        handler: CommandHandlerFn,
        socket_factory: Callable[[int, int], Any] | None = None,
        endpoint: Endpoint | None = None,
    ) -> None:
        self.socket_path = Path(socket_path)
        self.handler = handler
        self._socket_factory = socket_factory
        # AF_UNIX is the default; TCP is opt-in via an explicit endpoint. The
        # POSIX gate applies only to a real AF_UNIX bind — an injected factory
        # is a test seam.
        self._requested = endpoint or Endpoint(
            kind="unix", address=str(self.socket_path)
        )
        if self._requested.kind == "unix" and socket_factory is None:
            require_posix()
        self.endpoint: Endpoint = self._requested
        self._server: Any = None
        self._threads: list[threading.Thread] = []
        self._running = False

    def start(self) -> None:
        """Bind the endpoint (cleaning any stale socket) and begin accepting."""
        if self._requested.kind == "unix":
            self.socket_path.parent.mkdir(parents=True, exist_ok=True)
            cleanup(self._requested)
        self._server = bind_server(self._requested, socket_factory=self._socket_factory)
        self.endpoint = bound_endpoint(self._server, self._requested)
        self._running = True
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_thread.start()

    def _accept_loop(self) -> None:
        while self._running:
            try:
                conn, _ = self._server.accept()
            except OSError:
                break
            t = threading.Thread(target=self._handle_conn, args=(conn,), daemon=True)
            self._threads.append(t)
            t.start()

    def _handle_conn(self, conn: Any) -> None:
        try:
            with conn:
                while self._running:
                    try:
                        command = read_command_frame(conn)
                    except (OSError, ValueError):
                        # EOF or a malformed frame ends the connection.
                        break
                    try:
                        response = self.handler(command)
                    except Exception as exc:  # noqa: BLE001 - never kill the connection
                        response = ErrorResponse(
                            error=f"{type(exc).__name__}: {exc}",
                            remediation="Check the command payload and retry.",
                        )
                    conn.sendall(encode_response(response))
        except OSError:
            pass

    def stop(self) -> None:
        """Stop accepting and close the server socket."""
        self._running = False
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
        cleanup(self.endpoint)
