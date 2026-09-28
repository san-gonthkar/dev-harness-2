"""IPC server: AF_UNIX or TCP loopback (V11 2.3, extended).

The server binds a length-prefixed JSON transport. AF_UNIX is used where
available (POSIX); on platforms without it the server binds TCP loopback
(``127.0.0.1``), so the engine daemon runs natively on Windows.

The bound endpoint is exposed as :attr:`IpcServer.endpoint`; for a TCP bind
with port 0 the OS-assigned port is resolved there.
"""

from __future__ import annotations

import os
import stat
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dev_harness.contracts.errors import InsecureSocketError
from dev_harness.contracts.events import Envelope
from dev_harness.ipc.framing import FrameError, read_frame
from dev_harness.ipc.transport import (
    Endpoint,
    bind_server,
    bound_endpoint,
    cleanup,
    require_posix,
)

Handler = Callable[[Envelope], Envelope | None]


class IpcServer:
    """A single socket server for framed envelopes.

    Each accepted connection is handled on its own thread; the handler
    receives each decoded Envelope and may return a reply envelope.

    A ``push_source`` turns the server into a **streaming** server: it is
    polled on a background thread and every envelope it returns is broadcast
    to all connected clients. That is how the TUI receives live state without
    having to ask for it.
    """

    def __init__(
        self,
        socket_path: str | Path,
        *,
        handler: Handler | None = None,
        socket_factory: Callable[[int, int], Any] | None = None,
        endpoint: Endpoint | None = None,
        push_source: Callable[[], list[Envelope]] | None = None,
        push_interval: float = 0.02,
    ) -> None:
        self.socket_path = Path(socket_path)
        self.handler = handler
        self._socket_factory = socket_factory
        # AF_UNIX is the default (the secure, documented path); TCP is opt-in
        # via an explicit endpoint. The POSIX gate applies only to a real
        # AF_UNIX bind — an injected factory is a test seam.
        self._requested = endpoint or Endpoint(
            kind="unix", address=str(self.socket_path)
        )
        if self._requested.kind == "unix" and socket_factory is None:
            require_posix()
        self.endpoint: Endpoint = self._requested
        self._server: Any = None
        self._threads: list[threading.Thread] = []
        self._running = False
        self._push_source = push_source
        self.push_interval = push_interval
        self._conns: list[Any] = []
        self._conns_lock = threading.Lock()
        self._push_thread: threading.Thread | None = None

    def start(self) -> None:
        """Bind the endpoint (cleaning any stale socket) and begin accepting."""
        if self._requested.kind == "unix":
            self.socket_path.parent.mkdir(parents=True, exist_ok=True)
            cleanup(self._requested)
        self._server = bind_server(self._requested, socket_factory=self._socket_factory)
        self.endpoint = bound_endpoint(self._server, self._requested)
        if self.endpoint.kind == "unix":
            self._verify_socket_permissions()
        self._running = True
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_thread.start()
        if self._push_source is not None:
            self._push_thread = threading.Thread(target=self._push_loop, daemon=True)
            self._push_thread.start()

    def _push_loop(self) -> None:
        """Broadcast every envelope the push source yields to all clients."""
        from dev_harness.ipc.framing import encode

        while self._running:
            envelopes = self._push_source() if self._push_source else []
            if not envelopes:
                time.sleep(self.push_interval)
                continue
            with self._conns_lock:
                conns = list(self._conns)
            for conn in conns:
                for env in envelopes:
                    try:
                        conn.sendall(encode(env))
                    except OSError:
                        self._drop_conn(conn)

    def _drop_conn(self, conn: Any) -> None:
        """Remove a dead connection from the broadcast set."""
        with self._conns_lock:
            if conn in self._conns:
                self._conns.remove(conn)

    def _verify_socket_permissions(self) -> None:
        """Raise :class:`InsecureSocketError` if the socket is group/other-accessible.

        POSIX-only: on Windows the POSIX mode bits are not meaningful, so the
        check is skipped there.
        """
        if os.name != "posix":
            return
        mode = stat.S_IMODE(os.stat(self.socket_path).st_mode)
        if mode & 0o077:
            raise InsecureSocketError(
                f"socket {self.socket_path} has permissions {oct(mode)}",
                remediation="chmod 600 the socket and ensure it is owned by the current user.",
            )

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
        with self._conns_lock:
            self._conns.append(conn)
        try:
            with conn:
                while self._running:
                    try:
                        env = read_frame(conn)
                    except (OSError, ValueError, FrameError):
                        # EOF, a framing error, or a peer disconnect ends the
                        # connection. FrameError covers IncompleteFrameError
                        # (a HarnessError, not a ValueError).
                        break
                    if self.handler is not None:
                        reply = self.handler(env)
                        if reply is not None:
                            from dev_harness.ipc.framing import encode

                            conn.sendall(encode(reply))
        except OSError:
            pass
        finally:
            self._drop_conn(conn)

    def stop(self) -> None:
        """Stop accepting and close the server socket."""
        self._running = False
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
        cleanup(self.endpoint)
