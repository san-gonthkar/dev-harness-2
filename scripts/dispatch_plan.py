#!/usr/bin/env python
"""Dependency-aware dispatch planner: find tasks that can run concurrently.

P7's 13 tasks are mostly serial because they share a single agent, but some
are genuinely independent — e.g. 7.2 (repo-manager) and 7.5 (model-registry)
share only the shell (7.1). Running them in parallel would cut wall-clock, but
doing so blindly risks two agents editing the same file.

This computes, from the plan's ``X.A`` prereq column, the set of tasks whose
dependencies are all satisfied — the "ready set" — and groups them by whether
they touch disjoint files (parallel-safe) or overlap (must serialize).

Usage:
    python scripts/dispatch_plan.py --phase 7
    python scripts/dispatch_plan.py --phase 7 --done 7.1,7.7,7.8
    python scripts/dispatch_plan.py --phase 7 --json

Exit 0 always (this is a planner, not a gate).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Allow both `python scripts/dispatch_plan.py` and `from scripts import ...`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.check_traceability import TaskTrace, parse_phase


@dataclass
class PlanRow:
    """A task's place in the dispatch order."""

    task_id: str
    prereqs: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)


@dataclass
class Wave:
    """One ready set, plus whether its members can run concurrently."""

    wave: int
    ready: list[str] = field(default_factory=list)
    parallel_safe: list[str] = field(default_factory=list)
    serialized: list[str] = field(default_factory=list)
    reason: str = ""


_REPO = Path(__file__).resolve().parents[1]
_PLAN = _REPO / "requirements" / "Dev_Harness_Implementation_Plan_V11_Final.md"


def _prereqs_by_task(phase: str) -> dict[str, list[str]]:
    """Read each task's prereq cell from the plan's X.A table."""
    import re

    result: dict[str, list[str]] = {}
    row_re = re.compile(r"^\|\s*(\d+\.\d+[a-z]?)\s*\|(.*)\|\s*$")
    for line in _PLAN.read_text(encoding="utf-8").splitlines():
        match = row_re.match(line)
        if match is None:
            continue
        task_id = match.group(1)
        if not task_id.startswith(f"{phase}."):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        # X.A rows have exactly 5 cells; X.B rows have 5 too. X.A's 4th cell is
        # the prereq list (numbers/ids), X.B's 4th is a success criterion.
        prereq_cell = cells[3]
        if not prereq_cell or not re.match(r"^[\d.,\s]+$", prereq_cell):
            continue
        # Only take the first (X.A) row per task.
        if task_id in result:
            continue
        prereqs = [p.strip() for p in prereq_cell.split(",") if p.strip()]
        result[task_id] = prereqs
    return result


def build_plan(phase: str, done: set[str]) -> list[Wave]:
    """Compute dispatch waves for ``phase`` given the tasks already done."""
    traces: dict[str, TaskTrace] = {t.task_id: t for t in parse_phase(phase)}
    prereqs = _prereqs_by_task(phase)

    unsatisfied = set(traces) - done
    waves: list[Wave] = []
    wave_index = 0
    while unsatisfied:
        wave_index += 1
        ready = sorted(
            task
            for task in unsatisfied
            if all(
                # In-phase prereqs must be done; cross-phase prereqs (e.g. 5.4)
                # are assumed satisfied because the phase is open.
                (prereq not in traces) or (prereq in done)
                for prereq in prereqs.get(task, [])
            )
        )
        if not ready:
            # No progress possible: a cycle or a missing dep. Emit and stop.
            waves.append(
                Wave(wave=wave_index, ready=[], reason="no ready tasks (cycle or unmet dep)")
            )
            break
        # Partition the ready set: tasks with disjoint file sets may run together.
        file_owners: dict[str, str] = {}
        parallel: list[str] = []
        serial: list[str] = []
        for task in ready:
            files = set(traces[task].deliverable_files)
            if files & set(file_owners):
                serial.append(task)
            else:
                parallel.append(task)
                for f in files:
                    file_owners[f] = task
        waves.append(
            Wave(
                wave=wave_index,
                ready=ready,
                parallel_safe=sorted(parallel),
                serialized=sorted(serial),
                reason="disjoint files" if not serial else "overlapping files",
            )
        )
        unsatisfied -= set(ready)
        done |= set(ready)
    return waves


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(prog="dispatch_plan")
    parser.add_argument("--phase", required=True)
    parser.add_argument("--done", default="", help="comma-separated completed task ids")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    done = {t.strip() for t in args.done.split(",") if t.strip()}
    waves = build_plan(args.phase, done)

    if args.json:
        print(json.dumps([asdict(w) for w in waves], indent=2))
        return 0
    for wave in waves:
        print(f"wave {wave.wave} ({wave.reason}):")
        if wave.parallel_safe:
            print(f"  parallel-safe: {', '.join(wave.parallel_safe)}")
        if wave.serialized:
            print(f"  serialize:     {', '.join(wave.serialized)}")
        if not wave.ready:
            print("  (none)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())