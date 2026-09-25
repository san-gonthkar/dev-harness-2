"""AF_UNIX server with 0600 permissions and stale-socket cleanup (V11 2.3).

The server binds a length-prefixed JSON transport on an AF_UNIX socket.
On non-POSIX platforms require_posix() raises UnsupportedPlatformError.
"""

from __future__ import annotations

import os
import socket
import stat
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dev_harness.contracts.errors import InsecureSocketError
from dev_harness.contracts.events import Envelope
from dev_harness.ipc.framing import read_frame
from dev_harness.ipc.transport import require_posix

# AF_UNIX is absent on Windows; the POSIX gate prevents use there.
_AF_UNIX = getattr(socket, "AF_UNIX", 1)
_SOCK_STREAM = getattr(socket, "SOCK_STREAM", 1)

Handler = Callable[[Envelope], Envelope | None]


class IpcServer:
    """A single AF_UNIX socket server for framed envelopes.

    Each accepted connection is handled on its own thread; the handler
    receives each decoded Envelope and may return a reply envelope.
    """

    def __init__(
        self,
        socket_path: str | Path,
        *,
        handler: Handler | None = None,
        socket_factory: Callable[[int, int], Any] | None = None,
    ) -> None:
        require_posix()
        self.socket_path = Path(socket_path)
        self.handler = handler
        self._socket_factory = socket_factory or socket.socket
        self._server: Any = None
        self._threads: list[threading.Thread] = []
        self._running = False

    def start(self) -> None:
        """Bind the socket (unlinking any stale one) and begin accepting."""
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        # Stale socket cleanup: unlink if present.
        if self.socket_path.exists():
            try:
                self.socket_path.unlink()
            except (OSError, NotImplementedError):
                # Windows cannot unlink a socket path via Path.unlink.
                os.unlink(self.socket_path)
        self._server = self._socket_factory(_AF_UNIX, _SOCK_STREAM)
        self._server.bind(str(self.socket_path))
        # 0600 permissions, then verify they took (POSIX only: Windows has no
        # meaningful POSIX mode bits on a socket path).
        os.chmod(self.socket_path, 0o600)
        self._verify_socket_permissions()
        self._server.listen(5)
        self._running = True
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept_thread.start()

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
        try:
            with conn:
                while self._running:
                    try:
                        env = read_frame(conn)
                    except (OSError, ValueError):
                        break  # EOF or framing error ends the connection
                    if self.handler is not None:
                        reply = self.handler(env)
                        if reply is not None:
                            from dev_harness.ipc.framing import encode

                            conn.sendall(encode(reply))
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
        try:
            self.socket_path.unlink(missing_ok=True)
        except (OSError, NotImplementedError):
            pass
