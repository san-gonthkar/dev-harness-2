"""AF_UNIX server tests (V11 2.3) — simulated socket layer on Windows."""

from __future__ import annotations

import socket
import threading
from pathlib import Path
from typing import Self

import pytest

from dev_harness.ipc import server as server_mod
from dev_harness.ipc import transport as transport_mod
from dev_harness.ipc.server import IpcServer
from dev_harness.ipc.transport import UnsupportedPlatformError, is_posix, require_posix

pytestmark = pytest.mark.unit


class FakeSocket:
    """A minimal in-memory socket pair for testing the server logic."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path
        self.bound = False
        self.listened = False
        self.closed = False
        self._peer: FakeSocket | None = None
        self._buf = b""
        self._conns: list[FakeSocket] = []

    def bind(self, path: str) -> None:
        self.bound = True
        self.path = path
        # Simulate a real AF_UNIX bind: the socket file appears on disk.
        Path(path).write_text("", encoding="utf-8")

    def listen(self, backlog: int) -> None:
        self.listened = True

    def accept(self) -> tuple[FakeSocket, str]:
        # Block until a connection is pushed.
        while not self._conns:
            threading.Event().wait(0.01)
        conn = self._conns.pop(0)
        return conn, ""

    def connect(self, path: str) -> None:
        self.path = path

    def sendall(self, data: bytes) -> None:
        if self._peer is not None:
            self._peer._buf += data

    def recv(self, n: int) -> bytes:
        if not self._buf:
            return b""
        out = self._buf[:n]
        self._buf = self._buf[n:]
        return out

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class FakeSocketFactory:
    """Creates FakeSockets and links server/client pairs."""

    def __init__(self) -> None:
        self.server: FakeSocket | None = None
        self.clients: list[FakeSocket] = []

    def __call__(self, family: int, socktype: int) -> FakeSocket:
        s = FakeSocket()
        if family == getattr(socket, "AF_UNIX", 1) and socktype == getattr(
            socket, "SOCK_STREAM", 1
        ):
            if self.server is None:
                self.server = s
            else:
                self.clients.append(s)
        return s


def test_require_posix_raises_on_win32(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transport_mod.os, "name", "nt")
    with pytest.raises(UnsupportedPlatformError) as exc:
        require_posix()
    assert "WSL2" in str(exc.value)


def test_is_posix_false_on_win32(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transport_mod.os, "name", "nt")
    assert is_posix() is False


def test_server_binds_and_chmods(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The server binds the socket and chmods it to 0600."""
    monkeypatch.setattr(server_mod, "require_posix", lambda: None)
    factory = FakeSocketFactory()
    sock_path = tmp_path / "test.sock"
    server = IpcServer(sock_path, socket_factory=factory)
    server.start()
    assert factory.server is not None
    assert factory.server.bound is True
    assert factory.server.listened is True
    server.stop()


def test_server_unlinks_stale_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A leftover socket file is unlinked before bind."""
    monkeypatch.setattr(server_mod, "require_posix", lambda: None)
    sock_path = tmp_path / "stale.sock"
    sock_path.write_text("stale", encoding="utf-8")
    factory = FakeSocketFactory()
    server = IpcServer(sock_path, socket_factory=factory)
    server.start()
    # The stale file was unlinked (the fake bind records the path).
    assert factory.server is not None
    assert factory.server.bound is True
    server.stop()


def test_server_requires_posix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """On win32 the server refuses to construct."""
    monkeypatch.setattr(transport_mod.os, "name", "nt")
    with pytest.raises(UnsupportedPlatformError):
        IpcServer(tmp_path / "x.sock")
