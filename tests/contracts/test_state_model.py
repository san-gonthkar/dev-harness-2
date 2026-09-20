"""V7 state model round-trip tests (V11 0.5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from dev_harness.contracts.enums import ExecutionState
from dev_harness.contracts.state import HarnessState

pytestmark = pytest.mark.unit

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "state_v7_golden.json"


def _load_golden() -> dict:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_round_trip_golden() -> None:
    golden = _load_golden()
    state = HarnessState.model_validate(golden)
    assert state.model_dump(mode="json") == golden


def test_missing_project_id_fails() -> None:
    golden = _load_golden()
    del golden["project_id"]
    with pytest.raises(ValidationError) as excinfo:
        HarnessState.model_validate(golden)
    assert any(e.get("loc") == ("project_id",) for e in excinfo.value.errors()), (
        excinfo.value
    )


def test_critic_gatekeeper_status_accepts_all_four() -> None:
    golden = _load_golden()
    for state_value in ExecutionState:
        data = dict(golden)
        data["tui_state"] = {
            **data["tui_state"],
            "critic_gatekeeper_status": state_value.value,
        }
        state = HarnessState.model_validate(data)
        assert state.tui_state.critic_gatekeeper_status == state_value


def test_stopped_persists_round_trip() -> None:
    """STOPPED must survive a full serialize/deserialize cycle (V11 audit C2)."""
    golden = _load_golden()
    data = dict(golden)
    data["tui_state"] = {**data["tui_state"], "critic_gatekeeper_status": "STOPPED"}
    state = HarnessState.model_validate(data)
    dumped = state.model_dump(mode="json")
    assert dumped["tui_state"]["critic_gatekeeper_status"] == "STOPPED"
    assert HarnessState.model_validate(dumped) == state


def test_extra_fields_rejected() -> None:
    golden = _load_golden()
    golden["surprise"] = True
    with pytest.raises(ValidationError):
        HarnessState.model_validate(golden)
