"""Token bucket tests (V11 4.1) — frozen-clock determinism."""

from __future__ import annotations

import threading

import pytest

from dev_harness.broker.bucket import TokenBucket


class FrozenClock:
    """A manually-advanced monotonic clock."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.mark.unit
def test_capacity_grants_exactly_capacity_in_window_1() -> None:
    clock = FrozenClock()
    bucket = TokenBucket(50, 50.0 / 60.0, clock=clock)
    granted = sum(1 for _ in range(100) if bucket.try_acquire(1.0))
    assert granted == 50
    # No refill yet: 0 until the clock advances.
    assert bucket.try_acquire(1.0) is False


@pytest.mark.unit
def test_no_drift_over_10_simulated_minutes() -> None:
    clock = FrozenClock()
    bucket = TokenBucket(50, 50.0 / 60.0, clock=clock)
    total = 0
    for _ in range(10):
        # One minute of simulated time: 50 tokens refill.
        clock.advance(60.0)
        granted = sum(1 for _ in range(100) if bucket.try_acquire(1.0))
        total += granted
        assert granted == 50
    assert total == 500


@pytest.mark.unit
def test_fractional_refill() -> None:
    clock = FrozenClock()
    bucket = TokenBucket(10, 1.0, clock=clock)
    assert bucket.try_acquire(10.0) is True
    assert bucket.try_acquire(1.0) is False
    clock.advance(0.5)
    # 0.5 tokens refilled; still not enough for 1.
    assert bucket.try_acquire(1.0) is False
    clock.advance(0.5)
    assert bucket.try_acquire(1.0) is True


@pytest.mark.unit
def test_acquire_with_timeout_returns_false() -> None:
    clock = FrozenClock()
    bucket = TokenBucket(1, 0.0, clock=clock)
    assert bucket.acquire(1.0, timeout=0.0) is True
    # No refill rate: the second acquire times out immediately.
    assert bucket.acquire(1.0, timeout=0.0) is False


@pytest.mark.unit
def test_acquire_blocks_until_refill() -> None:
    # Real clock with a fast refill: the worker waits ~10ms then succeeds.
    bucket = TokenBucket(1, 100.0)
    assert bucket.acquire(1.0, timeout=0.0) is True
    result: list[bool] = []

    def worker() -> None:
        result.append(bucket.acquire(1.0, timeout=5.0))

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=2.0)
    assert result == [True]


@pytest.mark.unit
def test_release_returns_tokens() -> None:
    clock = FrozenClock()
    bucket = TokenBucket(5, 0.0, clock=clock)
    assert bucket.try_acquire(5.0) is True
    assert bucket.try_acquire(1.0) is False
    bucket.release(2.0)
    assert bucket.try_acquire(2.0) is True


@pytest.mark.unit
def test_invalid_capacity_raises() -> None:
    with pytest.raises(ValueError):
        TokenBucket(0, 1.0)
    with pytest.raises(ValueError):
        TokenBucket(1.0, -1.0)


@pytest.mark.property
def test_tokens_never_exceed_capacity() -> None:
    from hypothesis import given, settings
    from hypothesis import strategies as st

    @given(
        cap=st.floats(min_value=1.0, max_value=100.0),
        rate=st.floats(min_value=0.1, max_value=10.0),
    )
    @settings(max_examples=20, deadline=None)
    def _check(cap: float, rate: float) -> None:
        clock = FrozenClock()
        bucket = TokenBucket(cap, rate, clock=clock)
        clock.advance(1000.0)
        assert bucket.tokens <= cap + 1e-9

    _check()
