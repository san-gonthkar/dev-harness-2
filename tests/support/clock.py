"""Frozen monotonic clock for deterministic tests (V11 0.12).

Tests must never use time.sleep(); they advance this clock with tick().
"""

from __future__ import annotations

import time
from collections.abc import Callable


class FrozenClock:
    """A monotonic clock that only advances on explicit tick()."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self._now = start

    def tick(self, seconds: float = 1.0) -> float:
        """Advance the clock and return the new time."""
        self._now += seconds
        return self._now

    def monotonic(self) -> float:
        """Drop-in replacement for time.monotonic()."""
        return self._now

    def time(self) -> float:
        """Drop-in replacement for time.time()."""
        return self._now

    def install(self) -> None:
        """Patch time.monotonic/time.time for the duration of a test."""
        self._orig_monotonic = time.monotonic
        self._orig_time = time.time
        time.monotonic = self.monotonic  # type: ignore[assignment]
        time.time = self.time  # type: ignore[assignment]

    def restore(self) -> None:
        """Restore the real clock functions."""
        if hasattr(self, "_orig_monotonic"):
            time.monotonic = self._orig_monotonic  # type: ignore[assignment]
            time.time = self._orig_time  # type: ignore[assignment]


def make_clock(start: float = 1_000_000.0) -> FrozenClock:
    """Factory for a fresh frozen clock."""
    return FrozenClock(start)
