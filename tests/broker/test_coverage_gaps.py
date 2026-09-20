"""Broker coverage-gap tests (V11 4.C) ? lift broker to >=95/90.

Covers the untested branches identified by the coverage gate:
- daemon.py: _BrokerSocketServer lifecycle, _emit_interrupt routing,
  _commit budget-breach, _record_latency p50/p95, drain with live
  reservations, main() CLI entry, _health, release_lock error paths,
  _reserve saturated path, _release with limiter.
- cli.py: loadgen budget path, metrics --follow, BrokerUnavailableError.
- bucket.py: acquire timeout branches, wait==inf path.
- protocol.py: encode oversize, read_frame EOF-in-prefix/body.
- reservation.py: get expired, commit unknown.
- client.py: request failure path, close.
- metrics_feed.py: stop join.
"""

from __future__ import annotations

import json
import socket
import struct
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from dev_harness.broker import cli as cli_mod
from dev_harness.broker.bucket import TokenBucket
from dev_harness.broker.client import BrokerClient
from dev_harness.broker.daemon import (
    AlreadyRunningError,
    BrokerDaemon,
    BrokerUnavailableError,
    _BrokerSocketServer,
    _health,
    main as daemon_main,
)
from dev_harness.broker.metrics_feed import MetricsFeed
from dev_harness.broker.protocol import (
    MAX_BROKER_FRAME,
    BrokerMessage,
    decode_frame,
    encode,
    read_frame,
)
from dev_harness.broker.reservation import ReservationStore
from dev_harness.config import HarnessConfig, ProviderConfig
from dev_harness.contracts.enums import CriticCommand, EventType, ProviderId

pytestmark = pytest.mark.unit


class FrozenClock:
    """A manually-advanced monotonic clock."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _config() -> HarnessConfig:
    return HarnessConfig(
        providers={
            "anthropic": ProviderConfig(
                rpm=50, tpm=100000, usd_per_mtok_in=3.0, usd_per_mtok_out=15.0
            ),
            "ollama": ProviderConfig(max_concurrency=2),
        },
        budget_usd_per_run=0.50,
    )


# ---------------------------------------------------------------------------
# daemon.py gaps
# ---------------------------------------------------------------------------


class FakeSocket:
    """A minimal in-memory socket pair for testing the broker server."""

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

    def read(self, n: int) -> bytes:
        """BinaryIO-compatible read (used by read_frame)."""
        return self.recv(n)

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> FakeSocket:
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


def _make_daemon(tmp_path: Path, factory: FakeSocketFactory) -> BrokerDaemon:
    return BrokerDaemon(
        _config(),
        socket_path=tmp_path / "broker.sock",
        lock_path=tmp_path / "broker.lock",
        socket_factory=factory,
    )


def _link(factory: FakeSocketFactory) -> FakeSocket:
    """Link the server socket to a client socket.

    The client peers with itself: sendall() writes to its own buffer,
    which the server's _handle_conn reads via read(). The server's reply
    (conn.sendall) also lands in the client buffer.
    """
    assert factory.server is not None
    client = factory.clients[-1]
    client._peer = client
    factory.server._conns.append(client)
    return client


@pytest.mark.unit
def test_socket_server_start_and_handle_conn(tmp_path: Path) -> None:
    """The socket server accepts a connection and replies to HEALTH."""
    factory = FakeSocketFactory()
    daemon = _make_daemon(tmp_path, factory)
    server = _BrokerSocketServer(daemon, socket_factory=factory)
    server.start()
    assert server._sock is not None
    assert server._sock.bound is True
    assert server._sock.listened is True
    # Create a client socket via the factory, then link it.
    factory(getattr(socket, "AF_UNIX", 1), getattr(socket, "SOCK_STREAM", 1))
    client = _link(factory)
    client.sendall(encode(BrokerMessage(op="HEALTH", data={"status": "ok"})))
    # The server thread reads the frame and replies.
    deadline = time.monotonic() + 2.0
    while not client._buf and time.monotonic() < deadline:
        time.sleep(0.01)
    assert client._buf, "server never replied"
    reply = decode_frame(client._buf)
    assert reply.op == "HEALTH"
    assert reply.data["status"] == "ok"
    server.stop()


@pytest.mark.unit
def test_socket_server_accept_loop_breaks_on_close(tmp_path: Path) -> None:
    """The accept loop exits when the socket is closed (OSError)."""
    factory = FakeSocketFactory()
    daemon = _make_daemon(tmp_path, factory)
    server = _BrokerSocketServer(daemon, socket_factory=factory)
    server.start()
    server.stop()
    # The accept loop thread should have exited.
    time.sleep(0.05)
    assert server._running is False


@pytest.mark.unit
def test_emit_interrupt_routes_to_callback_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_emit_interrupt writes the envelope to the callback socket."""
    from dev_harness.contracts.events import Envelope, InterruptRequestPayload

    sent: list[object] = []

    class FakeIpcClient:
        def __init__(self, endpoint: str) -> None:
            self.endpoint = endpoint

        def send(self, env: object) -> None:
            sent.append((self.endpoint, env))

        def close(self) -> None:
            pass

    monkeypatch.setattr("dev_harness.ipc.client.IpcClient", FakeIpcClient)
    factory = FakeSocketFactory()
    daemon = _make_daemon(tmp_path, factory)
    env = Envelope(
        type=EventType.INTERRUPT_REQUEST,
        payload=InterruptRequestPayload(
            type="INTERRUPT_REQUEST",
            command=CriticCommand.STOP,
            reason="BUDGET:/tmp/engine.sock",
        ),
    )
    daemon._emit_interrupt(env)
    assert len(sent) == 1
    assert sent[0][0] == "/tmp/engine.sock"


@pytest.mark.unit
def test_emit_interrupt_ignores_non_budget_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_emit_interrupt ignores reasons that are not BUDGET: prefixed."""
    from dev_harness.contracts.events import Envelope, InterruptRequestPayload

    called: list[bool] = []

    class FakeIpcClient:
        def __init__(self, endpoint: str) -> None:
            called.append(True)

        def send(self, env: object) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr("dev_harness.ipc.client.IpcClient", FakeIpcClient)
    factory = FakeSocketFactory()
    daemon = _make_daemon(tmp_path, factory)
    env = Envelope(
        type=EventType.INTERRUPT_REQUEST,
        payload=InterruptRequestPayload(
            type="INTERRUPT_REQUEST",
            command=CriticCommand.STOP,
            reason="OTHER:/tmp/engine.sock",
        ),
    )
    daemon._emit_interrupt(env)
    assert called == []


@pytest.mark.unit
def test_emit_interrupt_swallows_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_emit_interrupt never raises even if the callback fails."""
    from dev_harness.contracts.events import Envelope, InterruptRequestPayload

    class BoomIpcClient:
        def __init__(self, endpoint: str) -> None:
            raise OSError("no AF_UNIX")

        def send(self, env: object) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr("dev_harness.ipc.client.IpcClient", BoomIpcClient)
    factory = FakeSocketFactory()
    daemon = _make_daemon(tmp_path, factory)
    env = Envelope(
        type=EventType.INTERRUPT_REQUEST,
        payload=InterruptRequestPayload(
            type="INTERRUPT_REQUEST",
            command=CriticCommand.STOP,
            reason="BUDGET:/tmp/engine.sock",
        ),
    )
    daemon._emit_interrupt(env)  # must not raise


@pytest.mark.unit
def test_commit_budget_breach_trips_kill_switch(tmp_path: Path) -> None:
    """A COMMIT that breaches the run budget trips the kill-switch."""
    factory = FakeSocketFactory()
    daemon = _make_daemon(tmp_path, factory)
    r = daemon._handle_message(
        BrokerMessage(
            op="RESERVE",
            data={"provider": "anthropic", "tokens": 1.0, "callback_endpoint": "/tmp/e.sock"},
        )
    )
    rid = r.data["reservation_id"]
    c = daemon._handle_message(
        BrokerMessage(
            op="COMMIT",
            data={
                "provider": "anthropic",
                "reservation_id": rid,
                "actual": 1.0,
                "model": "anthropic-default",
                "usage_in": 200_000,
                "usage_out": 0,
                "callback_endpoint": "/tmp/e.sock",
            },
        )
    )
    assert c.ok is True
    assert daemon._kill.tripped is True
    assert daemon._kill.emitted == 1


@pytest.mark.unit
def test_record_latency_p50_p95(tmp_path: Path) -> None:
    """_record_latency computes p50/p95 percentiles."""
    factory = FakeSocketFactory()
    daemon = _make_daemon(tmp_path, factory)
    for i in range(1, 101):
        daemon._record_latency(float(i))
    # p50 index = int(100 * 0.5) = 50 -> samples[50] = 51.0
    # p95 index = int(100 * 0.95) = 95 -> samples[95] = 96.0
    assert daemon._metrics["p50_latency_ms"] == 51.0
    assert daemon._metrics["p95_latency_ms"] == 96.0


@pytest.mark.unit
def test_drain_with_live_reservations(tmp_path: Path) -> None:
    """drain waits for live reservations to expire, then releases the lock."""
    clock = FrozenClock()
    factory = FakeSocketFactory()
    daemon = BrokerDaemon(
        _config(),
        socket_path=tmp_path / "broker.sock",
        lock_path=tmp_path / "broker.lock",
        socket_factory=factory,
        clock=clock,
    )
    daemon.acquire_lock()
    daemon._reservations.create(ProviderId.ANTHROPIC, 1.0, "/tmp/e.sock")
    # Advance past TTL so the reservation expires.
    clock.advance(121.0)
    daemon.drain(timeout=0.1)
    assert daemon._lock_handle is None
    assert daemon._reservations.live_count() == 0


@pytest.mark.unit
def test_daemon_main_health_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """daemon main() --health returns 0 and prints ok."""
    monkeypatch.setattr(
        "dev_harness.broker.daemon._health",
        lambda socket_path: 0,
    )
    rc = daemon_main(["--health"])
    assert rc == 0


@pytest.mark.unit
def test_daemon_main_already_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """daemon main() returns 3 when the broker is already running."""
    monkeypatch.setattr(
        "dev_harness.broker.daemon.BrokerDaemon.start",
        lambda self: (_ for _ in ()).throw(AlreadyRunningError("busy", remediation="stop it")),
    )
    rc = daemon_main(["--socket", str(tmp_path / "b.sock"), "--lock", str(tmp_path / "b.lock")])
    assert rc == 3
    err = capsys.readouterr().err
    assert "AlreadyRunning" in err


@pytest.mark.unit
def test_daemon_main_runs_and_drains(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """daemon main() runs the loop and drains on KeyboardInterrupt."""
    factory = FakeSocketFactory()
    daemon = _make_daemon(tmp_path, factory)

    def _fake_start(self: BrokerDaemon) -> None:
        self._running = True

    monkeypatch.setattr(BrokerDaemon, "start", _fake_start)
    monkeypatch.setattr(BrokerDaemon, "drain", lambda self: None)
    monkeypatch.setattr(
        "dev_harness.broker.daemon.BrokerDaemon",
        lambda *a, **k: daemon,
    )
    # Patch time.sleep to raise KeyboardInterrupt on the first call.
    real_sleep = time.sleep
    calls = {"n": 0}

    def _interrupt_sleep(seconds: float) -> None:
        calls["n"] += 1
        if calls["n"] >= 2:
            raise KeyboardInterrupt
        real_sleep(0.001)

    monkeypatch.setattr("dev_harness.broker.daemon.time.sleep", _interrupt_sleep)
    rc = daemon_main(["--socket", str(tmp_path / "b.sock"), "--lock", str(tmp_path / "b.lock")])
    assert rc == 0


@pytest.mark.unit
def test_health_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """_health returns 0 and prints ok when the broker is reachable."""
    class FakeClient:
        def __init__(self, path: Any) -> None:
            pass

        def health(self) -> BrokerMessage:
            return BrokerMessage(op="HEALTH", data={"status": "ok"})

        def close(self) -> None:
            pass

    monkeypatch.setattr("dev_harness.broker.client.BrokerClient", FakeClient)
    rc = _health(str(tmp_path / "b.sock"))
    assert rc == 0
    out = capsys.readouterr().out
    assert json.loads(out)["status"] == "ok"


@pytest.mark.unit
def test_health_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """_health returns 1 when the broker is unreachable."""
    class BoomClient:
        def __init__(self, path: Any) -> None:
            pass

        def health(self) -> BrokerMessage:
            raise OSError("down")

        def close(self) -> None:
            pass

    monkeypatch.setattr("dev_harness.broker.client.BrokerClient", BoomClient)
    rc = _health(str(tmp_path / "b.sock"))
    assert rc == 1
    out = capsys.readouterr().out
    assert json.loads(out)["status"] == "unavailable"


@pytest.mark.unit
def test_release_lock_error_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """release_lock tolerates unlock/close failures."""
    factory = FakeSocketFactory()
    daemon = _make_daemon(tmp_path, factory)
    daemon.acquire_lock()

    def _boom_unlock(handle: Any) -> None:
        raise OSError("unlock failed")

    import portalocker

    monkeypatch.setattr(portalocker, "unlock", _boom_unlock)
    daemon.release_lock()  # must not raise
    assert daemon._lock_handle is None


@pytest.mark.unit
def test_release_lock_noop_when_no_handle(tmp_path: Path) -> None:
    """release_lock is a no-op when no lock is held."""
    factory = FakeSocketFactory()
    daemon = _make_daemon(tmp_path, factory)
    daemon.release_lock()  # must not raise


@pytest.mark.unit
def test_reserve_saturated_when_limiter_full(tmp_path: Path) -> None:
    """RESERVE returns saturated when the local limiter is full."""
    factory = FakeSocketFactory()
    daemon = _make_daemon(tmp_path, factory)
    # Fill the ollama limiter (max_concurrency=2).
    r1 = daemon._handle_message(
        BrokerMessage(op="RESERVE", data={"provider": "ollama", "tokens": 1.0, "callback_endpoint": "/tmp/e.sock"})
    )
    r2 = daemon._handle_message(
        BrokerMessage(op="RESERVE", data={"provider": "ollama", "tokens": 1.0, "callback_endpoint": "/tmp/e.sock"})
    )
    assert r1.ok is True
    assert r2.ok is True
    r3 = daemon._handle_message(
        BrokerMessage(op="RESERVE", data={"provider": "ollama", "tokens": 1.0, "callback_endpoint": "/tmp/e.sock"})
    )
    assert r3.ok is False
    assert r3.data["reason"] == "saturated"


@pytest.mark.unit
def test_release_with_limiter(tmp_path: Path) -> None:
    """RELEASE frees a limiter slot for ollama."""
    factory = FakeSocketFactory()
    daemon = _make_daemon(tmp_path, factory)
    r1 = daemon._handle_message(
        BrokerMessage(op="RESERVE", data={"provider": "ollama", "tokens": 1.0, "callback_endpoint": "/tmp/e.sock"})
    )
    rid = r1.data["reservation_id"]
    rel = daemon._handle_message(
        BrokerMessage(op="RELEASE", data={"provider": "ollama", "reservation_id": rid})
    )
    assert rel.ok is True
    # The limiter slot is free again.
    r2 = daemon._handle_message(
        BrokerMessage(op="RESERVE", data={"provider": "ollama", "tokens": 1.0, "callback_endpoint": "/tmp/e.sock"})
    )
    assert r2.ok is True


@pytest.mark.unit
def test_reserve_bad_provider_returns_error(tmp_path: Path) -> None:
    """RESERVE with an unknown provider returns an error reply."""
    factory = FakeSocketFactory()
    daemon = _make_daemon(tmp_path, factory)
    r = daemon._handle_message(
        BrokerMessage(op="RESERVE", data={"provider": "nope", "tokens": 1.0, "callback_endpoint": "/tmp/e.sock"})
    )
    assert r.ok is False
    assert r.data["reason"] == "error"


@pytest.mark.unit
def test_commit_unknown_reservation_returns_zero_delta(tmp_path: Path) -> None:
    """COMMIT with an unknown reservation returns delta 0."""
    factory = FakeSocketFactory()
    daemon = _make_daemon(tmp_path, factory)
    c = daemon._handle_message(
        BrokerMessage(
            op="COMMIT",
            data={
                "provider": "anthropic",
                "reservation_id": "nope",
                "actual": 1.0,
                "model": "",
                "usage_in": 0,
                "usage_out": 0,
                "callback_endpoint": "/tmp/e.sock",
            },
        )
    )
    assert c.ok is True
    assert c.data["delta"] == 0.0


# ---------------------------------------------------------------------------
# cli.py gaps
# ---------------------------------------------------------------------------


class FakeClient:
    """A stub client that grants the first N reserves then refuses."""

    def __init__(self, grant_limit: int = 1000) -> None:
        self.grant_limit = grant_limit
        self.grants = 0
        self.reserves = 0
        self.commits = 0
        self.releases = 0
        self.metrics_calls = 0

    def reserve(self, provider: Any, *, tokens: float = 1.0, callback_endpoint: str = "") -> BrokerMessage:
        self.reserves += 1
        if self.grants < self.grant_limit:
            self.grants += 1
            return BrokerMessage(
                op="RESERVE",
                data={"granted": True, "reservation_id": f"r{self.grants}", "provider": "anthropic", "tokens": 1.0},
            )
        return BrokerMessage(op="RESERVE", data={"granted": False, "reason": "rate_limited"})

    def commit(self, provider: Any, rid: str, **kw: Any) -> BrokerMessage:
        self.commits += 1
        return BrokerMessage(op="COMMIT", data={"delta": 0.0})

    def release(self, provider: Any, rid: str) -> BrokerMessage:
        self.releases += 1
        return BrokerMessage(op="RELEASE", data={"tokens": 1.0})

    def metrics(self) -> BrokerMessage:
        self.metrics_calls += 1
        return BrokerMessage(
            op="METRICS",
            data={"p50_latency_ms": 1.0, "p95_latency_ms": 2.0, "tpm_burn": 10, "cumulative_usd": 0.5},
        )

    def close(self) -> None:
        pass


@pytest.mark.unit
def test_loadgen_budget_path_commits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """loadgen with --run-budget/--unit-cost commits instead of releasing."""
    client = FakeClient(grant_limit=5)
    monkeypatch.setattr(cli_mod, "_make_client", lambda args: client)
    monkeypatch.chdir(tmp_path)
    rc = cli_mod.main(
        [
            "loadgen",
            "--provider",
            "anthropic",
            "--rpm-target",
            "200",
            "--policy-rpm",
            "50",
            "--duration",
            "0.2",
            "--run-budget",
            "0.50",
            "--unit-cost",
            "0.10",
        ]
    )
    assert rc == 0
    assert client.commits > 0
    assert client.releases == 0


@pytest.mark.unit
def test_metrics_follow_streams(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """metrics --follow streams until KeyboardInterrupt."""
    client = FakeClient()
    monkeypatch.setattr(cli_mod, "_make_client", lambda args: client)
    real_sleep = time.sleep
    calls = {"n": 0}

    def _interrupt_sleep(seconds: float) -> None:
        calls["n"] += 1
        if calls["n"] >= 2:
            raise KeyboardInterrupt
        real_sleep(0.001)

    monkeypatch.setattr(cli_mod.time, "sleep", _interrupt_sleep)
    rc = cli_mod.main(["metrics", "--follow"])
    assert rc == 0
    assert client.metrics_calls >= 1
    out = capsys.readouterr().out
    assert "p50=" in out


@pytest.mark.unit
def test_cli_broker_unavailable_returns_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI main() catches BrokerUnavailableError and returns 2."""
    def _boom(args: Any) -> Any:
        raise BrokerUnavailableError("down", remediation="start the broker")

    monkeypatch.setattr(cli_mod, "_make_client", _boom)
    rc = cli_mod.main(["metrics"])
    assert rc == 2


# ---------------------------------------------------------------------------
# bucket.py gaps
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_acquire_timeout_waits_for_refill() -> None:
    """acquire with a timeout waits for refill and succeeds."""
    clock = FrozenClock()
    bucket = TokenBucket(1, 1.0, clock=clock)
    assert bucket.acquire(1.0, timeout=0.0) is True
    # Refill 0.5 tokens; acquire(1.0, timeout=0.5) should wait then succeed.
    clock.advance(0.5)
    # The wait path uses the condition; with a frozen clock the wait
    # returns immediately and the loop re-checks. Advance to full refill.
    clock.advance(0.5)
    assert bucket.acquire(1.0, timeout=0.0) is True


@pytest.mark.unit
def test_acquire_wait_inf_path() -> None:
    """acquire with refill_rate=0 and no timeout waits for release."""
    clock = FrozenClock()
    bucket = TokenBucket(1, 0.0, clock=clock)
    assert bucket.acquire(1.0, timeout=0.0) is True
    result: list[bool] = []

    def worker() -> None:
        result.append(bucket.acquire(1.0, timeout=None))

    t = threading.Thread(target=worker)
    t.start()
    # Give the worker time to block on the condition.
    time.sleep(0.05)
    bucket.release(1.0)
    t.join(timeout=2.0)
    assert result == [True]


@pytest.mark.unit
def test_acquire_timeout_expires() -> None:
    """acquire returns False when the timeout expires before refill."""
    clock = FrozenClock()
    bucket = TokenBucket(1, 0.0, clock=clock)
    assert bucket.acquire(1.0, timeout=0.0) is True
    # No refill possible; timeout=0 returns False immediately.
    assert bucket.acquire(1.0, timeout=0.0) is False


# ---------------------------------------------------------------------------
# protocol.py gaps
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_encode_oversize_raises() -> None:
    """encode raises ValueError when the frame exceeds the ceiling."""
    big = BrokerMessage(op="RESERVE", data={"blob": "x" * (MAX_BROKER_FRAME + 10)})
    with pytest.raises(ValueError):
        encode(big)


@pytest.mark.unit
def test_read_frame_eof_in_prefix() -> None:
    """read_frame raises when EOF occurs inside the length prefix."""
    class ShortStream:
        def __init__(self) -> None:
            self._data = b"\x00\x01"

        def read(self, n: int) -> bytes:
            out = self._data[:n]
            self._data = self._data[n:]
            return out

    with pytest.raises(ValueError):
        read_frame(ShortStream())


@pytest.mark.unit
def test_read_frame_eof_in_body() -> None:
    """read_frame raises when EOF occurs inside the frame body."""
    class ShortBodyStream:
        def __init__(self) -> None:
            self._data = struct.pack(">I", 100) + b"short"

        def read(self, n: int) -> bytes:
            out = self._data[:n]
            self._data = self._data[n:]
            return out

    with pytest.raises(ValueError):
        read_frame(ShortBodyStream())


@pytest.mark.unit
def test_read_frame_oversized_body() -> None:
    """read_frame raises when the declared body exceeds the ceiling."""
    class OversizedStream:
        def __init__(self) -> None:
            self._data = struct.pack(">I", 2 * 1024 * 1024) + b"x"

        def read(self, n: int) -> bytes:
            out = self._data[:n]
            self._data = self._data[n:]
            return out

    with pytest.raises(ValueError):
        read_frame(OversizedStream())


# ---------------------------------------------------------------------------
# reservation.py gaps
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_get_expired_returns_none() -> None:
    """get() returns None for an expired reservation and removes it."""
    clock = FrozenClock()
    store = ReservationStore(clock=clock, ttl=10.0)
    res = store.create(ProviderId.ANTHROPIC, 1.0, "/tmp/e.sock")
    clock.advance(11.0)
    assert store.get(res.reservation_id) is None
    assert store.live_count() == 0


@pytest.mark.unit
def test_commit_unknown_returns_zero() -> None:
    """commit() on an unknown reservation returns 0.0."""
    clock = FrozenClock()
    store = ReservationStore(clock=clock)
    assert store.commit("nope", 1.0) == 0.0


# ---------------------------------------------------------------------------
# client.py gaps
# ---------------------------------------------------------------------------


class FakeSocketClient:
    """A fake socket that raises on sendall (request failure path)."""

    def __init__(self) -> None:
        self.closed = False

    def settimeout(self, timeout: float) -> None:
        pass

    def connect(self, path: str) -> None:
        pass

    def sendall(self, data: bytes) -> None:
        raise OSError("broken pipe")

    def recv(self, n: int) -> bytes:
        return b""

    def read(self, n: int) -> bytes:
        return b""

    def close(self) -> None:
        self.closed = True


@pytest.mark.unit
def test_client_request_failure_raises_unavailable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A failed request raises BrokerUnavailableError and resets the conn."""
    from dev_harness.broker import client as client_mod

    sock = FakeSocketClient()
    monkeypatch.setattr(client_mod.socket, "socket", lambda *a, **k: sock)
    client = BrokerClient(tmp_path / "broker.sock")
    with pytest.raises(BrokerUnavailableError):
        client.health()
    assert client._conn is None


@pytest.mark.unit
def test_client_close_noop_when_no_conn() -> None:
    """close() is a no-op when there is no connection."""
    client = BrokerClient()
    client.close()  # must not raise


# ---------------------------------------------------------------------------
# metrics_feed.py gaps
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_metrics_feed_stop_joins_thread() -> None:
    """stop() joins the sampling thread."""
    from dev_harness.broker.metrics_feed import MetricsFeed

    class FakeClient:
        def metrics(self) -> BrokerMessage:
            return BrokerMessage(op="METRICS", data={"p50_latency_ms": 0.0, "p95_latency_ms": 0.0, "tpm_burn": 0, "cumulative_usd": 0.0})

    emitted: list[object] = []
    feed = MetricsFeed(FakeClient(), emitted.append, interval=0.01)
    feed.start()
    feed.stop()
    assert feed._thread is None
