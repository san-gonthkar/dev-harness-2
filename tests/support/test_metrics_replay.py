"""MetricsReplay tests (V11 7.13).

Validation matrix: replay emits the recorded feed with original values and
timing. Covers fast replay fidelity, JSON round-trip, injected-sleep pacing,
and the empty/raising-sink negative cases.
"""

from __future__ import annotations

import pytest

from dev_harness.contracts.enums import EventType
from dev_harness.contracts.events import Envelope, MetricsUpdatePayload
from tests.support.metrics_replay import MetricsReplay


def _payload(p95: float, usd: float) -> MetricsUpdatePayload:
    return MetricsUpdatePayload(
        type="METRICS_UPDATE",
        p50_latency_ms=p95 / 2.0,
        p95_latency_ms=p95,
        tpm_burn=1_000,
        cumulative_usd=usd,
    )


@pytest.mark.unit
def test_replay_fast_emits_original_values_in_order() -> None:
    """``replay_fast`` emits exactly len(feed) envelopes with original values."""
    feed = [_payload(240.0, 0.0125), _payload(305.0, 0.0544), _payload(342.0, 0.1102)]
    collected: list[Envelope] = []
    replay = MetricsReplay(feed=feed, sink=collected.append)

    emitted = replay.replay_fast()

    assert emitted == 3
    assert replay.emitted == 3
    assert [e.seq for e in collected] == [0, 1, 2]
    assert all(e.type == EventType.METRICS_UPDATE for e in collected)
    for envelope, sample in zip(collected, feed, strict=True):
        assert isinstance(envelope.payload, MetricsUpdatePayload)
        assert envelope.payload.p95_latency_ms == sample.p95_latency_ms
        assert envelope.payload.cumulative_usd == sample.cumulative_usd


@pytest.mark.unit
def test_default_feed_replays_with_original_values() -> None:
    """The built-in feed replays its recorded values unchanged."""
    collected: list[Envelope] = []
    replay = MetricsReplay(sink=collected.append)
    recorded = replay.feed

    assert replay.replay_fast() == len(recorded)
    assert [e.payload for e in collected] == recorded  # type: ignore[misc]


@pytest.mark.unit
def test_json_round_trip_preserves_feed() -> None:
    """``to_json``/``from_json`` round-trips the feed exactly."""
    original = MetricsReplay()
    text = original.to_json()

    restored = MetricsReplay.from_json(text)

    assert restored.feed == original.feed
    assert restored.to_json() == text


@pytest.mark.unit
def test_replay_paces_by_recorded_deltas() -> None:
    """``replay`` sleeps the recorded inter-sample deltas via the injected sleep."""
    feed = [_payload(200.0, 0.01), _payload(210.0, 0.02), _payload(220.0, 0.03)]
    sleeps: list[float] = []
    replay = MetricsReplay(feed=feed, sink=lambda _env: None, sleep=sleeps.append)

    emitted = replay.replay()

    assert emitted == 3
    # Caller-supplied feeds are spaced 0.5s apart: two deltas, no leading sleep.
    assert sleeps == [0.5, 0.5]


@pytest.mark.unit
def test_record_uses_injected_clock_and_paces() -> None:
    """``record`` timestamps from the injected clock; replay paces those deltas."""
    now = [100.0]
    sleeps: list[float] = []
    replay = MetricsReplay(
        feed=[],
        sink=lambda _env: None,
        sleep=sleeps.append,
        clock=lambda: now[0],
    )
    replay.record(_payload(200.0, 0.01))
    now[0] = 100.25
    replay.record(_payload(210.0, 0.02))
    now[0] = 101.0
    replay.record(_payload(220.0, 0.03))

    assert replay.replay() == 3
    assert sleeps == [0.25, 0.75]


@pytest.mark.negative
def test_empty_feed_replays_nothing() -> None:
    """An empty feed emits nothing and returns 0."""
    collected: list[Envelope] = []
    replay = MetricsReplay(feed=[], sink=collected.append)

    assert replay.replay() == 0
    assert replay.replay_fast() == 0
    assert collected == []
    assert replay.emitted == 0


@pytest.mark.negative
def test_raising_sink_propagates_without_corrupting_emitted() -> None:
    """A raising sink propagates; ``emitted`` counts only delivered envelopes."""
    delivered: list[Envelope] = []

    def sink(envelope: Envelope) -> None:
        if envelope.seq == 1:
            raise RuntimeError("sink boom")
        delivered.append(envelope)

    replay = MetricsReplay(feed=[_payload(200.0, 0.01), _payload(210.0, 0.02)], sink=sink)

    with pytest.raises(RuntimeError, match="sink boom"):
        replay.replay_fast()

    # seq 0 delivered; seq 1 raised before the counter advanced.
    assert [e.seq for e in delivered] == [0]
    assert replay.emitted == 1