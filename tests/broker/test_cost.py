"""Cost governor tests (V11 4.6) — arithmetic + ceilings."""

from __future__ import annotations

import threading

import pytest

from dev_harness.broker.cost import DAY_SECONDS, BudgetExceededError, CostGovernor
from dev_harness.config import HarnessConfig, ProviderConfig
from dev_harness.contracts.llm import Usage
from dev_harness.providers.registry import ModelRegistry


class FrozenClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _registry() -> ModelRegistry:
    config = HarnessConfig(
        providers={
            "anthropic": ProviderConfig(
                usd_per_mtok_in=3.0, usd_per_mtok_out=15.0
            )
        }
    )
    return ModelRegistry(config)


@pytest.mark.unit
def test_1m_in_1m_out_equals_expected_usd() -> None:
    gov = CostGovernor(_registry())
    cost = gov.commit("anthropic-default", Usage(1_000_000, 1_000_000))
    # 1M in * $3/M + 1M out * $15/M = $18.00
    assert abs(cost - 18.0) < 1e-4
    assert abs(gov.run_usd - 18.0) < 1e-4


@pytest.mark.unit
def test_100_concurrent_commits_lose_nothing() -> None:
    gov = CostGovernor(_registry())
    errors: list[Exception] = []

    def worker() -> None:
        try:
            gov.commit("anthropic-default", Usage(1000, 1000))
        except (ValueError, KeyError) as exc:  # pragma: no cover - unexpected
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)
    assert errors == []
    # 100 * (1000*3 + 1000*15)/1e6 = 100 * 0.018 = 1.8
    assert abs(gov.run_usd - 1.8) < 1e-4


@pytest.mark.unit
def test_run_ceiling_breach_raises() -> None:
    gov = CostGovernor(_registry(), budget_usd_per_run=0.50)
    gov.commit("anthropic-default", Usage(100_000, 0))  # $0.30
    with pytest.raises(BudgetExceededError):
        gov.commit("anthropic-default", Usage(100_000, 0))  # would be $0.60


@pytest.mark.unit
def test_day_ceiling_breach_raises() -> None:
    gov = CostGovernor(_registry(), budget_usd_per_day=0.50)
    gov.commit("anthropic-default", Usage(100_000, 0))  # $0.30
    with pytest.raises(BudgetExceededError):
        gov.commit("anthropic-default", Usage(100_000, 0))  # would be $0.60


@pytest.mark.unit
def test_day_rollover_resets_day_budget() -> None:
    clock = FrozenClock()
    gov = CostGovernor(
        _registry(), budget_usd_per_day=0.50, clock=clock
    )
    gov.commit("anthropic-default", Usage(100_000, 0))  # $0.30
    clock.advance(DAY_SECONDS + 1)
    # Day window rolled: the same commit is allowed again.
    gov.commit("anthropic-default", Usage(100_000, 0))
    assert abs(gov.day_usd - 0.30) < 1e-4


@pytest.mark.unit
def test_cost_usd_matches_commit() -> None:
    gov = CostGovernor(_registry())
    usage = Usage(500_000, 250_000)
    assert gov.cost_usd("anthropic-default", usage) == gov.commit("anthropic-default", usage)


@pytest.mark.unit
def test_unknown_model_raises() -> None:
    from dev_harness.contracts.errors import UnknownModelError

    gov = CostGovernor(_registry())
    with pytest.raises(UnknownModelError):
        gov.commit("nope", Usage(1, 1))
