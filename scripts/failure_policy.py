#!/usr/bin/env python
"""Typed failure recovery: the failure class determines the response.

The retry -> re-dispatch -> escalate ladder was applied by human judgment, so
a hang was retried (wasted) and an empty return was retried (wasted) instead
of being split. The failure classes are known and distinct; this encodes the
mapping so the response is mechanical.

Usage:
    python scripts/failure_policy.py --class empty_return
    python scripts/failure_policy.py --list

Exit 0 always (this is a lookup, not a gate).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class Policy:
    """The prescribed response for one failure class."""

    failure_class: str
    response: str
    retry: bool
    action: str
    rationale: str


POLICIES: dict[str, Policy] = {
    "empty_return": Policy(
        failure_class="empty_return",
        response="split",
        retry=False,
        action="Split the task into smaller single-task dispatches; re-dispatch the first piece.",
        rationale="Context exhaustion: the brief was too large. Retrying the same brief fails again.",
    ),
    "hang": Policy(
        failure_class="hang",
        response="fix_loop",
        retry=False,
        action="Find the unbounded wait in the code, bound it, then re-run once.",
        rationale="A hang is a real defect (unbounded loop). Retrying reproduces it.",
    ),
    "test_failure": Policy(
        failure_class="test_failure",
        response="redispatch_with_output",
        retry=True,
        action="Re-dispatch with the exact failure output and the command that produced it.",
        rationale="The agent needs the failure text; a bare retry repeats the mistake.",
    ),
    "lane_breach": Policy(
        failure_class="lane_breach",
        response="log_and_discard",
        retry=False,
        action="Log the breach; do NOT count that run as evidence. Re-run the smoke lane only.",
        rationale="An unauthorized full-suite run is not valid evidence.",
    ),
    "budget_exceeded": Policy(
        failure_class="budget_exceeded",
        response="checkpoint_and_split",
        retry=False,
        action="Commit what exists, record the checkpoint, split the remainder.",
        rationale="The task was too large for one budget; the remainder needs its own dispatch.",
    ),
    "aborted": Policy(
        failure_class="aborted",
        response="escalate",
        retry=False,
        action="Record the trigger and last 5 actions; ask the user.",
        rationale="An abort is a guardrail firing; a human decides the next step.",
    ),
    "success": Policy(
        failure_class="success",
        response="continue",
        retry=False,
        action="Verify the commit, then dispatch the next task.",
        rationale="Normal path.",
    ),
}


def policy_for(failure_class: str) -> Policy:
    """Return the policy for ``failure_class``; raise KeyError if unknown."""
    return POLICIES[failure_class]


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(prog="failure_policy")
    parser.add_argument("--class", dest="failure_class", default=None)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.list:
        for name, policy in POLICIES.items():
            print(f"{name:20} -> {policy.response:24} retry={policy.retry}")
        return 0
    if args.failure_class is None:
        print("usage: failure_policy.py --class <name> | --list", file=sys.stderr)
        return 2
    try:
        policy = policy_for(args.failure_class)
    except KeyError:
        print(f"unknown failure class {args.failure_class!r}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(asdict(policy), indent=2))
    else:
        print(f"class:     {policy.failure_class}")
        print(f"response:  {policy.response}")
        print(f"retry:     {policy.retry}")
        print(f"action:    {policy.action}")
        print(f"rationale: {policy.rationale}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())