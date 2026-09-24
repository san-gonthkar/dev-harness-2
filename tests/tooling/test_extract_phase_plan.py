"""Tests for the phase plan extractor (process fix 8)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import extract_phase_plan

pytestmark = pytest.mark.unit


def test_extract_phase_7_has_all_tasks() -> None:
    rows = extract_phase_plan.extract_phase("7")
    assert len(rows) == 13
    assert "7.1" in rows
    assert "7.13" in rows


def test_extract_combines_xa_and_xb_rows() -> None:
    row = extract_phase_plan.extract_phase("7")["7.1"]
    assert row.deliverable.startswith("`HermesApp`")
    assert row.files == ["tui/app.py", "tui/app.tcss"]
    assert row.prereqs == ["5.4"]
    assert row.validation_cmd == "pytest tests/tui/test_layout.py -q"
    assert row.tier == "PR"


def test_extract_files_excludes_commands() -> None:
    row = extract_phase_plan.extract_phase("7")["7.11"]
    # The X.B row's command is not treated as a deliverable file.
    assert all(" " not in f for f in row.files)


def test_is_path_rejects_commands() -> None:
    assert extract_phase_plan._is_path("pytest tests/tui/test_layout.py") is False
    assert extract_phase_plan._is_path("--flag") is False
    assert extract_phase_plan._is_path("tui/app.py") is True


def test_main_single_task_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert extract_phase_plan.main(["--phase", "7", "--task", "7.7"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert list(payload) == ["7.7"]
    assert payload["7.7"]["validation_cmd"] == "pytest tests/tui/test_bridge.py -q"


def test_main_writes_file(tmp_path: Path) -> None:
    out = tmp_path / "plan_07.json"
    assert extract_phase_plan.main(["--phase", "7", "--out", str(out)]) == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert len(payload) == 13


def test_main_unknown_phase_exits_1() -> None:
    assert extract_phase_plan.main(["--phase", "99"]) == 1


def test_main_unknown_task_exits_1() -> None:
    assert extract_phase_plan.main(["--phase", "7", "--task", "7.99"]) == 1


def test_build_brief_has_required_fields() -> None:
    from scripts.check_brief import validate

    brief = extract_phase_plan.build_brief("7.12", phase="7")
    assert validate(brief) == []
    assert brief["task_id"] == "7.12"
    assert brief["files"] == ["scripts/verify_phase_07.sh"]
    assert brief["budget"] == {"tool_calls": 25, "minutes": 25}


def test_build_brief_marks_api_signatures_for_replacement() -> None:
    brief = extract_phase_plan.build_brief("7.12", phase="7")
    signatures = brief["api_signatures"]
    assert isinstance(signatures, list)
    assert "REPLACE" in signatures[0]


def test_build_brief_unknown_task_raises() -> None:
    with pytest.raises(KeyError):
        extract_phase_plan.build_brief("7.99", phase="7")


def test_main_brief_requires_task() -> None:
    assert extract_phase_plan.main(["--phase", "7", "--brief"]) == 1


def test_main_brief_writes_skeleton(tmp_path: Path) -> None:
    from scripts.check_brief import validate

    out = tmp_path / "7.12.json"
    assert (
        extract_phase_plan.main(
            ["--phase", "7", "--task", "7.12", "--brief", "--out", str(out)]
        )
        == 0
    )
    brief = json.loads(out.read_text(encoding="utf-8"))
    assert validate(brief) == []
