"""Local limiter: ollama concurrency semaphore + queue depth (V11 4.4).

Local providers (ollama) are limited by memory, not remote quota. The
limiter caps in-flight requests at ``max_concurrency`` and reports queue
depth so the broker can refuse rather than saturate.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

Clock = Callable[[], float]


class LocalLimiter:
    """A concurrency semaphore with queue-depth accounting."""

    def __init__(self, max_concurrency: int, *, clock: Clock = time.monotonic) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self.max_concurrency = max_concurrency
        self._clock = clock
        self._sem = threading.BoundedSemaphore(max_concurrency)
        self._lock = threading.Lock()
        self._in_flight = 0
        self._queue_depth = 0

    @property
    def in_flight(self) -> int:
        """Currently executing requests."""
        with self._lock:
            return self._in_flight

    @property
    def queue_depth(self) -> int:
        """Requests waiting for a slot."""
        with self._lock:
            return self._queue_depth

    def acquire(self, timeout: float | None = None) -> bool:
        """Acquire a concurrency slot, blocking up to ``timeout``.

        Returns True on success, False on timeout.
        """
        with self._lock:
            self._queue_depth += 1
        try:
            acquired = self._sem.acquire(timeout=timeout)
            if not acquired:
                return False
            with self._lock:
                self._in_flight += 1
                self._queue_depth -= 1
            return True
        finally:
            if not acquired:
                with self._lock:
                    self._queue_depth -= 1

    def release(self) -> None:
        """Release a concurrency slot."""
        with self._lock:
            self._in_flight -= 1
        self._sem.release()
