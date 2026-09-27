"""Full SDLC E2E: requirement -> tested chunk with a scripted mid-run PAUSE/RESUME (V11 10.1).

The 10.B row is exact: the run **completes**; a mid-run **PAUSE** seals a
checkpoint with ``is_paused=True``; **RESUME continues from that checkpoint**
(the pre-pause personas are not re-run); the final chunk is ``COMPLETED``; and
**no process group is left behind**.

The graph is the real compiled SDLC graph (8.18), so every node executes -
``groomer`` -> ``architect`` -> ``develop`` -> ``integrate`` -> ``critic`` - which
is what the 10.C graph-node coverage contract requires. The pause is a runtime
``interrupt_before`` on the ``develop`` node: the graph halts after the architect
and before any chunk work, the halted state is sealed with
:class:`~dev_harness.core.pause_seal.PauseSealer` and written through
:class:`~dev_harness.storage.checkpoint_binding.CheckpointBinding` with
``is_paused=True``, and the run resumes with ``ainvoke(None, ...)`` from that
checkpoint.

Two variants share one flow:

* :func:`test_full_sdlc_pause_resume_leaves_no_process_group` (``e2e``, NIGHTLY)
  runs the chunk's suite through a launcher that records its own pid and its
  child's pid, then asserts both are gone - the Windows equivalent of
  ``pgrep -g`` being empty.
* :func:`test_full_sdlc_pause_resume_fast` (``integration``, PR tier) runs the
  same pause/resume flow with a trivial test command so the smoke lane covers it.

No network (``MockLLM`` + sockets disabled), no ``time.sleep``, no ``tui/``
import, no new dependencies.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import pytest_socket
from langchain_core.runnables import RunnableConfig

from dev_harness.contracts.enums import ChunkStatus
from dev_harness.contracts.llm import Message, Usage
from dev_harness.contracts.state import Chunk, HarnessState
from dev_harness.core.pause_seal import PauseSealer
from dev_harness.engine.pipeline import (
    DEVELOP_NODE,
    PipelineConfig,
    build_graph,
    initial_state,
)
from dev_harness.engine.worker_workspace import WorkerWorkspace
from dev_harness.storage.checkpoint_binding import CheckpointBinding
from dev_harness.storage.sqlite_saver import Scope, SqliteSaver
from tests.support.mock_llm import MockLLM
from tests.support.workspace import make_workspace

_BASE_TEST = "def test_base():\n    assert True\n"
_GREEN_TEST = "def test_chunk_c1_is_green():\n    assert 1 + 1 == 2\n"

_GROOMER_JSON = json.dumps(
    {
        "status": "LOCKED",
        "prd_content": "1. The system shall add one.",
        "version": "V7",
        "locked_at_timestamp": 1_700_000_000,
    }
)
_ARCHITECT_JSON = json.dumps(
    {
        "architecture_spec": "A single module implementing addition.",
        "interface_contracts": {"openapi_spec": "{}", "db_schema": ""},
        "status": "APPROVED",
    }
)
_CRITIC_JSON = json.dumps({"verdict": "APPROVED", "target": "c1"})

#: A launcher that records its own pid and its child's pid, then runs the chunk
#: suite. The child inherits the launcher's stdout, so pytest's summary reaches
#: the tester's captured output. Both pids must be gone once the run completes.
_LAUNCHER = (
    "import os, subprocess, sys\n"
    "child = subprocess.Popen(\n"
    "    [sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider']\n"
    ")\n"
    "with open(sys.argv[1], 'w', encoding='utf-8') as handle:\n"
    "    handle.write(f'{os.getpid()} {child.pid}')\n"
    "raise SystemExit(child.wait())\n"
)

_ROLE_PREFIX = "# Persona: "


def _role_of(messages: list[Message]) -> str:
    """Extract the persona role from a message list's system-prompt header."""
    if not messages:
        return ""
    first = messages[0].content
    if not first.startswith(_ROLE_PREFIX):
        return ""
    return first[len(_ROLE_PREFIX) :].splitlines()[0].strip()


class _MockPersonaClient:
    """A deterministic ``CompletionClient`` over :class:`MockLLM` (no network).

    Persona calls are answered from the scripted contracts so the pipeline
    completes; any other call falls back to the injected ``MockLLM`` so the mock
    stays the single source of determinism.
    """

    def __init__(self, llm: MockLLM, *, developer_writes: dict[str, str]) -> None:
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
            return json.dumps(self._developer_writes), Usage(1, 1)
        if role == "Critic":
            return _CRITIC_JSON, Usage(1, 1)
        prompt = "\n\n".join(f"{m.role}: {m.content}" for m in messages)
        text, _ = self._llm.complete(prompt)
        return text, Usage(input_tokens=0, output_tokens=len(text))


def _config(
    repo: Path,
    client: _MockPersonaClient,
    chunk: Chunk,
    *,
    test_command: tuple[str, ...],
    thread_id: str,
) -> PipelineConfig:
    """Build a ``PipelineConfig`` over a fixture repo with a real test command."""
    return PipelineConfig(
        client=client,
        workspace=WorkerWorkspace(repo),
        chunks=[chunk],
        max_parallel_workers=1,
        test_command=test_command,
        test_timeout=60.0,
        thread_id=thread_id,
        raw_input="Add one to a number.",
    )


async def _pause_then_resume(
    repo: Path,
    client: _MockPersonaClient,
    chunk: Chunk,
    *,
    test_command: tuple[str, ...],
    thread_id: str,
) -> tuple[Scope, str, HarnessState, HarnessState, HarnessState]:
    """Run to a mid-run pause, seal it, then resume from that checkpoint.

    Returns ``(scope, checkpoint_id, sealed_state, halted_state, final_state)``.
    """
    config = _config(
        repo, client, chunk, test_command=test_command, thread_id=thread_id
    )
    graph = build_graph(config)
    run_config: RunnableConfig = {"configurable": {"thread_id": config.thread_id}}
    scope = Scope(project_id=config.project_id, thread_id=config.thread_id)

    pytest_socket.disable_socket()
    try:
        # Phase 1: run to the pause point (before the develop node).
        await graph.ainvoke(
            initial_state(config), run_config, interrupt_before=[DEVELOP_NODE]
        )
        halted = graph.get_state(run_config)
        assert halted.next == (DEVELOP_NODE,), "the run did not halt before develop"
        halted_state = HarnessState.model_validate(dict(halted.values))

        # PAUSE: seal the halted state with is_paused=True and persist it.
        sealed_state = PauseSealer(repo).apply(halted_state)
        checkpoint_id = CheckpointBinding(repo).put_bound(
            scope, sealed_state, checkpoint_id="pause-1", is_paused=True
        )

        # Phase 2: RESUME from the sealed checkpoint (input None).
        await graph.ainvoke(None, run_config)
        final_state = HarnessState.model_validate(
            dict(graph.get_state(run_config).values)
        )
        assert graph.get_state(run_config).next == (), "the resumed run did not finish"
    finally:
        pytest_socket.enable_socket()

    return scope, checkpoint_id, sealed_state, halted_state, final_state


def _assert_pause_sealed(
    repo: Path,
    scope: Scope,
    checkpoint_id: str,
    sealed_state: HarnessState,
    halted_state: HarnessState,
) -> None:
    """(b) the PAUSE sealed a checkpoint with ``is_paused=True``."""
    saver = SqliteSaver(CheckpointBinding(repo).db_path)
    try:
        row = saver.get_tuple(scope, checkpoint_id)
    finally:
        saver.close()
    assert row is not None, "the pause checkpoint was not persisted"
    assert row["is_paused"] is True
    stored = HarnessState.model_validate(json.loads(str(row["state_json"])))
    assert stored.tui_state.is_paused is True
    # The sealed checkpoint is the halted state, field-for-field (plus the seal).
    assert stored.groomed_requirements == halted_state.groomed_requirements
    assert stored.technical_design == halted_state.technical_design
    assert stored.chunk_dag == halted_state.chunk_dag
    assert sealed_state.tui_state.is_paused is True


def _assert_resumed_from_checkpoint(
    client: _MockPersonaClient, sealed_state: HarnessState, final_state: HarnessState
) -> None:
    """(c) RESUME continued from the sealed checkpoint, not from scratch."""
    # The pre-pause personas ran exactly once: the resume did not re-run them.
    assert client.roles.count("Groomer") == 1
    assert client.roles.count("Architect") == 1
    # The final state carries the sealed artifacts unchanged.
    assert final_state.groomed_requirements == sealed_state.groomed_requirements
    assert final_state.technical_design == sealed_state.technical_design


def _pid_alive(pid: int) -> bool:
    """True while ``pid`` is a live process (``pgrep`` equivalent)."""
    if os.name == "nt":
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        return re.search(rf"\b{pid}\b", out) is not None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@pytest.mark.e2e
async def test_full_sdlc_pause_resume_leaves_no_process_group(tmp_path: Path) -> None:
    """A requirement reaches a tested chunk across a PAUSE/RESUME, no leaks."""
    repo = make_workspace(tmp_path, files={"tests/test_base.py": _BASE_TEST})
    chunk = Chunk(chunk_id="c1", title="Implement addition")
    client = _MockPersonaClient(
        MockLLM(seed="e2e"), developer_writes={"tests/test_chunk_c1.py": _GREEN_TEST}
    )
    pidfile = tmp_path / "launcher.pid"
    test_command = (sys.executable, "-c", _LAUNCHER, str(pidfile))

    scope, checkpoint_id, sealed, halted, final = await _pause_then_resume(
        repo, client, chunk, test_command=test_command, thread_id="sdlc-e2e"
    )

    # (a) the run COMPLETES.
    assert final.chunk_dag[0].status is ChunkStatus.COMPLETED
    # (b) the PAUSE sealed a checkpoint with is_paused=True.
    _assert_pause_sealed(repo, scope, checkpoint_id, sealed, halted)
    # (c) RESUME continued from that checkpoint.
    _assert_resumed_from_checkpoint(client, sealed, final)
    # (d) the final chunk is COMPLETED.
    assert final.chunk_dag[0].status is ChunkStatus.COMPLETED
    # (e) no process group is left behind: the launcher and its child are gone.
    assert pidfile.exists(), "the launcher never recorded its pid"
    pids = [int(part) for part in pidfile.read_text(encoding="utf-8").split()]
    assert len(pids) == 2, "the launcher did not record both pids"
    assert all(not _pid_alive(pid) for pid in pids)
    # Every graph node ran (10.C graph-node coverage).
    assert client.roles == ["Groomer", "Architect", "Developer", "Critic"]


@pytest.mark.integration
async def test_full_sdlc_pause_resume_fast(tmp_path: Path) -> None:
    """The same PAUSE/RESUME flow with a trivial suite, for the smoke lane."""
    repo = make_workspace(tmp_path, files={"tests/test_base.py": _BASE_TEST})
    chunk = Chunk(chunk_id="c1", title="Implement addition")
    client = _MockPersonaClient(
        MockLLM(seed="fast"), developer_writes={"tests/test_chunk_c1.py": _GREEN_TEST}
    )
    test_command = (sys.executable, "-c", "print('1 passed in 0.01s')")

    scope, checkpoint_id, sealed, halted, final = await _pause_then_resume(
        repo, client, chunk, test_command=test_command, thread_id="sdlc-fast"
    )

    # (a) the run COMPLETES; (d) the final chunk is COMPLETED.
    assert final.chunk_dag[0].status is ChunkStatus.COMPLETED
    # (b) the PAUSE sealed a checkpoint with is_paused=True.
    _assert_pause_sealed(repo, scope, checkpoint_id, sealed, halted)
    # (c) RESUME continued from that checkpoint.
    _assert_resumed_from_checkpoint(client, sealed, final)
    # Every graph node ran (10.C graph-node coverage).
    assert client.roles == ["Groomer", "Architect", "Developer", "Critic"]
