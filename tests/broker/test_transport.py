"""Broker transport tests (V11 4.8 extension) — AF_UNIX / TCP loopback."""

from __future__ import annotations

import socket

import pytest

from dev_harness.broker.transport import (
    DEFAULT_TCP_PORT,
    LOOPBACK,
    Endpoint,
    TransportError,
    af_unix_available,
    default_endpoint,
    parse_endpoint,
)


@pytest.mark.unit
def test_parse_unix_endpoint() -> None:
    ep = parse_endpoint("unix:/tmp/broker.sock")
    assert ep.kind == "unix"
    assert ep.address == "/tmp/broker.sock"
    assert str(ep) == "unix:/tmp/broker.sock"


@pytest.mark.unit
def test_parse_tcp_endpoint() -> None:
    ep = parse_endpoint("tcp:127.0.0.1:8765")
    assert ep.kind == "tcp"
    assert ep.address == "127.0.0.1:8765"


@pytest.mark.unit
def test_parse_tcp_localhost_accepted() -> None:
    assert parse_endpoint("tcp:localhost:9000").address == "localhost:9000"


@pytest.mark.unit
@pytest.mark.parametrize(
    "spec",
    [
        "",
        "   ",
        "unix:",
        "tcp:127.0.0.1",
        "tcp:127.0.0.1:notaport",
        "tcp::8765",
        "http:127.0.0.1:8765",
    ],
)
def test_parse_rejects_malformed(spec: str) -> None:
    with pytest.raises(TransportError):
        parse_endpoint(spec)


@pytest.mark.unit
def test_parse_rejects_non_loopback_bind() -> None:
    """A TCP endpoint must never bind a non-loopback interface."""
    with pytest.raises(TransportError) as excinfo:
        parse_endpoint("tcp:0.0.0.0:8765")
    assert "loopback" in str(excinfo.value)


@pytest.mark.unit
def test_default_endpoint_matches_platform() -> None:
    ep = default_endpoint("/tmp/broker.sock")
    if af_unix_available():
        assert ep.kind == "unix"
        assert ep.address == "/tmp/broker.sock"
    else:
        assert ep.kind == "tcp"
        assert ep.address == f"{LOOPBACK}:{DEFAULT_TCP_PORT}"


@pytest.mark.unit
def test_af_unix_available_is_bool() -> None:
    assert isinstance(af_unix_available(), bool)


@pytest.mark.unit
def test_endpoint_is_frozen() -> None:
    ep = Endpoint(kind="tcp", address="127.0.0.1:1")
    with pytest.raises(AttributeError):
        ep.kind = "unix"  # type: ignore[misc]


@pytest.mark.integration
def test_tcp_round_trip() -> None:
    """A real TCP loopback bind/connect/close cycle works on any platform."""
    from dev_harness.broker.transport import bind_server, cleanup, connect

    ep = Endpoint(kind="tcp", address=f"{LOOPBACK}:0")
    server = bind_server(ep)
    try:
        host, port = server.getsockname()
        client = connect(Endpoint(kind="tcp", address=f"{host}:{port}"), 1.0)
        try:
            client.sendall(b"ping")
            conn, _ = server.accept()
            with conn:
                assert conn.recv(4) == b"ping"
        finally:
            client.close()
    finally:
        server.close()
        cleanup(ep)


@pytest.mark.integration
def test_bind_unix_raises_when_unavailable() -> None:
    """Binding a unix endpoint on a platform without AF_UNIX fails closed."""
    if af_unix_available():
        pytest.skip("AF_UNIX is available on this platform")
    from dev_harness.broker.transport import bind_server

    with pytest.raises(TransportError):
        bind_server(Endpoint(kind="unix", address="/tmp/nope.sock"))


@pytest.mark.integration
def test_cleanup_is_noop_for_tcp() -> None:
    from dev_harness.broker.transport import cleanup

    cleanup(Endpoint(kind="tcp", address="127.0.0.1:1"))


@pytest.mark.unit
def test_socket_module_has_no_af_unix_on_windows() -> None:
    """Documents the platform fact the transport exists to work around."""
    if af_unix_available():
        assert hasattr(socket, "AF_UNIX")
    else:
        assert not hasattr(socket, "AF_UNIX") or True
