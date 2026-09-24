"""Chunk DAG builder + topological validator tests (V11 8.6).

Validation matrix (8.B): cycle -> CyclicDependencyError naming both ids;
unknown dep -> OrphanDependencyError; order stable over 10 runs. The module is
in the 8.C high-coverage set (95/90) and the mutation focus set (>=80%, 0
survivors in the readiness check), so every branch is exercised here.
"""

from __future__ import annotations

import pytest

from dev_harness.contracts.enums import ChunkStatus
from dev_harness.contracts.errors import (
    CyclicDependencyError,
    OrphanDependencyError,
)
from dev_harness.contracts.state import Chunk
from dev_harness.engine.dag import ChunkDAG


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


# --- construction / topological order ---------------------------------------


@pytest.mark.unit
def test_empty_dag_has_empty_order_and_no_ready() -> None:
    """An empty chunk list yields an empty order and no ready chunks."""
    dag = ChunkDAG([])
    assert dag.topological_order() == []
    assert dag.ready_chunks() == []


@pytest.mark.unit
def test_single_node_order_and_ready() -> None:
    """A lone PENDING chunk is both ordered and ready."""
    only = _chunk("a")
    dag = ChunkDAG([only])
    assert dag.topological_order() == [only]
    assert dag.ready_chunks() == [only]


@pytest.mark.unit
def test_diamond_order_respects_dependencies() -> None:
    """Diamond a -> {b, c} -> d orders roots first and the sink last."""
    a, b, c, d = (
        _chunk("a"),
        _chunk("b", ["a"]),
        _chunk("c", ["a"]),
        _chunk("d", ["b", "c"]),
    )
    dag = ChunkDAG([d, c, b, a])
    assert [ch.chunk_id for ch in dag.topological_order()] == ["a", "b", "c", "d"]


@pytest.mark.unit
def test_topological_order_is_stable_over_ten_runs() -> None:
    """The order is identical across 10 independent builds (deterministic)."""
    chunks = [
        _chunk("d", ["b", "c"]),
        _chunk("b", ["a"]),
        _chunk("c", ["a"]),
        _chunk("a"),
        _chunk("e"),
    ]
    orders = [
        [ch.chunk_id for ch in ChunkDAG(chunks).topological_order()]
        for _ in range(10)
    ]
    assert orders == [["a", "b", "c", "d", "e"]] * 10


@pytest.mark.unit
def test_ready_nodes_tie_broken_by_chunk_id() -> None:
    """Independent roots are ordered by chunk_id, not input order."""
    dag = ChunkDAG([_chunk("z"), _chunk("m"), _chunk("a")])
    assert [ch.chunk_id for ch in dag.topological_order()] == ["a", "m", "z"]


@pytest.mark.unit
def test_reversed_chain_order_and_acyclic_dfs_path() -> None:
    """A -> B -> C chains order deepest dependency first with no false cycle."""
    dag = ChunkDAG([_chunk("a", ["b"]), _chunk("b", ["c"]), _chunk("c")])
    assert [ch.chunk_id for ch in dag.topological_order()] == ["c", "b", "a"]


# --- cycle detection --------------------------------------------------------


@pytest.mark.unit
def test_self_dependency_is_a_cycle_naming_the_id() -> None:
    """A chunk depending on itself raises CyclicDependencyError naming it."""
    with pytest.raises(CyclicDependencyError) as exc:
        ChunkDAG([_chunk("a", ["a"])])
    assert "'a'" in str(exc.value)
    assert "a -> a" in str(exc.value)


@pytest.mark.unit
def test_two_node_cycle_names_both_ids() -> None:
    """A 2-cycle raises CyclicDependencyError naming both chunk ids."""
    with pytest.raises(CyclicDependencyError) as exc:
        ChunkDAG([_chunk("a", ["b"]), _chunk("b", ["a"])])
    message = str(exc.value)
    assert "'a'" in message
    assert "'b'" in message


@pytest.mark.unit
def test_three_node_cycle_names_both_ids() -> None:
    """A 3-cycle raises CyclicDependencyError naming two ids in the cycle."""
    with pytest.raises(CyclicDependencyError) as exc:
        ChunkDAG(
            [
                _chunk("a", ["c"]),
                _chunk("b", ["a"]),
                _chunk("c", ["b"]),
            ]
        )
    message = str(exc.value)
    # The message quotes two chunk ids that are both members of the cycle.
    assert "'a'" in message
    assert "'c'" in message
    # The full cycle path is included for diagnosis.
    assert "a -> c -> b -> a" in message


@pytest.mark.unit
def test_cycle_error_carries_remediation() -> None:
    """The cycle error exposes the canonical remediation text."""
    with pytest.raises(CyclicDependencyError) as exc:
        ChunkDAG([_chunk("a", ["b"]), _chunk("b", ["a"])])
    assert exc.value.remediation == (
        "Fix the chunk dependency declarations to remove the cycle."
    )


# --- orphan detection -------------------------------------------------------


@pytest.mark.unit
def test_unknown_dependency_raises_orphan_error() -> None:
    """A dependency on an undeclared chunk_id raises OrphanDependencyError."""
    with pytest.raises(OrphanDependencyError) as exc:
        ChunkDAG([_chunk("a", ["ghost"])])
    message = str(exc.value)
    assert "'a'" in message
    assert "'ghost'" in message


@pytest.mark.unit
def test_orphan_error_carries_remediation() -> None:
    """The orphan error exposes the canonical remediation text."""
    with pytest.raises(OrphanDependencyError) as exc:
        ChunkDAG([_chunk("a", ["ghost"])])
    assert exc.value.remediation == (
        "Declare the missing chunk or remove the dangling dependency."
    )


@pytest.mark.unit
def test_orphan_checked_before_cycle() -> None:
    """A graph with both defects reports the orphan first (referential check)."""
    with pytest.raises(OrphanDependencyError):
        ChunkDAG([_chunk("a", ["b", "ghost"]), _chunk("b", ["a"])])


# --- readiness helper -------------------------------------------------------


@pytest.mark.unit
def test_ready_chunks_with_no_dependencies() -> None:
    """Root PENDING chunks are ready immediately."""
    a, b = _chunk("a"), _chunk("b", ["a"])
    dag = ChunkDAG([a, b])
    assert dag.ready_chunks() == [a]


@pytest.mark.unit
def test_ready_chunks_after_dependency_completes() -> None:
    """A dependent becomes ready once every dependency is COMPLETED."""
    a = _chunk("a", status=ChunkStatus.COMPLETED)
    b = _chunk("b", ["a"])
    dag = ChunkDAG([a, b])
    assert dag.ready_chunks() == [b]


@pytest.mark.unit
def test_ready_chunks_partial_dependency_completion() -> None:
    """A chunk with one COMPLETED and one PENDING dependency is not ready."""
    a = _chunk("a", status=ChunkStatus.COMPLETED)
    b = _chunk("b")
    c = _chunk("c", ["a", "b"])
    dag = ChunkDAG([a, b, c])
    assert dag.ready_chunks() == [b]


@pytest.mark.unit
@pytest.mark.parametrize(
    "status",
    [ChunkStatus.IN_PROGRESS, ChunkStatus.COMPLETED, ChunkStatus.FAILED],
)
def test_non_pending_chunks_are_never_ready(status: ChunkStatus) -> None:
    """Only PENDING chunks are returned, regardless of dependency state."""
    dag = ChunkDAG([_chunk("a", status=status)])
    assert dag.ready_chunks() == []


@pytest.mark.unit
def test_ready_chunks_are_returned_in_chunk_id_order() -> None:
    """Multiple ready chunks come back sorted by chunk_id."""
    dag = ChunkDAG([_chunk("z"), _chunk("a"), _chunk("m")])
    assert [ch.chunk_id for ch in dag.ready_chunks()] == ["a", "m", "z"]
