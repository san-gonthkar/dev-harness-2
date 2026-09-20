"""IPC client tests (V11 2.4) — simulated socket layer."""

from __future__ import annotations

import itertools
import time

import pytest

from dev_harness.contracts.enums import EventType
from dev_harness.contracts.events import Envelope, FileChangePayload
from dev_harness.ipc import client as client_mod
from dev_harness.ipc.client import MAX_RETRY_DELAY, IpcClient
from dev_harness.ipc.transport import UnsupportedPlatformError

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _posix(monkeypatch: pytest.MonkeyPatch) -> None:
    """Allow the client to construct on Windows."""
    monkeypatch.setattr(client_mod, "require_posix", lambda: None)


class FakeConn:
    """A fake connection that can fail connect/send on demand."""

    def __init__(self, *, fail_connect: bool = False, fail_send: bool = False) -> None:
        self.fail_connect = fail_connect
        self.fail_send = fail_send
        self.sent: list[bytes] = []
        self.closed = False
        self._buf = b""

    def connect(self, path: str) -> None:
        if self.fail_connect:
            raise OSError("connection refused")
        self.path = path

    def sendall(self, data: bytes) -> None:
        if self.fail_send:
            raise OSError("broken pipe")
        self.sent.append(data)

    def recv(self, n: int) -> bytes:
        if not self._buf:
            return b""
        out = self._buf[:n]
        self._buf = self._buf[n:]
        return out

    def close(self) -> None:
        self.closed = True


class FakeClientFactory:
    """Creates FakeConns; first N fail connect, then succeed."""

    def __init__(self, fail_connects: int = 0) -> None:
        self.fail_connects = fail_connects
        self.conns: list[FakeConn] = []
        self.delays: list[float] = []

    def __call__(self, family: int, socktype: int) -> FakeConn:
        if self.fail_connects > 0:
            self.fail_connects -= 1
            conn = FakeConn(fail_connect=True)
        else:
            conn = FakeConn()
        self.conns.append(conn)
        return conn


def test_client_retries_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    """The client retries connect until it succeeds."""
    factory = FakeClientFactory(fail_connects=2)
    client = IpcClient("/tmp/x.sock", socket_factory=factory, max_retry_delay=0.01)
    # Patch time.sleep to avoid real delays.
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    env = Envelope(
        type=EventType.FILE_CHANGE,
        payload=FileChangePayload(
            type="FILE_CHANGE", path="/a", change_type="modified"
        ),
    )
    client.send(env)
    assert len(factory.conns) == 3  # 2 failures + 1 success
    assert factory.conns[-1].sent  # the envelope was sent
    client.close()


def test_retry_delays_non_decreasing_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retry intervals are non-decreasing and capped at 5s."""
    factory = FakeClientFactory(fail_connects=10)
    client = IpcClient(
        "/tmp/x.sock", socket_factory=factory, max_retry_delay=MAX_RETRY_DELAY
    )
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    env = Envelope(
        type=EventType.FILE_CHANGE,
        payload=FileChangePayload(
            type="FILE_CHANGE", path="/a", change_type="modified"
        ),
    )
    client.send(env)
    assert len(sleeps) == 10
    for a, b in itertools.pairwise(sleeps):
        assert b >= a  # non-decreasing
    assert max(sleeps) <= MAX_RETRY_DELAY  # capped


def test_client_survives_server_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    """A send on a dead connection reconnects and delivers."""
    factory = FakeClientFactory()
    client = IpcClient("/tmp/x.sock", socket_factory=factory, max_retry_delay=0.01)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    env = Envelope(
        type=EventType.FILE_CHANGE,
        payload=FileChangePayload(
            type="FILE_CHANGE", path="/a", change_type="modified"
        ),
    )
    client.send(env)
    # Simulate server death: the connection now fails sends.
    factory.conns[-1].fail_send = True
    client.send(env)
    # A new connection was created and the envelope delivered.
    assert len(factory.conns) == 2
    assert factory.conns[-1].sent
    client.close()


def test_client_requires_posix(monkeypatch: pytest.MonkeyPatch) -> None:
    from dev_harness.ipc import transport as transport_mod

    # Undo the autouse fixture patch, then force win32.
    monkeypatch.setattr(client_mod, "require_posix", transport_mod.require_posix)
    monkeypatch.setattr(transport_mod.os, "name", "nt")
    with pytest.raises(UnsupportedPlatformError):
        IpcClient("/tmp/x.sock")
