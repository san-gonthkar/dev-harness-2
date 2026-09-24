"""Groomer node tests (V11 8.4).

Validation matrix (8.B): the node validates persona output as
``GroomedRequirements`` (status ``LOCKED``) and writes it to the
``groomed_requirements`` channel; re-invocation is a no-op that never calls the
client. The client is a scripted fake (no network).
"""

from __future__ import annotations

import json

import pytest

from dev_harness.contracts.errors import PersonaOutputError
from dev_harness.contracts.llm import Message, Usage
from dev_harness.contracts.state import GroomedRequirements
from dev_harness.engine.nodes.groomer import GroomerNode, load_persona_prompt
from dev_harness.engine.state import HarnessStateChannels

_GROOMER_JSON = json.dumps(
    {
        "status": "LOCKED",
        "prd_content": "1. The system shall do X.",
        "version": "V7",
        "locked_at_timestamp": 1_700_000_000,
    }
)

_LOCKED = GroomedRequirements.model_validate(json.loads(_GROOMER_JSON))


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


def _state(**overrides: object) -> HarnessStateChannels:
    base: HarnessStateChannels = {
        "project_id": "p1",
        "workspace_path": "/tmp/ws",
        "thread_id": "t1",
        "raw_input": "Build a task tracker.",
    }
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


@pytest.mark.unit
def test_load_persona_prompt_reads_from_disk() -> None:
    """The groomer prompt is loaded from the packaged persona file."""
    prompt = load_persona_prompt("groomer")

    assert prompt.startswith("# Persona: Groomer")
    assert "## Output Contract" in prompt


@pytest.mark.unit
async def test_groomer_validates_and_writes_locked_requirements() -> None:
    """Valid persona output -> LOCKED GroomedRequirements in a partial update."""
    client = ScriptedClient([_GROOMER_JSON])
    node = GroomerNode(client)

    update = await node(_state())

    assert update == {"groomed_requirements": _LOCKED}
    assert update["groomed_requirements"].status == "LOCKED"
    assert len(client.calls) == 1
    # The system message carries the persona prompt; the user message raw_input.
    assert client.calls[0][0].role == "system"
    assert "Groomer" in client.calls[0][0].content
    assert client.calls[0][1] == Message(role="user", content="Build a task tracker.")


@pytest.mark.unit
async def test_groomer_reinvocation_is_noop() -> None:
    """A set channel returns an empty update and never calls the client."""
    client = ScriptedClient([_GROOMER_JSON])
    node = GroomerNode(client)

    update = await node(_state(groomed_requirements=_LOCKED))

    assert update == {}
    assert client.calls == []


@pytest.mark.negative
async def test_groomer_malformed_output_raises_and_round_trips_state() -> None:
    """Malformed persona output raises PersonaOutputError; nothing is written."""
    client = ScriptedClient(["not json", "still not json"])
    node = GroomerNode(client)

    with pytest.raises(PersonaOutputError):
        await node(_state())

    # Exactly one repair retry: two client calls, no partial update emitted.
    assert len(client.calls) == 2