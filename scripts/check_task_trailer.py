#!/usr/bin/env python3
"""Handoff enforcement: verify a commit/range carries Task-Id trailers (V11 0.11).

Usage:
    python scripts/check_task_trailer.py --rev HEAD        # check the last commit
    python scripts/check_task_trailer.py --rev main..HEAD  # check a range

Valid Task-Ids match this plan's <phase>.<task> numbering (e.g. 0.1, 8.21a).
Missing or malformed trailers exit 1.
"""

from __future__ import annotations

import re
import subprocess
import sys

TASK_ID_RE = re.compile(r"^(?:[0-9]|10)\.(?:[1-9]|[12][0-9]|3[0-9])[a-z]?$")
TRAILER_PREFIX = "Task-Id: "


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _rev_range(rev: str) -> list[str]:
    """Expand a rev spec into individual commit hashes."""
    if ".." in rev:
        out = _git("rev-list", rev)
    else:
        out = _git("rev-parse", rev)
    return [line for line in out.splitlines() if line]


def check_commits(commits: list[str]) -> tuple[list[str], list[str]]:
    """Return (bad_commits, reasons) for commits missing/invalid trailers."""
    bad: list[str] = []
    reasons: list[str] = []
    for commit in commits:
        body = _git("log", "-1", "--format=%B", commit)
        trailer_found = False
        for line in body.splitlines():
            if line.startswith(TRAILER_PREFIX):
                trailer_found = True
                task_id = line[len(TRAILER_PREFIX) :].strip()
                if not TASK_ID_RE.match(task_id):
                    bad.append(commit)
                    reasons.append(f"{commit}: invalid Task-Id {task_id!r}")
        if not trailer_found:
            bad.append(commit)
            reasons.append(f"{commit}: missing Task-Id trailer")
    return bad, reasons


def main() -> int:
    args = sys.argv[1:]
    rev = "HEAD"
    if "--rev" in args:
        idx = args.index("--rev")
        rev = args[idx + 1]
    try:
        commits = _rev_range(rev)
    except subprocess.CalledProcessError as exc:
        print(f"error resolving rev {rev}: {exc}", file=sys.stderr)
        return 1
    if not commits:
        print("no commits to check")
        return 0
    bad, reasons = check_commits(commits)
    if bad:
        print("Task-Id trailer check FAILED:", file=sys.stderr)
        for r in reasons:
            print(f"  - {r}", file=sys.stderr)
        return 1
    print(f"Task-Id trailers OK ({len(commits)} commit(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
