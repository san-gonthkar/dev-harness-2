"""Endpoint discovery tests (V11 4.8/5.6) — dynamic ports, no manual config.

A daemon that binds a dynamic port publishes it to a discovery file; clients
read that file instead of being told the port. These tests cover the publish /
read / resolve precedence rules.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dev_harness.ipc.discovery import (
    endpoint_is_alive,
    publish_endpoint,
    read_endpoint,
    remove_endpoint,
    resolve_endpoint,
    resolve_live_endpoint,
)
from dev_harness.ipc.transport import EPHEMERAL_PORT, LOOPBACK, Endpoint


@pytest.mark.unit
def test_publish_and_read_round_trip(tmp_path: Path) -> None:
    """A TCP endpoint survives a publish/read round trip."""
    path = tmp_path / "broker.endpoint"
    publish_endpoint(Endpoint(kind="tcp", address="127.0.0.1:58610"), path)
    assert path.read_text(encoding="utf-8") == "tcp:127.0.0.1:58610"
    assert read_endpoint(path) == Endpoint(kind="tcp", address="127.0.0.1:58610")


@pytest.mark.unit
def test_publish_creates_parent_dirs(tmp_path: Path) -> None:
    """Publishing creates the harness directory if absent."""
    path = tmp_path / "nested" / "dir" / "broker.endpoint"
    publish_endpoint(Endpoint(kind="tcp", address="127.0.0.1:1"), path)
    assert path.exists()


@pytest.mark.unit
def test_publish_skips_unix_endpoints(tmp_path: Path) -> None:
    """A unix endpoint is deterministic, so no file is written for it."""
    path = tmp_path / "broker.endpoint"
    publish_endpoint(Endpoint(kind="unix", address="/tmp/broker.sock"), path)
    assert not path.exists()


@pytest.mark.unit
def test_publish_none_is_noop(tmp_path: Path) -> None:
    """Publishing ``None`` writes nothing."""
    path = tmp_path / "broker.endpoint"
    publish_endpoint(None, path)
    assert not path.exists()


@pytest.mark.unit
def test_read_missing_returns_none(tmp_path: Path) -> None:
    assert read_endpoint(tmp_path / "absent.endpoint") is None


@pytest.mark.unit
def test_read_malformed_returns_none(tmp_path: Path) -> None:
    """A stale or corrupt file is treated as absent, never fatal."""
    path = tmp_path / "broker.endpoint"
    path.write_text("not-an-endpoint", encoding="utf-8")
    assert read_endpoint(path) is None


@pytest.mark.unit
def test_remove_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "broker.endpoint"
    remove_endpoint(path)  # absent: no raise
    publish_endpoint(Endpoint(kind="tcp", address="127.0.0.1:1"), path)
    remove_endpoint(path)
    assert not path.exists()


@pytest.mark.unit
def test_resolve_prefers_explicit_over_everything(tmp_path: Path) -> None:
    """An explicit endpoint wins over config and the published file."""
    path = tmp_path / "broker.endpoint"
    publish_endpoint(Endpoint(kind="tcp", address="127.0.0.1:1111"), path)
    resolved = resolve_endpoint(
        explicit="tcp:127.0.0.1:2222",
        configured="tcp:127.0.0.1:3333",
        published=path,
        unix_path="/tmp/x.sock",
        tcp_port=4444,
    )
    assert resolved.address == "127.0.0.1:2222"


@pytest.mark.unit
def test_resolve_prefers_config_over_published(tmp_path: Path) -> None:
    """A configured endpoint beats the published file."""
    path = tmp_path / "broker.endpoint"
    publish_endpoint(Endpoint(kind="tcp", address="127.0.0.1:1111"), path)
    resolved = resolve_endpoint(
        explicit=None,
        configured="tcp:127.0.0.1:3333",
        published=path,
        unix_path="/tmp/x.sock",
        tcp_port=4444,
    )
    assert resolved.address == "127.0.0.1:3333"


@pytest.mark.unit
def test_resolve_uses_published_file(tmp_path: Path) -> None:
    """The discovery file is what makes the dynamic port automatic."""
    path = tmp_path / "broker.endpoint"
    publish_endpoint(Endpoint(kind="tcp", address="127.0.0.1:58610"), path)
    resolved = resolve_endpoint(
        explicit=None,
        configured=None,
        published=path,
        unix_path="/tmp/x.sock",
        tcp_port=4444,
    )
    assert resolved == Endpoint(kind="tcp", address="127.0.0.1:58610")


@pytest.mark.unit
def test_resolve_accepts_an_endpoint_object(tmp_path: Path) -> None:
    """An ``Endpoint`` passed as ``explicit`` is used verbatim."""
    ep = Endpoint(kind="tcp", address="127.0.0.1:9999")
    resolved = resolve_endpoint(
        explicit=ep,
        configured=None,
        published=tmp_path / "absent",
        unix_path="/tmp/x.sock",
        tcp_port=4444,
    )
    assert resolved is ep


@pytest.mark.unit
def test_resolve_empty_explicit_is_ignored(tmp_path: Path) -> None:
    """An empty string is not a valid endpoint and falls through."""
    resolved = resolve_endpoint(
        explicit="",
        configured=None,
        published=tmp_path / "absent",
        unix_path="/tmp/x.sock",
        tcp_port=4444,
    )
    assert resolved.kind in {"unix", "tcp"}


@pytest.mark.unit
def test_resolve_port_zero_is_not_passed_to_client(tmp_path: Path) -> None:
    """The last-resort port is a real fixed port, never 0.

    A client cannot connect to port 0; when no discovery file exists the
    fallback must be a concrete port.
    """
    resolved = resolve_endpoint(
        explicit=None,
        configured=None,
        published=tmp_path / "absent",
        unix_path="/tmp/x.sock",
        tcp_port=8765,
    )
    if resolved.kind == "tcp":
        assert not resolved.address.endswith(":0")
        assert resolved.address == f"{LOOPBACK}:8765"


@pytest.mark.unit
def test_ephemeral_port_constant_is_zero() -> None:
    assert EPHEMERAL_PORT == 0


# --- staleness --------------------------------------------------------------


@pytest.mark.unit
def test_endpoint_is_alive_false_for_dead_port() -> None:
    """A port nothing listens on is not alive."""
    # Port 1 is privileged and almost never listening; connect fails fast.
    assert endpoint_is_alive(Endpoint(kind="tcp", address="127.0.0.1:1")) is False


@pytest.mark.unit
def test_endpoint_is_alive_true_for_live_server() -> None:
    """A bound-and-listening socket is alive."""
    import socket

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        host, port = server.getsockname()[:2]
        assert endpoint_is_alive(Endpoint(kind="tcp", address=f"{host}:{port}")) is True
    finally:
        server.close()


@pytest.mark.unit
def test_endpoint_is_alive_unix_checks_the_path(tmp_path: Path) -> None:
    """A unix endpoint is alive exactly when its path exists."""
    absent = tmp_path / "absent.sock"
    assert endpoint_is_alive(Endpoint(kind="unix", address=str(absent))) is False
    absent.write_text("", encoding="utf-8")
    assert endpoint_is_alive(Endpoint(kind="unix", address=str(absent))) is True


@pytest.mark.integration
def test_resolve_live_endpoint_discards_stale_file(tmp_path: Path) -> None:
    """A published file pointing at a dead port is ignored.

    Regression: a daemon killed hard (SIGKILL / taskkill /F) never cleans up,
    so its file goes stale. Clients must not chase the dead port.
    """
    path = tmp_path / "broker.endpoint"
    publish_endpoint(Endpoint(kind="tcp", address="127.0.0.1:1"), path)
    resolved = resolve_live_endpoint(
        explicit=None,
        configured=None,
        published=path,
        unix_path="/tmp/x.sock",
        tcp_port=8765,
    )
    assert resolved != Endpoint(kind="tcp", address="127.0.0.1:1")
    if resolved.kind == "tcp":
        assert resolved.address == "127.0.0.1:8765"


@pytest.mark.integration
def test_resolve_live_endpoint_uses_a_live_file(tmp_path: Path) -> None:
    """A published file that IS listening is used."""
    import socket

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        host, port = server.getsockname()[:2]
        live = Endpoint(kind="tcp", address=f"{host}:{port}")
        path = tmp_path / "broker.endpoint"
        publish_endpoint(live, path)
        resolved = resolve_live_endpoint(
            explicit=None,
            configured=None,
            published=path,
            unix_path="/tmp/x.sock",
            tcp_port=8765,
        )
        assert resolved == live
    finally:
        server.close()


@pytest.mark.unit
def test_resolve_live_endpoint_honours_explicit_even_if_dead(tmp_path: Path) -> None:
    """An explicit endpoint is used as given — the caller named it."""
    resolved = resolve_live_endpoint(
        explicit="tcp:127.0.0.1:1",
        configured=None,
        published=tmp_path / "absent",
        unix_path="/tmp/x.sock",
        tcp_port=8765,
    )
    assert resolved == Endpoint(kind="tcp", address="127.0.0.1:1")
