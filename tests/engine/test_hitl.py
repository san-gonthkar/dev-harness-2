"""HITL gate round trip: halt with a persisted checkpoint, resume one node (V11 8.15).

Validation matrix (8.B): the graph halts at the approval node with a persisted
checkpoint, and a button resume (``Command(resume=...)``) advances exactly one
node. The gate is exercised against a minimal graph with an in-memory
checkpointer and a queue-backed resume source - no socket, no clock, no network.
"""

from __future__ import annotations

import pytest

from dev_harness.contracts.enums import CriticCommand
from dev_harness.engine.hitl import (
    APPROVAL_NODE,
    PREPARE_NODE,
    HitlGate,
    HitlResumeError,
    QueueResumeSource,
)
from dev_harness.engine.state import HarnessStateChannels


def _initial() -> HarnessStateChannels:
    return {
        "project_id": "p1",
        "workspace_path": "/tmp/ws",
        "thread_id": "t1",
        "raw_input": "build it",
    }


def _gate(*commands: CriticCommand) -> HitlGate:
    return HitlGate(resume_source=QueueResumeSource(list(commands)))


@pytest.mark.unit
def test_queue_resume_source_pops_in_order_then_exhausts() -> None:
    """The queue source yields commands in order and then ``None``."""
    source = QueueResumeSource([CriticCommand.RESUME, CriticCommand.STOP])
    assert source.next_command() is CriticCommand.RESUME
    assert source.next_command() is CriticCommand.STOP
    assert source.next_command() is None


@pytest.mark.unit
def test_hitl_resume_error_has_remediation() -> None:
    """HitlResumeError carries a non-empty remediation (error taxonomy)."""
    assert HitlResumeError.remediation


@pytest.mark.integration
def test_start_halts_at_approval_with_persisted_checkpoint() -> None:
    """(a) The graph halts at the approval node with a persisted checkpoint."""
    gate = _gate(CriticCommand.RESUME)
    gate.start(_initial())

    assert gate.is_halted()
    assert gate.pending_node() == APPROVAL_NODE
    assert gate.checkpoint_persisted()
    # Only the prepare node ran; the approval node is still pending.
    assert gate.executed_nodes == [PREPARE_NODE]


@pytest.mark.integration
def test_resume_advances_exactly_one_node() -> None:
    """(b) A button resume advances exactly one node (the approval node)."""
    gate = _gate(CriticCommand.RESUME)
    gate.start(_initial())
    before = gate.executed_nodes

    gate.resume()

    after = gate.executed_nodes
    assert after == [*before, APPROVAL_NODE]
    assert len(after) - len(before) == 1
    # The graph reached END: nothing further is pending.
    assert gate.pending_node() is None


@pytest.mark.negative
def test_resume_without_source_raises() -> None:
    """Resuming with no injected resume source fails closed."""
    gate = HitlGate()
    gate.start(_initial())
    with pytest.raises(HitlResumeError):
        gate.resume()


@pytest.mark.negative
def test_resume_with_exhausted_source_raises() -> None:
    """Resuming with an exhausted resume source fails closed."""
    gate = _gate()  # no commands queued
    gate.start(_initial())
    with pytest.raises(HitlResumeError):
        gate.resume()
