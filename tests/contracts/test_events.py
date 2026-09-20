"""IPC envelope payload discrimination tests (V11 0.6)."""

from __future__ import annotations

from typing import Literal

import pytest
from pydantic import ValidationError

from dev_harness.contracts.enums import CriticCommand, EventType
from dev_harness.contracts.events import (
    PAYLOAD_BY_TYPE,
    AgentTokenStreamPayload,
    Envelope,
    InterruptRequestPayload,
    SnapshotPayload,
)
from dev_harness.contracts.state import HarnessState

pytestmark = pytest.mark.unit


def test_all_nine_types_have_payload() -> None:
    assert len(PAYLOAD_BY_TYPE) == 9
    assert set(PAYLOAD_BY_TYPE) == set(EventType)


def test_type_to_payload_1to1() -> None:
    for event_type, payload_cls in PAYLOAD_BY_TYPE.items():
        ann = payload_cls.model_fields["type"].annotation
        assert ann == Literal[event_type.value], (event_type, ann)


def test_unknown_type_rejected() -> None:
    with pytest.raises(ValidationError):
        Envelope.model_validate(
            {
                "type": "NOT_A_TYPE",
                "seq": 1,
                "payload": {"type": "NOT_A_TYPE"},
            }
        )


def test_type_payload_mismatch_rejected() -> None:
    """A GIT_STATUS_UPDATE envelope carrying a token payload must be rejected."""
    with pytest.raises(ValidationError):
        Envelope.model_validate(
            {
                "type": "GIT_STATUS_UPDATE",
                "seq": 2,
                "payload": {"type": "AGENT_TOKEN_STREAM", "seq": 1, "token": "x"},
            }
        )


def test_token_stream_envelope_round_trip() -> None:
    env = Envelope.model_validate(
        {
            "type": "AGENT_TOKEN_STREAM",
            "seq": 3,
            "payload": {
                "type": "AGENT_TOKEN_STREAM",
                "seq": 1,
                "token": "hello",
                "agent": "groomer",
            },
        }
    )
    assert env.type == EventType.AGENT_TOKEN_STREAM
    assert isinstance(env.payload, AgentTokenStreamPayload)
    assert env.payload.token == "hello"
    assert Envelope.model_validate_json(env.model_dump_json()) == env


def test_interrupt_request_payload() -> None:
    env = Envelope.model_validate(
        {
            "type": "INTERRUPT_REQUEST",
            "seq": 4,
            "payload": {
                "type": "INTERRUPT_REQUEST",
                "command": "PAUSE",
                "reason": "user",
            },
        }
    )
    assert isinstance(env.payload, InterruptRequestPayload)
    assert env.payload.command == CriticCommand.PAUSE


def test_snapshot_carries_full_state() -> None:
    state = HarnessState(project_id="p1", workspace_path="/w", thread_id="t1")
    env = Envelope.model_validate(
        {
            "type": "SNAPSHOT",
            "seq": 5,
            "payload": {"type": "SNAPSHOT", "state": state.model_dump(mode="json")},
        }
    )
    assert isinstance(env.payload, SnapshotPayload)
    assert env.payload.state.project_id == "p1"
