"""Recorded ``METRICS_UPDATE`` replay feed (V11 7.13).

The P7 (7.D) acceptance protocol step 4 replays a recorded metrics feed into
``#model-registry`` and asserts the displayed p95 and cumulative USD match the
feed to the displayed precision. This module is that feed: a finite list of
``MetricsUpdatePayload`` samples with their recorded timestamps, replayable
with the original values and the original inter-sample timing.

Mirrors :class:`tests.support.stub_workload.StubWorkload`'s shape: a scripted
emitter with an injectable sink. Pacing uses an injectable ``sleep`` callable
(default :func:`time.sleep`) so tests inject a no-op/fake and never sleep for
real; the loop is bounded by the finite recorded list.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict

from dev_harness.contracts.enums import EventType
from dev_harness.contracts.events import Envelope, MetricsUpdatePayload

Sink = Callable[[Envelope], None]
Sleep = Callable[[float], None]
Clock = Callable[[], float]


class RecordedSample(BaseModel):
    """One recorded metrics sample: its payload and the time it was captured."""

    model_config = ConfigDict(extra="forbid")

    at: float
    payload: MetricsUpdatePayload


class _FeedDocument(BaseModel):
    """Serialized form of a recorded feed (Pydantic v2, never hand-rolled)."""

    model_config = ConfigDict(extra="forbid")

    samples: list[RecordedSample]


def _default_feed() -> list[RecordedSample]:
    """A small built-in recorded feed: rising USD, varying p95, 0.5s spacing."""
    rows: list[tuple[float, float, float, int, float]] = [
        # at, p50, p95, tpm, cumulative_usd
        (0.0, 120.0, 240.0, 1_800, 0.0125),
        (0.5, 118.0, 260.0, 1_950, 0.0310),
        (1.0, 131.0, 305.0, 2_100, 0.0544),
        (1.5, 125.0, 288.0, 2_050, 0.0798),
        (2.0, 140.0, 342.0, 2_300, 0.1102),
        (2.5, 133.0, 311.0, 2_240, 0.1456),
    ]
    return [
        RecordedSample(
            at=at,
            payload=MetricsUpdatePayload(
                type="METRICS_UPDATE",
                p50_latency_ms=p50,
                p95_latency_ms=p95,
                tpm_burn=tpm,
                cumulative_usd=usd,
            ),
        )
        for at, p50, p95, tpm, usd in rows
    ]


class MetricsReplay:
    """Replays a recorded ``METRICS_UPDATE`` feed with original values + timing.

    ``replay`` paces by the recorded inter-sample deltas via the injected
    ``sleep``; ``replay_fast`` emits everything with no pacing. Both emit
    ``Envelope(type=METRICS_UPDATE, seq=i, payload=sample)`` in recorded order
    and return the number emitted.
    """

    def __init__(
        self,
        *,
        feed: list[MetricsUpdatePayload] | None = None,
        sink: Sink | None = None,
        sleep: Sleep | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._sink = sink
        self._sleep: Sleep = sleep if sleep is not None else time.sleep
        self._clock: Clock = clock if clock is not None else time.monotonic
        self._emitted = 0
        if feed is None:
            self._samples = _default_feed()
        else:
            # A caller-supplied feed carries no timestamps: space it 0.5s apart.
            self._samples = [
                RecordedSample(at=index * 0.5, payload=payload)
                for index, payload in enumerate(feed)
            ]

    # -- introspection -------------------------------------------------------

    @property
    def emitted(self) -> int:
        """How many envelopes have been emitted so far."""
        return self._emitted

    @property
    def feed(self) -> list[MetricsUpdatePayload]:
        """The recorded payloads, in order."""
        return [sample.payload for sample in self._samples]

    # -- recording -----------------------------------------------------------

    def record(self, payload: MetricsUpdatePayload, *, at: float | None = None) -> None:
        """Append a sample with its timestamp (defaults to the injected clock)."""
        timestamp = self._clock() if at is None else at
        self._samples.append(RecordedSample(at=timestamp, payload=payload))

    # -- replay --------------------------------------------------------------

    def replay(self) -> int:
        """Emit every sample in order, pacing by the recorded deltas."""
        for index, sample in enumerate(self._samples):
            if index > 0:
                delta = sample.at - self._samples[index - 1].at
                if delta > 0:
                    self._sleep(delta)
            self._emit(index, sample.payload)
        return self._emitted

    def replay_fast(self) -> int:
        """Emit every sample in order with no pacing (tests/CI)."""
        for index, sample in enumerate(self._samples):
            self._emit(index, sample.payload)
        return self._emitted

    def _emit(self, seq: int, payload: MetricsUpdatePayload) -> None:
        envelope = Envelope(type=EventType.METRICS_UPDATE, seq=seq, payload=payload)
        if self._sink is not None:
            self._sink(envelope)
        # Count only after the sink returns: a raising sink must not corrupt
        # ``emitted`` (the envelope was not delivered).
        self._emitted += 1

    # -- serialization -------------------------------------------------------

    def to_json(self) -> str:
        """Serialize the recorded feed (payloads + timestamps) to JSON."""
        return _FeedDocument(samples=self._samples).model_dump_json()

    @classmethod
    def from_json(
        cls,
        text: str,
        *,
        sink: Sink | None = None,
        sleep: Sleep | None = None,
        clock: Clock | None = None,
    ) -> MetricsReplay:
        """Rebuild a replay from a previously recorded feed."""
        document = _FeedDocument.model_validate_json(text)
        replay = cls(sink=sink, sleep=sleep, clock=clock)
        replay._samples = list(document.samples)
        return replay