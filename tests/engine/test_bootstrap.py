"""Engine bootstrap tests (V11 5.6) — autostart, handshake, version check.

Validation matrix: cold start spawns the daemon and the handshake completes
in under 3s; a version mismatch raises EngineVersionMismatch and never
silently attaches. The socket layer is simulated with FakeSocket (same
pattern as tests/engine/test_daemon.py) because AF_UNIX is POSIX-only.
"""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any, Self

import pytest

from dev_harness import __version__
from dev_harness.contracts.enums import ExecutionState
from dev_harness.contracts.errors import EngineVersionMismatch, HarnessError
from dev_harness.engine.bootstrap import (
    HANDSHAKE_TIMEOUT,
    CommandClient,
    EngineBootstrap,
    EngineUnreachableError,
)
from dev_harness.engine.commands import (
    StatusCommand,
    StatusResponse,
    encode_response,
)

pytestmark = pytest.mark.unit


class FakeSocket:
    """A minimal in-memory socket pair for the bootstrap client."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path
        self.closed = False
        self._buf = b""
        self._peer: FakeSocket | None = None
        self._timeout: float | None = None

    def connect(self, path: str) -> None:
        # Mirror AF_UNIX: connecting to a missing socket raises OSError.
        if not Path(path).exists():
            raise OSError(f"no such socket: {path}")
        self.path = path

    def settimeout(self, timeout: float) -> None:
        self._timeout = timeout

    def sendall(self, data: bytes) -> None:
        if self._peer is not None:
            self._peer._buf += data

    def recv(self, n: int) -> bytes:
        if not self._buf:
            return b""
        out = self._buf[:n]
        self._buf = self._buf[n:]
        return out

    def read(self, n: int) -> bytes:
        """BinaryIO-compatible read used by the response framing."""
        return self.recv(n)

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class FakeSocketFactory:
    """Creates FakeSockets; the first is the server, the rest are clients.

    Every client socket is auto-linked to the server so frames flow in
    both directions (mirrors a real AF_UNIX accept()).
    """

    def __init__(self) -> None:
        self.server: FakeSocket | None = None
        self.clients: list[FakeSocket] = []
        self._pending_replies: list[bytes] = []

    def make_server(self) -> FakeSocket:
        """Create the server socket explicitly (before any client)."""
        self.server = FakeSocket()
        return self.server

    def queue_reply(self, response: StatusResponse) -> None:
        """Queue a response frame for the next client connection."""
        self._pending_replies.append(encode_response(response))

    def __call__(self, family: int, socktype: int) -> FakeSocket:
        s = FakeSocket()
        if family == getattr(socket, "AF_UNIX", 1) and socktype == getattr(
            socket, "SOCK_STREAM", 1
        ):
            if self.server is None:
                self.server = s
            else:
                self.clients.append(s)
                s._peer = self.server
                self.server._peer = s
                if self._pending_replies:
                    s._buf += b"".join(self._pending_replies)
                    self._pending_replies.clear()
        return s


def _server_reply(factory: FakeSocketFactory, response: StatusResponse) -> None:
    """Queue a response frame for the next client connection."""
    factory.queue_reply(response)


def _touch_socket(bootstrap: EngineBootstrap) -> None:
    """Create the socket file (with parents) to simulate a running daemon."""
    bootstrap.socket_path.parent.mkdir(parents=True, exist_ok=True)
    bootstrap.socket_path.write_text("", encoding="utf-8")


def _status_response(**overrides: object) -> StatusResponse:
    kwargs: dict[str, object] = {
        "thread_id": "t1",
        "state": ExecutionState.READY,
        "version": __version__,
    }
    kwargs.update(overrides)
    return StatusResponse(**kwargs)  # type: ignore[arg-type]


class FakeClock:
    """A monotonic clock that advances by a fixed step per call."""

    def __init__(self, step: float = 1.0) -> None:
        self._now = 0.0
        self._step = step

    def __call__(self) -> float:
        self._now += self._step
        return self._now


def _bootstrap(
    tmp_path: Path,
    factory: FakeSocketFactory,
    *,
    spawn: Any = None,
    clock: Any = None,
) -> EngineBootstrap:
    """Build a bootstrap whose client factory links to the fake server."""

    def _make(path: Path) -> CommandClient:
        return CommandClient(path, socket_factory=factory)

    return EngineBootstrap(
        tmp_path,
        client_factory=_make,
        spawn=spawn or (lambda _ws: None),
        clock=clock or FakeClock(),
    )


@pytest.mark.unit
def test_handshake_returns_status(tmp_path: Path) -> None:
    """A handshake returns the daemon's StatusResponse."""
    factory = FakeSocketFactory()
    factory.make_server()
    bootstrap = _bootstrap(tmp_path, factory)
    _touch_socket(bootstrap)
    _server_reply(factory, _status_response(thread_id="t42"))

    response = bootstrap.handshake()
    assert isinstance(response, StatusResponse)
    assert response.thread_id == "t42"
    assert response.state == ExecutionState.READY


@pytest.mark.unit
def test_handshake_under_three_seconds(tmp_path: Path) -> None:
    """The handshake completes well under the 3s SLO."""
    factory = FakeSocketFactory()
    factory.make_server()
    bootstrap = _bootstrap(tmp_path, factory)
    _touch_socket(bootstrap)
    _server_reply(factory, _status_response())

    import time

    started = time.monotonic()
    bootstrap.handshake()
    elapsed = time.monotonic() - started
    assert elapsed < 3.0


@pytest.mark.unit
def test_version_mismatch_raises(tmp_path: Path) -> None:
    """A version mismatch raises EngineVersionMismatch — no silent attach."""
    factory = FakeSocketFactory()
    factory.make_server()
    bootstrap = _bootstrap(tmp_path, factory)
    _touch_socket(bootstrap)
    _server_reply(factory, _status_response(version="999.0.0"))

    with pytest.raises(EngineVersionMismatch):
        bootstrap.handshake()


@pytest.mark.unit
def test_version_mismatch_is_harness_error() -> None:
    """EngineVersionMismatch is part of the HarnessError taxonomy."""
    assert issubclass(EngineVersionMismatch, HarnessError)
    assert EngineVersionMismatch.remediation


@pytest.mark.unit
def test_cold_start_spawns_daemon(tmp_path: Path) -> None:
    """ensure_daemon spawns the daemon when the socket is absent."""
    spawned: list[Path] = []
    factory = FakeSocketFactory()
    factory.make_server()
    def _fake_spawn(ws: Path) -> None:
        spawned.append(ws)
        # The spawned daemon creates the socket file, then the handshake
        # retry loop connects and succeeds.
        bootstrap.socket_path.parent.mkdir(parents=True, exist_ok=True)
        bootstrap.socket_path.write_text("", encoding="utf-8")

    bootstrap = _bootstrap(tmp_path, factory, spawn=_fake_spawn)
    _server_reply(factory, _status_response())

    response = bootstrap.ensure_daemon()
    assert spawned == [tmp_path]
    assert response.state == ExecutionState.READY


@pytest.mark.unit
def test_no_spawn_when_socket_present(tmp_path: Path) -> None:
    """ensure_daemon does not spawn when the socket already exists."""
    spawned: list[Path] = []
    factory = FakeSocketFactory()
    factory.make_server()
    bootstrap = _bootstrap(tmp_path, factory, spawn=lambda ws: spawned.append(ws))
    _touch_socket(bootstrap)
    _server_reply(factory, _status_response())

    bootstrap.ensure_daemon()
    assert spawned == []


@pytest.mark.unit
def test_unreachable_raises_after_timeout(tmp_path: Path) -> None:
    """A daemon that never appears raises EngineUnreachableError."""
    factory = FakeSocketFactory()
    factory.make_server()
    bootstrap = _bootstrap(tmp_path, factory, clock=FakeClock(step=10.0))
    # No socket file: connect raises OSError; the clock jumps past the deadline.
    with pytest.raises(EngineUnreachableError):
        bootstrap.handshake(timeout=3.0)


@pytest.mark.unit
def test_command_client_request_round_trip(tmp_path: Path) -> None:
    """CommandClient sends a command and reads the typed response."""
    factory = FakeSocketFactory()
    factory.make_server()
    client = CommandClient(tmp_path / "sock", socket_factory=factory)
    (tmp_path / "sock").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "sock").write_text("", encoding="utf-8")
    _server_reply(factory, _status_response(thread_id="t9"))

    response = client.request(StatusCommand(workspace=str(tmp_path)))
    assert isinstance(response, StatusResponse)
    assert response.thread_id == "t9"
    # The command frame reached the server.
    assert factory.server is not None
    assert factory.server._buf


@pytest.mark.unit
def test_command_client_unreachable(tmp_path: Path) -> None:
    """CommandClient raises EngineUnreachableError when the socket is absent."""
    factory = FakeSocketFactory()
    factory.make_server()
    client = CommandClient(tmp_path / "missing.sock", socket_factory=factory)
    with pytest.raises(EngineUnreachableError):
        client.request(StatusCommand(workspace=str(tmp_path)))


@pytest.mark.unit
def test_spawn_daemon_uses_module_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_spawn_daemon launches the daemon module with the workspace flag."""
    import dev_harness.engine.bootstrap as bootstrap_mod

    calls: list[list[str]] = []

    class FakePopen:
        def __init__(self, argv: list[str], **kwargs: object) -> None:
            calls.append(argv)

    monkeypatch.setattr(bootstrap_mod.subprocess, "Popen", FakePopen)
    EngineBootstrap._spawn_daemon(tmp_path)
    assert calls
    assert "--workspace" in calls[0]
    assert str(tmp_path) in calls[0]


@pytest.mark.unit
def test_main_self_check_ok(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """main() with --self-check prints the engine status and exits 0."""
    import dev_harness.engine.bootstrap as bootstrap_mod

    factory = FakeSocketFactory()
    factory.make_server()
    bootstrap = _bootstrap(tmp_path, factory)
    _touch_socket(bootstrap)
    _server_reply(factory, _status_response(thread_id="t7"))

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(bootstrap_mod, "EngineBootstrap", lambda _ws: bootstrap)
    try:
        rc = bootstrap_mod.main(["--workspace", str(tmp_path), "--self-check"])
    finally:
        monkeypatch.undo()
    assert rc == 0
    out = capsys.readouterr().out
    assert "engine ok" in out
    assert "t7" in out


@pytest.mark.unit
def test_main_unavailable_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """main() exits 1 when the daemon cannot be reached."""
    import dev_harness.engine.bootstrap as bootstrap_mod

    factory = FakeSocketFactory()
    factory.make_server()
    bootstrap = _bootstrap(tmp_path, factory, clock=FakeClock(step=10.0))
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(bootstrap_mod, "EngineBootstrap", lambda _ws: bootstrap)
    try:
        rc = bootstrap_mod.main(["--workspace", str(tmp_path), "--self-check"])
    finally:
        monkeypatch.undo()
    assert rc == 1
    assert "engine unavailable" in capsys.readouterr().err


@pytest.mark.unit
def test_handshake_timeout_constant() -> None:
    """The handshake SLO is 3 seconds per the validation matrix."""
    assert HANDSHAKE_TIMEOUT == 3.0

@pytest.mark.unit
def test_request_failure_raises_unreachable(tmp_path: Path) -> None:
    """A failed request (send/read error) raises EngineUnreachableError."""
    factory = FakeSocketFactory()
    factory.make_server()
    client = CommandClient(tmp_path / "sock", socket_factory=factory)
    (tmp_path / "sock").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "sock").write_text("", encoding="utf-8")
    # No reply queued: read_response_frame hits EOF -> ValueError -> unreachable.
    with pytest.raises(EngineUnreachableError):
        client.request(StatusCommand(workspace=str(tmp_path)))


@pytest.mark.unit
def test_close_with_connection(tmp_path: Path) -> None:
    """close() closes an open connection and clears it."""
    factory = FakeSocketFactory()
    factory.make_server()
    client = CommandClient(tmp_path / "sock", socket_factory=factory)
    (tmp_path / "sock").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "sock").write_text("", encoding="utf-8")
    _server_reply(factory, _status_response())
    client.request(StatusCommand(workspace=str(tmp_path)))
    assert client._conn is not None
    client.close()
    assert client._conn is None


@pytest.mark.unit
def test_handshake_unexpected_response_type(tmp_path: Path) -> None:
    """A non-StatusResponse handshake reply raises EngineUnreachableError."""
    from dev_harness.engine.commands import ErrorResponse

    factory = FakeSocketFactory()
    factory.make_server()
    bootstrap = _bootstrap(tmp_path, factory)
    _touch_socket(bootstrap)
    factory.queue_reply(ErrorResponse(error="boom"))

    with pytest.raises(EngineUnreachableError):
        bootstrap.handshake()


@pytest.mark.unit
def test_close_tolerates_oserror(tmp_path: Path) -> None:
    """close() swallows OSError from the underlying socket."""

    class FailingCloseSocket(FakeSocket):
        def close(self) -> None:
            raise OSError("boom")

    factory = FakeSocketFactory()
    factory.make_server()
    client = CommandClient(tmp_path / "sock", socket_factory=factory)
    (tmp_path / "sock").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "sock").write_text("", encoding="utf-8")
    _server_reply(factory, _status_response())
    client.request(StatusCommand(workspace=str(tmp_path)))
    client._conn = FailingCloseSocket()
    client.close()
    assert client._conn is None


@pytest.mark.unit
def test_handshake_timeout_raises_last_error(tmp_path: Path) -> None:
    """The handshake retry loop raises the last error once the deadline passes."""
    factory = FakeSocketFactory()
    factory.make_server()
    bootstrap = _bootstrap(tmp_path, factory, clock=FakeClock(step=10.0))
    # No socket file and no reply: every attempt fails until the deadline.
    with pytest.raises(EngineUnreachableError):
        bootstrap.handshake()
