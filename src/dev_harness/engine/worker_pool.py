"""Worker pool: DAG-ready chunk scheduling under a parallelism bound (V11 8.7).

The pool claims chunks in dependency order, runs them across a bounded thread
pool, and completes every chunk. Scheduling rules (8.B acceptance):

* a chunk is only claimed when *every* dependency is ``COMPLETED``;
* at most ``max_parallel_workers`` chunks run concurrently;
* each claimed chunk carries a non-``None`` ``assigned_worker_id``.

The chunk-execution callable is injected, so the pool performs no work itself
and creates no worktrees - 8.8 (``worker_workspace``) binds a worktree per
assigned worker id. All ordering is deterministic: ready chunks are visited in
``ChunkDAG.topological_order`` order and worker ids are the lowest free slot.

No clock, no randomness, bounded loop: every iteration either submits work or
waits for at least one in-flight chunk to finish.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait

from dev_harness.contracts.enums import ChunkStatus
from dev_harness.contracts.errors import EngineError
from dev_harness.contracts.state import Chunk
from dev_harness.engine.dag import ChunkDAG

ChunkExecutor = Callable[[Chunk], None]
"""Runs one chunk to completion or raises; injected so the pool stays pure."""

_WORKER_ID_PREFIX = "worker"


def dependencies_completed(chunk: Chunk, by_id: dict[str, Chunk]) -> bool:
    """``True`` when *every* dependency of ``chunk`` is ``COMPLETED``.

    Exposed module-level so the readiness rule is unit-testable in isolation
    and the mutation gate has an exact target (8.C, 0 survivors).
    """
    return all(by_id[dep].status is ChunkStatus.COMPLETED for dep in chunk.dependencies)


def ready_chunks(order: list[Chunk], by_id: dict[str, Chunk]) -> list[Chunk]:
    """The ``PENDING`` chunks in ``order`` whose dependencies are all done.

    A chunk already ``IN_PROGRESS``/``COMPLETED``/``FAILED`` is never returned.
    Results preserve ``order`` (the DAG topological order) so scheduling is
    deterministic.
    """
    return [
        chunk
        for chunk in order
        if chunk.status is ChunkStatus.PENDING and dependencies_completed(chunk, by_id)
    ]


class WorkerPool:
    """Schedules a :class:`ChunkDAG`'s chunks across bounded workers.

    :param dag: the validated chunk graph; its chunk objects are updated
        in place (``status`` / ``assigned_worker_id``) as they are scheduled.
    :param max_parallel_workers: the concurrency ceiling (``>= 1``).
    :param execute: the injected chunk executor, invoked on a worker thread.
    """

    def __init__(
        self,
        dag: ChunkDAG,
        max_parallel_workers: int,
        execute: ChunkExecutor,
    ) -> None:
        if max_parallel_workers < 1:
            msg = f"max_parallel_workers must be >= 1, got {max_parallel_workers}."
            raise EngineError(
                msg,
                remediation="Configure at least one worker slot (max_parallel_workers >= 1).",
            )
        self._dag = dag
        self._max_parallel = max_parallel_workers
        self._execute = execute
        self._in_use: set[str] = set()

    # -- public API ---------------------------------------------------------

    def run(self) -> list[Chunk]:
        """Run every chunk to ``COMPLETED`` and return them in DAG order.

        Raises :class:`EngineError` if a chunk's executor fails (that chunk is
        marked ``FAILED``) or if the graph stalls with no runnable chunk.
        """
        order = self._dag.topological_order()
        by_id = {chunk.chunk_id: chunk for chunk in order}
        total = len(by_id)
        if total == 0:
            return []

        completed = 0
        in_flight: dict[Future[None], str] = {}
        with ThreadPoolExecutor(max_workers=self._max_parallel) as executor:
            while completed < total:
                for chunk in ready_chunks(order, by_id):
                    if len(in_flight) >= self._max_parallel:
                        break
                    chunk.assigned_worker_id = self._acquire_worker_id()
                    chunk.status = ChunkStatus.IN_PROGRESS
                    in_flight[executor.submit(self._execute, chunk)] = chunk.chunk_id

                if not in_flight:
                    stalled = ", ".join(sorted(chunk_id for chunk_id in by_id))
                    msg = (
                        "No schedulable chunk remains but the graph is "
                        f"incomplete (chunks: {stalled})."
                    )
                    raise EngineError(
                        msg,
                        remediation=(
                            "Fix the chunk statuses/dependencies; a PENDING chunk "
                            "depends on a non-COMPLETED dependency."
                        ),
                    )

                done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
                for future in done:
                    chunk = by_id[in_flight.pop(future)]
                    self._settle(future, chunk)
                    completed += 1

        return [by_id[chunk.chunk_id] for chunk in order]

    # -- internals ----------------------------------------------------------

    def _settle(self, future: Future[None], chunk: Chunk) -> None:
        """Mark a finished chunk ``COMPLETED`` or propagate its failure."""
        try:
            future.result()
        except Exception as exc:  # boundary: any worker error must fail the chunk
            chunk.status = ChunkStatus.FAILED
            msg = f"Chunk '{chunk.chunk_id}' failed during execution."
            raise EngineError(
                msg,
                remediation="Inspect the worker log for the chunk and retry the run.",
            ) from exc
        chunk.status = ChunkStatus.COMPLETED
        self._release_worker_id(chunk.assigned_worker_id)

    def _acquire_worker_id(self) -> str:
        """Return the lowest free worker slot id and mark it in use."""
        for slot in range(1, self._max_parallel + 1):
            worker_id = f"{_WORKER_ID_PREFIX}-{slot}"
            if worker_id not in self._in_use:
                self._in_use.add(worker_id)
                return worker_id
        raise EngineError(  # pragma: no cover - bound guarantees a free slot
            "No free worker slot available.",
            remediation="Increase max_parallel_workers or report a pool bug.",
        )

    def _release_worker_id(self, worker_id: str | None) -> None:
        """Return a worker slot to the free pool."""
        if worker_id is not None:
            self._in_use.discard(worker_id)
