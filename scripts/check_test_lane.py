#!/usr/bin/env python
"""PreToolUse hook: gate full-suite test runs behind user approval.

Policy (see .github/copilot-instructions.md "Test Lane Policy"):
  - The smoke lane is always allowed.
  - The full suite / coverage / nightly / mutation runs require explicit user
    approval. This hook returns permissionDecision="ask" so the user is prompted
    instead of the run starting silently.

Reads the hook JSON on stdin, writes the decision JSON on stdout. Any internal
error fails OPEN (allow) so a hook bug can never deadlock a session.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any

# Commands the user must approve explicitly.
BLOCKED_PATTERNS: tuple[str, ...] = (
    r"\bmake\s+test-full\b",
    r"\bmake\s+coverage\b",
    r"\bmake\s+ci\b",
    r"\bmake\s+test-nightly\b",
    r"test_lane\.(?:ps1|sh)\s+(?:full|coverage|nightly)\b",
    r"\bmutation_gate\.py\b",
    r"\bchaos_drill\.py\b",
)

# The smoke lane: always allowed, even though it mentions `pytest tests`.
ALLOWED_MARKERS: tuple[str, ...] = (
    "not timing and not slow and not e2e",
    "--ignore-glob=",
    "test_lane.ps1 smoke",
    "test_lane.sh smoke",
    "make test-smoke",
)

# A bare `pytest tests` (whole suite) is a full-suite run; a targeted path is not.
FULL_PYTEST = re.compile(r"\bpytest\b[^\n|;&]*\btests\b(?![/\\])")
TARGETED_PYTEST = re.compile(r"\bpytest\b[^\n|;&]*\btests[/\\][\w./\\-]*")

MAX_COMMAND_LEN = 4000


def extract_command(payload: dict[str, Any]) -> str:
    """Pull the shell command out of the hook payload, if present."""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return ""
    for key in ("command", "commandLine"):
        value = tool_input.get(key)
        if isinstance(value, str):
            return value
    # create_and_run_task passes the command nested under "task".
    task = tool_input.get("task")
    if isinstance(task, dict):
        value = task.get("command")
        if isinstance(value, str):
            return value
    return ""


def is_full_suite(command: str) -> str | None:
    """Return the matched reason if the command needs user approval, else None."""
    for marker in ALLOWED_MARKERS:
        if marker in command:
            return None
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, command):
            return f"matches full-suite pattern {pattern!r}"
    if FULL_PYTEST.search(command) and not TARGETED_PYTEST.search(command):
        return "runs the whole pytest suite without smoke-lane exclusions"
    return None


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            return 0
        command = extract_command(payload)[:MAX_COMMAND_LEN]
        if not command:
            return 0
        reason = is_full_suite(command)
        if reason is None:
            return 0
        decision = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": (
                    "Full-suite run detected: "
                    f"{reason}. Policy: use the smoke lane "
                    "(`make test` / `scripts/test_lane.ps1 smoke`). Approve only "
                    "if you explicitly asked for the full suite."
                ),
            }
        }
        json.dump(decision, sys.stdout)
        return 0
    except Exception:  # noqa: BLE001 - fail open, never block on a hook bug
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
