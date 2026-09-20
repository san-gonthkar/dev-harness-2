"""Backoff tests (V11 4.5) — shape + Retry-After override."""

from __future__ import annotations

import pytest

from dev_harness.broker.backoff import Backoff


@pytest.mark.unit
def test_attempts_0_to_5_non_decreasing_pre_jitter_capped() -> None:
    b = Backoff(base=1.0, max_delay=10.0)
    delays = [b.pre_jitter(n) for n in range(6)]
    assert delays == [1.0, 2.0, 4.0, 8.0, 10.0, 10.0]
    # Non-decreasing.
    assert all(delays[i] <= delays[i + 1] for i in range(len(delays) - 1))


@pytest.mark.unit
def test_retry_after_overrides() -> None:
    b = Backoff(base=1.0, max_delay=60.0)
    assert b.delay(0, retry_after=30.0) == 30.0
    assert b.delay(5, retry_after=30.0) == 30.0


@pytest.mark.unit
def test_jitter_adds_within_bounds() -> None:
    b = Backoff(base=1.0, max_delay=10.0, jitter=0.5, random_fn=lambda: 1.0)
    assert b.delay(0) == 1.5
    assert b.delay(1) == 2.5


@pytest.mark.unit
def test_zero_jitter_is_deterministic() -> None:
    b = Backoff(base=1.0, max_delay=10.0, jitter=0.0)
    assert b.delay(3) == 8.0


@pytest.mark.unit
def test_invalid_attempt_raises() -> None:
    b = Backoff()
    with pytest.raises(ValueError):
        b.delay(-1)


@pytest.mark.unit
def test_invalid_base_raises() -> None:
    with pytest.raises(ValueError):
        Backoff(base=0.0)
    with pytest.raises(ValueError):
        Backoff(base=5.0, max_delay=1.0)
