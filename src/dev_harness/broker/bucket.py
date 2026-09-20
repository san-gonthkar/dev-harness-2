"""Thread-safe token bucket on time.monotonic() (V11 4.1).

Fractional refill: capacity is a float and refills continuously at
``refill_rate`` tokens/second. ``acquire`` blocks up to ``timeout`` seconds
using a condition variable; the clock is injectable for frozen-clock tests.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

Clock = Callable[[], float]


class TokenBucket:
    """A thread-safe token bucket with fractional refill."""

    def __init__(
        self,
        capacity: float,
        refill_rate: float,
        *,
        clock: Clock = time.monotonic,
    ) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        if refill_rate < 0:
            raise ValueError("refill_rate must be non-negative")
        self.capacity = float(capacity)
        self.refill_rate = float(refill_rate)
        self._clock = clock
        self._tokens = float(capacity)
        self._last = clock()
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)

    def _refill(self) -> None:
        """Refill tokens based on elapsed time (caller holds the lock)."""
        now = self._clock()
        elapsed = now - self._last
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_rate)
            self._last = now

    @property
    def tokens(self) -> float:
        """Current token count (refilled to now)."""
        with self._lock:
            self._refill()
            return self._tokens

    def try_acquire(self, tokens: float = 1.0) -> bool:
        """Acquire without blocking; returns False if insufficient tokens."""
        if tokens <= 0:
            raise ValueError("tokens must be positive")
        with self._lock:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False

    def acquire(self, tokens: float = 1.0, timeout: float | None = None) -> bool:
        """Acquire tokens, blocking up to ``timeout`` seconds.

        Returns True on success, False on timeout. A ``timeout`` of None
        blocks indefinitely.
        """
        if tokens <= 0:
            raise ValueError("tokens must be positive")
        deadline = None if timeout is None else self._clock() + timeout
        with self._cond:
            while True:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return True
                # Wait exactly until enough tokens refill (real time).
                deficit = tokens - self._tokens
                wait = deficit / self.refill_rate if self.refill_rate > 0 else float("inf")
                if deadline is not None:
                    remaining = deadline - self._clock()
                    if remaining <= 0:
                        return False
                    wait = min(wait, remaining)
                if wait == float("inf"):
                    # No refill possible; wait for a release notification.
                    self._cond.wait()
                else:
                    self._cond.wait(wait)

    def release(self, tokens: float = 1.0) -> None:
        """Return tokens to the bucket (used by reservation release)."""
        if tokens <= 0:
            raise ValueError("tokens must be positive")
        with self._cond:
            self._tokens = min(self.capacity, self._tokens + tokens)
            self._cond.notify_all()
