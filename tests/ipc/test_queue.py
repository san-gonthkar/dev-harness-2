"""Backpressure queue tests (V11 2.6)."""

from __future__ import annotations

import pytest

from dev_harness.contracts.enums import EventType
from dev_harness.contracts.events import (
    AgentTokenStreamPayload,
    Envelope,
    InterruptRequestPayload,
)
from dev_harness.ipc.queue import CAPACITY, BackpressureQueue

pytestmark = pytest.mark.slow


def _token(seq: int) -> Envelope:
    return Envelope(
        type=EventType.AGENT_TOKEN_STREAM,
        payload=AgentTokenStreamPayload(type="AGENT_TOKEN_STREAM", seq=seq, token="x"),
    )


def _control() -> Envelope:
    return Envelope(
        type=EventType.INTERRUPT_REQUEST,
        payload=InterruptRequestPayload(type="INTERRUPT_REQUEST", command="PAUSE"),
    )


def test_10k_tokens_bounded_memory() -> None:
    """10k events into a 2048 queue: bounded, drops counted, no control drops."""
    q = BackpressureQueue()
    control_dropped = 0
    for i in range(10_000):
        if i % 100 == 0:
            ok = q.put(_control())
            if not ok:
                control_dropped += 1
        q.put(_token(i))
    assert len(q) <= CAPACITY
    assert q.dropped_frames > 0
    assert control_dropped == 0  # control events never dropped


def test_drop_oldest_token() -> None:
    q = BackpressureQueue(capacity=3)
    q.put(_token(1))
    q.put(_token(2))
    q.put(_token(3))
    q.put(_token(4))  # drops token 1
    assert q.dropped_frames == 1
    first = q.get()
    assert first is not None
    assert first.payload.seq == 2  # oldest surviving


def test_control_evicts_token_when_full() -> None:
    q = BackpressureQueue(capacity=2)
    q.put(_token(1))
    q.put(_token(2))
    # Full of tokens; a control event evicts the oldest token.
    ok = q.put(_control())
    assert ok is True
    assert q.dropped_frames == 1
    # The control event is in the queue.
    types = [q.get().type for _ in range(len(q))]
    assert EventType.INTERRUPT_REQUEST in types


def test_get_empty_returns_none() -> None:
    q = BackpressureQueue()
    assert q.get() is None
    assert q.is_empty() is True


def test_fifo_order() -> None:
    q = BackpressureQueue(capacity=10)
    for i in range(5):
        q.put(_token(i))
    seqs = [q.get().payload.seq for _ in range(5)]
    assert seqs == [0, 1, 2, 3, 4]
