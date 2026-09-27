"""Engine daemon transport tests (V11 5.1 extension) — TCP loopback on Windows.

The engine daemon binds AF_UNIX where available. On platforms without it
(native Windows) it binds TCP loopback on an OS-assigned port and publishes
the resolved endpoint to ``<workspace>/.dev-harness/engine.endpoint``.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from dev_harness.contracts.events import Envelope, SnapshotPayload
from dev_harness.contracts.state import HarnessState
from dev_harness.engine.daemon import EngineDaemon
from dev_harness.ipc.client import IpcClient
from dev_harness.ipc.transport import (
    EPHEMERAL_PORT,
    LOOPBACK,
    Endpoint,
    af_unix_available,
    bound_endpoint,
    parse_endpoint,
)


def _snapshot(ws: Path) -> Envelope:
    return Envelope(
        type="SNAPSHOT",
        payload=SnapshotPayload(
            type="SNAPSHOT",
            state=HarnessState(project_id="p", workspace_path=str(ws), thread_id="t"),
        ),
    )


@pytest.mark.integration
def test_daemon_binds_tcp_when_af_unix_unavailable(tmp_path: Path) -> None:
    """On a platform without AF_UNIX the daemon binds TCP loopback."""
    if af_unix_available():
        pytest.skip("AF_UNIX is available; the TCP fallback is not exercised")
    daemon = EngineDaemon(tmp_path, handler=lambda env: env)
    daemon.start()
    try:
        assert daemon.endpoint is not None
        assert daemon.endpoint.kind == "tcp"
        host, _, port = daemon.endpoint.address.rpartition(":")
        assert host == LOOPBACK
        assert int(port) > 0  # an OS-assigned ephemeral port
    finally:
        daemon.stop()


@pytest.mark.integration
def test_daemon_publishes_and_removes_endpoint_file(tmp_path: Path) -> None:
    """The daemon publishes its endpoint and removes it on stop."""
    if af_unix_available():
        pytest.skip("AF_UNIX is available; no endpoint file is written")
    daemon = EngineDaemon(tmp_path, handler=lambda env: env)
    daemon.start()
    endpoint_file = tmp_path / ".dev-harness" / "engine.endpoint"
    try:
        assert endpoint_file.exists()
        published = parse_endpoint(endpoint_file.read_text(encoding="utf-8"))
        assert published == daemon.endpoint
    finally:
        daemon.stop()
    assert not endpoint_file.exists()


@pytest.mark.integration
def test_envelope_round_trip_over_tcp(tmp_path: Path) -> None:
    """A client reaches the daemon over the published TCP endpoint."""
    if af_unix_available():
        pytest.skip("AF_UNIX is available; the TCP path is not exercised")
    daemon = EngineDaemon(tmp_path, handler=lambda env: env)
    daemon.start()
    try:
        assert daemon.endpoint is not None
        client = IpcClient(tmp_path / "x.sock", endpoint=daemon.endpoint)
        try:
            reply = client.request(_snapshot(tmp_path))
            assert reply.type.value == "SNAPSHOT"
        finally:
            client.close()
    finally:
        daemon.stop()


@pytest.mark.integration
def test_two_daemons_get_distinct_ports(tmp_path: Path) -> None:
    """Dynamic ports mean concurrent workspaces never collide."""
    if af_unix_available():
        pytest.skip("AF_UNIX is available; ports are not used")
    ws_a = tmp_path / "a"
    ws_b = tmp_path / "b"
    ws_a.mkdir()
    ws_b.mkdir()
    da = EngineDaemon(ws_a, handler=lambda env: env)
    db = EngineDaemon(ws_b, handler=lambda env: env)
    da.start()
    db.start()
    try:
        assert da.endpoint is not None
        assert db.endpoint is not None
        assert da.endpoint.address != db.endpoint.address
    finally:
        da.stop()
        db.stop()


@pytest.mark.unit
def test_bound_endpoint_resolves_ephemeral_port() -> None:
    """bound_endpoint replaces port 0 with the OS-assigned port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((LOOPBACK, EPHEMERAL_PORT))
        resolved = bound_endpoint(sock, Endpoint(kind="tcp", address=f"{LOOPBACK}:0"))
        assert resolved.kind == "tcp"
        _, _, port = resolved.address.rpartition(":")
        assert int(port) > 0
    finally:
        sock.close()


@pytest.mark.unit
def test_bound_endpoint_passes_unix_through() -> None:
    """A unix endpoint is returned unchanged."""
    ep = Endpoint(kind="unix", address="/tmp/x.sock")
    assert bound_endpoint(object(), ep) is ep


@pytest.mark.unit
def test_daemon_accepts_explicit_endpoint(tmp_path: Path) -> None:
    """An explicit endpoint overrides the platform default."""
    daemon = EngineDaemon(
        tmp_path,
        handler=lambda env: env,
        endpoint=Endpoint(kind="tcp", address=f"{LOOPBACK}:{EPHEMERAL_PORT}"),
    )
    assert daemon._requested_endpoint().kind == "tcp"
