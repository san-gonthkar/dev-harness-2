"""Event contract harness (V11 2.8).

The binding metric for P2: every EventType member must have a fixture that
round-trips through the real consumer parser. A fixture missing a required
field must fail validation (mutation asserted).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from dev_harness.contracts.enums import EventType
from dev_harness.contracts.events import Envelope

pytestmark = pytest.mark.contract

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "events"


def _fixture_path(event_type: EventType) -> Path:
    return FIXTURES / f"{event_type.value.lower()}.json"


def test_all_9_fixtures_exist() -> None:
    for event_type in EventType:
        assert _fixture_path(event_type).exists(), (
            f"missing fixture for {event_type.value}"
        )


def test_all_fixtures_parse_with_real_parser() -> None:
    """Every fixture parses through the real consumer parser (Envelope)."""
    for event_type in EventType:
        raw = _fixture_path(event_type).read_text(encoding="utf-8")
        env = Envelope.model_validate_json(raw)
        assert env.type == event_type


def test_9_of_9_members_exercised() -> None:
    """The harness exercises 100% of EventType members."""
    exercised = set()
    for event_type in EventType:
        raw = _fixture_path(event_type).read_text(encoding="utf-8")
        env = Envelope.model_validate_json(raw)
        exercised.add(env.type)
    assert exercised == set(EventType)
    assert len(exercised) == 9


def test_missing_required_field_fails() -> None:
    """A fixture missing a required field must fail validation (mutation)."""
    raw = json.loads(_fixture_path(EventType.FILE_CHANGE).read_text(encoding="utf-8"))
    del raw["payload"]["path"]  # required field removed
    with pytest.raises(ValidationError):
        Envelope.model_validate_json(json.dumps(raw))


def test_type_mismatch_fails() -> None:
    """An envelope whose type tag mismatches its payload is rejected."""
    raw = json.loads(_fixture_path(EventType.FILE_CHANGE).read_text(encoding="utf-8"))
    raw["type"] = "GIT_STATUS_UPDATE"  # wrong type for the payload
    with pytest.raises(ValidationError):
        Envelope.model_validate_json(json.dumps(raw))


def test_round_trip_through_framing() -> None:
    """Each fixture survives encode -> decode through the framing codec."""
    from dev_harness.ipc.framing import decode_frame, encode

    for event_type in EventType:
        raw = _fixture_path(event_type).read_text(encoding="utf-8")
        env = Envelope.model_validate_json(raw)
        decoded = decode_frame(encode(env))
        assert decoded == env
