"""SDLC graph assembly: ``build_graph(config)`` (V11 8.18).

This is the **integration** task of Phase 8: it wires the already-built parts
into one LangGraph state machine and compiles it with a checkpointer. Assembly
is deliberately thin - every node delegates to its module:

* Groomer (:func:`~dev_harness.engine.nodes.groomer.make_groomer_node`) turns
  ``raw_input`` into a locked PRD.
* Architect (:func:`~dev_harness.engine.nodes.architect.make_architect_node`)
  produces the technical design.
* ``develop`` runs the chunk DAG through the worker pool (8.7). Each chunk is
  bound to its own worktree (8.8), implemented by the Developer node (8.10) and
  verified by the Tester node (8.11) - **in the worktree**, so the green unit
  test is genuinely executed. A failing chunk is retried, bounded by the e2e
  ceiling in :func:`~dev_harness.engine.routing.route`.
* ``integrate`` merges the chunk branches into the primary branch (8.9).
* Critic (:func:`~dev_harness.engine.nodes.critic.make_critic_node`) publishes
  the terminal binary gate verdict.

Retry routing (8.13) is wired as a conditional edge after ``develop``; the HITL
escalation target (8.15) is a terminal node. Context budgeting (8.16) is applied
by wrapping the injected client in :class:`_BudgetedClient` when a model entry is
supplied, so every persona prompt is clipped to ``context_window - max_output``.

Checkpointer note: the plan says "compiled with ``SqliteSaver``", but
``langgraph.checkpoint.sqlite`` is **not** an installed dependency. Following
8.15, the graph takes an injectable checkpointer defaulting to
:class:`~langgraph.checkpoint.memory.InMemorySaver`; a SQLite-backed
:class:`~langgraph.checkpoint.base.BaseCheckpointSaver` can be swapped in without
touching this module.

No clock, no randomness, no network: the client is injected (``MockLLM`` or a
scripted fake in tests).
"""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from dev_harness.contracts.enums import ChunkStatus, ExecutionState
from dev_harness.contracts.errors import EngineError
from dev_harness.contracts.events import Envelope
from dev_harness.contracts.llm import Message, Usage
from dev_harness.contracts.state import Chunk, TuiState
from dev_harness.engine.context import build_prompt
from dev_harness.engine.dag import ChunkDAG
from dev_harness.engine.integrator import Integrator
from dev_harness.engine.nodes.architect import make_architect_node
from dev_harness.engine.nodes.critic import make_critic_node
from dev_harness.engine.nodes.developer import make_developer_node
from dev_harness.engine.nodes.groomer import make_groomer_node
from dev_harness.engine.nodes.tester import make_tester_node
from dev_harness.engine.personas.validators import CompletionClient
from dev_harness.engine.routing import (
    ARCHITECT_NODE,
    DEVELOPER_NODE,
    HITL_NODE,
    FailureKind,
    route,
)
from dev_harness.engine.state import HarnessStateChannels
from dev_harness.engine.worker_pool import WorkerPool
from dev_harness.engine.worker_workspace import WorkerWorkspace
from dev_harness.providers.registry import ModelEntry

# Node names of the assembled graph. ``architect``/``developer``/``hitl`` reuse
# the canonical routing constants so a :class:`RouteDecision` maps 1:1 to a node.
GROOMER_NODE = "groomer"
DEVELOP_NODE = DEVELOPER_NODE
INTEGRATE_NODE = "integrate"
CRITIC_NODE = "critic"

#: The compiled SDLC graph type (state, context, input, output).
CompiledSdlcGraph = CompiledStateGraph[
    HarnessStateChannels, None, HarnessStateChannels, HarnessStateChannels
]


class _BudgetedClient:
    """A :class:`CompletionClient` that clips every prompt to a model budget.

    Wraps the injected client and applies
    :func:`~dev_harness.engine.context.build_prompt` for ``entry`` so the
    estimated prompt tokens never exceed ``context_window - max_output`` (8.16).
    """

    def __init__(self, inner: CompletionClient, entry: ModelEntry) -> None:
        self._inner = inner
        self._entry = entry

    async def complete(
        self, messages: list[Message], *, model: str | None = None
    ) -> tuple[str, Usage]:
        """Clip ``messages`` to the budget, then delegate to the inner client."""
        clipped = build_prompt(messages, entry=self._entry)
        return await self._inner.complete(clipped, model=model)


@dataclass(frozen=True)
class PipelineConfig:
    """The inputs :func:`build_graph` needs to assemble the SDLC graph.

    :param client: the injected LLM client (never a live provider in tests).
    :param workspace: the per-worker worktree binding (8.8).
    :param chunks: the planned chunks carried into the DAG (8.6).
    :param max_parallel_workers: the worker-pool concurrency ceiling (8.7).
    :param checkpointer: the LangGraph checkpointer; defaults to ``InMemorySaver``.
    :param test_command: the tester's command; defaults to ``pytest -q``.
    :param test_timeout: wall-clock seconds before a chunk suite is killed.
    :param emit: optional ``TEST_PROGRESS`` envelope sink for the tester.
    :param thread_id: the checkpointer thread id for a run.
    :param model: an optional model id passed to every persona node.
    :param model_entry: when set, persona prompts are budgeted against it (8.16).
    :param integrator: the merge strategy; defaults to one rooted at the workspace.
    :param project_id: the identity channel written into the graph state.
    :param raw_input: the requirement text groomed by the first node.
    """

    client: CompletionClient
    workspace: WorkerWorkspace
    chunks: Sequence[Chunk]
    max_parallel_workers: int = 1
    checkpointer: BaseCheckpointSaver[Any] | None = None
    test_command: Sequence[str] | None = None
    test_timeout: float = 300.0
    emit: Callable[[Envelope], None] | None = None
    thread_id: str = "sdlc"
    model: str | None = None
    model_entry: ModelEntry | None = None
    integrator: Integrator | None = None
    project_id: str = "pipeline"
    raw_input: str = ""


class _Pipeline:
    """Builds the node callables and holds per-run bookkeeping for one graph."""

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config
        self._client: CompletionClient = (
            _BudgetedClient(config.client, config.model_entry)
            if config.model_entry is not None
            else config.client
        )

    # -- develop: chunk DAG through the worker pool -------------------------

    async def develop(self, state: HarnessStateChannels) -> dict[str, Any]:
        """Run the chunk DAG through the worker pool and return ``chunk_dag``.

        The pool is synchronous (it blocks on its own thread pool), so it runs in
        a worker thread via :func:`asyncio.to_thread` and the async Developer
        nodes are scheduled back onto this (main) event loop with
        :func:`asyncio.run_coroutine_threadsafe`. Keeping every coroutine on the
        main loop is what lets the caller block sockets for the whole run - a
        nested :func:`asyncio.run` would create a second loop whose self-pipe
        needs a socket.

        A failed chunk is reset to ``PENDING`` for a bounded retry (the e2e
        ceiling in :func:`~dev_harness.engine.routing.route` terminates the loop)
        and the ``e2e_retry_count`` channel is incremented so the router sees it.
        """
        chunks = self.config.chunks
        for chunk in chunks:
            if chunk.status is ChunkStatus.FAILED:
                chunk.status = ChunkStatus.PENDING

        loop = asyncio.get_running_loop()
        failed = False
        try:
            await asyncio.to_thread(self._run_pool, state, loop)
        except EngineError:
            failed = True

        update: dict[str, Any] = {"chunk_dag": list(chunks)}
        if failed:
            update["e2e_retry_count"] = 1
        return update

    def _run_pool(
        self, state: HarnessStateChannels, loop: asyncio.AbstractEventLoop
    ) -> None:
        """Run the worker pool to completion on a worker thread."""
        WorkerPool(
            ChunkDAG(self.config.chunks),
            self.config.max_parallel_workers,
            lambda chunk: self._run_chunk(chunk, state, loop),
        ).run()

    def _run_chunk(
        self,
        chunk: Chunk,
        state: HarnessStateChannels,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        """Implement and test one chunk inside its bound worktree.

        Runs on a worker-pool thread: the async Developer node is awaited on the
        main loop, then the sync Tester node runs the chunk's suite. A red suite
        raises :class:`EngineError` so the pool marks the chunk ``FAILED``.
        """
        worker_id = chunk.assigned_worker_id
        assert worker_id is not None  # the pool stamps this before executing
        self.config.workspace.bind(worker_id, chunk)
        developer = make_developer_node(
            self._client, self.config.workspace, chunk, model=self.config.model
        )
        asyncio.run_coroutine_threadsafe(developer(state), loop).result()
        tester = make_tester_node(
            self.config.workspace,
            chunk,
            command=self.config.test_command,
            timeout=self.config.test_timeout,
            emit=self.config.emit,
        )
        tester(state)
        if chunk.status is ChunkStatus.FAILED:
            raise EngineError(
                f"chunk '{chunk.chunk_id}' did not pass its test suite.",
                remediation="Fix the chunk implementation and retry the run.",
            )
        self._commit_chunk(chunk)

    def _commit_chunk(self, chunk: Chunk) -> None:
        """Commit the chunk's work on its branch so the integrator can merge it.

        The developer writes into the worktree but never commits; without this
        the chunk branch would equal the primary branch and integration (8.9)
        would be a no-op. The worktree shares the primary's git config, so the
        commit uses the workspace's configured identity.
        """
        root = self.config.workspace.root_for(chunk)
        for args in (("add", "-A"), ("commit", "-m", f"chunk {chunk.chunk_id}")):
            subprocess.run(
                ["git", "-C", str(root), *args],
                capture_output=True,
                text=True,
                check=True,
            )

    # -- integrate / hitl ---------------------------------------------------

    def integrate(self, state: HarnessStateChannels) -> dict[str, Any]:
        """Merge the chunk branches into the primary branch (8.9)."""
        dag = ChunkDAG(state.get("chunk_dag", []))
        integrator = self.config.integrator or Integrator(self.config.workspace.repo)
        integrator.integrate(dag.topological_order())
        return {}

    def hitl(self, state: HarnessStateChannels) -> dict[str, Any]:
        """Terminal HITL escalation: pause the gate and stop the run (8.15)."""
        tui = state.get("tui_state") or TuiState()
        return {
            "tui_state": tui.model_copy(
                update={"critic_gatekeeper_status": ExecutionState.STOPPED}
            )
        }

    def route_after_develop(self, state: HarnessStateChannels) -> str:
        """Conditional edge: integrate on success, else route the failure (8.13)."""
        chunks = state.get("chunk_dag", [])
        if not any(chunk.status is ChunkStatus.FAILED for chunk in chunks):
            return INTEGRATE_NODE
        decision = route(state, failure_kind=FailureKind.E2E)
        if decision.escalate_to_hitl:
            return HITL_NODE
        return decision.next_node or DEVELOP_NODE

    # -- assembly -----------------------------------------------------------

    def build(self) -> CompiledSdlcGraph:
        """Assemble and compile the SDLC graph with the configured checkpointer."""
        model = self.config.model
        graph: StateGraph[HarnessStateChannels] = StateGraph(HarnessStateChannels)
        graph.add_node(GROOMER_NODE, make_groomer_node(self._client, model=model))
        graph.add_node(ARCHITECT_NODE, make_architect_node(self._client, model=model))
        graph.add_node(DEVELOP_NODE, self.develop)
        graph.add_node(INTEGRATE_NODE, self.integrate)
        graph.add_node(CRITIC_NODE, make_critic_node(self._client, model=model))
        graph.add_node(HITL_NODE, self.hitl)

        graph.add_edge(START, GROOMER_NODE)
        graph.add_edge(GROOMER_NODE, ARCHITECT_NODE)
        graph.add_edge(ARCHITECT_NODE, DEVELOP_NODE)
        graph.add_conditional_edges(
            DEVELOP_NODE,
            self.route_after_develop,
            [INTEGRATE_NODE, DEVELOP_NODE, HITL_NODE],
        )
        graph.add_edge(INTEGRATE_NODE, CRITIC_NODE)
        graph.add_edge(CRITIC_NODE, END)
        graph.add_edge(HITL_NODE, END)

        saver = self.config.checkpointer or InMemorySaver()
        return graph.compile(checkpointer=saver)


def build_graph(config: PipelineConfig) -> CompiledSdlcGraph:
    """Build the Groomer -> Architect -> workers -> integrator -> Critic graph.

    Returns a compiled, checkpointer-backed graph. As the persona nodes are
    async, invoke it with ``await graph.ainvoke(initial_state(config),
    config_run)`` where ``config_run`` carries the ``thread_id``.
    """
    return _Pipeline(config).build()


def initial_state(config: PipelineConfig) -> HarnessStateChannels:
    """The identity channels seeded into :func:`build_graph`'s graph."""
    return {
        "project_id": config.project_id,
        "workspace_path": str(config.workspace.repo),
        "thread_id": config.thread_id,
        "raw_input": config.raw_input,
    }

__all__ = [
    "ARCHITECT_NODE",
    "CRITIC_NODE",
    "DEVELOP_NODE",
    "GROOMER_NODE",
    "HITL_NODE",
    "INTEGRATE_NODE",
    "CompiledSdlcGraph",
    "PipelineConfig",
    "build_graph",
    "initial_state",
]
