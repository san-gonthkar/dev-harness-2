"""Architect node tests (V11 8.5).

Validation matrix (8.B): the node validates persona output as
``TechnicalDesign``, writes it to the ``technical_design`` channel, the returned
``openapi_spec`` parses, and an empty design never leaves ``status ==
APPROVED``. The client is a scripted fake (no network).
"""

from __future__ import annotations

import json

import pytest

from dev_harness.contracts.errors import EngineError, PersonaOutputError
from dev_harness.contracts.llm import Message, Usage
from dev_harness.contracts.state import GroomedRequirements, TechnicalDesign
from dev_harness.engine.nodes.architect import ArchitectNode, load_persona_prompt
from dev_harness.engine.state import HarnessStateChannels

_OPENAPI = json.dumps({"openapi": "3.0.0", "paths": {}})

_DESIGN_JSON = json.dumps(
    {
        "architecture_spec": "# Architecture\nHexagonal layers.",
        "interface_contracts": {
            "openapi_spec": _OPENAPI,
            "db_schema": "CREATE TABLE chunk (id TEXT);",
        },
        "status": "PENDING",
    }
)

_EMPTY_APPROVED_JSON = json.dumps(
    {"architecture_spec": "", "interface_contracts": {}, "status": "APPROVED"}
)

_YAML_OPENAPI_JSON = json.dumps(
    {
        "architecture_spec": "# Architecture",
        "interface_contracts": {"openapi_spec": "openapi: 3.0.0"},
        "status": "PENDING",
    }
)

_LOCKED = GroomedRequirements(
    prd_content="1. The system shall do X.",
    version="V7",
    locked_at_timestamp=1_700_000_000,
)


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
        "groomed_requirements": _LOCKED,
    }
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


@pytest.mark.unit
def test_load_persona_prompt_reads_architect_from_disk() -> None:
    """The architect prompt is loaded from the packaged persona file."""
    prompt = load_persona_prompt("architect")

    assert prompt.startswith("# Persona: Architect")
    assert "## Output Contract" in prompt


@pytest.mark.unit
async def test_architect_validates_and_writes_parsable_design() -> None:
    """Valid persona output -> TechnicalDesign in a partial update; spec parses."""
    client = ScriptedClient([_DESIGN_JSON])
    node = ArchitectNode(client)

    update = await node(_state())

    design = update["technical_design"]
    assert design == TechnicalDesign.model_validate(json.loads(_DESIGN_JSON))
    assert design.status == "PENDING"
    # 8.B acceptance: the returned openapi_spec parses.
    assert json.loads(design.interface_contracts.openapi_spec)["openapi"] == "3.0.0"
    assert len(client.calls) == 1
    assert client.calls[0][0].role == "system"
    assert "Architect" in client.calls[0][0].content
    assert "1. The system shall do X." in client.calls[0][1].content


@pytest.mark.unit
async def test_architect_reinvocation_is_noop() -> None:
    """A set channel returns an empty update and never calls the client."""
    client = ScriptedClient([_DESIGN_JSON])
    node = ArchitectNode(client)
    existing = TechnicalDesign.model_validate(json.loads(_DESIGN_JSON))

    update = await node(_state(technical_design=existing))

    assert update == {}
    assert client.calls == []


@pytest.mark.negative
async def test_architect_missing_groomed_requirements_raises() -> None:
    """An absent groomed_requirements channel raises before any client call."""
    client = ScriptedClient([_DESIGN_JSON])
    node = ArchitectNode(client)

    with pytest.raises(EngineError) as excinfo:
        await node(_state(groomed_requirements=None))

    assert excinfo.value.remediation
    assert client.calls == []


@pytest.mark.negative
async def test_architect_empty_design_is_never_approved() -> None:
    """An empty APPROVED design is downgraded so status != APPROVED."""
    client = ScriptedClient([_EMPTY_APPROVED_JSON])
    node = ArchitectNode(client)

    update = await node(_state())

    design = update["technical_design"]
    assert design.architecture_spec == ""
    assert design.status != "APPROVED"
    assert design.status == "REJECTED"


@pytest.mark.negative
async def test_architect_non_json_openapi_spec_raises() -> None:
    """A non-empty openapi_spec that is not JSON fails the interface gate."""
    client = ScriptedClient([_YAML_OPENAPI_JSON])
    node = ArchitectNode(client)

    with pytest.raises(PersonaOutputError) as excinfo:
        await node(_state())

    assert excinfo.value.remediation


@pytest.mark.negative
async def test_architect_malformed_output_raises_and_round_trips() -> None:
    """Malformed persona output raises PersonaOutputError; nothing is written."""
    client = ScriptedClient(["not json", "still not json"])
    node = ArchitectNode(client)

    with pytest.raises(PersonaOutputError):
        await node(_state())

    # Exactly one repair retry: two client calls, no partial update emitted.
    assert len(client.calls) == 2