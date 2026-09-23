"""Backoff: min(max, base*2^n) + U(0,jitter), honoring Retry-After (V11 4.5).

The pre-jitter delay is non-decreasing and capped at ``max_delay``. If the
provider returned a ``Retry-After`` header, it overrides the computed delay.
"""

from __future__ import annotations

import random
from collections.abc import Callable

Random = Callable[[], float]


class Backoff:
    """Exponential backoff with jitter and Retry-After override."""

    def __init__(
        self,
        *,
        base: float = 1.0,
        max_delay: float = 60.0,
        jitter: float = 0.5,
        random_fn: Random = random.random,
    ) -> None:
        if base <= 0:
            raise ValueError("base must be positive")
        if max_delay < base:
            raise ValueError("max_delay must be >= base")
        self.base = base
        self.max_delay = max_delay
        self.jitter = jitter
        self._random = random_fn

    def delay(self, attempt: int, *, retry_after: float | None = None) -> float:
        """Compute the delay for ``attempt`` (0-based).

        If ``retry_after`` is given it overrides the exponential shape.
        """
        if attempt < 0:
            raise ValueError("attempt must be >= 0")
        if retry_after is not None:
            return retry_after
        pre_jitter = min(self.max_delay, self.base * float(2**attempt))
        jitter_val = float(self._random())
        return pre_jitter + jitter_val * self.jitter

    def pre_jitter(self, attempt: int) -> float:
        """The deterministic (pre-jitter) delay for ``attempt``."""
        return min(self.max_delay, self.base * float(2**attempt))
