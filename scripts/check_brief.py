#!/usr/bin/env python
"""Brief schema validator: fail a dispatch before it burns a budget.

The P6 failures were all *brief* failures: a brief with no inline API surface
sent the subagent on a 15-file read sweep, exhausting its context and
returning empty. A prose brief can omit the API surface and nothing catches
it. This makes the required fields machine-checked.

Usage:
    python scripts/check_brief.py --file brief.json
    python scripts/check_brief.py --stdin < brief.json

A brief is a JSON object with these REQUIRED fields:

    task_id          str   e.g. "7.12b"
    deliverable      str   one sentence: what artifact is produced
    files            list  targeted file paths (>= 1)
    api_signatures   list  exact signatures the task needs (>= 1)
    validation_cmd   str   the exact command that proves success
    budget           obj   {"tool_calls": int, "minutes": int}

Optional: ``pattern_file`` (one file to mirror), ``plan_row`` (pasted X.A/X.B/
X.C text), ``notes``.

Exit 0 when valid; exit 1 with the missing/invalid fields listed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REQUIRED: dict[str, type] = {
    "task_id": str,
    "deliverable": str,
    "files": list,
    "api_signatures": list,
    "validation_cmd": str,
    "budget": dict,
}


def validate(brief: dict[str, Any]) -> list[str]:
    """Return a list of problems; empty means the brief is dispatchable."""
    problems: list[str] = []
    for field, expected in REQUIRED.items():
        if field not in brief:
            problems.append(f"missing required field: {field}")
            continue
        value = brief[field]
        if not isinstance(value, expected):
            problems.append(
                f"{field}: expected {expected.__name__}, got {type(value).__name__}"
            )
            continue
        if expected is str and not value.strip():
            problems.append(f"{field}: must not be empty")
        if expected is list and not value:
            problems.append(f"{field}: must have at least one entry")
    budget = brief.get("budget")
    if isinstance(budget, dict):
        for key in ("tool_calls", "minutes"):
            if key not in budget:
                problems.append(f"budget: missing {key}")
            elif not isinstance(budget[key], int) or budget[key] <= 0:
                problems.append(f"budget.{key}: must be a positive int")
    return problems


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(prog="check_brief")
    parser.add_argument("--file", default=None, help="path to brief.json")
    parser.add_argument("--stdin", action="store_true", help="read brief from stdin")
    args = parser.parse_args(argv)

    if args.stdin:
        raw = sys.stdin.read()
    elif args.file:
        raw = Path(args.file).read_text(encoding="utf-8")
    else:
        print("usage: check_brief.py --file brief.json | --stdin", file=sys.stderr)
        return 2

    try:
        brief = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"brief is not valid JSON: {exc}", file=sys.stderr)
        return 1
    if not isinstance(brief, dict):
        print("brief must be a JSON object", file=sys.stderr)
        return 1

    problems = validate(brief)
    if problems:
        print("brief REJECTED:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(f"brief OK: {brief['task_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())