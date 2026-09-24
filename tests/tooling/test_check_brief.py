"""Tests for the brief schema validator (process fix 2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import check_brief

pytestmark = pytest.mark.unit


def _valid_brief() -> dict[str, object]:
    return {
        "task_id": "7.12b",
        "deliverable": "verify_phase_07.sh driver + steps 1-5",
        "files": ["scripts/verify_phase_07.sh"],
        "api_signatures": ["Bridge(app, *, source=None, queue=None)"],
        "validation_cmd": "bash -n scripts/verify_phase_07.sh",
        "budget": {"tool_calls": 25, "minutes": 25},
    }


def test_valid_brief_has_no_problems() -> None:
    assert check_brief.validate(_valid_brief()) == []


def test_missing_field_is_reported() -> None:
    brief = _valid_brief()
    del brief["api_signatures"]
    problems = check_brief.validate(brief)
    assert any("api_signatures" in p for p in problems)


def test_empty_list_is_rejected() -> None:
    brief = _valid_brief()
    brief["files"] = []
    assert any("files" in p for p in check_brief.validate(brief))


def test_empty_string_is_rejected() -> None:
    brief = _valid_brief()
    brief["validation_cmd"] = "   "
    assert any("validation_cmd" in p for p in check_brief.validate(brief))


def test_wrong_type_is_rejected() -> None:
    brief = _valid_brief()
    brief["files"] = "scripts/verify_phase_07.sh"
    assert any("expected list" in p for p in check_brief.validate(brief))


def test_budget_missing_key_is_rejected() -> None:
    brief = _valid_brief()
    brief["budget"] = {"tool_calls": 25}
    assert any("budget: missing minutes" in p for p in check_brief.validate(brief))


def test_budget_non_positive_is_rejected() -> None:
    brief = _valid_brief()
    brief["budget"] = {"tool_calls": 0, "minutes": 25}
    assert any("budget.tool_calls" in p for p in check_brief.validate(brief))


def test_main_accepts_valid_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "brief.json"
    path.write_text(json.dumps(_valid_brief()), encoding="utf-8")
    assert check_brief.main(["--file", str(path)]) == 0
    assert "brief OK: 7.12b" in capsys.readouterr().out


def test_main_rejects_invalid_file(tmp_path: Path) -> None:
    path = tmp_path / "brief.json"
    path.write_text(json.dumps({"task_id": "7.12b"}), encoding="utf-8")
    assert check_brief.main(["--file", str(path)]) == 1


def test_main_rejects_malformed_json(tmp_path: Path) -> None:
    path = tmp_path / "brief.json"
    path.write_text("{not json", encoding="utf-8")
    assert check_brief.main(["--file", str(path)]) == 1


def test_main_requires_input() -> None:
    assert check_brief.main([]) == 2