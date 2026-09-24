"""HITL gate via LangGraph ``interrupt_before`` + ``Command(resume=...)`` (V11 8.15).

The human-in-the-loop gate halts the SDLC graph *before* the approval node and
persists a checkpoint, so an operator can inspect the locked artifacts and then
resume. Two properties are load-bearing and their acceptance is exact (8.B):

* **Halt with a persisted checkpoint.** The graph is compiled with
  ``interrupt_before=[APPROVAL_NODE]``; after :meth:`HitlGate.start` the graph is
  suspended at the approval node and the checkpointer holds a checkpoint for the
  thread (:meth:`HitlGate.checkpoint_persisted`).
* **Resume advances exactly one node.** :meth:`HitlGate.resume` pulls one command
  from the injected :class:`ResumeSource` and invokes ``Command(resume=...)``,
  which runs the approval node and nothing more.

The resume trigger is the TUI critic bar's ``INTERRUPT_REQUEST`` (7.6). To keep
the gate free of sockets and of any ``tui/`` import, the command is delivered
through an injected :class:`ResumeSource`; :class:`QueueResumeSource` is the
deterministic test double.

Checkpointer note: the plan says "compiled with ``SqliteSaver``", but
``langgraph.checkpoint.sqlite`` is not an installed dependency and the project's
``storage.sqlite_saver.SqliteSaver`` is a custom ``HarnessState`` store, not a
LangGraph :class:`~langgraph.checkpoint.base.BaseCheckpointSaver`. The gate
therefore takes an injectable checkpointer and defaults to the in-memory saver
shipped with ``langgraph-checkpoint``; a SQLite-backed saver can be swapped in
without touching this module.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph._node import _Node
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from dev_harness.contracts.enums import CriticCommand
from dev_harness.contracts.errors import EngineError
from dev_harness.engine.state import HarnessStateChannels

#: Node names in the minimal HITL graph.
PREPARE_NODE = "prepare"
APPROVAL_NODE = "approval"

#: A graph node: reads the channels, returns a partial update.
NodeFn = _Node[HarnessStateChannels]

#: The compiled minimal graph type (state, context, input, output).
CompiledHitlGraph = CompiledStateGraph[
    HarnessStateChannels, None, HarnessStateChannels, HarnessStateChannels
]


class HitlResumeError(EngineError):
    """A resume was requested without a usable command from the resume source."""

    remediation = (
        "Publish a RESUME/STOP INTERRUPT_REQUEST from the critic bar before "
        "resuming the halted graph."
    )


class ResumeSource(Protocol):
    """Supplies the next gatekeeper command that drives ``Command(resume=...)``."""

    def next_command(self) -> CriticCommand | None:
        """Return the next command, or ``None`` when none is available."""
        ...


class QueueResumeSource:
    """Deterministic :class:`ResumeSource` for tests (no socket, no clock)."""

    def __init__(self, commands: Sequence[CriticCommand]) -> None:
        self._commands: list[CriticCommand] = list(commands)

    def next_command(self) -> CriticCommand | None:
        """Pop and return the next queued command, or ``None`` when exhausted."""
        return self._commands.pop(0) if self._commands else None


def _passthrough(state: HarnessStateChannels) -> dict[str, Any]:
    """A trivial node that writes nothing (used when no node is injected)."""
    return {}


class HitlGate:
    """A minimal graph that halts before approval and resumes one node at a time."""

    def __init__(
        self,
        *,
        checkpointer: BaseCheckpointSaver[Any] | None = None,
        resume_source: ResumeSource | None = None,
        approval_node: NodeFn | None = None,
        prepare_node: NodeFn | None = None,
        thread_id: str = "hitl",
    ) -> None:
        self._saver: BaseCheckpointSaver[Any] = checkpointer or InMemorySaver()
        self._resume_source = resume_source
        self._thread_id = thread_id
        self._executed: list[str] = []
        self._approval_node: NodeFn = approval_node or _passthrough
        self._prepare_node: NodeFn = prepare_node or _passthrough
        self._app = self._build()

    def _build(self) -> CompiledHitlGraph:
        """Assemble and compile the minimal graph with the approval breakpoint."""
        graph: StateGraph[HarnessStateChannels] = StateGraph(HarnessStateChannels)
        graph.add_node(PREPARE_NODE, self._wrap(PREPARE_NODE, self._prepare_node))
        graph.add_node(APPROVAL_NODE, self._wrap(APPROVAL_NODE, self._approval_node))
        graph.add_edge(START, PREPARE_NODE)
        graph.add_edge(PREPARE_NODE, APPROVAL_NODE)
        graph.add_edge(APPROVAL_NODE, END)
        return graph.compile(checkpointer=self._saver, interrupt_before=[APPROVAL_NODE])

    def _wrap(self, name: str, fn: NodeFn) -> NodeFn:
        """Wrap a node so the gate records exactly which nodes executed."""

        def node(state: HarnessStateChannels) -> dict[str, Any]:
            self._executed.append(name)
            return cast("dict[str, Any]", fn(state))

        return node

    @property
    def config(self) -> RunnableConfig:
        """The LangGraph run config for this gate's thread."""
        return {"configurable": {"thread_id": self._thread_id}}

    @property
    def executed_nodes(self) -> list[str]:
        """Names of the nodes that have executed, in order."""
        return list(self._executed)

    def start(self, initial: HarnessStateChannels) -> HarnessStateChannels:
        """Run until the approval breakpoint; returns the halted channel values."""
        return cast("HarnessStateChannels", self._app.invoke(initial, self.config))

    def pending_node(self) -> str | None:
        """The node the graph is suspended before, or ``None`` at ``END``."""
        nxt = self._app.get_state(self.config).next
        return nxt[0] if nxt else None

    def is_halted(self) -> bool:
        """True when the graph is suspended at the approval node."""
        return self.pending_node() == APPROVAL_NODE

    def checkpoint_persisted(self) -> bool:
        """True when the checkpointer holds a checkpoint for this thread."""
        return self._saver.get_tuple(self.config) is not None

    def resume(self) -> HarnessStateChannels:
        """Resume with one command, advancing exactly one node (the approval node)."""
        if self._resume_source is None:
            raise HitlResumeError("no resume source injected into the HITL gate")
        command = self._resume_source.next_command()
        if command is None:
            raise HitlResumeError("resume source returned no command")
        return cast(
            "HarnessStateChannels",
            self._app.invoke(Command(resume=command.value), self.config),
        )
