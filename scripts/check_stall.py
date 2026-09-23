#!/usr/bin/env python
"""PreToolUse hook: kill identical-repeat agent loops deterministically.

The documented self-loop signature (see `.github/skills/phase-orchestrator/
SKILL.md`, "Loop Guardrail") is *the same tool with the same input, repeated*.
A hook cannot see tool output, so we track the strongest observable signal:
consecutive PreToolUse calls with an identical ``(tool_name, tool_input)``
signature.

Escalation:
  - 3rd identical consecutive call  -> ``ask``  (warn; the user is prompted)
  - 5th identical consecutive call  -> ``deny`` (hard stop; "a third repeat is
    forbidden" already applies at 3, so 5 means the soft gate was ignored)

Any *different* call resets the counter, so normal iteration (edit -> test ->
edit) never trips the guard. Fails OPEN on any error so a hook bug can never
deadlock a session.

State lives in the system temp dir keyed by workspace path, so it never
pollutes the repo and separate checkouts do not collide.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

ASK_AT = 3
DENY_AT = 5
MAX_INPUT_LEN = 8000


def _state_path() -> Path:
    """A per-workspace state file under the system temp dir."""
    try:
        key = os.getcwd()
    except OSError:  # pragma: no cover - cwd always resolvable in practice
        key = "unknown"
    digest = hashlib.sha1(key.encode("utf-8", "replace")).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / f"dev-harness-stall-{digest}.json"


def _signature(payload: dict[str, Any]) -> str:
    """Stable hash of (tool_name, tool_input)."""
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    try:
        blob = json.dumps([tool, tool_input], sort_keys=True, default=str)[
            :MAX_INPUT_LEN
        ]
    except (TypeError, ValueError):  # pragma: no cover - defensive
        blob = str(tool)
    return hashlib.sha1(blob.encode("utf-8", "replace")).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _store(path: Path, sig: str, count: int) -> None:
    try:
        path.write_text(json.dumps({"sig": sig, "count": count}), encoding="utf-8")
    except OSError:  # pragma: no cover - temp dir write failure is not fatal
        pass


def decide(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Return a decision dict when the guard fires, else None."""
    if not payload.get("tool_name"):
        return None
    sig = _signature(payload)
    path = _state_path()
    previous = _load(path)
    if previous.get("sig") == sig:
        count = int(previous.get("count", 1)) + 1
    else:
        count = 1
    _store(path, sig, count)

    if count >= DENY_AT:
        decision = "deny"
        detail = f"the same tool call with identical input has repeated {count} times"
    elif count >= ASK_AT:
        decision = "ask"
        detail = f"the same tool call with identical input has repeated {count} times"
    else:
        return None

    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": (
                f"Loop guardrail: {detail}. This is the documented self-loop "
                "signature (polling `git log`/`git status` or re-running the "
                "same probe to wait). Stop and produce output, or change the "
                "call. Record the trigger and the exact next safe step."
            ),
        }
    }


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            return 0
        decision = decide(payload)
        if decision is None:
            return 0
        json.dump(decision, sys.stdout)
        return 0
    except Exception:  # noqa: BLE001 - fail open, never block on a hook bug
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
