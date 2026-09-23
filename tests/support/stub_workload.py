"""StubWorkload: scripted AGENT_TOKEN_STREAM emitter (V11 5.10).

The P5 (5.D) and P7 (7.D) acceptance protocols need a trivial workload that
produces a known number of ``AGENT_TOKEN_STREAM`` events so client transcripts
can be diffed and late-attach snapshots can be observed. This is that stub: it
emits exactly ``count`` tokens with strictly increasing ``seq`` and can be
paused and resumed mid-stream.

No pipeline, no personas, no provider: the sink is any callable accepting an
``Envelope`` (the daemon feeds it ``Fanout.publish``).
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from dev_harness.contracts.enums import EventType
from dev_harness.contracts.events import AgentTokenStreamPayload, Envelope

Sink = Callable[[Envelope], None]


def _token_envelope(seq: int, agent: str) -> Envelope:
    return Envelope(
        type=EventType.AGENT_TOKEN_STREAM,
        seq=seq,
        payload=AgentTokenStreamPayload(
            type="AGENT_TOKEN_STREAM",
            seq=seq,
            token=f"{agent}:{seq}",
            agent=agent,
        ),
    )


class StubWorkload:
    """Emits ``count`` scripted token events with strictly increasing ``seq``.

    ``seq`` starts at 0 and increments by one per emitted event, so a
    consumer that receives every event sees ``sequence == emitted`` identity.
    ``pause`` blocks ``run``/``step`` from emitting until ``resume``; the next
    emitted event always continues the sequence (no gaps, no repeats).
    """

    def __init__(
        self,
        *,
        count: int = 100,
        agent: str = "stub",
        sink: Sink | None = None,
    ) -> None:
        if count < 0:
            raise ValueError("count must be non-negative")
        self.count = count
        self.agent = agent
        self._sink = sink
        self._emitted = 0
        self._paused = False
        self._done = threading.Event()
        self._resumed = threading.Event()
        self._resumed.set()
        self._lock = threading.Lock()

    # -- introspection -------------------------------------------------------

    @property
    def next_seq(self) -> int:
        """The ``seq`` the next emitted event will carry."""
        return self._emitted

    @property
    def emitted(self) -> int:
        """How many events have been emitted so far."""
        return self._emitted

    @property
    def paused(self) -> bool:
        """True while emission is suspended."""
        return self._paused

    @property
    def finished(self) -> bool:
        """True once all ``count`` events have been emitted."""
        return self._emitted >= self.count

    # -- control -------------------------------------------------------------

    def pause(self) -> None:
        """Suspend emission; the next ``step``/``run`` iteration blocks."""
        with self._lock:
            self._paused = True
            self._resumed.clear()

    def resume(self) -> None:
        """Resume emission from the next sequence number."""
        with self._lock:
            self._paused = False
            self._resumed.set()

    # -- emission ------------------------------------------------------------

    def step(self, *, timeout: float | None = None) -> Envelope | None:
        """Emit exactly one event, or return None when paused/exhausted.

        With ``timeout`` set and the workload paused, block up to ``timeout``
        seconds for a resume before returning None.
        """
        while True:
            if self._emitted >= self.count:
                return None
            if not self._paused:
                break
            if not self._resumed.wait(timeout) and timeout is not None:
                return None
        with self._lock:
            if self._emitted >= self.count:
                return None
            seq = self._emitted
            self._emitted += 1
        envelope = _token_envelope(seq, self.agent)
        if self._sink is not None:
            self._sink(envelope)
        if self._emitted >= self.count:
            self._done.set()
        return envelope

    def run(self) -> int:
        """Emit all ``count`` events, blocking on pause; returns the count."""
        while self.step() is not None:
            pass
        return self._emitted

    def wait(self, timeout: float | None = None) -> bool:
        """Wait until all events are emitted; True when finished."""
        return self._done.wait(timeout)
