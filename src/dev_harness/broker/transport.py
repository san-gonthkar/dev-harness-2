"""Broker transport: AF_UNIX or TCP loopback (V11 4.8 extension).

The broker protocol is length-prefixed JSON over a stream, so the wire is
interchangeable. AF_UNIX is preferred on POSIX (filesystem permissions,
no port); TCP loopback is the fallback where AF_UNIX is unavailable
(native Windows), bound to 127.0.0.1 so it is never remotely reachable.

Endpoint specs:

* ``unix:/path/to/broker.sock`` — AF_UNIX (POSIX only).
* ``tcp:127.0.0.1:8765`` — TCP loopback.

Security note: a Unix socket's ``0600`` mode restricts access to the owning
user. A loopback TCP port is reachable by any local process, so it is a
weaker boundary; it is bound to ``127.0.0.1`` only and never to ``0.0.0.0``.
"""

from __future__ import annotations

import os
import socket
import sys
from dataclasses import dataclass
from typing import Any, Literal

from dev_harness.contracts.errors import HarnessError

#: Default loopback port for the TCP transport.
DEFAULT_TCP_PORT = 8765
#: Loopback host — never bind a wider interface.
LOOPBACK = "127.0.0.1"


class TransportError(HarnessError):
    """A broker endpoint could not be parsed or used."""

    remediation = "Use 'unix:/path/to.sock' or 'tcp:127.0.0.1:8765'."


@dataclass(frozen=True)
class Endpoint:
    """A resolved broker endpoint."""

    kind: Literal["unix", "tcp"]
    #: Filesystem path for ``unix``; ``host:port`` for ``tcp``.
    address: str

    def __str__(self) -> str:
        return f"{self.kind}:{self.address}"


def af_unix_available() -> bool:
    """True when this interpreter exposes AF_UNIX (POSIX only)."""
    return hasattr(socket, "AF_UNIX") and os.name == "posix" and sys.platform != "win32"


def parse_endpoint(spec: str) -> Endpoint:
    """Parse an endpoint spec into an :class:`Endpoint`.

    Raises :class:`TransportError` for an unknown scheme or a malformed
    address.
    """
    raw = spec.strip()
    if not raw:
        raise TransportError("empty broker endpoint")
    if raw.startswith("unix:"):
        path = raw[len("unix:") :]
        if not path:
            raise TransportError("unix endpoint has no path")
        return Endpoint(kind="unix", address=path)
    if raw.startswith("tcp:"):
        hostport = raw[len("tcp:") :]
        host, sep, port = hostport.rpartition(":")
        if not sep or not host or not port.isdigit():
            raise TransportError(f"malformed tcp endpoint: {raw}")
        if host not in {LOOPBACK, "localhost"}:
            raise TransportError(
                f"tcp endpoint must bind loopback, got {host!r}",
            )
        return Endpoint(kind="tcp", address=f"{host}:{port}")
    raise TransportError(f"unknown endpoint scheme: {raw}")


def default_endpoint(unix_path: str) -> Endpoint:
    """The default endpoint: AF_UNIX where available, else TCP loopback."""
    if af_unix_available():
        return Endpoint(kind="unix", address=unix_path)
    return Endpoint(kind="tcp", address=f"{LOOPBACK}:{DEFAULT_TCP_PORT}")


def _split_tcp(address: str) -> tuple[str, int]:
    host, _, port = address.rpartition(":")
    return host, int(port)


def bind_server(endpoint: Endpoint) -> Any:
    """Create and bind a listening server socket for ``endpoint``."""
    if endpoint.kind == "unix":
        if not af_unix_available():
            raise TransportError(
                "AF_UNIX is unavailable on this platform",
            )
        sock = socket.socket(getattr(socket, "AF_UNIX", 1), socket.SOCK_STREAM)
        sock.bind(endpoint.address)
        try:
            os.chmod(endpoint.address, 0o600)
        except OSError:
            pass
        sock.listen(16)
        return sock
    host, port = _split_tcp(endpoint.address)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(16)
    return sock


def connect(endpoint: Endpoint, timeout: float, *, socket_factory: Any = None) -> Any:
    """Connect a client socket to ``endpoint``.

    ``socket_factory`` defaults to :func:`socket.socket`; tests inject a fake.
    """
    make = socket_factory or socket.socket
    if endpoint.kind == "unix":
        # getattr fallback keeps the historical Windows behaviour: family 1
        # (AF_INET) with a path argument fails with OSError, which the client
        # maps to BrokerUnavailableError rather than an AttributeError.
        conn = make(getattr(socket, "AF_UNIX", 1), socket.SOCK_STREAM)
        conn.settimeout(timeout)
        conn.connect(endpoint.address)
        return conn
    host, port = _split_tcp(endpoint.address)
    conn = make(socket.AF_INET, socket.SOCK_STREAM)
    conn.settimeout(timeout)
    conn.connect((host, port))
    return conn


def cleanup(endpoint: Endpoint) -> None:
    """Remove a stale Unix socket path (no-op for TCP)."""
    if endpoint.kind != "unix":
        return
    try:
        if os.path.exists(endpoint.address):
            os.unlink(endpoint.address)
    except OSError:
        pass
