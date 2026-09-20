"""Broker client SDK, fail-closed (V11 4.9).

The client is the only way engine code talks to the broker. It is
fail-closed: if the broker is unreachable, every operation raises
BrokerUnavailableError. ``allow_unbrokered=true`` is the sole escape
hatch (config broker.allow_unbrokered) — it lets a local-only setup
proceed without a broker.
"""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

from dev_harness.broker.daemon import DEFAULT_SOCKET_PATH, BrokerUnavailableError
from dev_harness.broker.protocol import BrokerMessage, encode, read_frame
from dev_harness.config import HarnessConfig
from dev_harness.contracts.enums import ProviderId

_AF_UNIX = getattr(socket, "AF_UNIX", 1)
_SOCK_STREAM = getattr(socket, "SOCK_STREAM", 1)


class BrokerClient:
    """A fail-closed client for the host-scoped broker."""

    def __init__(
        self,
        socket_path: str | Path | None = None,
        *,
        config: HarnessConfig | None = None,
        connect_timeout: float = 2.0,
    ) -> None:
        self.socket_path = Path(socket_path) if socket_path else DEFAULT_SOCKET_PATH
        self._config = config or HarnessConfig()
        self.connect_timeout = connect_timeout
        self._conn: Any = None

    @property
    def allow_unbrokered(self) -> bool:
        """The sole escape hatch: proceed without a broker."""
        return bool(self._config.broker.allow_unbrokered)

    def _connect(self) -> Any:
        """Connect to the broker socket, raising BrokerUnavailableError."""
        try:
            conn = socket.socket(_AF_UNIX, _SOCK_STREAM)
            conn.settimeout(self.connect_timeout)
            conn.connect(str(self.socket_path))
            self._conn = conn
            return conn
        except OSError as exc:
            raise BrokerUnavailableError(
                f"cannot reach broker at {self.socket_path}",
                remediation="Start the broker daemon or set broker.allow_unbrokered=true.",
            ) from exc

    def _request(self, msg: BrokerMessage) -> BrokerMessage:
        """Send a request and read the reply (fail-closed)."""
        if self._conn is None:
            self._connect()
        try:
            self._conn.sendall(encode(msg))
            return read_frame(self._conn)
        except (OSError, ValueError) as exc:
            self._conn = None
            raise BrokerUnavailableError(
                f"broker request {msg.op} failed",
                remediation="Start the broker daemon or set broker.allow_unbrokered=true.",
            ) from exc

    def health(self) -> BrokerMessage:
        """Check broker health."""
        return self._request(BrokerMessage(op="HEALTH", data={"status": "ok"}))

    def reserve(
        self,
        provider: ProviderId | str,
        *,
        tokens: float = 1.0,
        callback_endpoint: str = "",
    ) -> BrokerMessage:
        """Reserve capacity for a provider."""
        pid = provider.value if isinstance(provider, ProviderId) else provider
        return self._request(
            BrokerMessage(
                op="RESERVE",
                data={
                    "provider": pid,
                    "tokens": tokens,
                    "callback_endpoint": callback_endpoint,
                },
            )
        )

    def commit(
        self,
        provider: ProviderId | str,
        reservation_id: str,
        *,
        actual: float = 0.0,
        model: str = "",
        usage_in: int = 0,
        usage_out: int = 0,
        callback_endpoint: str = "",
    ) -> BrokerMessage:
        """Commit a reservation with actual usage."""
        pid = provider.value if isinstance(provider, ProviderId) else provider
        return self._request(
            BrokerMessage(
                op="COMMIT",
                data={
                    "provider": pid,
                    "reservation_id": reservation_id,
                    "actual": actual,
                    "model": model,
                    "usage_in": usage_in,
                    "usage_out": usage_out,
                    "callback_endpoint": callback_endpoint,
                },
            )
        )

    def release(self, provider: ProviderId | str, reservation_id: str) -> BrokerMessage:
        """Release a reservation."""
        pid = provider.value if isinstance(provider, ProviderId) else provider
        return self._request(
            BrokerMessage(
                op="RELEASE",
                data={"provider": pid, "reservation_id": reservation_id},
            )
        )

    def metrics(self) -> BrokerMessage:
        """Fetch the current metrics snapshot."""
        return self._request(BrokerMessage(op="METRICS"))

    def close(self) -> None:
        """Close the connection."""
        if self._conn is not None:
            try:
                self._conn.close()
            except OSError:
                pass
            self._conn = None
