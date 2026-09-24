#!/usr/bin/env python
"""Dispatch telemetry log: one JSONL record per subagent dispatch.

The orchestrator had no per-dispatch instrumentation, so "why is this slow?"
required manual transcript archaeology. This records the observable facts of
every dispatch so the question becomes a query.

Usage:
    python scripts/dispatch_log.py start --task 7.12b --agent python-developer
    python scripts/dispatch_log.py end --id <id> --outcome success \\
        --commit abc1234 --tool-calls 18 --files-read 3
    python scripts/dispatch_log.py summary [--phase 7]

Records are appended to ``reports/dispatch_log.jsonl`` (one JSON object per
line). ``start`` prints the dispatch id; ``end`` closes the matching record.
``summary`` aggregates by phase and outcome.

Outcomes (the failure taxonomy, fix 4): ``success``, ``empty_return``,
``hang``, ``test_failure``, ``lane_breach``, ``budget_exceeded``, ``aborted``.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
LOG_PATH = REPO / "reports" / "dispatch_log.jsonl"

OUTCOMES = (
    "success",
    "empty_return",
    "hang",
    "test_failure",
    "lane_breach",
    "budget_exceeded",
    "aborted",
)


def _append(record: dict[str, Any]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def _read_all() -> list[dict[str, Any]]:
    if not LOG_PATH.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in LOG_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def start(task: str, agent: str, phase: str | None = None) -> str:
    """Record a dispatch start; return its id."""
    dispatch_id = uuid.uuid4().hex[:12]
    _append(
        {
            "id": dispatch_id,
            "event": "start",
            "task": task,
            "agent": agent,
            "phase": phase or task.split(".")[0],
            "started": time.time(),
        }
    )
    return dispatch_id


def end(
    dispatch_id: str,
    *,
    outcome: str,
    commit: str = "",
    tool_calls: int = 0,
    files_read: int = 0,
    note: str = "",
) -> bool:
    """Close a dispatch record. Returns False when the id is unknown."""
    if outcome not in OUTCOMES:
        raise ValueError(f"unknown outcome {outcome!r}; expected one of {OUTCOMES}")
    records = _read_all()
    started: float | None = None
    for record in records:
        if record.get("id") == dispatch_id and record.get("event") == "start":
            started = float(record["started"])
            break
    if started is None:
        return False
    _append(
        {
            "id": dispatch_id,
            "event": "end",
            "outcome": outcome,
            "commit": commit,
            "tool_calls": tool_calls,
            "files_read": files_read,
            "ended": time.time(),
            "duration_s": round(time.time() - started, 1),
            "note": note,
        }
    )
    return True


def _paired() -> list[dict[str, Any]]:
    """Join start/end records into one row per dispatch."""
    starts: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    for record in _read_all():
        if record.get("event") == "start":
            starts[record["id"]] = record
        elif record.get("event") == "end":
            base = starts.get(record["id"], {})
            rows.append({**base, **record})
    return rows


def summary(phase: str | None = None) -> dict[str, Any]:
    """Aggregate dispatches by phase and outcome."""
    rows = [r for r in _paired() if phase is None or r.get("phase") == phase]
    by_outcome: dict[str, int] = {}
    by_phase: dict[str, dict[str, Any]] = {}
    for row in rows:
        outcome = str(row.get("outcome", "unknown"))
        by_outcome[outcome] = by_outcome.get(outcome, 0) + 1
        ph = str(row.get("phase", "?"))
        bucket = by_phase.setdefault(ph, {"dispatches": 0, "tool_calls": 0, "duration_s": 0.0})
        bucket["dispatches"] += 1
        bucket["tool_calls"] += int(row.get("tool_calls", 0))
        bucket["duration_s"] = round(bucket["duration_s"] + float(row.get("duration_s", 0.0)), 1)
    return {
        "dispatches": len(rows),
        "by_outcome": by_outcome,
        "by_phase": by_phase,
        "failures": sum(v for k, v in by_outcome.items() if k != "success"),
    }


def phase_report(phase: str) -> dict[str, Any]:
    """Per-phase budget rollup for the acceptance report (fix 5).

    Reports dispatch count, total tool calls, total wall-clock, the failure
    breakdown, and the average cost per dispatch — the numbers that were
    previously unknown.
    """
    rows = [r for r in _paired() if str(r.get("phase")) == str(phase)]
    tool_calls = sum(int(r.get("tool_calls", 0)) for r in rows)
    duration = round(sum(float(r.get("duration_s", 0.0)) for r in rows), 1)
    failures = sum(1 for r in rows if r.get("outcome") != "success")
    return {
        "phase": phase,
        "dispatches": len(rows),
        "failures": failures,
        "tool_calls": tool_calls,
        "duration_s": duration,
        "avg_tool_calls": round(tool_calls / len(rows), 1) if rows else 0.0,
        "avg_duration_s": round(duration / len(rows), 1) if rows else 0.0,
        "by_outcome": _count_by(rows, "outcome"),
    }


def _count_by(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    """Count rows by a key."""
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key, "unknown"))
        counts[value] = counts.get(value, 0) + 1
    return counts


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(prog="dispatch_log")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_start = sub.add_parser("start", help="Record a dispatch start")
    p_start.add_argument("--task", required=True)
    p_start.add_argument("--agent", required=True)
    p_start.add_argument("--phase", default=None)

    p_end = sub.add_parser("end", help="Close a dispatch record")
    p_end.add_argument("--id", required=True)
    p_end.add_argument("--outcome", required=True, choices=OUTCOMES)
    p_end.add_argument("--commit", default="")
    p_end.add_argument("--tool-calls", type=int, default=0)
    p_end.add_argument("--files-read", type=int, default=0)
    p_end.add_argument("--note", default="")

    p_sum = sub.add_parser("summary", help="Aggregate dispatches")
    p_sum.add_argument("--phase", default=None)

    p_report = sub.add_parser("phase-report", help="Per-phase budget rollup")
    p_report.add_argument("--phase", required=True)

    args = parser.parse_args(argv)
    if args.cmd == "start":
        print(start(args.task, args.agent, args.phase))
        return 0
    if args.cmd == "end":
        ok = end(
            args.id,
            outcome=args.outcome,
            commit=args.commit,
            tool_calls=args.tool_calls,
            files_read=args.files_read,
            note=args.note,
        )
        if not ok:
            print(f"unknown dispatch id {args.id!r}", file=sys.stderr)
            return 1
        return 0
    if args.cmd == "phase-report":
        print(json.dumps(phase_report(args.phase), indent=2))
        return 0
    print(json.dumps(summary(args.phase), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())