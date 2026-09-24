#!/usr/bin/env python
"""PreToolUse hook: require a validated brief before a subagent dispatch.

Policy (see `.github/skills/phase-orchestrator/SKILL.md`, "Context Economy"):

  - Every `runSubagent` dispatch of an implementing agent must carry a brief
    that passes `scripts/check_brief.py` (task_id, deliverable, files,
    api_signatures, validation_cmd, budget).
  - The brief lives at `briefs/<task_id>.json` (transient; gitignored).

Enforcement is graduated:

  - no brief for the task          -> ``ask``   (soft: the user is prompted;
    a reviewer/one-off dispatch can proceed)
  - brief exists but is invalid    -> ``deny``  (hard: a malformed brief is a
    mistake that would burn a subagent budget)

Reads the hook JSON on stdin, writes the decision JSON on stdout. Any internal
error fails OPEN (allow) so a hook bug can never deadlock a session.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
BRIEFS = REPO / "briefs"

# Dispatch tools this guard applies to.
DISPATCH_TOOLS = frozenset({"runSubagent", "agent"})

# Agents whose dispatches are not V11 tasks and need no task brief.
EXEMPT_AGENTS = frozenset({"reviewer-agent", "release-agent", "Explore"})

# A V11 task id, e.g. 7.12b / 6.9 / 10.5.
TASK_ID_RE = re.compile(r"\b((?:10|[0-9])\.[0-9]+[a-z]?)\b")

MAX_PROMPT_LEN = 200_000


def _payload_prompt(payload: dict[str, Any]) -> tuple[str, str]:
    """Return ``(agent_name, prompt_text)`` from the hook payload."""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return "", ""
    agent = tool_input.get("agentName") or ""
    prompt = tool_input.get("prompt") or tool_input.get("description") or ""
    if not isinstance(agent, str):
        agent = ""
    if not isinstance(prompt, str):
        prompt = ""
    return agent, prompt[:MAX_PROMPT_LEN]


def _load_brief(path: Path) -> dict[str, Any] | None:
    """Load a brief; None when it is not a JSON object."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _validate(brief: dict[str, Any]) -> list[str]:
    """Validate a brief using the shared checker."""
    sys.path.insert(0, str(REPO))
    try:
        from scripts.check_brief import validate
    except ImportError:  # pragma: no cover - import path differs outside pytest
        return []
    return validate(brief)


def _target_task(prompt: str) -> str | None:
    """The V11 task id referenced by the prompt, if any.

    Prefers an explicit ``TASK:`` line (the brief template), else the first
    task-id-shaped token.
    """
    for line in prompt.splitlines():
        if line.strip().upper().startswith("TASK:"):
            match = TASK_ID_RE.search(line)
            if match:
                return match.group(1)
    match = TASK_ID_RE.search(prompt)
    return match.group(1) if match else None


def decide(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Return a decision dict when the guard fires, else None."""
    if payload.get("tool_name") not in DISPATCH_TOOLS:
        return None
    agent, prompt = _payload_prompt(payload)
    if agent in EXEMPT_AGENTS:
        return None
    task_id = _target_task(prompt)
    if task_id is None:
        # No task id in the prompt: cannot attribute a brief. Soft-ask.
        return _decision(
            "ask",
            "Dispatch has no V11 task id (expected a `TASK: <id>` line), so no "
            "brief could be matched. Write the brief, or proceed knowingly.",
        )

    brief_path = BRIEFS / f"{task_id}.json"
    if not brief_path.exists():
        return _decision(
            "ask",
            f"No validated brief at briefs/{task_id}.json for task {task_id}. "
            "Generate it with `python scripts/extract_phase_plan.py --phase N "
            f"--task {task_id}` then add api_signatures/validation_cmd/budget, "
            "and validate with `python scripts/check_brief.py --file ...`.",
        )

    brief = _load_brief(brief_path)
    if brief is None:
        return _decision(
            "deny",
            f"briefs/{task_id}.json is not a valid JSON object.",
        )
    problems = _validate(brief)
    if problems:
        return _decision(
            "deny",
            f"briefs/{task_id}.json is invalid: {'; '.join(problems)}. "
            "A malformed brief burns a subagent budget — fix it before dispatch.",
        )
    return None


def _decision(decision: str, reason: str) -> dict[str, Any]:
    """Build the hook decision payload."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": f"Brief guard: {reason}",
        }
    }


def main() -> int:
    """Hook entry point: fail OPEN on any error."""
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            return 0
        decision = decide(payload)
        if decision is None:
            return 0
        print(json.dumps(decision))
        return 0
    except Exception:  # noqa: BLE001 - a hook must never deadlock a session
        return 0


if __name__ == "__main__":
    raise SystemExit(main())