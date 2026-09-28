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

from dev_harness.broker.daemon import (
    DEFAULT_ENDPOINT_FILE,
    DEFAULT_SOCKET_PATH,
    BrokerUnavailableError,
)
from dev_harness.broker.protocol import BrokerMessage, encode, read_frame
from dev_harness.broker.transport import (
    DEFAULT_TCP_PORT,
    Endpoint,
    TransportError,
)
from dev_harness.broker.transport import connect as transport_connect
from dev_harness.config import HarnessConfig
from dev_harness.contracts.enums import ProviderId
from dev_harness.ipc.discovery import resolve_endpoint

_AF_UNIX = getattr(socket, "AF_UNIX", 1)
_SOCK_STREAM = getattr(socket, "SOCK_STREAM", 1)


class BrokerClient:
    """A fail-closed client for the host-scoped broker.

    Endpoint resolution order: an explicit ``endpoint`` argument, then the
    ``broker.endpoint`` config value, then the daemon's published discovery
    file, then the platform default. The discovery file is what makes the
    broker's dynamic port automatic — no port needs to be passed.
    """

    def __init__(
        self,
        socket_path: str | Path | None = None,
        *,
        config: HarnessConfig | None = None,
        connect_timeout: float = 2.0,
        endpoint: str | Endpoint | None = None,
        socket_factory: Any = None,
        endpoint_file: str | Path | None = None,
    ) -> None:
        self.socket_path = Path(socket_path) if socket_path else DEFAULT_SOCKET_PATH
        self._config = config or HarnessConfig()
        self.connect_timeout = connect_timeout
        self._socket_factory = socket_factory
        self._conn: Any = None
        # Resolution order: explicit endpoint -> config -> published discovery
        # file -> platform default. The published file is what makes the
        # broker's dynamic port fully automatic for clients.
        explicit: str | Endpoint | None = endpoint
        if explicit is None and socket_path is not None:
            explicit = Endpoint(kind="unix", address=str(self.socket_path))
        configured = (
            self._config.broker.endpoint if self._config.broker.endpoint else None
        )
        published = Path(endpoint_file) if endpoint_file else DEFAULT_ENDPOINT_FILE
        self.endpoint = resolve_endpoint(
            explicit=explicit,
            configured=configured,
            published=published,
            unix_path=str(self.socket_path),
            tcp_port=DEFAULT_TCP_PORT,
        )

    @property
    def allow_unbrokered(self) -> bool:
        """The sole escape hatch: proceed without a broker."""
        return bool(self._config.broker.allow_unbrokered)

    def _connect(self) -> Any:
        """Connect to the broker endpoint, raising BrokerUnavailableError."""
        try:
            conn = transport_connect(
                self.endpoint,
                self.connect_timeout,
                socket_factory=self._socket_factory,
            )
            self._conn = conn
            return conn
        except (OSError, TransportError) as exc:
            raise BrokerUnavailableError(
                f"cannot reach broker at {self.endpoint}",
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
