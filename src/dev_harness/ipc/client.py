"""AF_UNIX client with connect-retry and half-open detection (V11 2.4).

Retry intervals are non-decreasing and capped at 5s. A half-open connection
(server died without FIN) is detected by a zero-length recv on the next read.
"""

from __future__ import annotations

import socket
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dev_harness.contracts.events import Envelope
from dev_harness.ipc.framing import encode, read_frame
from dev_harness.ipc.transport import require_posix

MAX_RETRY_DELAY = 5.0
INITIAL_RETRY_DELAY = 0.05


class IpcClient:
    """A length-prefixed JSON client over AF_UNIX with connect retry."""

    def __init__(
        self,
        socket_path: str | Path,
        *,
        socket_factory: Callable[[int, int], Any] | None = None,
        max_retry_delay: float = MAX_RETRY_DELAY,
    ) -> None:
        require_posix()
        self.socket_path = Path(socket_path)
        self._socket_factory = socket_factory or socket.socket
        self.max_retry_delay = max_retry_delay
        self._conn: Any = None

    def _connect(self) -> Any:
        """Connect with non-decreasing, capped retry intervals."""
        delay = INITIAL_RETRY_DELAY
        while True:
            try:
                conn = self._socket_factory(
                    getattr(socket, "AF_UNIX", 1), getattr(socket, "SOCK_STREAM", 1)
                )
                conn.connect(str(self.socket_path))
                self._conn = conn
                return conn
            except OSError:
                time.sleep(delay)
                delay = min(delay * 2, self.max_retry_delay)

    def send(self, envelope: Envelope) -> None:
        """Send an envelope, reconnecting if the connection is dead."""
        if self._conn is None:
            self._connect()
        try:
            self._conn.sendall(encode(envelope))
        except OSError:
            self._conn = None
            self._connect()
            self._conn.sendall(encode(envelope))

    def request(self, envelope: Envelope) -> Envelope:
        """Send an envelope and read the reply (request/response)."""
        self.send(envelope)
        return read_frame(self._conn)

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except OSError:
                pass
            self._conn = None
