"""Developer node tests (V11 8.10).

Validation matrix (8.B, acceptance is exact) against a REAL temp git repo:

(a) a write to ``../../etc/passwd`` raises ``WorkspaceEscapeError``;
(b) a SYMLINK escape (a link inside the worktree pointing outside) is blocked;
(c) valid relative writes land INSIDE the worker's worktree root.

The client is a scripted fake (no network); no ``time.sleep``.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from dev_harness.contracts.enums import ChunkStatus
from dev_harness.contracts.errors import PersonaOutputError, WorkspaceEscapeError
from dev_harness.contracts.llm import Message, Usage
from dev_harness.contracts.state import Chunk
from dev_harness.engine.nodes.developer import (
    DeveloperNode,
    load_persona_prompt,
    parse_file_writes,
)
from dev_harness.engine.state import HarnessStateChannels
from dev_harness.engine.worker_workspace import WorkerWorkspace


class ScriptedClient:
    """A deterministic fake client returning scripted replies in order."""

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls: list[list[Message]] = []

    async def complete(
        self, messages: list[Message], *, model: str | None = None
    ) -> tuple[str, Usage]:
        self.calls.append(list(messages))
        text = self._replies.pop(0) if self._replies else ""
        return text, Usage(input_tokens=1, output_tokens=1)


def _chunk(worker_id: str = "worker-1") -> Chunk:
    return Chunk(
        chunk_id="c1",
        title="Implement the parser",
        assigned_worker_id=worker_id,
    )


def _state(**overrides: object) -> HarnessStateChannels:
    base: HarnessStateChannels = {
        "project_id": "p1",
        "workspace_path": "/tmp/ws",
        "thread_id": "t1",
    }
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


def _make_dir_link(link: Path, target: Path) -> None:
    """Create a directory link, falling back to a Windows junction.

    ``os.symlink`` needs a privilege on Windows; a junction (``mklink /J``) does
    not, and ``Path.resolve`` follows both, so the escape guard is exercised
    identically.
    """
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except OSError:
        pass
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.mark.unit
def test_load_persona_prompt_reads_developer_from_disk() -> None:
    """The developer prompt is loaded from the packaged persona file."""
    prompt = load_persona_prompt("developer")

    assert prompt.startswith("# Persona: Developer")
    assert "## Output Contract" in prompt


@pytest.mark.unit
def test_parse_file_writes_accepts_plain_and_fenced_json() -> None:
    """The documented write map parses, with or without a markdown fence."""
    payload = {"src/app.py": "print('hi')\n", "README.md": "# hi\n"}

    assert parse_file_writes(json.dumps(payload)) == payload
    assert parse_file_writes(f"```json\n{json.dumps(payload)}\n```") == payload


@pytest.mark.negative
def test_parse_file_writes_rejects_non_object() -> None:
    """A reply that is not a path->content object raises PersonaOutputError."""
    with pytest.raises(PersonaOutputError) as excinfo:
        parse_file_writes("not json")

    assert excinfo.value.remediation
    with pytest.raises(PersonaOutputError):
        parse_file_writes(json.dumps(["src/app.py"]))


@pytest.mark.unit
async def test_developer_writes_land_inside_worktree(tmp_workspace: Path) -> None:
    """(c): valid relative writes land inside the worker's worktree root."""
    ws = WorkerWorkspace(tmp_workspace)
    chunk = _chunk()
    root = ws.bind("worker-1", chunk)
    client = ScriptedClient([json.dumps({"src/app.py": "print('hi')\n"})])
    node = DeveloperNode(client, ws, chunk)

    update = await node(_state())

    assert (root / "src" / "app.py").read_text(encoding="utf-8") == "print('hi')\n"
    assert update["chunk_dag"] == [chunk]
    assert chunk.status is ChunkStatus.COMPLETED
    # The write never leaked into the primary tree.
    assert (tmp_workspace / "src" / "app.py").exists() is False
    assert client.calls[0][0].role == "system"
    assert "Developer" in client.calls[0][0].content


@pytest.mark.negative
async def test_developer_relative_escape_raises(tmp_workspace: Path) -> None:
    """(a): a write to ../../etc/passwd raises WorkspaceEscapeError."""
    ws = WorkerWorkspace(tmp_workspace)
    chunk = _chunk()
    ws.bind("worker-1", chunk)
    client = ScriptedClient([json.dumps({"../../etc/passwd": "root:x:0:0\n"})])
    node = DeveloperNode(client, ws, chunk)

    with pytest.raises(WorkspaceEscapeError) as excinfo:
        await node(_state())

    assert excinfo.value.remediation
    assert (tmp_workspace.parent / "etc" / "passwd").exists() is False


@pytest.mark.negative
async def test_developer_symlink_escape_is_blocked(tmp_workspace: Path) -> None:
    """(b): a symlink inside the worktree pointing outside is blocked."""
    ws = WorkerWorkspace(tmp_workspace)
    chunk = _chunk()
    root = ws.bind("worker-1", chunk)
    outside = tmp_workspace.parent / "outside"
    outside.mkdir()
    _make_dir_link(root / "link", outside)
    client = ScriptedClient([json.dumps({"link/evil.txt": "pwned\n"})])
    node = DeveloperNode(client, ws, chunk)

    with pytest.raises(WorkspaceEscapeError):
        await node(_state())

    assert (outside / "evil.txt").exists() is False


@pytest.mark.unit
async def test_developer_completed_chunk_is_noop(tmp_workspace: Path) -> None:
    """A COMPLETED chunk returns an empty update and never calls the client."""
    ws = WorkerWorkspace(tmp_workspace)
    chunk = _chunk()
    ws.bind("worker-1", chunk)
    chunk.status = ChunkStatus.COMPLETED
    client = ScriptedClient([json.dumps({"src/app.py": "x"})])
    node = DeveloperNode(client, ws, chunk)

    update = await node(_state())

    assert update == {}
    assert client.calls == []
