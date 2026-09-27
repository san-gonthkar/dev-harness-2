"""Control server tests (V11 5.3, ADR-0002) — the command vocabulary socket.

The engine daemon owns two sockets: a streaming socket (envelope vocabulary)
and a control socket (command vocabulary). This module covers the control
half, including the end-to-end handshake that previously could not complete
because both vocabularies shared one socket.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from dev_harness.contracts.enums import ExecutionState
from dev_harness.engine.bootstrap import CommandClient, EngineBootstrap
from dev_harness.engine.commands import (
    CommandHandler,
    ErrorResponse,
    StartSessionCommand,
    StatusCommand,
    StatusResponse,
)
from dev_harness.engine.daemon import EngineDaemon
from dev_harness.engine.session import SessionManager
from dev_harness.ipc.control_server import ControlServer
from dev_harness.ipc.transport import af_unix_available


def _handler() -> CommandHandler:
    return CommandHandler(SessionManager())


@pytest.mark.integration
def test_control_server_round_trips_a_command(tmp_path: Path) -> None:
    """A STATUS command gets a typed StatusResponse over a real socket."""
    server = ControlServer(
        tmp_path / "ctl.sock",
        handler=_handler().handle,
        endpoint=_tcp_endpoint(),
    )
    server.start()
    try:
        client = CommandClient(tmp_path / "ctl.sock", endpoint=server.endpoint)
        try:
            response = client.request(StatusCommand(workspace=str(tmp_path)))
        finally:
            client.close()
        assert isinstance(response, StatusResponse)
        assert response.state == ExecutionState.READY
    finally:
        server.stop()


@pytest.mark.integration
def test_control_server_dispatches_start_session(tmp_path: Path) -> None:
    """START_SESSION creates a session and returns its thread id."""
    server = ControlServer(
        tmp_path / "ctl.sock",
        handler=_handler().handle,
        endpoint=_tcp_endpoint(),
    )
    server.start()
    try:
        client = CommandClient(tmp_path / "ctl.sock", endpoint=server.endpoint)
        try:
            response = client.request(StartSessionCommand(workspace=str(tmp_path)))
        finally:
            client.close()
        assert response.ok is True
        assert getattr(response, "thread_id", "")
    finally:
        server.stop()


@pytest.mark.integration
def test_control_server_returns_error_response_on_handler_failure(
    tmp_path: Path,
) -> None:
    """A raising handler yields an ErrorResponse; the connection stays open."""
    calls: list[str] = []

    def _boom(command: object) -> StatusResponse:
        calls.append("called")
        raise RuntimeError("handler exploded")

    server = ControlServer(
        tmp_path / "ctl.sock",
        handler=_boom,  # type: ignore[arg-type]
        endpoint=_tcp_endpoint(),
    )
    server.start()
    try:
        client = CommandClient(tmp_path / "ctl.sock", endpoint=server.endpoint)
        try:
            response = client.request(StatusCommand(workspace=str(tmp_path)))
        finally:
            client.close()
        assert isinstance(response, ErrorResponse)
        assert "handler exploded" in response.error
        assert calls == ["called"]
    finally:
        server.stop()


@pytest.mark.integration
def test_daemon_serves_both_vocabularies(tmp_path: Path) -> None:
    """The daemon binds a streaming socket and a control socket separately."""
    daemon = EngineDaemon(
        tmp_path,
        handler=lambda env: env,
        command_handler=_handler().handle,
        endpoint=_tcp_endpoint(),
        control_endpoint=_tcp_endpoint(),
    )
    daemon.start()
    try:
        assert daemon.endpoint is not None
        assert daemon.control_endpoint is not None
        # Distinct ports: one socket per vocabulary.
        assert daemon.endpoint.address != daemon.control_endpoint.address
    finally:
        daemon.stop()


@pytest.mark.integration
def test_daemon_handshake_completes_end_to_end(tmp_path: Path) -> None:
    """The STATUS handshake completes against a real daemon.

    This is the regression test for the protocol mismatch: the daemon served
    envelopes while the bootstrap sent command frames, so the handshake could
    never complete.
    """
    daemon = EngineDaemon(
        tmp_path,
        handler=lambda env: env,
        command_handler=_handler().handle,
        endpoint=_tcp_endpoint(),
        control_endpoint=_tcp_endpoint(),
    )
    daemon.start()
    try:
        bootstrap = EngineBootstrap(tmp_path)
        response = bootstrap.handshake(timeout=3.0)
        assert isinstance(response, StatusResponse)
        assert response.state == ExecutionState.READY
    finally:
        daemon.stop()


@pytest.mark.integration
def test_daemon_publishes_both_endpoint_files(tmp_path: Path) -> None:
    """Both dynamic ports are published, and both files are removed on stop."""
    daemon = EngineDaemon(
        tmp_path,
        handler=lambda env: env,
        command_handler=_handler().handle,
        endpoint=_tcp_endpoint(),
        control_endpoint=_tcp_endpoint(),
    )
    daemon.start()
    streaming_file = tmp_path / ".dev-harness" / "engine.endpoint"
    control_file = tmp_path / ".dev-harness" / "engine.ctl.endpoint"
    try:
        assert streaming_file.exists()
        assert control_file.exists()
        assert streaming_file.read_text(encoding="utf-8") != control_file.read_text(
            encoding="utf-8"
        )
    finally:
        daemon.stop()
    assert not streaming_file.exists()
    assert not control_file.exists()


@pytest.mark.integration
def test_daemon_without_command_handler_returns_typed_error(tmp_path: Path) -> None:
    """A daemon with no command surface answers with an ErrorResponse."""
    daemon = EngineDaemon(
        tmp_path,
        handler=lambda env: env,
        endpoint=_tcp_endpoint(),
        control_endpoint=_tcp_endpoint(),
    )
    daemon.start()
    try:
        client = CommandClient(tmp_path / "ctl.sock", endpoint=daemon.control_endpoint)
        try:
            response = client.request(StatusCommand(workspace=str(tmp_path)))
        finally:
            client.close()
        assert isinstance(response, ErrorResponse)
        assert "no command handler" in response.error
    finally:
        daemon.stop()


@pytest.mark.unit
def test_control_server_requires_posix_for_unix_endpoint(tmp_path: Path) -> None:
    """A real AF_UNIX control bind is gated on POSIX."""
    if af_unix_available():
        pytest.skip("AF_UNIX is available on this platform")
    from dev_harness.ipc.transport import UnsupportedPlatformError

    with pytest.raises(UnsupportedPlatformError):
        ControlServer(tmp_path / "ctl.sock", handler=_handler().handle)


def _tcp_endpoint() -> object:
    """A TCP loopback endpoint on an OS-assigned port (works on any platform)."""
    from dev_harness.ipc.transport import EPHEMERAL_PORT, LOOPBACK, Endpoint

    return Endpoint(kind="tcp", address=f"{LOOPBACK}:{EPHEMERAL_PORT}")


@pytest.mark.unit
def test_tcp_endpoint_helper_is_loopback() -> None:
    """The test helper never binds a wider interface."""
    ep = _tcp_endpoint()
    assert ep.kind == "tcp"  # type: ignore[attr-defined]
    assert ep.address.startswith("127.0.0.1:")  # type: ignore[attr-defined]


@pytest.mark.unit
def test_socket_module_has_no_af_unix_on_windows() -> None:
    """Documents the platform fact the TCP transport exists to work around."""
    if not af_unix_available():
        assert not hasattr(socket, "AF_UNIX")
