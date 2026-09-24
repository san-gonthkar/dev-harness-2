"""Architect persona graph node (V11 8.5).

The Architect is the second node of the SDLC pipeline: it consumes the locked
``groomed_requirements`` and emits a ``TechnicalDesign`` (architecture spec plus
interface contracts) into the ``technical_design`` channel. Like the groomer
node (8.4) it is a LangGraph node - a callable that takes the state and returns
a **partial** update dict - and it is idempotent: a set channel returns an empty
update without calling the client, so a resumed graph never re-runs (or
re-bills) the persona.

Two output-contract invariants are enforced after validation:

* a non-empty ``interface_contracts.openapi_spec`` must parse as JSON
  (``json.loads`` is stdlib, so the gate adds no dependency and keeps
  ``mypy --strict`` clean);
* an empty design (blank ``architecture_spec`` or empty interface contracts)
  may never be ``APPROVED`` - it is downgraded to ``REJECTED`` so a resume can
  never advance on a design with no substance.

The client is injected (``providers.base.LLMClient`` satisfies
``CompletionClient``) so tests run against a scripted fake with no network.
"""

from __future__ import annotations

import json
from typing import cast

from dev_harness.contracts.errors import EngineError, PersonaOutputError
from dev_harness.contracts.llm import Message
from dev_harness.contracts.state import GroomedRequirements, TechnicalDesign
from dev_harness.engine.nodes.groomer import load_persona_prompt
from dev_harness.engine.personas.validators import (
    CompletionClient,
    validator_for,
)
from dev_harness.engine.state import HarnessStateChannels


def _requirements_prompt(groomed: GroomedRequirements) -> str:
    """Render the locked requirements as the architect's user message."""
    return (
        f"Locked requirements ({groomed.version}, status {groomed.status}):\n\n"
        f"{groomed.prd_content}"
    )


def _is_empty_design(design: TechnicalDesign) -> bool:
    """True when the design carries no architecture spec or no contracts."""
    contracts = design.interface_contracts
    return design.architecture_spec.strip() == "" or (
        contracts.openapi_spec.strip() == "" and contracts.db_schema.strip() == ""
    )


def _enforce_contract(design: TechnicalDesign) -> TechnicalDesign:
    """Enforce the OpenAPI parse and empty-design gates on validated output.

    Raises ``PersonaOutputError`` if a non-empty ``openapi_spec`` is not valid
    JSON; downgrades an empty ``APPROVED`` design to ``REJECTED``.
    """
    spec = design.interface_contracts.openapi_spec
    if spec.strip():
        try:
            json.loads(spec)
        except json.JSONDecodeError as exc:
            raise PersonaOutputError(
                f"architect output has a non-JSON openapi_spec: {exc}"
            ) from exc
    if _is_empty_design(design) and design.status == "APPROVED":
        return design.model_copy(update={"status": "REJECTED"})
    return design


class ArchitectNode:
    """LangGraph node running the architect persona over locked requirements."""

    def __init__(self, client: CompletionClient, *, model: str | None = None) -> None:
        self._client = client
        self._model = model
        self._validator = validator_for("architect")
        self._prompt = load_persona_prompt("architect")

    async def __call__(
        self, state: HarnessStateChannels
    ) -> dict[str, TechnicalDesign]:
        """Return the ``technical_design`` partial update, or ``{}``.

        A set channel makes this a no-op (no client call). A missing
        ``groomed_requirements`` channel is an error: the architect cannot
        design against a PRD that has not been locked.
        """
        if state.get("technical_design") is not None:
            return {}

        groomed = state.get("groomed_requirements")
        if groomed is None:
            raise EngineError(
                "architect node requires groomed_requirements, but the channel "
                "is absent",
                remediation="Run the groomer node first so a locked PRD exists.",
            )

        messages = [
            Message(role="system", content=self._prompt),
            Message(role="user", content=_requirements_prompt(groomed)),
        ]
        outcome = await self._validator.validate(
            self._client, messages, model=self._model
        )
        design = _enforce_contract(cast("TechnicalDesign", outcome.value))
        return {"technical_design": design}


def make_architect_node(
    client: CompletionClient, *, model: str | None = None
) -> ArchitectNode:
    """Build the architect node bound to an injected client."""
    return ArchitectNode(client, model=model)