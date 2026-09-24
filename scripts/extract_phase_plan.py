#!/usr/bin/env python
"""Extract one phase's plan rows into JSON so briefs can paste them inline.

The V11 plan is 961 lines; a task brief needs ~5. Reading the whole plan (or
making an agent read it) is the most expensive avoidable cost in a dispatch.
This extracts just the active phase's ``X.A`` (deliverable/files/prereqs) and
``X.B`` (validation command/criteria/tier) rows into a small JSON file the
orchestrator can quote.

Usage:
    python scripts/extract_phase_plan.py --phase 7
    python scripts/extract_phase_plan.py --phase 7 --out reports/plan_07.json
    python scripts/extract_phase_plan.py --phase 7 --task 7.1

Exit 0 on success; exit 1 when the phase has no rows.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PLAN = REPO / "requirements" / "Dev_Harness_Implementation_Plan_V11_Final.md"

# | 7.1 | desc | `files` | prereqs | est |
_ROW_RE = re.compile(r"^\|\s*(\d+\.\d+[a-z]?)\s*\|(.*)\|\s*$")
_PATH_RE = re.compile(r"`([^`]+)`")
_PREREQ_RE = re.compile(r"^[\d.,\s]+$")


@dataclass
class TaskRow:
    """One task's contract, extracted from the plan."""

    task_id: str
    deliverable: str = ""
    files: list[str] = field(default_factory=list)
    prereqs: list[str] = field(default_factory=list)
    est_hours: str = ""
    validation_cmd: str = ""
    success_criteria: str = ""
    tier: str = ""


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def extract_phase(phase: str) -> dict[str, TaskRow]:
    """Parse the phase's X.A and X.B rows into TaskRow objects."""
    rows: dict[str, TaskRow] = {}
    for line in PLAN.read_text(encoding="utf-8").splitlines():
        match = _ROW_RE.match(line)
        if match is None:
            continue
        task_id = match.group(1)
        if not task_id.startswith(f"{phase}."):
            continue
        cells = _cells(line)
        row = rows.setdefault(task_id, TaskRow(task_id=task_id))
        if len(cells) >= 5 and _PREREQ_RE.match(cells[3] or ""):
            # X.A row: | id | deliverable | files | prereqs | est |
            row.deliverable = cells[1]
            row.files = [t for t in _PATH_RE.findall(cells[2]) if _is_path(t)]
            row.prereqs = [p.strip() for p in cells[3].split(",") if p.strip()]
            row.est_hours = cells[4]
        elif len(cells) >= 5 and cells[4] in {"PR", "NIGHTLY"}:
            # X.B row: | id | strategy | command | criteria | tier |
            row.validation_cmd = cells[2].strip("` ")
            row.success_criteria = cells[3]
            row.tier = cells[4]
    return rows


def _is_path(token: str) -> bool:
    """True when a backticked token is a file path, not a command."""
    if " " in token or token.startswith("-"):
        return False
    return "/" in token or token.endswith((".py", ".sh", ".ps1", ".tcss"))


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(prog="extract_phase_plan")
    parser.add_argument("--phase", required=True)
    parser.add_argument("--task", default=None, help="emit only this task")
    parser.add_argument("--out", default=None, help="write JSON to this path")
    args = parser.parse_args(argv)

    rows = extract_phase(args.phase)
    if not rows:
        print(f"no plan rows for phase {args.phase}", file=sys.stderr)
        return 1

    selected = {args.task: rows[args.task]} if args.task in rows else rows
    if args.task and args.task not in rows:
        print(f"task {args.task} not in phase {args.phase}", file=sys.stderr)
        return 1

    payload = {tid: asdict(row) for tid, row in selected.items()}
    text = json.dumps(payload, indent=2)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"wrote {out} ({len(selected)} tasks)")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())