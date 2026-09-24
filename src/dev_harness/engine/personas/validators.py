"""Persona output validators with one repair retry (V11 8.2).

A persona emits free text; the pipeline needs a typed object. This module parses
that text as JSON and validates it against the role's target Pydantic model. On
parse or validation failure it performs **exactly one** repair retry - re-invoking
the client with a repair prompt that carries the malformed text and the error -
then raises ``PersonaOutputError`` if the output is still malformed. Valid output
costs zero retries.

The client is injected (``providers.base.LLMClient`` satisfies
``CompletionClient``) so tests run against a scripted fake with no network.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel, ValidationError

from dev_harness.contracts.errors import PersonaOutputError
from dev_harness.contracts.llm import Message, Usage
from dev_harness.contracts.state import GroomedRequirements, TechnicalDesign

ModelT = TypeVar("ModelT", bound=BaseModel)

# The plan fixes the repair budget at exactly one retry (V11 8.2).
_MAX_REPAIRS = 1

# Builds the repair prompt from the malformed text and the parse/validation error.
RepairPrompt = Callable[[str, str], str]


@runtime_checkable
class CompletionClient(Protocol):
    """The minimal client surface a validator needs.

    ``providers.base.LLMClient`` satisfies this structurally, so production code
    passes a real client and tests pass a scripted fake.
    """

    async def complete(
        self, messages: list[Message], *, model: str | None = None
    ) -> tuple[str, Usage]:
        """Return (text, usage) for a non-streaming completion."""
        ...


def default_repair_prompt(raw: str, error: str) -> str:
    """Return the single repair prompt for malformed persona output."""
    return (
        "Your previous output was not a valid JSON object for the required "
        f"schema.\nError: {error}\n"
        "Return ONLY one corrected JSON object that satisfies the output "
        "contract. No prose and no markdown fences.\n"
        f"Previous output:\n{raw}"
    )


@dataclass(frozen=True)
class ValidationOutcome(Generic[ModelT]):
    """A validated persona output and the number of repair retries it cost."""

    value: ModelT
    retries: int


class PersonaOutputValidator(Generic[ModelT]):
    """Validates one persona role's JSON output against its target model."""

    def __init__(
        self,
        model: type[ModelT],
        *,
        repair_prompt: RepairPrompt = default_repair_prompt,
    ) -> None:
        self._model = model
        self._repair_prompt = repair_prompt
        self.retries = 0

    @property
    def model(self) -> type[ModelT]:
        """The target Pydantic model this validator enforces."""
        return self._model

    async def validate(
        self,
        client: CompletionClient,
        messages: list[Message],
        *,
        model: str | None = None,
    ) -> ValidationOutcome[ModelT]:
        """Parse and validate a persona's output, repairing at most once.

        Raises ``PersonaOutputError`` if the output is still malformed after the
        single repair retry.
        """
        self.retries = 0
        text, _usage = await client.complete(messages, model=model)
        parsed, error = self._parse(text)
        if parsed is not None:
            return ValidationOutcome(value=parsed, retries=0)

        for _ in range(_MAX_REPAIRS):
            self.retries += 1
            repair_messages = [
                *messages,
                Message(role="assistant", content=text),
                Message(role="user", content=self._repair_prompt(text, error)),
            ]
            text, _usage = await client.complete(repair_messages, model=model)
            parsed, error = self._parse(text)
            if parsed is not None:
                return ValidationOutcome(value=parsed, retries=self.retries)

        raise PersonaOutputError(
            f"persona output for {self._model.__name__} is malformed after "
            f"{self.retries} repair retry: {error}"
        )

    def _parse(self, text: str) -> tuple[ModelT | None, str]:
        """Return (model, "") on success or (None, error) on failure."""
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            return None, f"invalid JSON: {exc}"
        try:
            return self._model.model_validate(data), ""
        except ValidationError as exc:
            return None, f"schema validation failed: {exc}"


# Roles whose output contract is a JSON object in ``contracts.state``. The
# developer emits a diff and the critic a verdict, so neither is mapped here;
# construct ``PersonaOutputValidator`` directly for those shapes.
PERSONA_MODELS: dict[str, type[BaseModel]] = {
    "groomer": GroomedRequirements,
    "architect": TechnicalDesign,
}


def validator_for(role: str) -> PersonaOutputValidator[BaseModel]:
    """Return the validator for a persona role with a JSON output contract."""
    try:
        model = PERSONA_MODELS[role]
    except KeyError:
        raise PersonaOutputError(
            f"persona role {role!r} has no JSON output contract"
        ) from None
    return PersonaOutputValidator(model)
