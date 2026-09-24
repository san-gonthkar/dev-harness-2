"""End-to-end SDLC graph assembly tests (V11 8.18).

Validation matrix (8.B) is exact: a requirement runs **end-to-end to a green unit
test**; the **final checkpoint is schema-valid** (``HarnessState.model_validate``);
``chunk_dag[0].status == COMPLETED``; and there are **zero live network calls**
(sockets are disabled for the duration of the run and the only client is the
injected scripted fake).

The graph is driven with ``ainvoke`` because the persona nodes are async. Every
chunk runs in a real git worktree (8.8), its suite runs as a real subprocess in
that worktree (8.11), and the chunk branch is merged into the primary branch
(8.9) - so the assertions are about real orchestration, not mocks of it.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest
import pytest_socket

from dev_harness.contracts.enums import ChunkStatus, ExecutionState
from dev_harness.contracts.errors import PersonaOutputError
from dev_harness.contracts.llm import Message, Usage
from dev_harness.contracts.state import Chunk, HarnessState
from dev_harness.engine.pipeline import (
    PipelineConfig,
    build_graph,
    initial_state,
)
from dev_harness.engine.worker_workspace import WorkerWorkspace
from tests.support.workspace import make_workspace

_BASE_TEST = "def test_base():\n    assert True\n"
_GREEN_TEST = "def test_chunk_c1_is_green():\n    assert 1 + 1 == 2\n"
_RED_TEST = "def test_chunk_c1_is_red():\n    assert 1 + 1 == 3\n"

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


class _RouterClient:
    """A scripted persona client: routes each call by the system prompt's role.

    The system message is the persona template (``# Persona: <Role>``), so the
    fake returns the role-appropriate contract without any network access.
    """

    def __init__(self, *, developer_writes: dict[str, str]) -> None:
        self._developer_writes = developer_writes
        self.roles: list[str] = []
        self.calls: list[list[Message]] = []

    async def complete(
        self, messages: list[Message], *, model: str | None = None
    ) -> tuple[str, Usage]:
        self.calls.append(list(messages))
        system = messages[0].content
        if system.startswith("# Persona: Groomer"):
            self.roles.append("Groomer")
            return _GROOMER_JSON, Usage(input_tokens=1, output_tokens=1)
        if system.startswith("# Persona: Architect"):
            self.roles.append("Architect")
            return _ARCHITECT_JSON, Usage(input_tokens=1, output_tokens=1)
        if system.startswith("# Persona: Developer"):
            self.roles.append("Developer")
            return json.dumps(self._developer_writes), Usage(1, 1)
        if system.startswith("# Persona: Critic"):
            self.roles.append("Critic")
            return _CRITIC_JSON, Usage(1, 1)
        return "", Usage(0, 0)


class _BrokenArchitectClient(_RouterClient):
    """Returns unparseable output for the architect (a negative-path client)."""

    async def complete(
        self, messages: list[Message], *, model: str | None = None
    ) -> tuple[str, Usage]:
        if messages[0].content.startswith("# Persona: Architect"):
            self.roles.append("Architect")
            return "this is not JSON at all", Usage(1, 1)
        return await super().complete(messages, model=model)


def _config(
    repo: Path,
    client: _RouterClient,
    chunk: Chunk,
    **overrides: object,
) -> PipelineConfig:
    """Build a ``PipelineConfig`` over a fixture repo with a real test command."""
    values: dict[str, object] = {
        "client": client,
        "workspace": WorkerWorkspace(repo),
        "chunks": [chunk],
        "max_parallel_workers": 1,
        "test_command": (sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"),
        "test_timeout": 60.0,
        "thread_id": "sdlc-e2e",
        "raw_input": "Add one to a number.",
    }
    values.update(overrides)
    return PipelineConfig(**values)  # type: ignore[arg-type]


def _run_config(config: PipelineConfig) -> dict[str, dict[str, str]]:
    return {"configurable": {"thread_id": config.thread_id}}


@pytest.mark.integration
async def test_requirement_runs_end_to_end_to_green_unit_test(tmp_path: Path) -> None:
    """A requirement reaches a green unit test; the checkpoint is schema-valid."""
    repo = make_workspace(tmp_path, files={"tests/test_base.py": _BASE_TEST})
    chunk = Chunk(chunk_id="c1", title="Implement addition")
    client = _RouterClient(developer_writes={"tests/test_chunk_c1.py": _GREEN_TEST})
    config = _config(repo, client, chunk)
    graph = build_graph(config)

    pytest_socket.disable_socket()
    try:
        await graph.ainvoke(initial_state(config), _run_config(config))
    finally:
        pytest_socket.enable_socket()

    # Every persona stage ran, in order, against the injected client only.
    assert client.roles == ["Groomer", "Architect", "Developer", "Critic"]

    # (b) the final checkpoint is schema-valid.
    values = graph.get_state(_run_config(config)).values
    state = HarnessState.model_validate(dict(values))
    assert state.groomed_requirements is not None
    assert state.groomed_requirements.status == "LOCKED"
    assert state.technical_design is not None
    assert state.tui_state.critic_gatekeeper_status is ExecutionState.RUNNING

    # (c) chunk_dag[0].status == COMPLETED.
    assert state.chunk_dag[0].status is ChunkStatus.COMPLETED
    assert chunk.status is ChunkStatus.COMPLETED

    # (a) the merged primary branch carries the chunk's work and its suite is green.
    merged = repo / "tests" / "test_chunk_c1.py"
    assert merged.read_text(encoding="utf-8") == _GREEN_TEST
    proc = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "2 passed" in proc.stdout


@pytest.mark.integration
async def test_failing_chunk_escalates_to_hitl_and_never_criticises(
    tmp_path: Path,
) -> None:
    """A red chunk is retried to the e2e ceiling, then HITL stops the run."""
    repo = make_workspace(tmp_path, files={"tests/test_base.py": _BASE_TEST})
    chunk = Chunk(chunk_id="c1", title="Implement broken addition")
    client = _RouterClient(developer_writes={"tests/test_chunk_c1.py": _RED_TEST})
    config = _config(repo, client, chunk)
    graph = build_graph(config)

    pytest_socket.disable_socket()
    try:
        await graph.ainvoke(initial_state(config), _run_config(config))
    finally:
        pytest_socket.enable_socket()

    state = HarnessState.model_validate(dict(graph.get_state(_run_config(config)).values))
    # The chunk never passed, so the Critic gate never ran.
    assert state.chunk_dag[0].status is ChunkStatus.FAILED
    assert state.tui_state.critic_gatekeeper_status is ExecutionState.STOPPED
    assert state.e2e_retry_count >= 2
    assert "Critic" not in client.roles
    assert client.roles.count("Developer") == 2  # bounded retry, not a loop
    # The failed work was never merged into the primary branch.
    assert not (repo / "tests" / "test_chunk_c1.py").exists()


@pytest.mark.negative
async def test_malformed_persona_output_fails_the_run(tmp_path: Path) -> None:
    """A malformed persona reply fails the run instead of silently proceeding."""
    repo = make_workspace(tmp_path, files={"tests/test_base.py": _BASE_TEST})
    chunk = Chunk(chunk_id="c1", title="Implement addition")
    client = _BrokenArchitectClient(developer_writes={"tests/test_chunk_c1.py": _GREEN_TEST})
    config = _config(repo, client, chunk)
    graph = build_graph(config)

    pytest_socket.disable_socket()
    try:
        with pytest.raises(PersonaOutputError):
            await graph.ainvoke(initial_state(config), _run_config(config))
    finally:
        pytest_socket.enable_socket()