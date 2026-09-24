"""Critic persona graph node: a strict binary, read-only gate (V11 8.14).

The Critic is the terminal node of the SDLC pipeline. Unlike the Groomer (8.4),
Architect (8.5) and Developer (8.10), it **builds nothing**: it reads the locked
artifacts and returns a strict binary verdict - ``APPROVED`` or ``REJECTED``, no
"approved with comments". Two properties are load-bearing and their acceptance
is exact (8.B):

* **Read-only scope.** The Critic may not write ``groomed_requirements``,
  ``technical_design``, ``chunk_dag``, or code. :data:`WRITABLE_CHANNELS` is the
  explicit whitelist - a single channel, ``tui_state`` - and :func:`guard_scope`
  raises :class:`CriticScopeViolation` on any write outside it.
* **Confined PAUSE/RESUME diff.** :func:`apply_command` applies a gatekeeper
  command through that same guard, so pausing/resuming a session can only ever
  change ``tui_state`` (:func:`state_diff` returns a subset of the whitelist).

The verdict is **advisory**: the node publishes the resulting gate status into
``tui_state.critic_gatekeeper_status`` (its verdict channel) and never mutates
the upstream artifacts. The client is injected (``providers.base.LLMClient``
satisfies ``CompletionClient``) so tests run against a scripted fake with no
network.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from dev_harness.contracts.enums import CriticCommand, ExecutionState
from dev_harness.contracts.errors import CriticScopeViolation, PersonaOutputError
from dev_harness.contracts.llm import Message
from dev_harness.contracts.state import HarnessState, TuiState
from dev_harness.engine.nodes.groomer import load_persona_prompt
from dev_harness.engine.personas.validators import CompletionClient
from dev_harness.engine.state import HarnessStateChannels

# A verdict is strictly binary - there is no third "approved with comments" value.
Verdict = Literal["APPROVED", "REJECTED"]

# The Critic's ONLY writable state channel. Every other key in an update dict is
# an out-of-scope write and raises CriticScopeViolation. ``tui_state`` carries the
# verdict channel (``critic_gatekeeper_status``) and the pause bookkeeping.
WRITABLE_CHANNELS: frozenset[str] = frozenset({"tui_state"})

_FENCE = "```"


class CriticVerdict(BaseModel):
    """The Critic's structured binary gate verdict (persona output contract)."""

    model_config = ConfigDict(extra="forbid")

    verdict: Verdict
    target: str
    reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _rejected_must_cite_reasons(self) -> CriticVerdict:
        """A rejection cites its requirement clauses; approval may be silent."""
        if self.verdict == "REJECTED" and not self.reasons:
            raise ValueError("a REJECTED verdict must cite at least one reason")
        return self


def _strip_fences(text: str) -> str:
    """Drop a single leading/trailing markdown code fence, if present."""
    stripped = text.strip()
    if not stripped.startswith(_FENCE):
        return stripped
    lines = stripped.splitlines()[1:]  # drop the opening ``` or ```json line
    if lines and lines[-1].strip() == _FENCE:
        lines = lines[:-1]
    return "\n".join(lines)


def parse_verdict(text: str) -> CriticVerdict:
    """Parse a persona reply into a :class:`CriticVerdict`.

    Raises :class:`PersonaOutputError` when the reply is not a valid verdict
    object, so malformed output never reaches the pipeline as a decision.
    """
    try:
        data = json.loads(_strip_fences(text))
    except json.JSONDecodeError as exc:
        raise PersonaOutputError(
            f"critic output is not a JSON verdict object: {exc}"
        ) from exc
    try:
        return CriticVerdict.model_validate(data)
    except ValidationError as exc:
        raise PersonaOutputError(f"critic verdict failed validation: {exc}") from exc


def _status_for(verdict: Verdict) -> ExecutionState:
    """Map a verdict to the gate status it publishes (approve runs, reject halts)."""
    return ExecutionState.RUNNING if verdict == "APPROVED" else ExecutionState.PAUSED


def guard_scope(update: Mapping[str, object]) -> None:
    """Enforce the Critic's read-only scope on a state update.

    Raises :class:`CriticScopeViolation` naming every key outside
    :data:`WRITABLE_CHANNELS` - so a write to ``groomed_requirements``,
    ``technical_design``, ``chunk_dag``, or any code channel is rejected.
    """
    offenders = sorted(set(update) - WRITABLE_CHANNELS)
    if offenders:
        raise CriticScopeViolation(
            "critic node attempted to write out-of-scope channels: "
            f"{offenders} (writable: {sorted(WRITABLE_CHANNELS)})"
        )


def state_diff(before: HarnessState, after: HarnessState) -> set[str]:
    """Return the names of ``HarnessState`` fields whose values changed."""
    return {
        name
        for name in HarnessState.model_fields
        if getattr(before, name) != getattr(after, name)
    }


def apply_command(
    state: HarnessState,
    command: CriticCommand,
    *,
    timestamp: int | None = None,
) -> HarnessState:
    """Apply a gatekeeper command, confined to the ``tui_state`` channel.

    PAUSE records the interrupt timestamp and sets ``is_paused``; RESUME clears
    it; STOP and START set the gate status. The update is routed through
    :func:`guard_scope`, so the resulting :func:`state_diff` is always a subset
    of :data:`WRITABLE_CHANNELS`.
    """
    status = ExecutionState.RUNNING
    is_paused = False
    interrupt = state.tui_state.last_interrupt_timestamp
    if command is CriticCommand.PAUSE:
        status = ExecutionState.PAUSED
        is_paused = True
        interrupt = timestamp
    elif command is CriticCommand.STOP:
        status = ExecutionState.STOPPED

    tui = state.tui_state.model_copy(
        update={
            "is_paused": is_paused,
            "critic_gatekeeper_status": status,
            "last_interrupt_timestamp": interrupt,
        }
    )
    update: dict[str, object] = {"tui_state": tui}
    guard_scope(update)
    return state.model_copy(update=update)


class CriticNode:
    """LangGraph node running the critic persona as a strict binary gate."""

    def __init__(self, client: CompletionClient, *, model: str | None = None) -> None:
        self._client = client
        self._model = model
        self._prompt = load_persona_prompt("critic")
        self.last_verdict: CriticVerdict | None = None

    async def __call__(self, state: HarnessStateChannels) -> dict[str, TuiState]:
        """Return the ``tui_state`` partial update carrying the gate verdict."""
        messages = [
            Message(role="system", content=self._prompt),
            Message(role="user", content=state.get("raw_input", "")),
        ]
        text, _usage = await self._client.complete(messages, model=self._model)
        verdict = parse_verdict(text)
        self.last_verdict = verdict

        tui = state.get("tui_state") or TuiState()
        update = {
            "tui_state": tui.model_copy(
                update={"critic_gatekeeper_status": _status_for(verdict.verdict)}
            )
        }
        guard_scope(update)
        return update


def make_critic_node(
    client: CompletionClient, *, model: str | None = None
) -> CriticNode:
    """Build the critic node bound to an injected client."""
    return CriticNode(client, model=model)
