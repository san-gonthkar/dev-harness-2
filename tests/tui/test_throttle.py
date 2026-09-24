"""20 Hz coalescing throttle tests (V11 task 7.8).

Deterministic unit tests drive the frozen clock (``tests/support/clock.py``) —
no ``time.sleep``. The ``timing`` test simulates 2 s / 10 000 tokens and is
NIGHTLY-tier (deselected in the smoke lane).
"""

from __future__ import annotations

import pytest

from dev_harness.contracts.enums import EventType
from dev_harness.contracts.events import AgentTokenStreamPayload, Envelope
from dev_harness.tui.throttle import CoalescingThrottle
from tests.support.clock import make_clock

INTERVAL = 0.05


def token_env(seq: int) -> Envelope:
    """A minimal AGENT_TOKEN_STREAM envelope."""
    return Envelope(
        type=EventType.AGENT_TOKEN_STREAM,
        seq=seq,
        payload=AgentTokenStreamPayload(type="AGENT_TOKEN_STREAM", seq=seq, token="tok"),
    )


def make_throttle(
    clock: object, *, max_batch: int = 4096
) -> tuple[CoalescingThrottle, list[list[Envelope]]]:
    """A throttle over a recording sink, wired to ``clock.monotonic``."""
    batches: list[list[Envelope]] = []
    throttle = CoalescingThrottle(
        batches.append,
        interval=INTERVAL,
        clock=clock.monotonic,  # type: ignore[attr-defined]
        max_batch=max_batch,
    )
    return throttle, batches


@pytest.mark.unit
def test_pushes_within_interval_do_not_flush() -> None:
    clock = make_clock()
    throttle, batches = make_throttle(clock)
    assert throttle.push(token_env(0)) is False
    assert throttle.push(token_env(1)) is False
    assert throttle.flushes == 0
    assert batches == []
    assert throttle.pending == 2


@pytest.mark.unit
def test_push_crossing_interval_flushes_once() -> None:
    clock = make_clock()
    throttle, batches = make_throttle(clock)
    throttle.push(token_env(0))
    clock.tick(INTERVAL)
    assert throttle.push(token_env(1)) is True
    assert throttle.flushes == 1
    assert throttle.written == 2
    assert [e.seq for e in batches[0]] == [0, 1]


@pytest.mark.unit
def test_flush_writes_remainder_and_empties_buffer() -> None:
    clock = make_clock()
    throttle, batches = make_throttle(clock)
    throttle.push(token_env(0))
    throttle.push(token_env(1))
    assert throttle.flush() == 2
    assert throttle.pending == 0
    assert throttle.written == 2
    assert [e.seq for e in batches[0]] == [0, 1]


@pytest.mark.unit
def test_empty_flush_returns_zero() -> None:
    clock = make_clock()
    throttle, batches = make_throttle(clock)
    assert throttle.flush() == 0
    assert throttle.flushes == 0
    assert batches == []


@pytest.mark.unit
def test_drain_is_idempotent_within_interval() -> None:
    clock = make_clock()
    throttle, _ = make_throttle(clock)
    throttle.push(token_env(0))
    now = clock.monotonic()
    assert throttle.drain(now) == 0
    assert throttle.drain(now) == 0
    assert throttle.flushes == 0


@pytest.mark.unit
def test_drain_flushes_once_when_interval_elapsed() -> None:
    clock = make_clock()
    throttle, batches = make_throttle(clock)
    throttle.push(token_env(0))
    throttle.push(token_env(1))
    now = clock.monotonic() + INTERVAL
    assert throttle.drain(now) == 2
    assert throttle.flushes == 1
    assert [e.seq for e in batches[0]] == [0, 1]


@pytest.mark.unit
def test_order_preserved_across_flushes() -> None:
    clock = make_clock()
    throttle, batches = make_throttle(clock)
    for seq in range(5):
        throttle.push(token_env(seq))
        clock.tick(INTERVAL)
    throttle.flush()
    sunk = [e.seq for batch in batches for e in batch]
    assert sunk == [0, 1, 2, 3, 4]


@pytest.mark.unit
def test_max_batch_bounds_buffer_before_overflow() -> None:
    clock = make_clock()
    throttle, batches = make_throttle(clock, max_batch=3)
    for seq in range(4):
        throttle.push(token_env(seq))
    assert throttle.flushes == 1
    assert throttle.written == 3
    assert throttle.pending == 1
    assert [e.seq for e in batches[0]] == [0, 1, 2]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("offset", "expected"),
    [
        (INTERVAL, 1),
        (INTERVAL - 1e-9, 0),
    ],
)
def test_interval_boundary(offset: float, expected: int) -> None:
    clock = make_clock()
    throttle, _ = make_throttle(clock)
    throttle.push(token_env(0))
    now = clock.monotonic() + offset
    assert throttle.drain(now) == expected
    assert throttle.flushes == expected


@pytest.mark.unit
def test_invalid_construction_rejected() -> None:
    clock = make_clock()
    with pytest.raises(ValueError):
        CoalescingThrottle(lambda _b: None, interval=0.0, clock=clock.monotonic)
    with pytest.raises(ValueError):
        CoalescingThrottle(lambda _b: None, max_batch=0, clock=clock.monotonic)


@pytest.mark.negative
def test_raising_sink_leaves_state_consistent() -> None:
    clock = make_clock()
    calls: list[list[Envelope]] = []

    def sink(batch: list[Envelope]) -> None:
        calls.append(batch)
        raise RuntimeError("sink boom")

    throttle = CoalescingThrottle(sink, interval=INTERVAL, clock=clock.monotonic)
    throttle.push(token_env(0))
    with pytest.raises(RuntimeError, match="sink boom"):
        throttle.flush()
    # The batch is considered flushed before the sink runs: no re-delivery.
    assert throttle.pending == 0
    assert throttle.flushes == 1
    assert throttle.written == 1
    assert len(calls) == 1


@pytest.mark.negative
def test_push_after_flush_starts_fresh_interval() -> None:
    clock = make_clock()
    throttle, batches = make_throttle(clock)
    throttle.push(token_env(0))
    throttle.flush()
    # A push immediately after a flush is within the new interval: no flush.
    assert throttle.push(token_env(1)) is False
    assert throttle.flushes == 1
    clock.tick(INTERVAL)
    assert throttle.push(token_env(2)) is True
    assert throttle.flushes == 2
    assert [e.seq for e in batches[1]] == [1, 2]


@pytest.mark.timing
def test_2s_10k_tokens_within_slo() -> None:
    """2 s / 10 000 tokens -> <= 44 sink calls; no step exceeds 50 ms simulated."""
    clock = make_clock()
    batches: list[list[Envelope]] = []
    throttle = CoalescingThrottle(batches.append, interval=INTERVAL, clock=clock.monotonic)
    total = 10_000
    step = 0.05  # 50 ms simulated per iteration
    steps = 40
    per_step = total // steps
    max_gap = 0.0
    last_flush_time = clock.monotonic()
    for _ in range(steps):
        clock.tick(step)
        for _ in range(per_step):
            throttle.push(token_env(0))
        throttle.drain()
        if throttle.flushes:
            gap = clock.monotonic() - last_flush_time
            max_gap = max(max_gap, gap)
            last_flush_time = clock.monotonic()
    throttle.flush()
    assert throttle.written == total
    assert throttle.flushes <= 44
    # No coalescing window ever exceeded one 50 ms step: the UI never freezes.
    assert max_gap <= step + 1e-9