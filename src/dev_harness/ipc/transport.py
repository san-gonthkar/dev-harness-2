"""Transport endpoints and the POSIX gate (V11 2.7, extended).

Two concerns live here:

1. **The POSIX gate** — AF_UNIX sockets are POSIX-only. :func:`require_posix`
   raises :class:`UnsupportedPlatformError` naming WSL2 as the supported path.
2. **Endpoint abstraction** — the harness speaks length-prefixed JSON over a
   stream, so the wire is interchangeable. An :class:`Endpoint` is either
   ``unix:/path/to.sock`` (POSIX) or ``tcp:127.0.0.1:<port>`` (any platform).

The TCP transport exists so the engine daemon and broker run natively on
Windows, where Python exposes no ``AF_UNIX``. It is bound to loopback only.

Security note: a Unix socket's ``0600`` mode restricts access to the owning
user. A loopback TCP port is reachable by any local process, so it is a weaker
boundary; it is never bound to a wider interface.
"""

from __future__ import annotations

import os
import socket
import sys
from dataclasses import dataclass
from typing import Any, Literal

from dev_harness.contracts.errors import HarnessError


class UnsupportedPlatformError(HarnessError):
    """The transport is not supported on this platform."""


class TransportError(HarnessError):
    """An endpoint could not be parsed or used."""

    remediation = "Use 'unix:/path/to.sock' or 'tcp:127.0.0.1:0'."


#: Loopback host — never bind a wider interface.
LOOPBACK = "127.0.0.1"
#: Port 0 asks the OS for a free ephemeral port (no collisions).
EPHEMERAL_PORT = 0
#: Default fixed port for the host-scoped broker on platforms without AF_UNIX.
DEFAULT_TCP_PORT = 8765


def require_posix() -> None:
    """Raise UnsupportedPlatformError on non-POSIX platforms.

    The error message must contain "WSL2" so users know the supported path.
    """
    if os.name != "posix" or sys.platform == "win32":
        raise UnsupportedPlatformError(
            "AF_UNIX IPC requires a POSIX platform; on Windows run inside WSL2",
            remediation="Run the harness inside WSL2 or on a POSIX host.",
        )


def is_posix() -> bool:
    """True when the current platform supports AF_UNIX sockets."""
    return os.name == "posix" and sys.platform != "win32"


def af_unix_available() -> bool:
    """True when this interpreter exposes AF_UNIX (POSIX only)."""
    return hasattr(socket, "AF_UNIX") and is_posix()


@dataclass(frozen=True)
class Endpoint:
    """A resolved transport endpoint."""

    kind: Literal["unix", "tcp"]
    #: Filesystem path for ``unix``; ``host:port`` for ``tcp``.
    address: str

    def __str__(self) -> str:
        return f"{self.kind}:{self.address}"


def parse_endpoint(spec: str) -> Endpoint:
    """Parse an endpoint spec into an :class:`Endpoint`.

    Raises :class:`TransportError` for an unknown scheme or a malformed
    address.
    """
    raw = spec.strip()
    if not raw:
        raise TransportError("empty endpoint")
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
            raise TransportError(f"tcp endpoint must bind loopback, got {host!r}")
        return Endpoint(kind="tcp", address=f"{host}:{port}")
    raise TransportError(f"unknown endpoint scheme: {raw}")


def default_endpoint(unix_path: str, *, tcp_port: int = DEFAULT_TCP_PORT) -> Endpoint:
    """The default endpoint: AF_UNIX where available, else TCP loopback.

    The default TCP port is fixed so a host-scoped client can find the broker
    without a discovery file. Pass ``tcp_port=EPHEMERAL_PORT`` for an
    OS-assigned port (used by the workspace-scoped engine daemon).
    """
    if af_unix_available():
        return Endpoint(kind="unix", address=unix_path)
    return Endpoint(kind="tcp", address=f"{LOOPBACK}:{tcp_port}")


def _split_tcp(address: str) -> tuple[str, int]:
    host, _, port = address.rpartition(":")
    return host, int(port)


def bind_server(endpoint: Endpoint, *, socket_factory: Any = None) -> Any:
    """Create and bind a listening server socket for ``endpoint``.

    For a TCP endpoint with port 0 the OS assigns a free port; read it back
    with :func:`bound_endpoint`. An injected ``socket_factory`` is a test seam
    and bypasses the AF_UNIX platform check.
    """
    make = socket_factory or socket.socket
    if endpoint.kind == "unix":
        if socket_factory is None and not af_unix_available():
            raise TransportError("AF_UNIX is unavailable on this platform")
        sock = make(getattr(socket, "AF_UNIX", 1), socket.SOCK_STREAM)
        sock.bind(endpoint.address)
        try:
            os.chmod(endpoint.address, 0o600)
        except OSError:
            pass
        sock.listen(16)
        return sock
    host, port = _split_tcp(endpoint.address)
    sock = make(socket.AF_INET, socket.SOCK_STREAM)
    # Test fakes may omit setsockopt; a real socket always has it.
    setopt = getattr(sock, "setsockopt", None)
    if setopt is not None:
        setopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(16)
    return sock


def bound_endpoint(sock: Any, requested: Endpoint) -> Endpoint:
    """The endpoint actually bound, resolving an ephemeral TCP port.

    A Unix endpoint is returned unchanged; a TCP endpoint with port 0 is
    replaced by the port the OS assigned.
    """
    if requested.kind != "tcp":
        return requested
    host, port = sock.getsockname()[:2]
    return Endpoint(kind="tcp", address=f"{host}:{port}")


def connect(endpoint: Endpoint, timeout: float, *, socket_factory: Any = None) -> Any:
    """Connect a client socket to ``endpoint``.

    ``socket_factory`` defaults to :func:`socket.socket`; tests inject a fake.
    """
    make = socket_factory or socket.socket
    if endpoint.kind == "unix":
        # getattr fallback keeps the historical Windows behaviour: family 1
        # (AF_INET) with a path argument fails with OSError, which callers map
        # to an unreachable error rather than an AttributeError.
        conn = make(getattr(socket, "AF_UNIX", 1), socket.SOCK_STREAM)
        _set_timeout(conn, timeout)
        conn.connect(endpoint.address)
        return conn
    host, port = _split_tcp(endpoint.address)
    conn = make(socket.AF_INET, socket.SOCK_STREAM)
    _set_timeout(conn, timeout)
    conn.connect((host, port))
    return conn


def _set_timeout(conn: Any, timeout: float) -> None:
    """Apply a connect timeout when the socket supports it.

    Test fakes may omit ``settimeout``; a real socket always has it.
    """
    setter = getattr(conn, "settimeout", None)
    if setter is not None:
        setter(timeout)


def cleanup(endpoint: Endpoint) -> None:
    """Remove a stale Unix socket path (no-op for TCP)."""
    if endpoint.kind != "unix":
        return
    try:
        if os.path.exists(endpoint.address):
            os.unlink(endpoint.address)
    except OSError:
        pass
