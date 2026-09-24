"""Persona output validator tests (V11 8.2).

Validation matrix (8.B): truncated JSON -> exactly one repair retry, then
``PersonaOutputError``; valid output -> 0 retries. The client is a scripted fake
(no network); the retry count is observable on the validator.
"""

from __future__ import annotations

import json

import pytest

from dev_harness.contracts.errors import PersonaOutputError
from dev_harness.contracts.llm import Message, Usage
from dev_harness.contracts.state import GroomedRequirements, TechnicalDesign
from dev_harness.engine.personas.validators import (
    PersonaOutputValidator,
    default_repair_prompt,
    validator_for,
)

_GROOMER_JSON = json.dumps(
    {
        "status": "LOCKED",
        "prd_content": "1. The system shall do X.",
        "version": "V7",
        "locked_at_timestamp": 1_700_000_000,
    }
)

_ARCHITECT_JSON = json.dumps(
    {
        "architecture_spec": "layered",
        "interface_contracts": {"openapi_spec": "openapi: 3.0.0", "db_schema": ""},
        "status": "APPROVED",
    }
)

# Truncated mid-object: not parseable as JSON.
_TRUNCATED_JSON = '{"status": "LOCKED", "prd_content": "1. The system shall do X."'

# Parseable JSON but missing required fields -> schema validation failure.
_SCHEMA_INVALID_JSON = '{"status": "LOCKED"}'


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


def _messages() -> list[Message]:
    return [Message(role="user", content="groom this request")]


@pytest.mark.unit
async def test_valid_output_zero_retries() -> None:
    """Valid output validates with 0 retries and one client call."""
    client = ScriptedClient([_GROOMER_JSON])
    validator = PersonaOutputValidator(GroomedRequirements)

    outcome = await validator.validate(client, _messages())

    assert outcome.retries == 0
    assert validator.retries == 0
    assert isinstance(outcome.value, GroomedRequirements)
    assert outcome.value.prd_content == "1. The system shall do X."
    assert len(client.calls) == 1


@pytest.mark.unit
async def test_valid_architect_output() -> None:
    """The architect's TechnicalDesign validates with 0 retries."""
    client = ScriptedClient([_ARCHITECT_JSON])
    validator = PersonaOutputValidator(TechnicalDesign)

    outcome = await validator.validate(client, _messages())

    assert outcome.retries == 0
    assert outcome.value.status == "APPROVED"
    assert outcome.value.interface_contracts.openapi_spec == "openapi: 3.0.0"


@pytest.mark.negative
async def test_truncated_json_one_retry_then_error() -> None:
    """Truncated JSON -> exactly one repair retry, then PersonaOutputError."""
    client = ScriptedClient([_TRUNCATED_JSON, _TRUNCATED_JSON])
    validator = PersonaOutputValidator(GroomedRequirements)

    with pytest.raises(PersonaOutputError) as excinfo:
        await validator.validate(client, _messages())

    assert validator.retries == 1
    assert len(client.calls) == 2
    assert "malformed" in str(excinfo.value)
    assert excinfo.value.remediation


@pytest.mark.unit
async def test_repair_succeeds_after_one_retry() -> None:
    """A malformed first reply repaired on the retry costs exactly one retry."""
    client = ScriptedClient([_TRUNCATED_JSON, _GROOMER_JSON])
    validator = PersonaOutputValidator(GroomedRequirements)

    outcome = await validator.validate(client, _messages())

    assert outcome.retries == 1
    assert validator.retries == 1
    assert isinstance(outcome.value, GroomedRequirements)
    assert len(client.calls) == 2


@pytest.mark.negative
async def test_schema_invalid_json_one_retry_then_error() -> None:
    """Parseable-but-invalid JSON also triggers exactly one repair retry."""
    client = ScriptedClient([_SCHEMA_INVALID_JSON, _SCHEMA_INVALID_JSON])
    validator = PersonaOutputValidator(GroomedRequirements)

    with pytest.raises(PersonaOutputError):
        await validator.validate(client, _messages())

    assert validator.retries == 1
    assert len(client.calls) == 2


@pytest.mark.unit
async def test_repair_prompt_carries_raw_and_error() -> None:
    """The repair prompt receives the malformed text and the parse error."""
    seen: list[tuple[str, str]] = []

    def capture(raw: str, error: str) -> str:
        seen.append((raw, error))
        return "repair please"

    client = ScriptedClient([_TRUNCATED_JSON, _GROOMER_JSON])
    validator = PersonaOutputValidator(GroomedRequirements, repair_prompt=capture)

    await validator.validate(client, _messages())

    assert seen == [(_TRUNCATED_JSON, seen[0][1])]
    assert "invalid JSON" in seen[0][1]
    # The repair turn is appended as assistant(raw) + user(repair prompt).
    repair_call = client.calls[1]
    assert repair_call[-2] == Message(role="assistant", content=_TRUNCATED_JSON)
    assert repair_call[-1] == Message(role="user", content="repair please")


@pytest.mark.unit
async def test_retries_counter_resets_between_calls() -> None:
    """The observable retry counter resets on each validate() call."""
    client = ScriptedClient([_TRUNCATED_JSON, _GROOMER_JSON, _GROOMER_JSON])
    validator = PersonaOutputValidator(GroomedRequirements)

    first = await validator.validate(client, _messages())
    second = await validator.validate(client, _messages())

    assert first.retries == 1
    assert second.retries == 0
    assert validator.retries == 0


@pytest.mark.unit
def test_validator_for_known_role() -> None:
    """validator_for maps a role to its target model."""
    assert validator_for("groomer").model is GroomedRequirements
    assert validator_for("architect").model is TechnicalDesign


@pytest.mark.negative
def test_validator_for_unknown_role_raises() -> None:
    """A role without a JSON output contract raises PersonaOutputError."""
    with pytest.raises(PersonaOutputError) as excinfo:
        validator_for("developer")
    assert excinfo.value.remediation


@pytest.mark.unit
def test_default_repair_prompt_includes_error_and_raw() -> None:
    """The default repair prompt embeds the error and the previous output."""
    prompt = default_repair_prompt("RAW", "invalid JSON: boom")
    assert "invalid JSON: boom" in prompt
    assert "RAW" in prompt
