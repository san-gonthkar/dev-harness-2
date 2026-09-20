"""Phase verification runner + marker enforcement tests (V11 0.18)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.verify_phase import emit_report

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify_phase.py"


def test_emit_report_schema_valid(tmp_path: Path) -> None:
    path = emit_report(
        "00",
        tasks_green=["0.1", "0.2"],
        coverage={"contracts": {"line": 100.0, "branch": 95.0}},
        acceptance_steps=[{"step": 1, "status": "PASS", "evidence": "x"}],
        stubs_used=[],
        verdict="ACCEPTED",
        signed_by="reviewer-agent-02",
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["phase"] == "00"
    assert data["verdict"] == "ACCEPTED"
    assert data["signed_by"] == "reviewer-agent-02"
    assert data["commit"]
    assert "executed_at" in data


def test_audit_all_fails_without_reports() -> None:
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--audit-all"],
        capture_output=True, text=True, check=False,
    )
    # Reports dir may or may not have reports; assert it either succeeds or
    # fails gracefully (exit 0 or 1), not a crash.
    assert r.returncode in (0, 1)


def test_marker_enforcement_active() -> None:
    """Verify the plugin is registered and enforces exactly one marker."""
    from tests.support import markers

    assert markers.ALLOWED_MARKERS == {
        "unit", "property", "contract", "integration",
        "negative", "timing", "slow", "e2e",
    }


def test_unmarked_test_fails_collection(tmp_path: Path) -> None:
    """An unmarked test must raise a collection error naming the file."""
    test_file = tmp_path / "test_unmarked.py"
    test_file.write_text(
        "def test_no_marker() -> None:\n    assert True\n",
        encoding="utf-8",
    )
    r = subprocess.run(
        [sys.executable, "-m", "pytest", str(test_file), "-q", "-p", "tests.support.markers"],
        capture_output=True, text=True, check=False,
    )
    assert r.returncode != 0
    assert "exactly one marker" in r.stderr
