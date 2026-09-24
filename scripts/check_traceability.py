#!/usr/bin/env python
"""Traceability check: task -> deliverable file -> test file -> evidence.

`check_task_trailer.py` validates the *format* of a ``Task-Id`` trailer; it does
not check that the deliverable exists. With 294 plan rows and 107 test files
there was no enforced mapping, so "done" meant "an agent said so".

This parses the V11 plan's ``X.A`` (deliverable files) and ``X.B`` (test
command) rows and asserts, for a given phase, that every task's targeted files
and named test files exist on disk.

Usage:
    python scripts/check_traceability.py --phase 7
    python scripts/check_traceability.py --phase 7 --json

Exit 0 when every task in the phase is traceable; exit 1 listing the gaps.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PLAN = REPO / "requirements" / "Dev_Harness_Implementation_Plan_V11_Final.md"

# Plan paths are written relative to the package root (e.g. `tui/app.py`), but
# some rows use repo-relative paths (e.g. `scripts/verify_phase_07.sh`). Try
# each base in order.
PATH_BASES = (REPO / "src" / "dev_harness", REPO)

# A plan table row: | 7.1 | description | `file`, `file` | prereq | est |
_ROW_RE = re.compile(r"^\|\s*(\d+\.\d+[a-z]?)\s*\|(.*)\|\s*$")
# Backticked paths inside a cell.
_PATH_RE = re.compile(r"`([^`]+)`")
# A pytest target inside a validation command.
_PYTEST_RE = re.compile(r"pytest\s+([^\s`]+)")


@dataclass
class TaskTrace:
    """One task's traceability facts."""

    task_id: str
    deliverable_files: list[str] = field(default_factory=list)
    test_files: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when every referenced file exists."""
        return not self.missing


def _cells(line: str) -> list[str]:
    """Split a markdown table row into its cells."""
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _looks_like_path(token: str) -> bool:
    """True when a backticked token is a repo-relative file path."""
    if token.startswith(("pytest ", "python ", "bash ", "make ")):
        return False
    # A CLI invocation (spaces or flags) is a command, not a file.
    if " " in token or token.startswith("-"):
        return False
    return "/" in token or token.endswith((".py", ".sh", ".ps1", ".tcss", ".md", ".json"))


def parse_phase(phase: str) -> list[TaskTrace]:
    """Parse the plan's X.A and X.B rows for ``phase`` into traces."""
    traces: dict[str, TaskTrace] = {}
    for line in PLAN.read_text(encoding="utf-8").splitlines():
        match = _ROW_RE.match(line)
        if match is None:
            continue
        task_id, _rest = match.group(1), match.group(2)
        if not task_id.startswith(f"{phase}."):
            continue
        cells = _cells(line)
        trace = traces.setdefault(task_id, TaskTrace(task_id=task_id))
        # X.A rows: cells[2] holds the targeted files.
        # X.B rows: cells[2] holds the test command.
        if len(cells) >= 3:
            for token in _PATH_RE.findall(cells[2]):
                if token.startswith("pytest "):
                    for target in _PYTEST_RE.findall(token):
                        trace.test_files.append(target)
                elif _looks_like_path(token):
                    trace.deliverable_files.append(token)
    for trace in traces.values():
        for rel in [*trace.deliverable_files, *trace.test_files]:
            if not any((base / rel).exists() for base in PATH_BASES):
                trace.missing.append(rel)
    return sorted(traces.values(), key=lambda t: t.task_id)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(prog="check_traceability")
    parser.add_argument("--phase", required=True, help="phase number, e.g. 7")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)

    traces = parse_phase(args.phase)
    if not traces:
        print(f"no plan rows found for phase {args.phase}", file=sys.stderr)
        return 1

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "task_id": t.task_id,
                        "deliverable_files": t.deliverable_files,
                        "test_files": t.test_files,
                        "missing": t.missing,
                        "ok": t.ok,
                    }
                    for t in traces
                ],
                indent=2,
            )
        )
        return 0 if all(t.ok for t in traces) else 1

    gaps = [t for t in traces if not t.ok]
    for trace in traces:
        status = "OK " if trace.ok else "GAP"
        print(f"[{status}] {trace.task_id}")
        for rel in trace.missing:
            print(f"        missing: {rel}")
    if gaps:
        print(f"\ntraceability FAIL: {len(gaps)}/{len(traces)} tasks have gaps", file=sys.stderr)
        return 1
    print(f"\ntraceability OK: {len(traces)} tasks, all files present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())