"""Core CLI for the interrupt engine (V11 6.9).

Subcommands:

- ``transitions --table`` — prints the 16-cell §0.22 transition table. The
  table is read from ``contracts/transitions.py`` (the single source of
  truth); this module never hardcodes a second copy, so the printed output
  cannot drift from the enforced spec. No ``UNDEFINED`` cells are possible:
  every cell is either a target state or ``ILLEGAL``.
- ``latency-drill --trials N`` — measures PAUSE -> PAUSED latency over ``N``
  trials using the real :class:`CriticGatekeeper` (6.1) and
  :class:`CriticCommandHandler` (6.2), computes p50/p95/max with
  :class:`InterruptMetrics` (6.7), writes ``reports/interrupt_latency.json``,
  and exits non-zero when p95 >= 500ms or max >= 1000ms (the 6.7 SLO).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path

from dev_harness.contracts.enums import CriticCommand, ExecutionState
from dev_harness.contracts.transitions import transition_table
from dev_harness.core.critic import CriticGatekeeper
from dev_harness.core.critic_commands import CriticCommandHandler
from dev_harness.core.metrics import InterruptMetrics

# SLO thresholds from the 6.7 validation row (p95 < 500ms, max < 1000ms).
P95_SLO_MS = 500.0
MAX_SLO_MS = 1000.0

# The report path is relative to the invocation directory (mirrors the broker
# CLI's ``reports/`` convention); tests chdir into a tmp_path.
REPORT_PATH = Path("reports") / "interrupt_latency.json"

# A monotonic clock; injectable so tests use the frozen clock.
Clock = Callable[[], float]

# Module-level clock hook: tests monkeypatch this with a frozen-clock wrapper.
_clock: Clock = time.monotonic


def _print_table(args: argparse.Namespace) -> int:
    """Print the §0.22 transition table from the single source of truth."""
    table = transition_table()
    states = [s.value for s in ExecutionState]
    commands = [c.value for c in CriticCommand]
    header = "State \\ Command | " + " | ".join(commands)
    print(header)
    print("-" * len(header))
    for state in states:
        row = [state]
        for command in commands:
            cell = table[state][command]
            assert cell != "UNDEFINED", f"undefined cell {state} + {command}"
            row.append(cell)
        print(" | ".join(row))
    return 0


def _measure_trial(clock: Clock) -> float:
    """Time one PAUSE -> PAUSED round trip in milliseconds.

    A fresh gatekeeper starts in RUNNING for every trial, so each trial
    performs a real transition (never an idempotent repeat).
    """
    gatekeeper = CriticGatekeeper(initial=ExecutionState.RUNNING)
    handler = CriticCommandHandler(gatekeeper=gatekeeper)
    start = clock()
    handler.handle(CriticCommand.PAUSE)
    return (clock() - start) * 1000.0


def _latency_drill(args: argparse.Namespace) -> int:
    """Run the PAUSE latency drill and write the JSON report."""
    trials = int(args.trials)
    if trials < 1:
        print("latency-drill: --trials must be >= 1", file=sys.stderr)
        return 2
    metrics = InterruptMetrics()
    for _ in range(trials):
        metrics.record_interrupt(_measure_trial(_clock))
    report = {
        "trials": trials,
        "p50_ms": metrics.p50(),
        "p95_ms": metrics.p95(),
        "max_ms": metrics.max_latency_ms(),
        "slo": {
            "p95_ms_max": P95_SLO_MS,
            "max_ms_max": MAX_SLO_MS,
            "passed": metrics.p95() < P95_SLO_MS
            and metrics.max_latency_ms() < MAX_SLO_MS,
        },
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        f"latency-drill: {trials} trials "
        f"p50={metrics.p50():.1f}ms p95={metrics.p95():.1f}ms "
        f"max={metrics.max_latency_ms():.1f}ms -> {REPORT_PATH}"
    )
    slo = report["slo"]
    assert isinstance(slo, dict)
    return 0 if slo["passed"] else 1


def main(argv: list[str] | None = None) -> int:
    """Core CLI entry point."""
    parser = argparse.ArgumentParser(prog="dev-harness-core-cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_trans = sub.add_parser("transitions", help="Print the §0.22 transition table")
    p_trans.add_argument("--table", action="store_true", help="Print the table")
    p_trans.set_defaults(func=_print_table)

    p_drill = sub.add_parser("latency-drill", help="Measure PAUSE latency")
    p_drill.add_argument("--trials", type=int, default=50)
    p_drill.set_defaults(func=_latency_drill)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
