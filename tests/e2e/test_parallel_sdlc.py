"""Parallel SDLC E2E: a 3-wide worker pool through the integration merge (V11 10.2).

The 10.B row is exact: three chunks run **concurrently** in three isolated
worktrees, the integration merge is **clean**, the **primary branch contains all
three chunks'** changes, and **0 writes are lost** (the union of the worktrees'
changes equals the primary's).

The graph is the real compiled SDLC graph (8.18), so every node executes -
``groomer`` -> ``architect`` -> ``develop`` -> ``integrate`` -> ``critic`` - which
is what the 10.C graph-node coverage contract requires. The three chunks are
independent (no dependencies), so the worker pool (8.7) schedules all three at
once; each is bound to its own worktree (8.8) and committed on its own branch,
and the integrator (8.9) merges them into the primary branch.

Two variants share one flow:

* :func:`test_parallel_sdlc_three_wide_merge` (``e2e``, NIGHTLY) runs the real
  chunk suites through pytest in each worktree.
* :func:`test_parallel_sdlc_three_wide_merge_fast` (``integration``, PR tier)
  runs the same flow with a trivial test command so the smoke lane covers it.

No network (``MockLLM`` + sockets disabled), no ``time.sleep``, no ``tui/``
import, no new dependencies.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest
import pytest_socket
from langchain_core.runnables import RunnableConfig

from dev_harness.contracts.enums import ChunkStatus
from dev_harness.contracts.llm import Message, Usage
from dev_harness.contracts.state import Chunk, HarnessState
from dev_harness.engine.pipeline import PipelineConfig, build_graph, initial_state
from dev_harness.engine.worker_workspace import WorkerWorkspace
from tests.support.graph_trace import NodeTrace
from tests.support.mock_llm import MockLLM
from tests.support.workspace import make_workspace

_BASE_TEST = "def test_base():\n    assert True\n"
#: Ignore pytest bytecode: each worktree runs its own suite, and a committed
#: ``__pycache__`` would differ per worktree and conflict on merge.
_GITIGNORE = "__pycache__/\n*.pyc\n"


def _base_files() -> dict[str, str]:
    """The fixture repo's initial files (a base test plus a .gitignore)."""
    return {".gitignore": _GITIGNORE, "tests/test_base.py": _BASE_TEST}


_GROOMER_JSON = json.dumps(
    {
        "status": "LOCKED",
        "prd_content": "1. The system shall implement three independent chunks.",
        "version": "V7",
        "locked_at_timestamp": 1_700_000_000,
    }
)
_ARCHITECT_JSON = json.dumps(
    {
        "architecture_spec": "Three independent modules, one per chunk.",
        "interface_contracts": {"openapi_spec": "{}", "db_schema": ""},
        "status": "APPROVED",
    }
)
_CRITIC_JSON = json.dumps({"verdict": "APPROVED", "target": "c1"})

_ROLE_PREFIX = "# Persona: "
# The developer node's user message names the chunk it implements; the mock uses
# it to answer each concurrent chunk with its own (non-overlapping) write map.
_CHUNK_RE = re.compile(r"Implement chunk '([^']+)'")


def _role_of(messages: list[Message]) -> str:
    """Extract the persona role from a message list's system-prompt header."""
    if not messages:
        return ""
    first = messages[0].content
    if not first.startswith(_ROLE_PREFIX):
        return ""
    return first[len(_ROLE_PREFIX) :].splitlines()[0].strip()


def _chunk_test(chunk_id: str) -> str:
    """A trivially green test file for ``chunk_id``."""
    return f"def test_chunk_{chunk_id}():\n    assert True\n"


def _developer_writes() -> dict[str, dict[str, str]]:
    """Per-chunk developer write maps: distinct files, so the merge is clean."""
    return {
        chunk_id: {
            f"chunk_{chunk_id}.txt": f"chunk {chunk_id}\n",
            f"tests/test_chunk_{chunk_id}.py": _chunk_test(chunk_id),
        }
        for chunk_id in ("c1", "c2", "c3")
    }


def _chunks() -> list[Chunk]:
    """Three independent chunks (no dependencies), so all three run at once."""
    return [
        Chunk(chunk_id="c1", title="Chunk one"),
        Chunk(chunk_id="c2", title="Chunk two"),
        Chunk(chunk_id="c3", title="Chunk three"),
    ]


def _git(repo: Path, *args: str) -> str:
    """Run ``git -C repo <args>`` and return its stripped stdout."""
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


class _MockPersonaClient:
    """A deterministic ``CompletionClient`` over :class:`MockLLM` (no network).

    Persona calls are answered from the scripted contracts so the pipeline
    completes; the Developer reply is selected by the chunk id in the prompt, so
    each concurrent chunk writes its own distinct files. Any other call falls
    back to the injected ``MockLLM`` so the mock stays the single source of
    determinism.
    """

    def __init__(
        self, llm: MockLLM, *, developer_writes: dict[str, dict[str, str]]
    ) -> None:
        self._llm = llm
        self._developer_writes = developer_writes
        self.roles: list[str] = []

    async def complete(
        self, messages: list[Message], *, model: str | None = None
    ) -> tuple[str, Usage]:
        """Return a scripted persona reply, else the mock's completion."""
        role = _role_of(messages)
        self.roles.append(role)
        if role == "Groomer":
            return _GROOMER_JSON, Usage(input_tokens=1, output_tokens=1)
        if role == "Architect":
            return _ARCHITECT_JSON, Usage(input_tokens=1, output_tokens=1)
        if role == "Developer":
            match = _CHUNK_RE.search(messages[-1].content)
            chunk_id = match.group(1) if match else ""
            return json.dumps(self._developer_writes[chunk_id]), Usage(1, 1)
        if role == "Critic":
            return _CRITIC_JSON, Usage(1, 1)
        prompt = "\n\n".join(f"{m.role}: {m.content}" for m in messages)
        text, _ = self._llm.complete(prompt)
        return text, Usage(input_tokens=0, output_tokens=len(text))


class _ConcurrencyRecordingWorkspace(WorkerWorkspace):
    """A ``WorkerWorkspace`` that records the peak number of concurrent binds.

    The worker pool calls :meth:`bind` on a worker thread for each chunk, so the
    peak number of overlapping binds is the observed peak worktree concurrency.
    A :class:`threading.Barrier` makes the three concurrent binds rendezvous, so
    the peak is deterministic rather than timing-dependent. The actual
    ``git worktree add`` is serialized (git's worktree list is not safe under
    concurrent writers); the workers still run concurrently in their worktrees.
    """

    def __init__(self, repo: str | Path, *, expected: int) -> None:
        super().__init__(repo)
        self._lock = threading.Lock()
        self._create_lock = threading.Lock()
        self._active = 0
        self.peak = 0
        self._barrier = threading.Barrier(expected)

    def bind(self, worker_id: str, chunk: Chunk) -> Path:
        """Record the overlap, rendezvous the workers, then create the worktree."""
        with self._lock:
            self._active += 1
            self.peak = max(self.peak, self._active)
        try:
            try:
                self._barrier.wait(timeout=15)
            except threading.BrokenBarrierError:  # pragma: no cover - pool bug
                pass
            with self._create_lock:
                return super().bind(worker_id, chunk)
        finally:
            with self._lock:
                self._active -= 1


async def _run_parallel(
    repo: Path,
    *,
    test_command: tuple[str, ...],
    thread_id: str,
) -> tuple[_ConcurrencyRecordingWorkspace, _MockPersonaClient, HarnessState]:
    """Run the real compiled graph with a 3-wide pool; return the observations."""
    client = _MockPersonaClient(
        MockLLM(seed="parallel"), developer_writes=_developer_writes()
    )
    workspace = _ConcurrencyRecordingWorkspace(repo, expected=3)
    config = PipelineConfig(
        client=client,
        workspace=workspace,
        chunks=_chunks(),
        max_parallel_workers=3,
        test_command=test_command,
        test_timeout=120.0,
        thread_id=thread_id,
        raw_input="Implement three independent chunks.",
    )
    graph = build_graph(config)
    trace = NodeTrace()
    run_config: RunnableConfig = {
        "configurable": {"thread_id": config.thread_id},
        "callbacks": [trace],
    }

    pytest_socket.disable_socket()
    try:
        await graph.ainvoke(initial_state(config), run_config)
        final = HarnessState.model_validate(dict(graph.get_state(run_config).values))
        assert graph.get_state(run_config).next == (), "the run did not finish"
    finally:
        pytest_socket.enable_socket()
    trace.merge_into()
    return workspace, client, final


def _assert_parallel_acceptance(
    repo: Path,
    initial_head: str,
    workspace: _ConcurrencyRecordingWorkspace,
    client: _MockPersonaClient,
    final: HarnessState,
    developer_writes: dict[str, dict[str, str]],
) -> None:
    """Assert the exact 10.B acceptance: (a) concurrency, (b) clean, (c)+(d)."""
    # (a) 3 CONCURRENT worktrees: the observed peak concurrency is exactly 3.
    assert workspace.peak == 3, (
        f"observed peak concurrency {workspace.peak}, expected 3"
    )
    # Every chunk completed (the pool ran all three to COMPLETED).
    assert [chunk.status for chunk in final.chunk_dag] == [ChunkStatus.COMPLETED] * 3

    # (b) the merge is CLEAN: the primary tree has no uncommitted changes.
    assert _git(repo, "status", "--porcelain") == ""

    # (c) the PRIMARY branch contains ALL THREE chunks' changes.
    for chunk_id, writes in developer_writes.items():
        # Each chunk branch is an ancestor of the primary HEAD (it was merged).
        ancestor = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "merge-base",
                "--is-ancestor",
                f"chunk/{chunk_id}",
                "HEAD",
            ],
            capture_output=True,
            check=False,
        )
        assert ancestor.returncode == 0, (
            f"chunk/{chunk_id} was not merged into the primary"
        )
        for relative_path, content in writes.items():
            path = repo / relative_path
            assert path.exists(), f"{relative_path} is missing from the primary branch"
            assert path.read_text(encoding="utf-8") == content

    # (d) 0 LOST WRITES: the union of the worktrees' changes equals the primary's.
    union: set[str] = set()
    for chunk_id in developer_writes:
        changed = _git(repo, "diff", "--name-only", initial_head, f"chunk/{chunk_id}")
        union |= set(changed.splitlines())
    primary_changed = set(
        _git(repo, "diff", "--name-only", initial_head, "HEAD").splitlines()
    )
    assert primary_changed == union, (
        "the primary lost or gained writes vs. the worktrees"
    )

    # Every graph node ran (10.C graph-node coverage).
    assert sorted(client.roles) == sorted(
        ["Groomer", "Architect", "Developer", "Developer", "Developer", "Critic"]
    )


@pytest.mark.e2e
async def test_parallel_sdlc_three_wide_merge(tmp_path: Path) -> None:
    """A 3-wide pool merges three chunks cleanly with 0 lost writes (NIGHTLY)."""
    repo = make_workspace(tmp_path, files=_base_files())
    initial_head = _git(repo, "rev-parse", "HEAD")
    test_command = (sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider")

    workspace, client, final = await _run_parallel(
        repo, test_command=test_command, thread_id="parallel-e2e"
    )

    _assert_parallel_acceptance(
        repo, initial_head, workspace, client, final, _developer_writes()
    )


@pytest.mark.integration
async def test_parallel_sdlc_three_wide_merge_fast(tmp_path: Path) -> None:
    """The same 3-wide merge flow with a trivial suite, for the smoke lane."""
    repo = make_workspace(tmp_path, files=_base_files())
    initial_head = _git(repo, "rev-parse", "HEAD")
    test_command = (sys.executable, "-c", "print('1 passed in 0.01s')")

    workspace, client, final = await _run_parallel(
        repo, test_command=test_command, thread_id="parallel-fast"
    )

    _assert_parallel_acceptance(
        repo, initial_head, workspace, client, final, _developer_writes()
    )
