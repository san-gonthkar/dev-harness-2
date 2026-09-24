"""Groomer persona graph node (V11 8.4).

The Groomer is the first node of the SDLC pipeline: it turns ``raw_input`` into a
locked ``GroomedRequirements`` and writes it to the ``groomed_requirements``
channel. The node is a LangGraph node - a callable that takes the state and
returns a **partial** update dict - and it is idempotent: if the channel is
already set, it returns an empty update without calling the client, so a resumed
graph never re-runs (or re-bills) the persona.

The client is injected (``providers.base.LLMClient`` satisfies
``CompletionClient``) so tests run against a scripted fake with no network.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from dev_harness.contracts.llm import Message
from dev_harness.contracts.state import GroomedRequirements
from dev_harness.engine.personas.validators import (
    CompletionClient,
    validator_for,
)
from dev_harness.engine.state import HarnessStateChannels

# Persona templates live alongside this package's parent (engine/personas/).
_PERSONAS_DIR = Path(__file__).resolve().parent.parent / "personas"


def load_persona_prompt(role: str) -> str:
    """Read a persona prompt template from disk, relative to the package."""
    return (_PERSONAS_DIR / f"{role}.md").read_text(encoding="utf-8")


class GroomerNode:
    """LangGraph node running the groomer persona over ``raw_input``."""

    def __init__(self, client: CompletionClient, *, model: str | None = None) -> None:
        self._client = client
        self._model = model
        self._validator = validator_for("groomer")
        self._prompt = load_persona_prompt("groomer")

    async def __call__(
        self, state: HarnessStateChannels
    ) -> dict[str, GroomedRequirements]:
        """Return the ``groomed_requirements`` partial update, or ``{}``.

        A set channel (always ``LOCKED``) makes this a no-op: no client call.
        """
        if state.get("groomed_requirements") is not None:
            return {}

        messages = [
            Message(role="system", content=self._prompt),
            Message(role="user", content=state.get("raw_input", "")),
        ]
        outcome = await self._validator.validate(
            self._client, messages, model=self._model
        )
        value = cast("GroomedRequirements", outcome.value)
        return {"groomed_requirements": value}


def make_groomer_node(
    client: CompletionClient, *, model: str | None = None
) -> GroomerNode:
    """Build the groomer node bound to an injected client."""
    return GroomerNode(client, model=model)