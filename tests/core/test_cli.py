"""Core CLI tests (V11 6.9) — transitions --table and latency-drill.

The 16 transition pairs are data, not functions: they are parametrized from
the same normative §0.22 table that ``contracts/transitions.py`` enforces.
The latency drill is exercised with the frozen clock (``tests/support/clock.py``)
so no test ever sleeps.
"""

from __future__ import annotations

import json
import runpy
from pathlib import Path
from typing import Any

import pytest

from dev_harness.contracts.enums import CriticCommand, ExecutionState
from dev_harness.contracts.transitions import transition_table
from dev_harness.core import cli as cli_mod
from dev_harness.core.cli import main
from tests.support.clock import make_clock

pytestmark = pytest.mark.unit

# The 16-cell §0.22 table (normative, from the plan). Expected result per
# (state, command); "ILLEGAL" means IllegalTransitionError.
EXPECTED: dict[ExecutionState, dict[CriticCommand, str]] = {
    ExecutionState.READY: {
        CriticCommand.START: "RUNNING",
        CriticCommand.PAUSE: "READY",
        CriticCommand.RESUME: "ILLEGAL",
        CriticCommand.STOP: "STOPPED",
    },
    ExecutionState.RUNNING: {
        CriticCommand.START: "ILLEGAL",
        CriticCommand.PAUSE: "PAUSED",
        CriticCommand.RESUME: "ILLEGAL",
        CriticCommand.STOP: "STOPPED",
    },
    ExecutionState.PAUSED: {
        CriticCommand.START: "ILLEGAL",
        CriticCommand.PAUSE: "PAUSED",
        CriticCommand.RESUME: "RUNNING",
        CriticCommand.STOP: "STOPPED",
    },
    ExecutionState.STOPPED: {
        CriticCommand.START: "ILLEGAL",
        CriticCommand.PAUSE: "ILLEGAL",
        CriticCommand.RESUME: "ILLEGAL",
        CriticCommand.STOP: "STOPPED",
    },
}

# All 16 (state, command) pairs as parametrize data.
PAIRS: list[tuple[ExecutionState, CriticCommand, str]] = [
    (state, command, EXPECTED[state][command])
    for state in ExecutionState
    for command in CriticCommand
]


# --- transitions --table ----------------------------------------------------


@pytest.mark.parametrize("state,command,expected", PAIRS)
def test_table_cell_matches_spec(
    state: ExecutionState, command: CriticCommand, expected: str
) -> None:
    """Every one of the 16 cells matches §0.22 (data, not 16 functions)."""
    assert transition_table()[state.value][command.value] == expected


def test_transitions_table_output(capsys: pytest.CaptureFixture[str]) -> None:
    """The printed table contains every state and command, no UNDEFINED."""
    assert main(["transitions", "--table"]) == 0
    out = capsys.readouterr().out
    for state in ExecutionState:
        assert state.value in out
    for command in CriticCommand:
        assert command.value in out
    assert "UNDEFINED" not in out


def test_transitions_table_matches_spec_row_by_row(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The printed table agrees cell-for-cell with the source of truth."""
    main(["transitions", "--table"])
    out = capsys.readouterr().out
    table = transition_table()
    for state in ExecutionState:
        for command in CriticCommand:
            assert f"{table[state.value][command.value]}" in out


def test_main_guard_runs_module(capsys: pytest.CaptureFixture[str]) -> None:
    """``python -m dev_harness.core.cli`` exits via SystemExit(main())."""
    with pytest.raises(SystemExit) as ei:
        runpy.run_module("dev_harness.core.cli", run_name="__main__")
    assert ei.value.code == 2  # no subcommand -> argparse usage error


# --- latency-drill ----------------------------------------------------------


def _run_drill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clock: Any,
    trials: int = 5,
) -> int:
    """Run latency-drill in tmp_path with the frozen clock injected."""
    monkeypatch.setattr(
        cli_mod, "REPORT_PATH", tmp_path / "reports" / "interrupt_latency.json"
    )
    monkeypatch.setattr(cli_mod, "_clock", clock.monotonic)
    return main(["latency-drill", "--trials", str(trials)])


def _ticking_clock(step_ms: float) -> Any:
    """A frozen clock that advances ``step_ms`` on every read.

    The drill reads the clock twice per trial (start + end); advancing on
    each read makes every trial measure exactly ``step_ms``.
    """
    clock = make_clock()

    def monotonic() -> float:
        clock.tick(step_ms / 1000.0)
        return clock.monotonic()

    return type("_TickingClock", (), {"monotonic": staticmethod(monotonic)})()


def test_latency_drill_writes_report_with_ordering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The report is written with p50 <= p95 <= max."""
    rc = _run_drill(tmp_path, monkeypatch, _ticking_clock(10.0), trials=5)
    assert rc == 0
    report_path = tmp_path / "reports" / "interrupt_latency.json"
    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["trials"] == 5
    assert report["p50_ms"] <= report["p95_ms"] <= report["max_ms"]
    assert report["slo"]["passed"] is True


def test_latency_drill_fails_slo_when_p95_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """p95 >= 500ms makes the drill exit non-zero."""
    rc = _run_drill(tmp_path, monkeypatch, _ticking_clock(600.0), trials=5)
    assert rc == 1
    report = json.loads(
        (tmp_path / "reports" / "interrupt_latency.json").read_text(encoding="utf-8")
    )
    assert report["slo"]["passed"] is False


def test_latency_drill_rejects_zero_trials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--trials 0 is a usage error, not a crash."""
    rc = _run_drill(tmp_path, monkeypatch, _ticking_clock(10.0), trials=0)
    assert rc == 2
    assert not (tmp_path / "reports" / "interrupt_latency.json").exists()


def test_latency_drill_uses_real_gatekeeper_and_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The drill drives the real 6.1 gatekeeper + 6.2 handler."""
    rc = _run_drill(tmp_path, monkeypatch, _ticking_clock(10.0), trials=3)
    assert rc == 0
    report = json.loads(
        (tmp_path / "reports" / "interrupt_latency.json").read_text(encoding="utf-8")
    )
    # Every trial is a real PAUSE -> PAUSED transition: 10ms each.
    assert report["p50_ms"] == pytest.approx(10.0)
    assert report["max_ms"] == pytest.approx(10.0)
