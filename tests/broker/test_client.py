"""Broker client tests (V11 4.9) — fail-closed."""

from __future__ import annotations

from pathlib import Path

import pytest

from dev_harness.broker import client as client_mod
from dev_harness.broker.client import BrokerClient
from dev_harness.broker.daemon import BrokerUnavailableError
from dev_harness.broker.protocol import BrokerMessage, encode
from dev_harness.config import HarnessConfig
from dev_harness.contracts.enums import ProviderId

pytestmark = pytest.mark.unit


class FakeSocket:
    """A fake socket that records sends and returns canned replies."""

    def __init__(self, replies: list[bytes] | None = None) -> None:
        self.replies = list(replies or [])
        self.sent: list[bytes] = []
        self.closed = False
        self.timeout: float | None = None

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def connect(self, path: str) -> None:
        self.connected_path = path

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def recv(self, n: int) -> bytes:
        if not self.replies:
            return b""
        out = self.replies[0][:n]
        self.replies[0] = self.replies[0][n:]
        if not self.replies[0]:
            self.replies.pop(0)
        return out

    def read(self, n: int) -> bytes:
        """BinaryIO-compatible read (used by read_frame)."""
        return self.recv(n)

    def close(self) -> None:
        self.closed = True


def _reply(msg: BrokerMessage) -> bytes:
    return encode(msg)


@pytest.mark.unit
def test_broker_down_raises_unavailable(tmp_path: Path) -> None:
    client = BrokerClient(tmp_path / "missing.sock", connect_timeout=0.1)
    with pytest.raises(BrokerUnavailableError):
        client.health()


@pytest.mark.unit
def test_health_ok(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sock = FakeSocket([_reply(BrokerMessage(op="HEALTH", data={"status": "ok"}))])
    monkeypatch.setattr(client_mod.socket, "socket", lambda *a, **k: sock)
    client = BrokerClient(tmp_path / "broker.sock")
    reply = client.health()
    assert reply.data["status"] == "ok"
    client.close()
    assert sock.closed is True


@pytest.mark.unit
def test_reserve_sends_callback_endpoint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sock = FakeSocket(
        [
            _reply(
                BrokerMessage(
                    op="RESERVE",
                    data={"granted": True, "reservation_id": "abc", "provider": "anthropic", "tokens": 1.0},
                )
            )
        ]
    )
    monkeypatch.setattr(client_mod.socket, "socket", lambda *a, **k: sock)
    client = BrokerClient(tmp_path / "broker.sock")
    reply = client.reserve(ProviderId.ANTHROPIC, tokens=1.0, callback_endpoint="/tmp/e.sock")
    assert reply.data["granted"] is True
    # The sent frame contains the callback_endpoint.
    sent = b"".join(sock.sent)
    assert b"/tmp/e.sock" in sent
    client.close()


@pytest.mark.unit
def test_allow_unbrokered_escape_hatch() -> None:
    config = HarnessConfig(broker={"allow_unbrokered": True})
    client = BrokerClient(config=config)
    assert client.allow_unbrokered is True


@pytest.mark.unit
def test_fail_closed_by_default() -> None:
    client = BrokerClient()
    assert client.allow_unbrokered is False


@pytest.mark.unit
def test_commit_and_release(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sock = FakeSocket(
        [
            _reply(BrokerMessage(op="COMMIT", data={"delta": 0.5})),
            _reply(BrokerMessage(op="RELEASE", data={"tokens": 1.0})),
        ]
    )
    monkeypatch.setattr(client_mod.socket, "socket", lambda *a, **k: sock)
    client = BrokerClient(tmp_path / "broker.sock")
    c = client.commit("anthropic", "rid1", actual=0.5, model="m", usage_in=10, usage_out=5)
    assert c.data["delta"] == 0.5
    r = client.release("anthropic", "rid1")
    assert r.data["tokens"] == 1.0
    client.close()


@pytest.mark.unit
def test_metrics(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sock = FakeSocket(
        [
            _reply(
                BrokerMessage(
                    op="METRICS",
                    data={"p50_latency_ms": 1.0, "p95_latency_ms": 2.0, "tpm_burn": 10, "cumulative_usd": 0.5},
                )
            )
        ]
    )
    monkeypatch.setattr(client_mod.socket, "socket", lambda *a, **k: sock)
    client = BrokerClient(tmp_path / "broker.sock")
    reply = client.metrics()
    assert reply.data["p95_latency_ms"] == 2.0
    client.close()
