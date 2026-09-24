"""20 Hz coalescing throttle (V11 task 7.8).

Sits between the bridge's per-event callbacks and a sink (the execution
canvas's ``RichLog.write``). Incoming envelopes are buffered and flushed to the
sink at most once per ``interval`` (default ``1/20`` s = 50 ms), so a burst of
10 000 token events over 2 s costs at most ~44 sink calls instead of 10 000.

The throttle is *half-open*: a flush happens when ``now - last_flush >=
interval``. It never blocks the UI thread and never spins — every method is
O(1) amortised and ``drain`` flushes at most once per call.

The clock is injectable so the coalescing math is deterministic under the
frozen clock (``tests/support/clock.py``); no ``time.sleep`` is ever needed.
``tui/`` never imports ``engine/``.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from dev_harness.contracts.events import Envelope

#: Default coalescing window: 20 Hz.
DEFAULT_INTERVAL = 1.0 / 20.0
#: Default cap on buffered envelopes before a forced flush bounds memory.
DEFAULT_MAX_BATCH = 4096

#: A sink receiving one coalesced batch per call.
BatchSink = Callable[[list[Envelope]], None]


class CoalescingThrottle:
    """Buffers envelopes and flushes them to ``sink`` at most once per interval."""

    def __init__(
        self,
        sink: BatchSink,
        *,
        interval: float = DEFAULT_INTERVAL,
        clock: Callable[[], float] = time.monotonic,
        max_batch: int = DEFAULT_MAX_BATCH,
    ) -> None:
        if interval <= 0:
            raise ValueError("interval must be positive")
        if max_batch < 1:
            raise ValueError("max_batch must be at least 1")
        self._sink = sink
        self._interval = interval
        self._clock = clock
        self._max_batch = max_batch
        self._buffer: list[Envelope] = []
        self._last_flush = clock()
        self._flushes = 0
        self._written = 0

    @property
    def pending(self) -> int:
        """Number of envelopes currently buffered."""
        return len(self._buffer)

    @property
    def flushes(self) -> int:
        """Number of ``sink`` calls made (one per coalesced batch)."""
        return self._flushes

    @property
    def written(self) -> int:
        """Total envelopes handed to the sink."""
        return self._written

    def push(self, env: Envelope) -> bool:
        """Buffer ``env``; return ``True`` if this push triggered a flush.

        The buffer is bounded: if it is already at ``max_batch`` it is flushed
        (oldest-first, preserving order) before ``env`` is appended, so memory
        never grows without bound.
        """
        flushed = False
        if len(self._buffer) >= self._max_batch:
            self.flush()
            flushed = True
        self._buffer.append(env)
        if self._clock() - self._last_flush >= self._interval:
            self.flush()
            flushed = True
        return flushed

    def flush(self) -> int:
        """Force-flush the buffer now; return the number of envelopes written.

        Returns ``0`` when the buffer is empty. The batch is considered flushed
        *before* the sink is invoked: a raising sink propagates but leaves the
        throttle consistent (empty buffer, no re-delivery of the same batch).
        """
        if not self._buffer:
            return 0
        batch = self._buffer
        self._buffer = []
        self._last_flush = self._clock()
        self._flushes += 1
        self._written += len(batch)
        self._sink(batch)
        return len(batch)

    def drain(self, now: float | None = None) -> int:
        """Flush iff the interval has elapsed; return envelopes written.

        Bounded: flushes at most once, then returns. Idempotent within an
        interval (a second call with the same ``now`` writes nothing).
        """
        if now is None:
            now = self._clock()
        if now - self._last_flush >= self._interval:
            return self.flush()
        return 0