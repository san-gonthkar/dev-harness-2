"""Broker daemon tests (V11 4.8) — lifecycle, single-instance, drain."""

from __future__ import annotations

import socket
import threading
from pathlib import Path
from typing import Self

import pytest

from dev_harness.broker.daemon import AlreadyRunningError, BrokerDaemon
from dev_harness.broker.protocol import BrokerMessage
from dev_harness.config import HarnessConfig, ProviderConfig

pytestmark = pytest.mark.unit


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


def _make_daemon(tmp_path: Path, factory: FakeSocketFactory) -> BrokerDaemon:
    return BrokerDaemon(
        _config(),
        socket_path=tmp_path / "broker.sock",
        lock_path=tmp_path / "broker.lock",
        socket_factory=factory,
    )


def _link(factory: FakeSocketFactory) -> FakeSocket:
    """Link the server socket to a client socket."""
    assert factory.server is not None
    client = factory.clients[-1]
    client._peer = factory.server
    factory.server._conns.append(client)
    return client


@pytest.mark.unit
def test_second_instance_exits_already_running(tmp_path: Path) -> None:
    factory = FakeSocketFactory()
    d1 = _make_daemon(tmp_path, factory)
    d1.acquire_lock()
    d2 = _make_daemon(tmp_path, factory)
    with pytest.raises(AlreadyRunningError):
        d2.acquire_lock()
    d1.release_lock()


@pytest.mark.unit
def test_health_returns_ok(tmp_path: Path) -> None:
    factory = FakeSocketFactory()
    daemon = _make_daemon(tmp_path, factory)
    reply = daemon._handle_message(BrokerMessage(op="HEALTH", data={"status": "ok"}))
    assert reply.op == "HEALTH"
    assert reply.data["status"] == "ok"


@pytest.mark.unit
def test_reserve_granted_and_released(tmp_path: Path) -> None:
    factory = FakeSocketFactory()
    daemon = _make_daemon(tmp_path, factory)
    reply = daemon._handle_message(
        BrokerMessage(
            op="RESERVE",
            data={
                "provider": "anthropic",
                "tokens": 1.0,
                "callback_endpoint": "/tmp/e.sock",
            },
        )
    )
    assert reply.ok is True
    assert reply.data["granted"] is True
    rid = reply.data["reservation_id"]
    # Release returns the tokens.
    rel = daemon._handle_message(
        BrokerMessage(
            op="RELEASE", data={"provider": "anthropic", "reservation_id": rid}
        )
    )
    assert rel.ok is True
    assert rel.data["tokens"] == 1.0


@pytest.mark.unit
def test_reserve_rate_limited_when_bucket_empty(tmp_path: Path) -> None:
    factory = FakeSocketFactory()
    daemon = _make_daemon(tmp_path, factory)
    # Drain the 50-token bucket.
    for _ in range(50):
        r = daemon._handle_message(
            BrokerMessage(
                op="RESERVE",
                data={
                    "provider": "anthropic",
                    "tokens": 1.0,
                    "callback_endpoint": "/tmp/e.sock",
                },
            )
        )
        assert r.ok is True
    r = daemon._handle_message(
        BrokerMessage(
            op="RESERVE",
            data={
                "provider": "anthropic",
                "tokens": 1.0,
                "callback_endpoint": "/tmp/e.sock",
            },
        )
    )
    assert r.ok is False
    assert r.data["reason"] == "rate_limited"


@pytest.mark.unit
def test_commit_with_budget_breach_trips_kill_switch(tmp_path: Path) -> None:
    factory = FakeSocketFactory()
    daemon = _make_daemon(tmp_path, factory)
    # Reserve capacity.
    r = daemon._handle_message(
        BrokerMessage(
            op="RESERVE",
            data={
                "provider": "anthropic",
                "tokens": 1.0,
                "callback_endpoint": "/tmp/e.sock",
            },
        )
    )
    rid = r.data["reservation_id"]
    # Commit a large usage that breaches the $0.50 run budget.
    # $3/M in * 200k + $15/M out * 0 = $0.60 > $0.50
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
    # Next reserve is refused.
    r2 = daemon._handle_message(
        BrokerMessage(
            op="RESERVE",
            data={
                "provider": "anthropic",
                "tokens": 1.0,
                "callback_endpoint": "/tmp/e.sock",
            },
        )
    )
    assert r2.ok is False
    assert r2.data["reason"] == "budget"


@pytest.mark.unit
def test_metrics_reply(tmp_path: Path) -> None:
    factory = FakeSocketFactory()
    daemon = _make_daemon(tmp_path, factory)
    reply = daemon._handle_message(BrokerMessage(op="METRICS"))
    assert reply.op == "METRICS"
    assert "p50_latency_ms" in reply.data
    assert "cumulative_usd" in reply.data


@pytest.mark.unit
def test_unknown_op_returns_error(tmp_path: Path) -> None:
    factory = FakeSocketFactory()
    daemon = _make_daemon(tmp_path, factory)
    reply = daemon._handle_message(BrokerMessage(op="NOPE"))
    assert reply.ok is False
    assert reply.data["reason"] == "unknown_op"


@pytest.mark.unit
def test_drain_releases_lock_and_reaps(tmp_path: Path) -> None:
    factory = FakeSocketFactory()
    daemon = _make_daemon(tmp_path, factory)
    daemon.acquire_lock()
    daemon.drain(timeout=0.1)
    assert daemon._lock_handle is None
