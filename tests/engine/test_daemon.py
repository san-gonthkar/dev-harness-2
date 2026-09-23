"""Engine daemon tests (V11 5.1) — bind, signal handling, socket cleanup.

On Windows the AF_UNIX transport is unavailable, so the socket layer is
simulated with FakeSocket/FakeSocketFactory (same pattern as
tests/broker/test_daemon.py and tests/ipc/test_server.py).
"""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path
from typing import Self

import pytest

from dev_harness.engine import daemon as daemon_mod
from dev_harness.engine.daemon import EngineDaemon
from dev_harness.ipc import server as server_mod
from dev_harness.paths import derive_paths

pytestmark = pytest.mark.unit


class FakeSocket:
    """A minimal in-memory socket pair for testing the engine server."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path
        self.bound = False
        self.listened = False
        self.closed = False
        self._peer: FakeSocket | None = None
        self._buf = b""
        self._conns: list[FakeSocket] = []
        self._accept_wait = threading.Event()

    def bind(self, path: str) -> None:
        self.bound = True
        self.path = path
        Path(path).write_text("", encoding="utf-8")

    def listen(self, backlog: int) -> None:
        self.listened = True

    def accept(self) -> tuple[FakeSocket, str]:
        while not self._conns:
            self._accept_wait.wait(0.01)
        conn = self._conns.pop(0)
        return conn, ""

    def connect(self, path: str) -> None:
        self.path = path

    def settimeout(self, timeout: float) -> None:
        pass

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


def _make_daemon(
    tmp_path: Path, factory: FakeSocketFactory, **kwargs: object
) -> EngineDaemon:
    return EngineDaemon(
        tmp_path / "ws",
        socket_factory=factory,
        **kwargs,
    )


@pytest.mark.unit
def test_binds_derived_socket(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The daemon binds the workspace-scoped derived socket path."""
    monkeypatch.setattr(server_mod, "require_posix", lambda: None)
    factory = FakeSocketFactory()
    daemon = _make_daemon(tmp_path, factory)
    daemon.start()
    assert factory.server is not None
    assert factory.server.bound is True
    assert factory.server.listened is True
    # The bound path is the derived workspace-scoped socket.
    expected = derive_paths(tmp_path / "ws").socket_path
    assert factory.server.path == str(expected)
    daemon.stop()


@pytest.mark.unit
def test_binds_under_two_seconds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Binding the socket completes well under the 2s SLO."""
    monkeypatch.setattr(server_mod, "require_posix", lambda: None)
    factory = FakeSocketFactory()
    daemon = _make_daemon(tmp_path, factory)
    started = time.monotonic()
    daemon.start()
    elapsed = time.monotonic() - started
    assert elapsed < 2.0
    assert daemon.running is True
    daemon.stop()


@pytest.mark.unit
def test_sigterm_exits_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """SIGTERM triggers a clean shutdown with exit code 0."""
    monkeypatch.setattr(server_mod, "require_posix", lambda: None)
    factory = FakeSocketFactory()
    daemon = _make_daemon(tmp_path, factory)
    daemon.start()

    # Simulate the signal arriving: invoke the installed handler directly.
    handler = daemon._make_signal_handler(15)
    handler(15, None)
    assert daemon.signal_received == 15
    assert daemon.running is False
    assert daemon.run() == 0
    daemon.stop()


@pytest.mark.unit
def test_sigint_requests_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SIGINT also requests a clean shutdown."""
    monkeypatch.setattr(server_mod, "require_posix", lambda: None)
    factory = FakeSocketFactory()
    daemon = _make_daemon(tmp_path, factory)
    daemon.start()
    handler = daemon._make_signal_handler(2)
    handler(2, None)
    assert daemon.signal_received == 2
    assert daemon.running is False
    daemon.stop()


@pytest.mark.unit
def test_socket_removed_on_drain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drain unlinks the socket file."""
    monkeypatch.setattr(server_mod, "require_posix", lambda: None)
    factory = FakeSocketFactory()
    daemon = _make_daemon(tmp_path, factory)
    daemon.start()
    sock_path = daemon.socket_path
    # The fake bind writes the socket file to disk.
    assert sock_path.exists()
    daemon.drain()
    assert not sock_path.exists()
    assert daemon.running is False
    assert daemon.draining is False


@pytest.mark.unit
def test_stop_closes_server_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """stop() closes the server socket and marks the daemon not running."""
    monkeypatch.setattr(server_mod, "require_posix", lambda: None)
    factory = FakeSocketFactory()
    daemon = _make_daemon(tmp_path, factory)
    daemon.start()
    assert factory.server is not None
    daemon.stop()
    assert factory.server.closed is True
    assert daemon.running is False


@pytest.mark.unit
def test_run_blocks_until_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run() returns the exit code once shutdown is requested."""
    monkeypatch.setattr(server_mod, "require_posix", lambda: None)
    factory = FakeSocketFactory()
    daemon = _make_daemon(tmp_path, factory)
    daemon.start()

    result: list[int] = []

    def _run() -> None:
        result.append(daemon.run())

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    # Give the loop a moment to start, then request shutdown.
    time.sleep(0.05)
    daemon.request_shutdown(exit_code=0)
    t.join(timeout=2.0)
    assert result == [0]
    daemon.stop()


@pytest.mark.unit
def test_install_signal_handlers_restores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Installed signal handlers are restored after run() completes."""
    monkeypatch.setattr(server_mod, "require_posix", lambda: None)
    factory = FakeSocketFactory()
    daemon = _make_daemon(tmp_path, factory)
    daemon.start()
    daemon._install_signal_handlers()
    assert len(daemon._original_handlers) >= 1
    daemon._restore_signal_handlers()
    assert daemon._original_handlers == {}
    daemon.stop()


@pytest.mark.unit
def test_drain_without_server_is_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """drain() with no bound server is a safe no-op."""
    monkeypatch.setattr(server_mod, "require_posix", lambda: None)
    daemon = _make_daemon(tmp_path, FakeSocketFactory())
    daemon.drain()
    assert daemon.running is False
    assert daemon.draining is False


@pytest.mark.unit
def test_stop_without_server_is_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """stop() with no bound server is a safe no-op."""
    monkeypatch.setattr(server_mod, "require_posix", lambda: None)
    daemon = _make_daemon(tmp_path, FakeSocketFactory())
    daemon.stop()
    assert daemon.running is False


@pytest.mark.unit
def test_signal_install_skips_on_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Signal installation tolerates environments where signals cannot be set."""
    monkeypatch.setattr(server_mod, "require_posix", lambda: None)
    monkeypatch.setattr(
        daemon_mod.signal,
        "signal",
        lambda sig, handler: (_ for _ in ()).throw(ValueError()),
    )
    daemon = _make_daemon(tmp_path, FakeSocketFactory())
    daemon._install_signal_handlers()
    # getsignal() succeeded (handlers recorded) but signal.signal() failed,
    # so no handler was installed — and no exception escaped.
    assert len(daemon._original_handlers) == 2
    daemon.stop()


@pytest.mark.unit
def test_daemon_error_is_harness_error() -> None:
    """EngineDaemonError subclasses HarnessError with a remediation."""
    from dev_harness.contracts.errors import HarnessError

    assert issubclass(daemon_mod.EngineDaemonError, HarnessError)
    assert daemon_mod.EngineDaemonError.remediation
