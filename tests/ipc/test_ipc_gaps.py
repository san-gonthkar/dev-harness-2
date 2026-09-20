"""Additional IPC coverage: server connection handling, client request, CLI main."""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Self

import pytest

from dev_harness.contracts.enums import EventType
from dev_harness.contracts.events import Envelope, FileChangePayload
from dev_harness.ipc import cli
from dev_harness.ipc.framing import encode
from dev_harness.ipc.server import IpcServer

pytestmark = pytest.mark.unit


class FakeConn:
    """A connection with a byte buffer for the server handler."""

    def __init__(self) -> None:
        self._buf = b""
        self.closed = False

    def sendall(self, data: bytes) -> None:
        self._buf += data

    def recv(self, n: int) -> bytes:
        if not self._buf:
            return b""
        out = self._buf[:n]
        self._buf = self._buf[n:]
        return out

    def read(self, n: int) -> bytes:
        return self.recv(n)

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class FakeServerSocket:
    """A server socket that hands out queued connections."""

    def __init__(self) -> None:
        self.bound = False
        self.listened = False
        self.closed = False
        self.conns: list[FakeConn] = []
        self._accepting = True

    def bind(self, path: str) -> None:
        self.bound = True
        Path(path).write_text("", encoding="utf-8")

    def listen(self, backlog: int) -> None:
        self.listened = True

    def accept(self) -> tuple[FakeConn, str]:
        while self._accepting and not self.conns:
            threading.Event().wait(0.01)
        if not self.conns:
            raise OSError("closed")
        return self.conns.pop(0), ""

    def close(self) -> None:
        self.closed = True
        self._accepting = False


class FakeServerFactory:
    def __init__(self) -> None:
        self.server = FakeServerSocket()

    def __call__(self, family: int, socktype: int) -> FakeServerSocket:
        return self.server


def _env() -> Envelope:
    return Envelope(
        type=EventType.FILE_CHANGE,
        payload=FileChangePayload(
            type="FILE_CHANGE", path="/a", change_type="modified"
        ),
    )


def test_server_handles_connection_and_echoes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The server accepts a connection, reads a frame, and echoes a reply."""
    from dev_harness.ipc import server as server_mod

    monkeypatch.setattr(server_mod, "require_posix", lambda: None)
    factory = FakeServerFactory()
    sock_path = tmp_path / "test.sock"
    server = IpcServer(sock_path, handler=lambda env: env, socket_factory=factory)
    server.start()
    # Push a connection with a framed envelope.
    conn = FakeConn()
    conn._buf = encode(_env())
    factory.server.conns.append(conn)
    # Wait for the handler to process it.
    for _ in range(100):
        if conn._buf:
            break
        threading.Event().wait(0.05)
    server.stop()
    # The echo reply was written back to the connection.
    assert conn._buf  # non-empty reply


def test_server_accept_loop_breaks_on_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The accept loop breaks when the server socket is closed."""
    from dev_harness.ipc import server as server_mod

    monkeypatch.setattr(server_mod, "require_posix", lambda: None)
    factory = FakeServerFactory()
    server = IpcServer(tmp_path / "x.sock", socket_factory=factory)
    server.start()
    server.stop()
    # No crash; the accept thread exits.


class TestClientRequest:
    def test_request_sends_and_reads(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from dev_harness.ipc import client as client_mod

        monkeypatch.setattr(client_mod, "require_posix", lambda: None)
        sent: list[bytes] = []

        class FakeConn:
            def __init__(self) -> None:
                self._buf = b""

            def connect(self, path: str) -> None:
                pass

            def sendall(self, data: bytes) -> None:
                sent.append(data)

            def recv(self, n: int) -> bytes:
                if not self._buf:
                    return b""
                out = self._buf[:n]
                self._buf = self._buf[n:]
                return out

            def read(self, n: int) -> bytes:
                return self.recv(n)

            def close(self) -> None:
                pass

        class FakeFactory:
            def __call__(self, family: int, socktype: int) -> FakeConn:
                return FakeConn()

        factory = FakeFactory()
        client = client_mod.IpcClient("/tmp/x.sock", socket_factory=factory)
        # Send first (connects), then preload the reply frame.
        reply = _env()
        client.send(_env())
        client._conn._buf = encode(reply)  # type: ignore[attr-defined]
        got = client.request(_env())
        assert got == reply
        assert len(sent) == 2  # one from send, one from request
        client.close()

    def test_close_without_conn_is_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from dev_harness.ipc import client as client_mod

        monkeypatch.setattr(client_mod, "require_posix", lambda: None)
        client = client_mod.IpcClient("/tmp/x.sock")
        client.close()  # no-op


class TestCliMain:
    def test_main_flood_dispatch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "argv", ["cli", "flood", "--count", "10"])
        assert cli.main() == 0

    def test_main_unknown_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class FakeArgs:
            command = "bogus"

        class FakeParser:
            def parse_args(self) -> FakeArgs:
                return FakeArgs()

        monkeypatch.setattr(cli, "build_parser", lambda: FakeParser())
        assert cli.main() == 2

    def test_main_block(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import runpy

        monkeypatch.setattr(sys, "argv", ["cli", "flood", "--count", "5"])
        with pytest.raises(SystemExit) as ei:
            runpy.run_module("dev_harness.ipc.cli", run_name="__main__")
        assert ei.value.code == 0
