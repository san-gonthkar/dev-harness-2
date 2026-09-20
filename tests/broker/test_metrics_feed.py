"""Metrics feed tests (V11 4.10) — METRICS_UPDATE emission."""

from __future__ import annotations

import time

import pytest

from dev_harness.broker.metrics_feed import MetricsFeed
from dev_harness.broker.protocol import BrokerMessage
from dev_harness.contracts.enums import EventType


class FakeClient:
    """A stub broker client returning canned metrics."""

    def __init__(self, data: dict[str, object]) -> None:
        self._data = data
        self.calls = 0

    def metrics(self) -> BrokerMessage:
        self.calls += 1
        return BrokerMessage(op="METRICS", data=dict(self._data))


class FailingClient:
    """A client whose metrics call raises (fail-closed)."""

    def metrics(self) -> BrokerMessage:
        raise RuntimeError("broker down")


@pytest.mark.unit
def test_metrics_update_carries_numeric_p95_and_usd() -> None:
    client = FakeClient(
        {"p50_latency_ms": 1.5, "p95_latency_ms": 3.5, "tpm_burn": 120, "cumulative_usd": 0.75}
    )
    emitted: list[object] = []
    feed = MetricsFeed(client, emitted.append, interval=0.05)
    feed.start()
    time.sleep(0.15)
    feed.stop()
    assert emitted
    env = emitted[0]
    assert env.type == EventType.METRICS_UPDATE
    assert env.payload.p95_latency_ms == 3.5
    assert env.payload.cumulative_usd == 0.75
    assert env.payload.tpm_burn == 120


@pytest.mark.unit
def test_feed_stops_cleanly() -> None:
    client = FakeClient({"p50_latency_ms": 0.0, "p95_latency_ms": 0.0, "tpm_burn": 0, "cumulative_usd": 0.0})
    emitted: list[object] = []
    feed = MetricsFeed(client, emitted.append, interval=0.01)
    feed.start()
    feed.stop()
    feed.stop()  # idempotent
    assert feed._thread is None


@pytest.mark.unit
def test_fail_closed_on_client_error() -> None:
    emitted: list[object] = []
    feed = MetricsFeed(FailingClient(), emitted.append, interval=0.01)
    feed.start()
    time.sleep(0.05)
    feed.stop()
    # No spam: the loop swallows errors and emits nothing.
    assert emitted == []
