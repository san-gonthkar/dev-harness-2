"""Kill-switch tests (V11 4.7) — exactly one STOP/BUDGET, refuse further."""

from __future__ import annotations

import pytest

from dev_harness.broker.cost import BudgetExceededError
from dev_harness.broker.kill_switch import KillSwitch
from dev_harness.contracts.enums import CriticCommand, EventType


@pytest.mark.unit
def test_ceiling_breach_emits_exactly_one_stop_budget() -> None:
    emitted: list[object] = []
    ks = KillSwitch(emitted.append)
    ks.trip("/tmp/engine.sock")
    ks.trip("/tmp/engine.sock")  # second trip is a no-op
    assert ks.emitted == 1
    assert ks.tripped is True
    env = emitted[0]
    assert env.type == EventType.INTERRUPT_REQUEST
    assert env.payload.command == CriticCommand.STOP
    assert env.payload.reason.startswith("BUDGET:")


@pytest.mark.unit
def test_next_reserve_refused_after_trip() -> None:
    ks = KillSwitch(lambda env: None)
    ks.trip("/tmp/engine.sock")
    with pytest.raises(BudgetExceededError):
        ks.check()


@pytest.mark.unit
def test_before_trip_check_passes() -> None:
    ks = KillSwitch(lambda env: None)
    ks.check()  # no raise


@pytest.mark.unit
def test_callback_endpoint_carried_in_reason() -> None:
    emitted: list[object] = []
    ks = KillSwitch(emitted.append)
    ks.trip("/tmp/engine.sock")
    assert emitted[0].payload.reason == "BUDGET:/tmp/engine.sock"
