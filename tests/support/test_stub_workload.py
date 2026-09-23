"""StubWorkload tests (V11 5.10).

Validation matrix: emits exactly the scripted event count with strictly
increasing ``seq``; pausable/resumable.
"""

from __future__ import annotations

import threading

import pytest

from dev_harness.contracts.enums import EventType
from dev_harness.contracts.events import Envelope
from tests.support.stub_workload import StubWorkload

pytestmark = pytest.mark.unit


@pytest.mark.unit
def test_emits_exactly_scripted_count() -> None:
    """Exactly ``count`` events are emitted with strictly increasing seq."""
    collected: list[Envelope] = []
    workload = StubWorkload(count=100, sink=collected.append)
    emitted = workload.run()

    assert emitted == 100
    assert len(collected) == 100
    assert [e.seq for e in collected] == list(range(100))
    assert [e.payload.seq for e in collected] == list(range(100))  # type: ignore[union-attr]
    assert all(e.type == EventType.AGENT_TOKEN_STREAM for e in collected)


@pytest.mark.unit
def test_seqs_strictly_increase_across_steps() -> None:
    """Step-by-step emission keeps ``seq`` strictly increasing by one."""
    workload = StubWorkload(count=5)
    seqs = []
    while (env := workload.step()) is not None:
        seqs.append(env.seq)
    assert seqs == [0, 1, 2, 3, 4]


@pytest.mark.unit
def test_zero_count_emits_nothing() -> None:
    """A zero-count workload emits nothing and is immediately finished."""
    workload = StubWorkload(count=0)
    assert workload.step() is None
    assert workload.run() == 0
    assert workload.finished is True


@pytest.mark.unit
def test_pause_blocks_emission_then_resume_continues() -> None:
    """Pausing suspends emission; resume continues the sequence with no gap."""
    workload = StubWorkload(count=4)
    assert workload.step() is not None  # seq 0
    workload.pause()
    assert workload.paused is True
    # Paused: a non-blocking step returns None rather than emitting.
    assert workload.step(timeout=0.0) is None
    assert workload.emitted == 1  # no gap created
    workload.resume()
    assert workload.paused is False
    rest: list[int] = []
    while (envelope := workload.step()) is not None:
        rest.append(envelope.seq)
    assert rest == [1, 2, 3]


@pytest.mark.unit
def test_resume_from_another_thread_completes_run() -> None:
    """A run blocked on pause completes once another thread resumes it."""
    stalled = threading.Event()
    pause_at = 5

    def sink(envelope: Envelope) -> None:
        # Pause the workload from inside emission: the runner thread blocks in
        # the next step() until the main thread resumes it.
        if envelope.seq == pause_at - 1:
            workload.pause()
            stalled.set()

    workload = StubWorkload(count=50, sink=sink)
    result: list[int] = []
    runner = threading.Thread(target=lambda: result.append(workload.run()))
    runner.start()

    assert stalled.wait(timeout=2.0), "runner never reached the pause point"
    # The runner is blocked on the paused workload: no further emission.
    assert workload.emitted == pause_at
    workload.resume()
    runner.join(timeout=2.0)

    assert not runner.is_alive()
    assert result == [50]
    assert workload.finished is True


@pytest.mark.unit
def test_wait_returns_when_finished() -> None:
    """wait() returns True once the workload is exhausted."""
    workload = StubWorkload(count=3, sink=lambda _env: None)
    workload.run()
    assert workload.wait(timeout=0.1) is True


@pytest.mark.unit
def test_negative_count_rejected() -> None:
    """A negative count is rejected at construction (fail fast)."""
    with pytest.raises(ValueError):
        StubWorkload(count=-1)
