"""Phase 8 acceptance-protocol driver (V11 8.19, 8.D steps 3-5, 7-8, 10-11).

The 8.D protocol drives the SDLC pipeline through
``python -m dev_harness.engine.cli``. The CLI's ``run`` subcommand covers the
serial baseline, DAG validation, conflict surfacing and the critic drill, but its
``--max-parallel > 1`` path cannot bind a *distinct* worktree per chunk: the
worker pool (8.7) reuses the lowest free worker-id slot, so a chunk scheduled
after an earlier chunk on the same slot resolves to a worktree still bound to
the first chunk's branch (``git merge-base chunk/c2`` -> not a valid object).
This driver therefore composes the **real** engine parts (``WorkerWorkspace``
8.8, the Developer 8.10 and Tester 8.11 nodes, ``Integrator`` 8.9,
``WorkerWorkspace.capture``/``restore`` 8.21b, ``HitlGate`` 8.15,
``engine.context`` 8.16) exactly as ``engine.pipeline`` does, but binds one
worktree per concurrently-running chunk. Every assertion is about real
orchestration (real git worktrees, a real ``pytest`` subprocess per chunk), not
a mock of it.

This is the deviation 8.19 records against the brief's "drive the CLI" note;
see the script header in ``verify_phase_08.sh``.

Subcommands (each prints one JSON object the protocol greps):

* ``parallel <ws> <req> <n>``   - steps 3/4/5 (parallelism, isolation, merge).
* ``failing  <ws> <req>``       - step 7 (bounded retry -> HITL, terminal FAILED).
* ``hitl     <ws>``             - step 8 (halt at approval, resume one node).
* ``budget   <ws>``             - step 10 (prompt budget + 50-line trace cap).
* ``checkpoint <ws>``           - step 11 (capture, restart, restore).

No clock, no randomness, no network.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

# Run as ``python scripts/p8_acceptance_driver.py``: sys.path[0] is ``scripts/``,
# so add the repo root for ``dev_harness`` and ``tests.support`` imports.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.runnables import RunnableConfig

from dev_harness.contracts.enums import ChunkStatus, CriticCommand, ExecutionState, ProviderId
from dev_harness.contracts.errors import IntegrationConflict
from dev_harness.contracts.llm import Message, Usage
from dev_harness.contracts.state import Chunk
from dev_harness.engine.cli import _mock_persona_client, parse_chunks
from dev_harness.engine.context import (
    build_prompt,
    cap_trace,
    count_messages,
    fits_budget,
    prompt_budget,
)
from dev_harness.engine.dag import ChunkDAG
from dev_harness.engine.hitl import HitlGate, QueueResumeSource
from dev_harness.engine.integrator import Integrator
from dev_harness.engine.nodes.developer import make_developer_node
from dev_harness.engine.nodes.tester import make_tester_node
from dev_harness.engine.pipeline import PipelineConfig, build_graph, initial_state
from dev_harness.engine.worker_workspace import WorkerWorkspace
from dev_harness.providers.registry import ModelEntry
from dev_harness.vcs.worktree import WorktreeManager

#: The per-chunk test command, identical to the CLI's (8.11).
TEST_COMMAND: tuple[str, ...] = (
    sys.executable,
    "-m",
    "pytest",
    "-q",
    "-p",
    "no:cacheprovider",
)

#: The file the scripted mock Developer writes into every chunk's worktree.
MOCK_FILE = "tests/test_chunk_mock.py"


def _git(repo: Path, *args: str) -> str:
    """Run ``git -C repo <args>`` and return its stdout (raises on failure)."""
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def _status(repo: Path) -> str:
    """The porcelain status of ``repo`` (empty when the tree is clean)."""
    return _git(repo, "status", "--porcelain").strip()


def _prime_ignore(repo: Path) -> None:
    """Ensure the fixture repo ignores Python build artifacts.

    Every chunk's ``git add -A`` would otherwise commit the ``__pycache__`` from
    its own pytest run, and two chunks committing *different* ``.pyc`` files for
    the same module would surface as a spurious integration conflict.
    """
    ignore = repo / ".gitignore"
    if ignore.exists():
        return
    ignore.write_text(
        "__pycache__/\n*.pyc\n.pytest_cache/\n.dev-harness/\n", encoding="utf-8"
    )
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "fixture: ignore build artifacts")


# -- step 7: a client whose Developer suite is red --------------------------


_RED_GROOMER = json.dumps(
    {
        "status": "LOCKED",
        "prd_content": "Mock requirement is locked.",
        "version": "V7",
        "locked_at_timestamp": 1_700_000_000,
    }
)
_RED_ARCHITECT = json.dumps(
    {
        "architecture_spec": "A single module.",
        "interface_contracts": {"openapi_spec": "{}", "db_schema": ""},
        "status": "APPROVED",
    }
)
_RED_TEST = "def test_chunk_is_red():\n    assert 1 + 1 == 3\n"


class _RedDeveloperClient:
    """A scripted persona client whose Developer suite always fails (step 7).

    Mirrors ``tests/engine/test_sdlc_pipeline.py``'s red client: the roles are
    routed by the persona header, and the Developer writes a failing test, so a
    chunk can never pass and the retry loop must terminate in HITL.
    """

    def __init__(self) -> None:
        self.roles: list[str] = []

    async def complete(
        self, messages: list[Message], *, model: str | None = None
    ) -> tuple[str, Usage]:
        system = messages[0].content
        if system.startswith("# Persona: Groomer"):
            self.roles.append("Groomer")
            return _RED_GROOMER, Usage(1, 1)
        if system.startswith("# Persona: Architect"):
            self.roles.append("Architect")
            return _RED_ARCHITECT, Usage(1, 1)
        if system.startswith("# Persona: Developer"):
            self.roles.append("Developer")
            return json.dumps({MOCK_FILE: _RED_TEST}), Usage(1, 1)
        if system.startswith("# Persona: Critic"):
            self.roles.append("Critic")
            return json.dumps({"verdict": "APPROVED", "target": "chunk"}), Usage(1, 1)
        return "", Usage(0, 0)


# -- step 3/4/5: parallel execution, isolation, merge -----------------------


def _levels(order: list[Chunk]) -> dict[str, int]:
    """The longest-path level of each chunk (a chunk's deps are all lower)."""
    level: dict[str, int] = {}
    for chunk in order:
        level[chunk.chunk_id] = (
            0
            if not chunk.dependencies
            else 1 + max(level[dep] for dep in chunk.dependencies)
        )
    return level


def _rendezvous(order: list[Chunk], max_parallel: int) -> frozenset[str]:
    """The first level with at least ``max_parallel`` chunks (the widest wave)."""
    level = _levels(order)
    groups: dict[int, list[str]] = {}
    for chunk in order:
        groups.setdefault(level[chunk.chunk_id], []).append(chunk.chunk_id)
    for depth in sorted(groups):
        if len(groups[depth]) >= max_parallel:
            return frozenset(groups[depth][:max_parallel])
    return frozenset()


def cmd_parallel(ws: Path, requirement: Path, max_parallel: int) -> dict[str, Any]:
    """Run the diamond's chunks across distinct worktrees, then integrate.

    A chunk is claimed only when every dependency is ``COMPLETED`` and at most
    ``max_parallel`` run at once. Each concurrently-running chunk holds a distinct
    worker slot; a slot whose worktree is still bound to a finished chunk is
    released and rebound (the CLI's slot reuse bug, worked around here). The
    widest wave rendezvous at a :class:`threading.Barrier`, so the peak
    concurrency of exactly ``max_parallel`` is proven, not timed. The chunk
    branches are then merged by the real :class:`Integrator`.
    """
    workspace = WorkerWorkspace(ws)
    order = ChunkDAG(parse_chunks(requirement.read_text(encoding="utf-8"))).topological_order()
    client = _mock_persona_client()
    state: dict[str, Any] = {"technical_design": "mock design"}
    by_id = {chunk.chunk_id: chunk for chunk in order}
    rendezvous = _rendezvous(order, max_parallel)
    _prime_ignore(ws)

    primary_clean_before = _status(ws) == ""
    lock = threading.Lock()
    active = 0
    peak = 0
    barrier = threading.Barrier(max_parallel)
    frames: list[dict[str, str]] = []
    slot_chunk: dict[int, str] = {}

    def bind_slot(chunk: Chunk, slot: int) -> None:
        """Bind ``chunk`` to slot ``slot``, rebinding a stale worktree if needed."""
        worker_id = f"worker-{slot}"
        current = slot_chunk.get(slot)
        if current is not None and current != chunk.chunk_id:
            workspace.release(worker_id)
        workspace.bind(worker_id, chunk)
        slot_chunk[slot] = chunk.chunk_id
        chunk.assigned_worker_id = worker_id

    def work(chunk: Chunk) -> None:
        nonlocal active, peak
        if chunk.chunk_id in rendezvous:
            # Rendezvous: all ``max_parallel`` slots must be occupied at once.
            barrier.wait(timeout=30)
        with lock:
            active += 1
            peak = max(peak, active)
            frames.append({"phase": "start", "worker": str(chunk.assigned_worker_id), "chunk": chunk.chunk_id})
        try:
            asyncio.run(make_developer_node(client, workspace, chunk)(state))
            tester = make_tester_node(workspace, chunk, command=TEST_COMMAND, timeout=120.0)
            tester(state)
            if chunk.status is ChunkStatus.FAILED:
                output = tester.outcome.output if tester.outcome else ""
                raise SystemExit(
                    f"chunk '{chunk.chunk_id}' did not pass its suite:\n{output}"
                )
            root = workspace.root_for(chunk)
            _git(root, "add", "-A")
            _git(root, "commit", "-m", f"chunk {chunk.chunk_id}")
        finally:
            with lock:
                active -= 1
                frames.append({"phase": "end", "worker": str(chunk.assigned_worker_id), "chunk": chunk.chunk_id})

    # Manual schedule: claim ready chunks into free slots, join one when full.
    from concurrent.futures import FIRST_COMPLETED, wait

    from dev_harness.engine.worker_pool import dependencies_completed

    completed: set[str] = set()
    slot_of: dict[str, int] = {}
    free = list(range(1, max_parallel + 1))
    in_flight: dict[Any, Chunk] = {}
    with ThreadPoolExecutor(max_workers=max_parallel) as pool:
        while len(completed) < len(order):
            for chunk in order:
                scheduled = chunk.chunk_id in completed or chunk.chunk_id in slot_of
                if scheduled or not free:
                    continue
                if not dependencies_completed(chunk, by_id):
                    continue
                slot = free.pop(0)
                bind_slot(chunk, slot)
                slot_of[chunk.chunk_id] = slot
                in_flight[pool.submit(work, chunk)] = chunk
            if not in_flight:
                raise SystemExit("graph stalled: no schedulable chunk")
            done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
            for future in done:
                chunk = in_flight.pop(future)
                future.result()
                completed.add(chunk.chunk_id)
                free.append(slot_of.pop(chunk.chunk_id))
                free.sort()

    primary_clean_mid = _status(ws) == ""
    Integrator(ws).integrate(order)
    primary_clean_after = _status(ws) == ""

    # The trace artifact is written last, so it cannot dirty the mid-run check.
    reports = ws / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "parallel_trace.json").write_text(
        json.dumps(frames, indent=2), encoding="utf-8"
    )

    log = _git(ws, "log", "--oneline")
    commits = sum(1 for line in log.splitlines() if "chunk " in line)
    merged_content = (ws / MOCK_FILE).read_text(encoding="utf-8")
    lost = [
        chunk.chunk_id
        for chunk in order
        if merged_content
        != _git(ws, "show", f"chunk/{chunk.chunk_id}:{MOCK_FILE}")
    ]
    return {
        "chunks": [chunk.chunk_id for chunk in order],
        "peak": peak,
        "worktrees": len(WorktreeManager(ws).list()),
        "primary_clean_before": primary_clean_before,
        "primary_clean_mid": primary_clean_mid,
        "primary_clean_after": primary_clean_after,
        "commits": commits,
        "merged": (ws / MOCK_FILE).is_file(),
        "lost_writes": lost,
    }


# -- step 6: conflict surfacing ---------------------------------------------


class _PerChunkClient:
    """A scripted Developer that writes *different* content per chunk (step 6).

    Two chunks that both touch the same file with different content cannot be
    merged, so the :class:`Integrator` must surface an ``IntegrationConflict``.
    The chunk id is read from the developer user prompt (``Implement chunk
    '<id>':``).
    """

    def __init__(self, contents: dict[str, str]) -> None:
        self._contents = contents

    async def complete(
        self, messages: list[Message], *, model: str | None = None
    ) -> tuple[str, Usage]:
        user = messages[-1].content
        chunk_id = user.split("Implement chunk '", 1)[-1].split("'", 1)[0]
        return json.dumps({MOCK_FILE: self._contents.get(chunk_id, "")}), Usage(1, 1)


def cmd_conflict(ws: Path, requirement: Path) -> dict[str, Any]:
    """Surface a merge conflict naming the file and both chunk ids."""
    workspace = WorkerWorkspace(ws)
    order = ChunkDAG(parse_chunks(requirement.read_text(encoding="utf-8"))).topological_order()
    _prime_ignore(ws)
    client = _PerChunkClient(
        {order[0].chunk_id: 'value = "left"\n', order[1].chunk_id: 'value = "right"\n'}
    )
    state: dict[str, Any] = {"technical_design": "mock design"}
    for index, chunk in enumerate(order, start=1):
        chunk.assigned_worker_id = f"worker-{index}"
        workspace.bind(chunk.assigned_worker_id, chunk)
        asyncio.run(make_developer_node(client, workspace, chunk)(state))
        root = workspace.root_for(chunk)
        _git(root, "add", "-A")
        _git(root, "commit", "-m", f"chunk {chunk.chunk_id}")

    head_before = _git(ws, "rev-parse", "HEAD").strip()
    message = ""
    try:
        Integrator(ws).integrate(order)
    except IntegrationConflict as exc:
        message = str(exc)

    return {
        "conflict": bool(message),
        "names_file": MOCK_FILE in message,
        "names_both_chunks": all(chunk.chunk_id in message for chunk in order),
        "head_unmodified": _git(ws, "rev-parse", "HEAD").strip() == head_before,
        "primary_clean": _status(ws) == "",
    }


# -- step 7: bounded retry -> HITL ------------------------------------------


def cmd_failing(ws: Path, requirement: Path) -> dict[str, Any]:
    """Run a requirement whose chunk can never pass; assert a bounded terminal."""
    chunks = parse_chunks(requirement.read_text(encoding="utf-8"))
    client = _RedDeveloperClient()
    config = PipelineConfig(
        client=client,
        workspace=WorkerWorkspace(ws),
        chunks=chunks,
        max_parallel_workers=1,
        test_command=TEST_COMMAND,
        test_timeout=120.0,
        thread_id="p8-failing",
        raw_input=requirement.read_text(encoding="utf-8"),
    )
    graph = build_graph(config)
    run_config: RunnableConfig = {"configurable": {"thread_id": config.thread_id}}
    asyncio.run(graph.ainvoke(initial_state(config), run_config))
    values = dict(graph.get_state(run_config).values)
    chunk = chunks[0]
    return {
        "status": chunk.status.value,
        "e2e_retry_count": values.get("e2e_retry_count", 0),
        "developer_calls": client.roles.count("Developer"),
        "critic_ran": "Critic" in client.roles,
        "gate": values["tui_state"].critic_gatekeeper_status.value,
        "terminal": values["tui_state"].critic_gatekeeper_status is ExecutionState.STOPPED,
    }


# -- step 8: HITL gate ------------------------------------------------------


def cmd_hitl(_ws: Path) -> dict[str, Any]:
    """Prove the HITL gate halts at approval, persists a checkpoint, resumes one node."""
    gate = HitlGate(resume_source=QueueResumeSource([CriticCommand.RESUME]))
    gate.start({"project_id": "hitl", "workspace_path": ".", "thread_id": "hitl"})
    halted = gate.is_halted()
    persisted = gate.checkpoint_persisted()
    before = len(gate.executed_nodes)
    gate.resume()
    return {
        "halted": halted,
        "checkpoint_persisted": persisted,
        "nodes_advanced": len(gate.executed_nodes) - before
        if halted
        else len(gate.executed_nodes),
        "pending": gate.pending_node(),
    }


# -- step 10: budget compliance ---------------------------------------------


def cmd_budget(_ws: Path) -> dict[str, Any]:
    """Assert the prompt budget and the 50-line trace cap (first + last frames)."""
    entry = ModelEntry(
        model_id="p8-mock",
        provider=ProviderId.OLLAMA,
        context_window=8192,
        max_output=4096,
        usd_per_mtok_in=0.0,
        usd_per_mtok_out=0.0,
    )
    trace = "\n".join(f"frame {index}" for index in range(200))
    capped = cap_trace(trace)
    lines = capped.splitlines()
    huge = [Message(role="user", content="x" * 100_000)]
    clipped = build_prompt(huge, entry=entry)
    return {
        "budget": prompt_budget(entry),
        "trace_lines": len(lines),
        "first_frame": lines[0] == "frame 0",
        "last_frame": lines[-1] == "frame 199",
        "bounded": len(lines) <= 51,
        "prompt_fits": fits_budget(count_messages(clipped), entry),
    }


# -- step 11: worktree checkpoint capture + restore -------------------------


def cmd_checkpoint(ws: Path) -> dict[str, Any]:
    """Capture an in-flight worktree, restart, and restore it field-for-field."""
    workspace = WorkerWorkspace(ws)
    source = Chunk(chunk_id="c1", title="source")
    source.assigned_worker_id = "worker-1"
    workspace.bind("worker-1", source)
    root = workspace.root_for(source)
    (root / "notes.txt").write_text("captured\n", encoding="utf-8")
    snapshot = workspace.capture("worker-1")

    # A fresh slot (new branch) proves restoration is driven by the snapshot,
    # not by the still-present worktree.
    target = Chunk(chunk_id="c2", title="target")
    target.assigned_worker_id = "worker-2"
    workspace.bind("worker-2", target)
    workspace.restore("worker-2", snapshot)

    restored_root = workspace.root_for(target)
    return {
        "head_matches": (restored_root / ".git").is_file()
        and _git(restored_root, "rev-parse", "HEAD").strip() == snapshot["head"],
        "file_restored": (restored_root / "notes.txt").read_text(encoding="utf-8")
        == "captured\n",
        "primary_clean": _status(ws) == "",
    }


def main() -> int:
    """Dispatch one driver subcommand and print its JSON evidence."""
    parser = argparse.ArgumentParser(prog="p8-acceptance-driver")
    sub = parser.add_subparsers(dest="command", required=True)

    parallel = sub.add_parser("parallel")
    parallel.add_argument("--workspace", required=True)
    parallel.add_argument("--requirement", required=True)
    parallel.add_argument("--max-parallel", type=int, default=3)

    failing = sub.add_parser("failing")
    failing.add_argument("--workspace", required=True)
    failing.add_argument("--requirement", required=True)

    conflict = sub.add_parser("conflict")
    conflict.add_argument("--workspace", required=True)
    conflict.add_argument("--requirement", required=True)

    for name in ("hitl", "budget", "checkpoint"):
        sub.add_parser(name).add_argument("--workspace", required=True)

    args = parser.parse_args()
    ws = Path(args.workspace)
    if args.command == "parallel":
        result = cmd_parallel(ws, Path(args.requirement), args.max_parallel)
    elif args.command == "failing":
        result = cmd_failing(ws, Path(args.requirement))
    elif args.command == "conflict":
        result = cmd_conflict(ws, Path(args.requirement))
    elif args.command == "hitl":
        result = cmd_hitl(ws)
    elif args.command == "budget":
        result = cmd_budget(ws)
    else:
        result = cmd_checkpoint(ws)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())