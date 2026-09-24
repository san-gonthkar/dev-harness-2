#!/usr/bin/env python
"""State-commit guard: reject commits whose only content is bookkeeping.

66 of 180 commits (37%) touched only `memory.md`/`progress.md`/`docs/`. The
rule "batch state-file commits once per phase" existed in the skill but was
violated every task because nothing enforced it.

Usage:
    python scripts/check_state_commit.py --rev HEAD
    python scripts/check_state_commit.py --range origin/main..HEAD
    python scripts/check_state_commit.py --range HEAD~10..HEAD --warn-only

Exit 1 when a commit's ONLY changed files are state files (a churn commit).
Exit 0 otherwise. With ``--warn-only`` it prints offenders but exits 0.
"""

from __future__ import annotations

import argparse
import subprocess
import sys

# Files whose sole change constitutes bookkeeping churn. Docs are deliberately
# NOT included: a plan/spec doc commit is legitimate work. Only the two shared
# state files are churn when they are a commit's entire content.
STATE_FILES = frozenset({"memory.md", "progress.md"})


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _changed_files(rev: str) -> list[str]:
    out = _git("diff-tree", "--no-commit-id", "--name-only", "-r", rev)
    return [line.strip() for line in out.splitlines() if line.strip()]


def is_churn(files: list[str]) -> bool:
    """True when every changed file is a shared state file."""
    if not files:
        return False
    return all(f in STATE_FILES for f in files)


def check_range(rev_range: str) -> list[tuple[str, str]]:
    """Return ``(sha, subject)`` for churn commits in ``rev_range``."""
    out = _git("log", "--pretty=format:%H%x09%s", rev_range)
    offenders: list[tuple[str, str]] = []
    for line in out.splitlines():
        if "\t" not in line:
            continue
        sha, subject = line.split("\t", 1)
        if is_churn(_changed_files(sha)):
            offenders.append((sha[:8], subject))
    return offenders


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(prog="check_state_commit")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--rev", default=None, help="a single commit")
    group.add_argument("--range", dest="rev_range", default=None, help="a commit range")
    parser.add_argument("--warn-only", action="store_true")
    args = parser.parse_args(argv)

    if args.rev:
        offenders = (
            [(args.rev[:8], _git("log", "-1", "--pretty=%s", args.rev).strip())]
            if is_churn(_changed_files(args.rev))
            else []
        )
    else:
        offenders = check_range(args.rev_range)

    if offenders:
        for sha, subject in offenders:
            print(f"churn commit {sha}: {subject}", file=sys.stderr)
        if not args.warn_only:
            print(
                "state-file churn: batch memory.md/progress.md into the phase-close commit",
                file=sys.stderr,
            )
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())