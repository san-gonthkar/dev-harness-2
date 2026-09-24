"""Chunk DAG builder and topological validator (V11 8.6).

Builds a dependency graph from the flat ``list[Chunk]`` carried on the
``chunk_dag`` state channel, rejects structurally invalid graphs, and exposes
the two queries the worker pool (8.7) needs:

* :meth:`ChunkDAG.topological_order` - a deterministic execution order that is
  stable across runs (ready nodes are tie-broken by ``chunk_id``).
* :meth:`ChunkDAG.ready_chunks` - the ``PENDING`` chunks whose every
  dependency is ``COMPLETED``; these are the chunks a worker may claim now.

All ordering guarantees are deterministic: iteration always happens over
``sorted()`` keys so two runs over the same chunk list produce identical
output. No I/O, no clock, no randomness.
"""

from __future__ import annotations

from collections.abc import Iterable

from dev_harness.contracts.enums import ChunkStatus
from dev_harness.contracts.errors import (
    CyclicDependencyError,
    OrphanDependencyError,
)
from dev_harness.contracts.state import Chunk


def _find_cycle(adjacency: dict[str, list[str]]) -> list[str]:
    """Return a cycle as a node path, or ``[]`` when the graph is acyclic.

    Nodes in each adjacency list are already sorted by the caller, and the
    outer loop visits nodes in sorted order, so the returned cycle is stable
    for a given graph.
    """
    white, grey, black = 0, 1, 2
    color: dict[str, int] = {node: white for node in adjacency}
    path: list[str] = []

    def visit(node: str) -> list[str]:
        color[node] = grey
        path.append(node)
        for dep in adjacency[node]:
            if color[dep] == grey:
                # Back edge: the cycle is the path tail starting at dep.
                return path[path.index(dep) :]
            if color[dep] == white:
                found = visit(dep)
                if found:
                    return found
        path.pop()
        color[node] = black
        return []

    for node in sorted(adjacency):
        if color[node] == white:
            found = visit(node)
            if found:
                return found
    return []


class ChunkDAG:
    """A validated chunk dependency graph.

    Construction validates referential integrity (no orphan dependencies) and
    acyclicity, raising the matching :class:`~dev_harness.contracts.errors.EngineError`
    subclass on violation.
    """

    def __init__(self, chunks: Iterable[Chunk]) -> None:
        self._chunks: dict[str, Chunk] = {c.chunk_id: c for c in chunks}
        self._adjacency: dict[str, list[str]] = {
            cid: sorted(chunk.dependencies)
            for cid, chunk in self._chunks.items()
        }
        self._validate_orphans()
        self._validate_acyclic()
        self._order: list[str] = self._compute_order()

    def _validate_orphans(self) -> None:
        """Raise :class:`OrphanDependencyError` on an unknown dependency id."""
        known = set(self._chunks)
        for chunk_id in sorted(self._chunks):
            for dep in self._adjacency[chunk_id]:
                if dep not in known:
                    msg = (
                        f"Chunk '{chunk_id}' depends on unknown chunk "
                        f"'{dep}'."
                    )
                    raise OrphanDependencyError(msg)

    def _validate_acyclic(self) -> None:
        """Raise :class:`CyclicDependencyError` naming both ids in the cycle."""
        cycle = _find_cycle(self._adjacency)
        if not cycle:
            return
        if len(cycle) == 1:
            pair = (cycle[0], cycle[0])
        else:
            pair = (cycle[0], cycle[1])
        path = " -> ".join([*cycle, cycle[0]])
        msg = (
            f"Cycle detected between chunks '{pair[0]}' and '{pair[1]}' "
            f"({path})."
        )
        raise CyclicDependencyError(msg)

    def _compute_order(self) -> list[str]:
        """Kahn's algorithm with ready nodes tie-broken by ``chunk_id``.

        ``indegree`` counts each node's unmet dependencies; emitting a node
        releases the chunks that depend on it (``dependents``).
        """
        dependents: dict[str, list[str]] = {cid: [] for cid in self._adjacency}
        indegree: dict[str, int] = {}
        for cid, deps in self._adjacency.items():
            indegree[cid] = len(deps)
            for dep in deps:
                dependents[dep].append(cid)
        ready = sorted(cid for cid, degree in indegree.items() if degree == 0)
        order: list[str] = []
        while ready:
            node = ready.pop(0)
            order.append(node)
            for dependent in dependents[node]:
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    ready.append(dependent)
            ready.sort()
        return order

    def topological_order(self) -> list[Chunk]:
        """Return every chunk in a deterministic dependency-respecting order.

        A chunk always appears after all of its dependencies. Ties between
        ready chunks are broken by ``chunk_id`` so the order is stable across
        repeated runs.
        """
        return [self._chunks[cid] for cid in self._order]

    def ready_chunks(self) -> list[Chunk]:
        """Return the ``PENDING`` chunks whose dependencies are all ``COMPLETED``.

        These are the chunks a worker may claim now. A chunk that is already
        ``IN_PROGRESS``, ``COMPLETED``, or ``FAILED`` is never returned, and a
        chunk with any non-``COMPLETED`` dependency is not ready. Results are
        returned in ``chunk_id`` order.
        """
        ready: list[Chunk] = []
        for cid in self._order:
            chunk = self._chunks[cid]
            if chunk.status is not ChunkStatus.PENDING:
                continue
            if all(
                self._chunks[dep].status is ChunkStatus.COMPLETED
                for dep in chunk.dependencies
            ):
                ready.append(chunk)
        return ready
