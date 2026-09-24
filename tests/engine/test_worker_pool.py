"""Worker-pool scheduling tests (V11 8.7).

Validation matrix (8.B, acceptance is exact): with ``max_parallel=3`` over a
7-chunk diamond -

(a) no chunk starts before its dependencies are ``COMPLETED``;
(b) peak concurrency is bounded by 3 (the observed peak is asserted);
(c) all 7 chunks end ``COMPLETED`` -

and the pool stamps ``assigned_worker_id`` on every chunk it schedules.

Determinism: the chunk-execution callable is injected, so the tests observe
concurrency without racing real work. The middle three diamond chunks rendezvous
at a :class:`threading.Barrier`; the barrier can only be satisfied if all three
are genuinely in flight. No ``time.sleep``; all waits are event/barrier driven.
The module is in the 8.C high-coverage set (95/90) and the mutation focus set
(>=80%, 0 survivors in the readiness check), so every branch is exercised.
"""

from __future__ import annotations

import threading

import pytest

from dev_harness.contracts.enums import ChunkStatus
from dev_harness.contracts.errors import EngineError
from dev_harness.contracts.state import Chunk
from dev_harness.engine.dag import ChunkDAG
from dev_harness.engine.worker_pool import (
    WorkerPool,
    dependencies_completed,
    ready_chunks,
)


def _chunk(
    chunk_id: str,
    deps: list[str] | None = None,
    status: ChunkStatus = ChunkStatus.PENDING,
) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        title=f"chunk {chunk_id}",
        dependencies=deps or [],
        status=status,
    )


def _diamond() -> list[Chunk]:
    """A 7-chunk diamond: a -> {b, c, d} -> e -> {f, g} (peak == 3)."""
    return [
        _chunk("a"),
        _chunk("b", ["a"]),
        _chunk("c", ["a"]),
        _chunk("d", ["a"]),
        _chunk("e", ["b", "c", "d"]),
        _chunk("f", ["e"]),
        _chunk("g", ["e"]),
    ]


class RecordingExecutor:
    """An injected executor recording order, concurrency and readiness.

    ``deps_completed_at_start`` captures, per chunk, whether each dependency was
    ``COMPLETED`` at the instant the chunk started - this is the readiness
    assertion. ``assigned_worker_id`` is captured at the same instant, proving
    the pool stamped it before the worker ran.
    """

    def __init__(
        self,
        chunks: list[Chunk],
        *,
        barrier: threading.Barrier | None = None,
        barrier_ids: frozenset[str] = frozenset(),
        fail_ids: frozenset[str] = frozenset(),
    ) -> None:
        self._chunks = {chunk.chunk_id: chunk for chunk in chunks}
        self._barrier = barrier
        self._barrier_ids = barrier_ids
        self._fail_ids = fail_ids
        self._lock = threading.Lock()
        self.start_order: list[str] = []
        self.active = 0
        self.peak = 0
        self.deps_completed_at_start: dict[str, list[bool]] = {}
        self.assigned_at_start: dict[str, str | None] = {}

    def __call__(self, chunk: Chunk) -> None:
        with self._lock:
            self.start_order.append(chunk.chunk_id)
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.deps_completed_at_start[chunk.chunk_id] = [
                self._chunks[dep].status is ChunkStatus.COMPLETED
                for dep in chunk.dependencies
            ]
            self.assigned_at_start[chunk.chunk_id] = chunk.assigned_worker_id

        if chunk.chunk_id in self._barrier_ids and self._barrier is not None:
            self._barrier.wait(timeout=10)

        if chunk.chunk_id in self._fail_ids:
            with self._lock:
                self.active -= 1
            msg = "worker exploded"
            raise ValueError(msg)

        with self._lock:
            self.active -= 1


# --- acceptance: scheduling over the 7-chunk diamond ------------------------


@pytest.mark.integration
def test_diamond_all_chunks_complete_and_readiness_respected() -> None:
    """(a)+(c): no chunk starts before deps; all 7 chunks end COMPLETED."""
    chunks = _diamond()
    recorder = RecordingExecutor(chunks)
    result = WorkerPool(ChunkDAG(chunks), 3, recorder).run()

    assert [chunk.chunk_id for chunk in result] == ["a", "b", "c", "d", "e", "f", "g"]
    assert all(chunk.status is ChunkStatus.COMPLETED for chunk in result)
    # Readiness: every dependency was COMPLETED when each chunk started.
    assert recorder.deps_completed_at_start
    assert all(all(flags) for flags in recorder.deps_completed_at_start.values())
    # The root ran first and the sink chunk started after its three deps.
    assert recorder.start_order[0] == "a"
    assert recorder.start_order.index("e") > max(
        recorder.start_order.index(dep) for dep in ("b", "c", "d")
    )


@pytest.mark.integration
def test_peak_concurrency_reaches_but_never_exceeds_bound() -> None:
    """(b): peak concurrency over the diamond is exactly max_parallel (<= 3)."""
    chunks = _diamond()
    barrier = threading.Barrier(3)
    recorder = RecordingExecutor(
        chunks, barrier=barrier, barrier_ids=frozenset({"b", "c", "d"})
    )
    result = WorkerPool(ChunkDAG(chunks), 3, recorder).run()

    assert recorder.peak == 3
    assert all(chunk.status is ChunkStatus.COMPLETED for chunk in result)


@pytest.mark.integration
def test_max_parallel_one_runs_strictly_serially() -> None:
    """max_parallel=1 never overlaps chunks; peak concurrency is 1."""
    chunks = _diamond()
    recorder = RecordingExecutor(chunks)
    result = WorkerPool(ChunkDAG(chunks), 1, recorder).run()

    assert recorder.peak == 1
    assert all(chunk.status is ChunkStatus.COMPLETED for chunk in result)


# --- worker-id assignment ---------------------------------------------------


@pytest.mark.unit
def test_every_scheduled_chunk_gets_a_bounded_worker_id() -> None:
    """Each chunk is stamped with a worker id drawn from the configured slots."""
    chunks = _diamond()
    recorder = RecordingExecutor(chunks)
    WorkerPool(ChunkDAG(chunks), 3, recorder).run()

    assigned = [recorder.assigned_at_start[chunk.chunk_id] for chunk in chunks]
    assert None not in assigned
    assert set(assigned) <= {"worker-1", "worker-2", "worker-3"}


@pytest.mark.unit
def test_worker_ids_are_reused_across_more_chunks_than_slots() -> None:
    """7 chunks over 2 slots reuse both slot ids (no unbounded id growth)."""
    chunks = _diamond()
    recorder = RecordingExecutor(chunks)
    WorkerPool(ChunkDAG(chunks), 2, recorder).run()

    assigned = [recorder.assigned_at_start[chunk.chunk_id] for chunk in chunks]
    assert len(assigned) == 7
    assert set(assigned) == {"worker-1", "worker-2"}


@pytest.mark.unit
def test_empty_dag_returns_empty_list() -> None:
    """An empty graph schedules nothing and returns an empty result."""
    assert WorkerPool(ChunkDAG([]), 2, lambda _chunk: None).run() == []


# --- negative: failure and stall paths --------------------------------------


@pytest.mark.negative
def test_zero_max_parallel_is_rejected() -> None:
    """A non-positive worker bound is rejected at construction."""
    with pytest.raises(EngineError):
        WorkerPool(ChunkDAG([]), 0, lambda _chunk: None)


@pytest.mark.negative
def test_executor_failure_marks_chunk_failed_and_raises() -> None:
    """A chunk whose executor raises is marked FAILED and aborts the run."""
    chunks = _diamond()
    recorder = RecordingExecutor(chunks, fail_ids=frozenset({"a"}))
    with pytest.raises(EngineError):
        WorkerPool(ChunkDAG(chunks), 3, recorder).run()

    assert chunks[0].status is ChunkStatus.FAILED


@pytest.mark.negative
def test_incomplete_graph_without_runnable_chunk_raises() -> None:
    """A PENDING chunk behind a FAILED dependency stalls the pool -> error."""
    a = _chunk("a", status=ChunkStatus.FAILED)
    b = _chunk("b", ["a"])
    with pytest.raises(EngineError):
        WorkerPool(ChunkDAG([a, b]), 2, lambda _chunk: None).run()


# --- readiness helpers (mutation-focus isolation) ---------------------------


@pytest.mark.unit
def test_dependencies_completed_true_only_when_all_deps_completed() -> None:
    """Readiness requires *every* dependency COMPLETED, not just some."""
    a = _chunk("a", status=ChunkStatus.COMPLETED)
    b = _chunk("b", status=ChunkStatus.IN_PROGRESS)
    child = _chunk("c", ["a", "b"])
    by_id = {chunk.chunk_id: chunk for chunk in (a, b, child)}

    assert dependencies_completed(_chunk("root"), by_id) is True  # no deps
    assert dependencies_completed(child, by_id) is False
    b.status = ChunkStatus.COMPLETED
    assert dependencies_completed(child, by_id) is True


@pytest.mark.unit
def test_ready_chunks_excludes_non_pending_and_unready() -> None:
    """Only PENDING chunks with COMPLETED deps are ready, in DAG order."""
    a = _chunk("a", status=ChunkStatus.COMPLETED)
    b = _chunk("b", ["a"])
    c = _chunk("c", ["b"])
    d = _chunk("d")
    e = _chunk("e", status=ChunkStatus.FAILED)
    order = [a, b, c, d, e]
    by_id = {chunk.chunk_id: chunk for chunk in order}

    assert [chunk.chunk_id for chunk in ready_chunks(order, by_id)] == ["b", "d"]
