"""Ceiling under load (V11 4.9) — NIGHTLY timing test."""

from __future__ import annotations

import threading

import pytest

from dev_harness.broker.bucket import TokenBucket


@pytest.mark.timing
def test_100_concurrent_vs_50_rpm_never_exceeds_ceiling() -> None:
    """100 concurrent acquirers against a 50-RPM bucket: the burst is capped
    at the 50-token capacity; the refill is bounded (50/min)."""
    bucket = TokenBucket(50, 50.0 / 60.0)
    granted: list[bool] = []
    lock = threading.Lock()

    def worker() -> None:
        # Short timeout: refill during the wait is negligible (< 1 token).
        ok = bucket.acquire(1.0, timeout=0.2)
        with lock:
            granted.append(ok)

    threads = [threading.Thread(target=worker) for _ in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)
    # Exactly the 50-token burst succeeds; the rest time out.
    assert sum(granted) == 50
    # No unhandled exceptions: every worker returned a bool.
    assert all(isinstance(g, bool) for g in granted)


@pytest.mark.timing
def test_broker_down_raises_unavailable() -> None:
    """A missing broker socket raises BrokerUnavailableError (fail-closed)."""
    from pathlib import Path

    from dev_harness.broker.client import BrokerClient
    from dev_harness.broker.daemon import BrokerUnavailableError

    client = BrokerClient(Path("nonexistent-broker.sock"), connect_timeout=0.1)
    with pytest.raises(BrokerUnavailableError):
        client.health()
