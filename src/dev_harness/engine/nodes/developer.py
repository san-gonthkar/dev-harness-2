"""Developer persona graph node with a worktree-root write guard (V11 8.10).

The Developer is the third node of the SDLC pipeline: it implements exactly one
chunk inside that chunk's isolated worker worktree. Like the groomer (8.4) and
architect (8.5) nodes it is a LangGraph node - a callable taking the state and
returning a **partial** update dict - and it is idempotent: a chunk already
``COMPLETED`` returns an empty update without calling the client, so a resumed
graph never re-runs (or re-bills) the persona.

The developer persona has a **non-JSON** output contract (the diff is the
artifact), so this node does **not** use ``validator_for("developer")``. Instead
it parses the persona output as a documented *file-write map* - a JSON object of
``relative_path -> content`` - and applies every write through
:meth:`WorkerWorkspace.write_path`, which rejects absolute paths and any path
that escapes the worktree root with :class:`WorkspaceEscapeError` (8.8). Because
``write_path`` resolves the candidate path, a **symlink** inside the worktree
that points outside is resolved to its target and blocked as well.

The client is injected (``providers.base.LLMClient`` satisfies
``CompletionClient``) so tests run against a scripted fake with no network.
"""

from __future__ import annotations

import json

from dev_harness.contracts.enums import ChunkStatus
from dev_harness.contracts.errors import PersonaOutputError
from dev_harness.contracts.llm import Message
from dev_harness.contracts.state import Chunk
from dev_harness.engine.nodes.groomer import load_persona_prompt
from dev_harness.engine.personas.validators import CompletionClient
from dev_harness.engine.state import HarnessStateChannels
from dev_harness.engine.worker_workspace import WorkerWorkspace

_FENCE = "```"


def _strip_fences(text: str) -> str:
    """Drop a single leading/trailing markdown code fence, if present."""
    stripped = text.strip()
    if not stripped.startswith(_FENCE):
        return stripped
    lines = stripped.splitlines()
    lines = lines[1:]  # drop the opening ``` or ```json line
    if lines and lines[-1].strip() == _FENCE:
        lines = lines[:-1]
    return "\n".join(lines)


def parse_file_writes(text: str) -> dict[str, str]:
    """Parse a persona reply into a ``relative_path -> content`` write map.

    The developer's documented output format is a JSON object mapping each
    relative path to the file's full content (an optional markdown fence is
    tolerated). Raises :class:`PersonaOutputError` when the reply is not such an
    object, so a malformed reply never reaches the filesystem.
    """
    try:
        data = json.loads(_strip_fences(text))
    except json.JSONDecodeError as exc:
        raise PersonaOutputError(
            f"developer output is not a JSON file-write map: {exc}"
        ) from exc
    if not isinstance(data, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in data.items()
    ):
        raise PersonaOutputError(
            "developer output must be a JSON object of relative_path -> content "
            "string pairs."
        )
    return data


def _chunk_prompt(chunk: Chunk, design: object) -> str:
    """Render the chunk and its approved design as the developer's user message."""
    return (
        f"Implement chunk '{chunk.chunk_id}': {chunk.title}\n"
        f"Dependencies: {', '.join(chunk.dependencies) or 'none'}\n\n"
        f"Approved design:\n{design}"
    )


class DeveloperNode:
    """LangGraph node running the developer persona for one chunk.

    :param client: the injected completion client (no network in tests).
    :param workspace: the worker-worktree binding; every write is guarded by it.
    :param chunk: the chunk this node implements (its assigned worker's worktree
        is resolved through ``workspace``).
    """

    def __init__(
        self,
        client: CompletionClient,
        workspace: WorkerWorkspace,
        chunk: Chunk,
        *,
        model: str | None = None,
    ) -> None:
        self._client = client
        self._workspace = workspace
        self._chunk = chunk
        self._model = model
        self._prompt = load_persona_prompt("developer")

    async def __call__(self, state: HarnessStateChannels) -> dict[str, list[Chunk]]:
        """Apply the persona's writes inside the worktree; return the chunk.

        A ``COMPLETED`` chunk makes this a no-op (no client call). Every write
        goes through the guarded path, so an escape raises
        :class:`WorkspaceEscapeError` before anything is written.
        """
        if self._chunk.status is ChunkStatus.COMPLETED:
            return {}

        self._chunk.status = ChunkStatus.IN_PROGRESS
        messages = [
            Message(role="system", content=self._prompt),
            Message(
                role="user",
                content=_chunk_prompt(self._chunk, state.get("technical_design")),
            ),
        ]
        text, _usage = await self._client.complete(messages, model=self._model)
        writes = parse_file_writes(text)
        for relative_path, content in writes.items():
            path = self._workspace.write_path(self._chunk, relative_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        self._chunk.status = ChunkStatus.COMPLETED
        return {"chunk_dag": [self._chunk]}


def make_developer_node(
    client: CompletionClient,
    workspace: WorkerWorkspace,
    chunk: Chunk,
    *,
    model: str | None = None,
) -> DeveloperNode:
    """Build the developer node bound to an injected client and worktree."""
    return DeveloperNode(client, workspace, chunk, model=model)
