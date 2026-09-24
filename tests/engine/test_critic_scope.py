"""Critic node scope-guard tests (V11 8.14).

Validation matrix (8.B) is **zero-drift** and its acceptance is exact:

* (a) a Critic attempt to write ``groomed_requirements`` (or code) raises
  :class:`CriticScopeViolation`;
* (b) a PAUSE/RESUME cycle produces a state diff **confined to ``tui_state``** -
  the diff keys are a subset of ``{"tui_state"}``.

The Critic returns a strict binary verdict and publishes it to its only writable
channel (``tui_state.critic_gatekeeper_status``); it never mutates the upstream
artifacts. The client is a scripted fake (no network).
"""

from __future__ import annotations

import json

import pytest

from dev_harness.contracts.enums import CriticCommand, ExecutionState
from dev_harness.contracts.errors import CriticScopeViolation, PersonaOutputError
from dev_harness.contracts.llm import Message, Usage
from dev_harness.contracts.state import HarnessState, TuiState
from dev_harness.engine.nodes.critic import (
    WRITABLE_CHANNELS,
    CriticNode,
    apply_command,
    guard_scope,
    parse_verdict,
    state_diff,
)
from dev_harness.engine.nodes.groomer import load_persona_prompt
from dev_harness.engine.state import HarnessStateChannels

_APPROVED_JSON = json.dumps({"verdict": "APPROVED", "target": "chunk-1", "reasons": []})
_REJECTED_JSON = json.dumps(
    {"verdict": "REJECTED", "target": "chunk-1", "reasons": ["REQ-3"]}
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
        "raw_input": "Evaluate chunk-1.",
    }
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


def _harness_state(**overrides: object) -> HarnessState:
    base: dict[str, object] = {
        "project_id": "p1",
        "workspace_path": "/tmp/ws",
        "thread_id": "t1",
        "raw_input": "Build a task tracker.",
    }
    base.update(overrides)
    return HarnessState.model_validate(base)


# --- (a) scope guard: out-of-scope writes raise ------------------------------


@pytest.mark.negative
def test_guard_scope_rejects_groomed_requirements_write() -> None:
    """A write to ``groomed_requirements`` raises CriticScopeViolation."""
    with pytest.raises(CriticScopeViolation, match="groomed_requirements"):
        guard_scope({"groomed_requirements": object()})


@pytest.mark.negative
def test_guard_scope_rejects_code_and_design_writes() -> None:
    """Code/design/DAG channels are all out of scope for the Critic."""
    for channel in ("technical_design", "chunk_dag", "raw_input", "git_state"):
        with pytest.raises(CriticScopeViolation, match=channel):
            guard_scope({channel: object()})


@pytest.mark.unit
def test_guard_scope_allows_only_the_writable_whitelist() -> None:
    """``tui_state`` is the sole writable channel and passes the guard."""
    assert WRITABLE_CHANNELS == frozenset({"tui_state"})

    guard_scope({"tui_state": TuiState()})  # must not raise


# --- node shape: strict binary verdict ---------------------------------------


@pytest.mark.unit
def test_load_persona_prompt_reads_critic_from_disk() -> None:
    """The critic prompt is loaded from the packaged persona file."""
    prompt = load_persona_prompt("critic")

    assert prompt.startswith("# Persona: Critic")
    assert "Output Contract" in prompt


@pytest.mark.unit
async def test_critic_approve_writes_only_tui_state() -> None:
    """An APPROVED verdict writes only ``tui_state`` and never calls out-of-scope."""
    client = ScriptedClient([_APPROVED_JSON])
    node = CriticNode(client)

    update = await node(_state())

    assert set(update) <= WRITABLE_CHANNELS
    assert update["tui_state"].critic_gatekeeper_status is ExecutionState.RUNNING
    assert node.last_verdict is not None
    assert node.last_verdict.verdict == "APPROVED"
    assert len(client.calls) == 1


@pytest.mark.unit
async def test_critic_reject_writes_status_without_mutating_artifacts() -> None:
    """A REJECTED verdict publishes a halted status and touches no artifact."""
    client = ScriptedClient([_REJECTED_JSON])
    node = CriticNode(client)

    update = await node(_state())

    assert set(update) <= WRITABLE_CHANNELS
    assert update["tui_state"].critic_gatekeeper_status is ExecutionState.PAUSED


@pytest.mark.negative
async def test_critic_malformed_output_raises_persona_output_error() -> None:
    """Non-verdict output is rejected before it can reach the pipeline."""
    client = ScriptedClient(["not json"])
    node = CriticNode(client)

    with pytest.raises(PersonaOutputError):
        await node(_state())


@pytest.mark.negative
def test_rejected_verdict_requires_a_reason() -> None:
    """A REJECTED verdict without reasons is malformed (no silent rejection)."""
    with pytest.raises(PersonaOutputError):
        parse_verdict(
            json.dumps({"verdict": "REJECTED", "target": "c1", "reasons": []})
        )


# --- (b) PAUSE/RESUME diff is confined to tui_state ---------------------------


@pytest.mark.unit
def test_pause_resume_diff_confined_to_tui_state() -> None:
    """Pausing then resuming changes only ``tui_state`` - never any artifact."""
    start = _harness_state()
    paused = apply_command(start, CriticCommand.PAUSE, timestamp=1_700_000_000)
    resumed = apply_command(paused, CriticCommand.RESUME)

    pause_diff = state_diff(start, paused)
    resume_diff = state_diff(paused, resumed)

    assert pause_diff <= {"tui_state"}
    assert resume_diff <= {"tui_state"}
    assert paused.tui_state.is_paused is True
    assert paused.tui_state.critic_gatekeeper_status is ExecutionState.PAUSED
    assert resumed.tui_state.is_paused is False
    assert resumed.tui_state.critic_gatekeeper_status is ExecutionState.RUNNING
    # The upstream artifacts are byte-identical across the whole cycle.
    assert resumed.groomed_requirements == start.groomed_requirements
    assert resumed.technical_design == start.technical_design
    assert resumed.chunk_dag == start.chunk_dag
