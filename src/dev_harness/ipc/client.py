"""IPC client with connect-retry and half-open detection (V11 2.4, extended).

Retry intervals are non-decreasing and capped at 5s. A half-open connection
(server died without FIN) is detected by a zero-length recv on the next read.

AF_UNIX is used where available; on platforms without it the client connects
over TCP loopback to the endpoint the daemon published.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dev_harness.contracts.events import Envelope
from dev_harness.ipc.framing import encode, read_frame
from dev_harness.ipc.transport import (
    Endpoint,
    require_posix,
)
from dev_harness.ipc.transport import (
    connect as transport_connect,
)

MAX_RETRY_DELAY = 5.0
INITIAL_RETRY_DELAY = 0.05


class IpcClient:
    """A length-prefixed JSON client with connect retry."""

    def __init__(
        self,
        socket_path: str | Path,
        *,
        socket_factory: Callable[[int, int], Any] | None = None,
        max_retry_delay: float = MAX_RETRY_DELAY,
        endpoint: Endpoint | None = None,
        connect_attempts: int | None = None,
    ) -> None:
        self.socket_path = Path(socket_path)
        self._socket_factory = socket_factory
        self.max_retry_delay = max_retry_delay
        # AF_UNIX is the default; TCP is opt-in via an explicit endpoint.
        self.endpoint = endpoint or Endpoint(kind="unix", address=str(self.socket_path))
        if self.endpoint.kind == "unix" and socket_factory is None:
            require_posix()
        #: ``None`` retries forever (the default); an int bounds the attempts
        #: so a best-effort caller can never block indefinitely.
        self.connect_attempts = connect_attempts
        self._conn: Any = None

    def _connect(self) -> Any:
        """Connect with non-decreasing, capped retry intervals.

        Raises the last ``OSError`` when ``connect_attempts`` is exhausted.
        """
        delay = INITIAL_RETRY_DELAY
        attempts = 0
        while True:
            try:
                conn = transport_connect(
                    self.endpoint,
                    self.max_retry_delay,
                    socket_factory=self._socket_factory,
                )
                self._conn = conn
                return conn
            except OSError:
                attempts += 1
                if (
                    self.connect_attempts is not None
                    and attempts >= self.connect_attempts
                ):
                    raise
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
