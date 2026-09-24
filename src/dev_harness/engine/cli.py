"""Engine CLI for the Phase 8 acceptance protocol (V11 8.20).

The 8.D protocol drives the SDLC pipeline through ``python -m
dev_harness.engine.cli``, so this module provides the three subcommands it
invokes:

* ``run`` - plan a requirement into chunks, assemble the graph
  (:func:`~dev_harness.engine.pipeline.build_graph`) and execute it end to end.
* ``plan`` - plan a requirement into chunks and validate the DAG; with
  ``--print-dag`` the topological order is printed (one ``chunk_id`` per line).
* ``critic-drill`` - drive the Critic node and prove its read-only containment:
  an attempted requirements write is rejected with ``CriticScopeViolation`` and
  the PAUSE/RESUME state diff stays confined to ``tui_state``.

All three are **offline**: ``--mock`` builds a deterministic client backed by
:class:`tests.support.mock_llm.MockLLM`. There is no live network path in this
build - omitting ``--mock`` fails closed with a remediation.

Requirement file format (deterministic, no LLM needed to plan): one chunk per
non-blank, non-``#`` line. A line may be ``<id>: <title>`` and may carry
dependencies as ``... deps: <id>, <id>``; a line with no ``<id>:`` prefix is
auto-numbered ``c1``, ``c2``, ... so a free-text requirement becomes a single
chunk. This is the fixture format the 8.D runner uses for ``req_simple.md`` /
``req_diamond.md`` / ``req_conflicting.md``.

No clock, no randomness, no network beyond the mock client.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from langchain_core.runnables import RunnableConfig

from dev_harness.contracts.enums import CriticCommand
from dev_harness.contracts.errors import EngineError, HarnessError
from dev_harness.contracts.events import Envelope
from dev_harness.contracts.llm import Message, Usage
from dev_harness.contracts.state import Chunk, HarnessState, TuiState
from dev_harness.engine.dag import ChunkDAG
from dev_harness.engine.nodes.critic import (
    CriticNode,
    apply_command,
    guard_scope,
    state_diff,
)
from dev_harness.engine.pipeline import PipelineConfig, build_graph, initial_state
from dev_harness.engine.state import HarnessStateChannels
from dev_harness.engine.worker_workspace import WorkerWorkspace

#: Successful completion (the acceptance rows are all "exit 0").
EXIT_OK = 0
#: A spec/usage failure (cycle, missing requirement, missing ``--mock``).
EXIT_ERROR = 2

#: The default per-chunk test command run inside each worker's worktree (8.11).
_TEST_COMMAND: tuple[str, ...] = (
    sys.executable,
    "-m",
    "pytest",
    "-q",
    "-p",
    "no:cacheprovider",
)

_STRUCTURED = re.compile(r"^(?P<id>[A-Za-z0-9_.-]+)\s*:\s*(?P<title>.+)$")

# The scripted persona replies the ``--mock`` client returns, keyed by the role
# named in the persona prompt header (``# Persona: <Role>``). ``Developer``
# returns a file-write map (8.10) whose suite is green, so a mock run reaches
# ``chunk_dag[*].status == COMPLETED``.
_PERSONA_RESPONSES: dict[str, str] = {
    "Groomer": json.dumps(
        {
            "status": "LOCKED",
            "prd_content": "Mock requirement is locked.",
            "version": "V7",
            "locked_at_timestamp": 1_700_000_000,
        }
    ),
    "Architect": json.dumps(
        {
            "architecture_spec": "A single module implementing the requirement.",
            "interface_contracts": {"openapi_spec": "{}", "db_schema": ""},
            "status": "APPROVED",
        }
    ),
    "Developer": json.dumps(
        {"tests/test_chunk_mock.py": "def test_chunk_mock():\n    assert True\n"}
    ),
    "Critic": json.dumps({"verdict": "APPROVED", "target": "chunk"}),
}

_ROLE_PREFIX = "# Persona: "


class _ScriptedLLM(Protocol):
    """The minimal surface of ``MockLLM`` this CLI relies on."""

    def complete(self, prompt: str) -> tuple[str, object]:
        """Return deterministic (text, usage) for a prompt."""
        ...

    def count_tokens(self, text: str) -> int:
        """Return a deterministic token count for text."""
        ...


def _role_of(messages: list[Message]) -> str:
    """Extract the persona role from a message list's system prompt header."""
    if not messages:
        return ""
    first = messages[0].content
    if not first.startswith(_ROLE_PREFIX):
        return ""
    return first[len(_ROLE_PREFIX) :].splitlines()[0].strip()


class _MockPersonaClient:
    """A deterministic :class:`CompletionClient` over ``MockLLM``.

    Persona calls are answered from :data:`_PERSONA_RESPONSES` so the pipeline
    completes; any other call is delegated to the injected ``MockLLM`` so the
    mock is a genuine, single source of determinism. No network.
    """

    def __init__(self, llm: _ScriptedLLM) -> None:
        self._llm = llm

    async def complete(
        self, messages: list[Message], *, model: str | None = None
    ) -> tuple[str, Usage]:
        """Return a scripted persona reply, else the mock's completion."""
        role = _role_of(messages)
        input_tokens = sum(self._llm.count_tokens(m.content) for m in messages)
        scripted = _PERSONA_RESPONSES.get(role)
        if scripted is None:
            prompt = "\n\n".join(f"{m.role}: {m.content}" for m in messages)
            text, _usage = self._llm.complete(prompt)
            return text, Usage(
                input_tokens=input_tokens, output_tokens=self._llm.count_tokens(text)
            )
        return scripted, Usage(
            input_tokens=input_tokens, output_tokens=self._llm.count_tokens(scripted)
        )


def _mock_persona_client() -> _MockPersonaClient:
    """Build the ``--mock`` client over ``tests.support.mock_llm.MockLLM``."""
    from tests.support.mock_llm import MockLLM

    return _MockPersonaClient(MockLLM(seed="engine-cli"))


def parse_chunks(text: str) -> list[Chunk]:
    """Parse a requirement into a deterministic chunk list.

    See the module docstring for the line grammar. Dependencies are attached
    verbatim; :class:`~dev_harness.engine.dag.ChunkDAG` validates referential
    integrity and acyclicity.
    """
    chunks: list[Chunk] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _STRUCTURED.match(line)
        if match is None:
            chunks.append(Chunk(chunk_id=f"c{len(chunks) + 1}", title=line))
            continue
        title, sep, dep_text = match.group("title").partition(" deps:")
        deps = [d.strip() for d in dep_text.split(",") if d.strip()] if sep else []
        chunks.append(
            Chunk(chunk_id=match.group("id"), title=title.strip(), dependencies=deps)
        )
    if not chunks:
        raise EngineError(
            f"requirement '{text[:40]}' declares no chunks.",
            remediation="Add at least one non-comment line to the requirement file.",
        )
    return chunks


def _read_requirement(path: str) -> str:
    """Read a requirement file, failing closed with a remediation if absent."""
    requirement = Path(path)
    if not requirement.is_file():
        raise EngineError(
            f"requirement file not found: {path}",
            remediation="Point --requirement at an existing markdown file.",
        )
    return requirement.read_text(encoding="utf-8")


def _require_mock(mock: bool) -> None:
    """Fail closed when ``--mock`` is absent (no live provider in this build)."""
    if not mock:
        raise EngineError(
            "only '--mock' is supported in this build.",
            remediation="Re-run with --mock (no live provider adapters are wired).",
        )


# -- plan -------------------------------------------------------------------


def cmd_plan(args: argparse.Namespace) -> int:
    """Plan a requirement, validate the DAG, and (optionally) print its order."""
    _require_mock(cast("bool", args.mock))
    chunks = parse_chunks(_read_requirement(cast("str", args.requirement)))
    dag = ChunkDAG(chunks)  # raises CyclicDependencyError / OrphanDependencyError
    order = dag.topological_order()
    if cast("bool", args.print_dag):
        for chunk in order:
            print(chunk.chunk_id)
    else:
        print(f"chunks: {len(order)}")
    return EXIT_OK


# -- run --------------------------------------------------------------------


class _TraceCollector:
    """Collects emitted envelopes so ``--trace`` can persist a run trace."""

    def __init__(self) -> None:
        self.envelopes: list[Envelope] = []

    def __call__(self, envelope: Envelope) -> None:
        self.envelopes.append(envelope)

    def write(self, workspace: str) -> Path:
        """Write the collected trace under ``<workspace>/reports/``."""
        reports = Path(workspace) / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        target = reports / "parallel_trace.json"
        target.write_text(
            json.dumps([e.model_dump(mode="json") for e in self.envelopes], indent=2),
            encoding="utf-8",
        )
        return target


def cmd_run(args: argparse.Namespace) -> int:
    """Plan, assemble and execute the SDLC graph against the mock client."""
    _require_mock(cast("bool", args.mock))
    requirement = cast("str", args.requirement)
    text = _read_requirement(requirement)
    chunks = parse_chunks(text)
    ChunkDAG(chunks)  # fail fast on a cyclic/orphan requirement

    workspace = WorkerWorkspace(cast("str", args.workspace))
    collector = _TraceCollector() if cast("bool", args.trace) else None
    config = PipelineConfig(
        client=_mock_persona_client(),
        workspace=workspace,
        chunks=chunks,
        max_parallel_workers=cast("int", args.max_parallel),
        test_command=_TEST_COMMAND,
        thread_id="sdlc",
        raw_input=text,
        emit=collector,
    )
    graph = build_graph(config)
    run_config: RunnableConfig = {"configurable": {"thread_id": config.thread_id}}
    asyncio.run(graph.ainvoke(initial_state(config), run_config))

    values = dict(graph.get_state(run_config).values)
    state = HarnessState.model_validate(values)
    for chunk in state.chunk_dag:
        print(f"{chunk.chunk_id}: {chunk.status.value}")
    print(f"gate: {state.tui_state.critic_gatekeeper_status.value}")

    if collector is not None:
        # ``--resume`` reuses the thread id; node idempotency makes a replay safe.
        trace_path = collector.write(cast("str", args.workspace))
        print(f"trace: {trace_path}")
    return EXIT_OK


# -- critic-drill -----------------------------------------------------------


@dataclass(frozen=True)
class DrillResult:
    """The critic containment drill's evidence."""

    violation: str
    diff: set[str]


def critic_drill(client: _MockPersonaClient) -> DrillResult:
    """Drive the Critic node and prove its read-only containment (8.14, 8.D 9).

    Runs the Critic node to a valid verdict, then shows that adding a
    ``groomed_requirements`` write to its update is rejected with
    ``CriticScopeViolation``, and that a PAUSE/RESUME command diff is confined
    to ``tui_state``.
    """
    state: HarnessStateChannels = {
        "project_id": "critic-drill",
        "workspace_path": ".",
        "thread_id": "critic-drill",
        "raw_input": "",
        "tui_state": TuiState(),
    }
    node = CriticNode(client)
    update = asyncio.run(node(state))
    tui = update["tui_state"]

    violation = ""
    try:
        # The Critic attempting a requirements write is the out-of-scope update.
        guard_scope({"tui_state": tui, "groomed_requirements": None})
    except HarnessError as exc:
        violation = f"{type(exc).__name__}: {exc}"

    before = HarnessState(
        project_id="critic-drill", workspace_path=".", thread_id="critic-drill"
    )
    after = apply_command(before, CriticCommand.PAUSE, timestamp=1)
    return DrillResult(violation=violation, diff=state_diff(before, after))


def cmd_critic_drill(args: argparse.Namespace) -> int:
    """Run the critic containment drill and print its evidence."""
    _require_mock(cast("bool", args.mock))
    result = critic_drill(_mock_persona_client())
    print(result.violation)
    print(f"diff: {sorted(result.diff)}")
    if not result.violation or result.diff - {"tui_state"}:
        raise EngineError(
            "critic drill failed to demonstrate containment.",
            remediation="Investigate engine/nodes/critic.py scope enforcement.",
        )
    return EXIT_OK


# -- entry point ------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """``python -m dev_harness.engine.cli`` entry point."""
    parser = argparse.ArgumentParser(prog="dev_harness.engine.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Execute the SDLC pipeline")
    run.add_argument("--workspace", default=".", help="Target git repository")
    run.add_argument("--requirement", required=True, help="Requirement markdown file")
    run.add_argument("--max-parallel", type=int, default=1, dest="max_parallel")
    run.add_argument("--mock", action="store_true", help="Use the deterministic mock")
    run.add_argument("--trace", action="store_true", help="Persist a run trace")
    run.add_argument("--resume", action="store_true", help="Reuse the run thread id")
    run.set_defaults(func=cmd_run)

    plan = sub.add_parser("plan", help="Plan and validate the chunk DAG")
    plan.add_argument("--workspace", default=".", help="Target git repository")
    plan.add_argument("--requirement", required=True, help="Requirement markdown file")
    plan.add_argument("--mock", action="store_true", help="Use the deterministic mock")
    plan.add_argument("--print-dag", action="store_true", dest="print_dag")
    plan.set_defaults(func=cmd_plan)

    drill = sub.add_parser("critic-drill", help="Prove the Critic's containment")
    drill.add_argument("--workspace", default=".", help="Target git repository")
    drill.add_argument("--mock", action="store_true", help="Use the deterministic mock")
    drill.set_defaults(func=cmd_critic_drill)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except HarnessError as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "EXIT_ERROR",
    "EXIT_OK",
    "DrillResult",
    "cmd_critic_drill",
    "cmd_plan",
    "cmd_run",
    "critic_drill",
    "main",
    "parse_chunks",
]
