"""Tests for the brief-guard PreToolUse hook (process fix: enforcement)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import check_dispatch_brief

pytestmark = pytest.mark.unit


@pytest.fixture
def briefs_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the briefs directory to a tmp path."""
    target = tmp_path / "briefs"
    target.mkdir()
    monkeypatch.setattr(check_dispatch_brief, "BRIEFS", target)
    return target


def _valid_brief() -> dict[str, object]:
    return {
        "task_id": "7.12b",
        "deliverable": "verify_phase_07.sh driver + steps 1-5",
        "files": ["scripts/verify_phase_07.sh"],
        "api_signatures": ["main(argv) -> int"],
        "validation_cmd": "bash -n scripts/verify_phase_07.sh",
        "budget": {"tool_calls": 25, "minutes": 25},
    }


def _dispatch(tool_input: dict[str, object], tool: str = "runSubagent") -> dict[str, object]:
    return {"tool_name": tool, "tool_input": tool_input}


def test_non_dispatch_tool_is_ignored() -> None:
    assert check_dispatch_brief.decide(_dispatch({}, tool="run_in_terminal")) is None


def test_exempt_agent_is_ignored() -> None:
    payload = _dispatch({"agentName": "reviewer-agent", "prompt": "review P6"})
    assert check_dispatch_brief.decide(payload) is None


def test_missing_task_id_asks() -> None:
    payload = _dispatch({"agentName": "python-developer", "prompt": "do some work"})
    decision = check_dispatch_brief.decide(payload)
    assert decision is not None
    assert decision["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_missing_brief_asks(briefs_dir: Path) -> None:
    payload = _dispatch({"agentName": "python-developer", "prompt": "TASK: 7.12b\nstuff"})
    decision = check_dispatch_brief.decide(payload)
    assert decision is not None
    assert decision["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_invalid_brief_denies(briefs_dir: Path) -> None:
    (briefs_dir / "7.12b.json").write_text(json.dumps({"task_id": "7.12b"}), encoding="utf-8")
    payload = _dispatch({"agentName": "python-developer", "prompt": "TASK: 7.12b\nstuff"})
    decision = check_dispatch_brief.decide(payload)
    assert decision is not None
    assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_malformed_json_denies(briefs_dir: Path) -> None:
    (briefs_dir / "7.12b.json").write_text("{not json", encoding="utf-8")
    payload = _dispatch({"agentName": "python-developer", "prompt": "TASK: 7.12b"})
    decision = check_dispatch_brief.decide(payload)
    assert decision is not None
    assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_valid_brief_allows(briefs_dir: Path) -> None:
    (briefs_dir / "7.12b.json").write_text(json.dumps(_valid_brief()), encoding="utf-8")
    payload = _dispatch({"agentName": "python-developer", "prompt": "TASK: 7.12b\nstuff"})
    assert check_dispatch_brief.decide(payload) is None


def test_task_id_prefers_task_line() -> None:
    prompt = "Read 6.9 for context.\nTASK: 7.12b — the driver\n"
    assert check_dispatch_brief._target_task(prompt) == "7.12b"


def test_target_task_falls_back_to_first_id() -> None:
    assert check_dispatch_brief._target_task("implement 8.21a please") == "8.21a"


def test_target_task_handles_phase_10() -> None:
    assert check_dispatch_brief._target_task("TASK: 10.5 release") == "10.5"


def test_target_task_none_when_absent() -> None:
    assert check_dispatch_brief._target_task("no ids here") is None


def test_explore_agent_is_exempt() -> None:
    payload = _dispatch({"agentName": "Explore", "prompt": "find 7.1"})
    assert check_dispatch_brief.decide(payload) is None


def test_main_fails_open_on_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert check_dispatch_brief.main() == 0