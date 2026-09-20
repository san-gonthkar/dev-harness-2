"""Cost governor: USD from Usage x registry pricing (V11 4.6).

Per-run and per-day ceilings. Cost is computed from token usage and the
model registry pricing (usd_per_mtok_in/out). The governor is the only
thing between a retry loop and an unbounded bill.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from dev_harness.contracts.llm import Usage
from dev_harness.providers.registry import ModelEntry, ModelRegistry

Clock = Callable[[], float]
DAY_SECONDS = 86400.0


class CostGovernor:
    """Tracks cumulative spend against per-run and per-day ceilings."""

    def __init__(
        self,
        registry: ModelRegistry,
        *,
        budget_usd_per_run: float | None = None,
        budget_usd_per_day: float | None = None,
        clock: Clock = time.monotonic,
    ) -> None:
        self._registry = registry
        self.budget_usd_per_run = budget_usd_per_run
        self.budget_usd_per_day = budget_usd_per_day
        self._clock = clock
        self._lock = threading.Lock()
        self._run_usd = 0.0
        self._day_usd = 0.0
        self._day_start = clock()

    def cost_usd(self, model_id: str, usage: Usage) -> float:
        """Compute the USD cost of a Usage for a model."""
        entry = self._registry.resolve(model_id)
        return _usd(entry, usage)

    def commit(self, model_id: str, usage: Usage) -> float:
        """Commit a usage; returns the cost. Raises BudgetExceededError if a
        ceiling would be breached."""
        cost = self.cost_usd(model_id, usage)
        with self._lock:
            self._roll_day()
            new_run = self._run_usd + cost
            new_day = self._day_usd + cost
            if self.budget_usd_per_run is not None and new_run > self.budget_usd_per_run:
                raise BudgetExceededError(
                    f"run budget ${self.budget_usd_per_run:.2f} would be exceeded",
                    remediation="Raise budget_usd_per_run or reduce model usage.",
                )
            if self.budget_usd_per_day is not None and new_day > self.budget_usd_per_day:
                raise BudgetExceededError(
                    f"day budget ${self.budget_usd_per_day:.2f} would be exceeded",
                    remediation="Raise budget_usd_per_day or wait for the day rollover.",
                )
            self._run_usd = new_run
            self._day_usd = new_day
            return cost

    def _roll_day(self) -> None:
        """Roll the day window if a full day has elapsed."""
        now = self._clock()
        if now - self._day_start >= DAY_SECONDS:
            self._day_usd = 0.0
            self._day_start = now

    @property
    def run_usd(self) -> float:
        """Cumulative spend this run."""
        with self._lock:
            return self._run_usd

    @property
    def day_usd(self) -> float:
        """Cumulative spend in the current day window."""
        with self._lock:
            return self._day_usd


def _usd(entry: ModelEntry, usage: Usage) -> float:
    """USD for a usage at a model's pricing (per million tokens)."""
    return (
        usage.input_tokens * entry.usd_per_mtok_in / 1_000_000
        + usage.output_tokens * entry.usd_per_mtok_out / 1_000_000
    )


class BudgetExceededError(Exception):
    """A configured budget ceiling was breached."""

    def __init__(self, message: str, *, remediation: str) -> None:
        super().__init__(message)
        self.remediation = remediation
