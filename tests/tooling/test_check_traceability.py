"""Tests for the traceability check (process fix 3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import check_traceability

pytestmark = pytest.mark.unit


def test_parse_phase_7_finds_all_tasks() -> None:
    traces = check_traceability.parse_phase("7")
    ids = {t.task_id for t in traces}
    assert "7.1" in ids
    assert "7.13" in ids
    assert len(traces) == 13


def test_parse_phase_7_extracts_deliverable_files() -> None:
    traces = {t.task_id: t for t in check_traceability.parse_phase("7")}
    assert "tui/app.py" in traces["7.1"].deliverable_files
    assert "tui/bridge.py" in traces["7.7"].deliverable_files


def test_parse_phase_7_extracts_test_files() -> None:
    traces = {t.task_id: t for t in check_traceability.parse_phase("7")}
    assert "tests/tui/test_layout.py" in traces["7.1"].test_files


def test_completed_task_has_no_missing_files() -> None:
    traces = {t.task_id: t for t in check_traceability.parse_phase("7")}
    assert traces["7.1"].ok
    assert traces["7.7"].ok


def test_unbuilt_task_reports_gap() -> None:
    # 8.19 is the next unbuilt verify script (7.12 was built in P7).
    traces = {t.task_id: t for t in check_traceability.parse_phase("8")}
    assert not traces["8.19"].ok
    assert "scripts/verify_phase_08.sh" in traces["8.19"].missing


def test_cli_invocation_is_not_treated_as_a_path() -> None:
    assert not check_traceability._looks_like_path("dev-harness --workspace ./tmp/ws")
    assert not check_traceability._looks_like_path("pytest tests/tui/test_layout.py -q")


def test_looks_like_path_accepts_real_paths() -> None:
    assert check_traceability._looks_like_path("tui/app.py")
    assert check_traceability._looks_like_path("scripts/verify_phase_07.sh")


def test_main_json_emits_valid_payload(capsys: pytest.CaptureFixture[str]) -> None:
    code = check_traceability.main(["--phase", "7", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload, list)
    assert any(row["task_id"] == "7.1" for row in payload)
    assert code in (0, 1)


def test_main_unknown_phase_exits_1() -> None:
    assert check_traceability.main(["--phase", "99"]) == 1


def test_path_bases_cover_src_and_repo() -> None:
    bases = check_traceability.PATH_BASES
    assert any(str(b).endswith("dev_harness") for b in bases)
    assert any(Path(b) == check_traceability.REPO for b in bases)
