"""Endpoint discovery: publish a bound endpoint, read it back (V11 4.8/5.6).

A daemon that binds a **dynamic** port must tell its clients where it landed.
The daemon writes its resolved endpoint to a well-known file; clients read that
file instead of being told the port out of band.

An AF_UNIX path is deterministic (it is derived from the workspace), so no file
is published for it. Only a dynamic TCP port needs discovery.

Resolution order for a client is:

1. an explicit endpoint argument (CLI flag or constructor);
2. the ``endpoint`` value in configuration;
3. the published discovery file (fully automatic);
4. the platform default (AF_UNIX, or the fixed TCP fallback port).
"""

from __future__ import annotations

from pathlib import Path

from dev_harness.ipc.transport import Endpoint, TransportError, parse_endpoint


def publish_endpoint(endpoint: Endpoint | None, path: Path) -> None:
    """Write ``endpoint`` to ``path`` unless it is a deterministic unix path.

    A unix endpoint is already discoverable by its derived path, so publishing
    it would add a file for no benefit.
    """
    if endpoint is None or endpoint.kind == "unix":
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(endpoint), encoding="utf-8")


def read_endpoint(path: Path) -> Endpoint | None:
    """Read a published endpoint, or ``None`` when absent or malformed.

    A malformed file is treated as absent rather than fatal: a stale file from
    a previous version must not make the client unusable.
    """
    if not path.exists():
        return None
    try:
        return parse_endpoint(path.read_text(encoding="utf-8"))
    except (OSError, TransportError):
        return None


def remove_endpoint(path: Path) -> None:
    """Remove a published endpoint file, ignoring a missing file."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def endpoint_is_alive(endpoint: Endpoint, timeout: float = 0.5) -> bool:
    """True when something is accepting connections at ``endpoint``.

    A daemon that is killed hard (SIGKILL / ``taskkill /F``) never runs its
    cleanup, so its published endpoint file goes **stale**: clients would keep
    resolving a dead port. Probing before use turns that stale file into a
    cache miss instead of a confusing connection error.
    """
    import socket

    if endpoint.kind == "unix":
        import os

        return os.path.exists(endpoint.address)
    host, _, port = endpoint.address.rpartition(":")
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def resolve_live_endpoint(
    *,
    explicit: str | Endpoint | None,
    configured: str | None,
    published: Path,
    unix_path: str,
    tcp_port: int,
    timeout: float = 0.5,
) -> Endpoint:
    """Resolve an endpoint, ignoring a **stale** published one.

    Same precedence as :func:`resolve_endpoint`, but a published endpoint that
    nothing is listening on is discarded — so a hard-killed daemon cannot
    strand clients on a dead port. An explicit or configured endpoint is always
    honoured as given (the caller asked for it by name).
    """
    if explicit or configured:
        return resolve_endpoint(
            explicit=explicit,
            configured=configured,
            published=published,
            unix_path=unix_path,
            tcp_port=tcp_port,
        )
    discovered = read_endpoint(published)
    if discovered is not None and endpoint_is_alive(discovered, timeout):
        return discovered
    from dev_harness.ipc.transport import af_unix_available

    if af_unix_available():
        return Endpoint(kind="unix", address=unix_path)
    return Endpoint(kind="tcp", address=f"127.0.0.1:{tcp_port}")


def resolve_endpoint(
    *,
    explicit: str | Endpoint | None,
    configured: str | None,
    published: Path,
    unix_path: str,
    tcp_port: int,
) -> Endpoint:
    """Resolve an endpoint by the documented precedence.

    :param explicit: a CLI flag or constructor argument (highest priority).
    :param configured: the ``endpoint`` value from the config file.
    :param published: the discovery file the daemon writes.
    :param unix_path: the derived AF_UNIX path (the POSIX default).
    :param tcp_port: the TCP port to use when nothing else resolves.
    """
    from dev_harness.ipc.transport import af_unix_available

    if isinstance(explicit, Endpoint):
        return explicit
    if isinstance(explicit, str) and explicit:
        return parse_endpoint(explicit)
    if configured:
        return parse_endpoint(configured)
    discovered = read_endpoint(published)
    if discovered is not None:
        return discovered
    if af_unix_available():
        return Endpoint(kind="unix", address=unix_path)
    return Endpoint(kind="tcp", address=f"127.0.0.1:{tcp_port}")
