"""Local limiter tests (V11 4.4) — max in-flight == 2 at every sample."""

from __future__ import annotations

import threading
import time

import pytest

from dev_harness.broker.local_limiter import LocalLimiter


@pytest.mark.unit
def test_max_in_flight_never_exceeds_2() -> None:
    limiter = LocalLimiter(2)
    in_flight: list[int] = []
    stop = threading.Event()

    def sampler() -> None:
        while not stop.is_set():
            in_flight.append(limiter.in_flight)
            time.sleep(0.01)

    t = threading.Thread(target=sampler)
    t.start()

    def worker() -> None:
        if limiter.acquire(timeout=5.0):
            time.sleep(0.1)
            limiter.release()

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=5.0)
    stop.set()
    t.join(timeout=2.0)
    assert max(in_flight) <= 2


@pytest.mark.unit
def test_queue_depth_reported() -> None:
    limiter = LocalLimiter(1)
    assert limiter.acquire(timeout=0.0) is True
    assert limiter.in_flight == 1
    # Second acquire blocks; queue depth is 1.
    result: list[bool] = []

    def worker() -> None:
        result.append(limiter.acquire(timeout=0.2))

    th = threading.Thread(target=worker)
    th.start()
    th.join(timeout=2.0)
    assert result == [False]
    assert limiter.queue_depth == 0  # timed out, so it left the queue
    limiter.release()
    assert limiter.in_flight == 0


@pytest.mark.unit
def test_acquire_timeout_returns_false() -> None:
    limiter = LocalLimiter(1)
    assert limiter.acquire(timeout=0.0) is True
    assert limiter.acquire(timeout=0.0) is False
    limiter.release()


@pytest.mark.unit
def test_invalid_max_concurrency_raises() -> None:
    with pytest.raises(ValueError):
        LocalLimiter(0)


@pytest.mark.unit
def test_release_after_acquire_restores_slot() -> None:
    limiter = LocalLimiter(1)
    assert limiter.acquire(timeout=0.0) is True
    limiter.release()
    assert limiter.acquire(timeout=0.0) is True
    limiter.release()
