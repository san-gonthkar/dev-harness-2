"""Tests for the state-commit churn guard (process fix 6)."""

from __future__ import annotations

import pytest

from scripts import check_state_commit

pytestmark = pytest.mark.unit


def test_memory_only_is_churn() -> None:
    assert check_state_commit.is_churn(["memory.md"]) is True


def test_progress_only_is_churn() -> None:
    assert check_state_commit.is_churn(["progress.md"]) is True


def test_both_state_files_is_churn() -> None:
    assert check_state_commit.is_churn(["memory.md", "progress.md"]) is True


def test_code_plus_state_is_not_churn() -> None:
    assert check_state_commit.is_churn(["memory.md", "src/dev_harness/tui/app.py"]) is False


def test_docs_only_is_not_churn() -> None:
    assert check_state_commit.is_churn(["docs/phase_07_implementation_plan.md"]) is False


def test_empty_file_list_is_not_churn() -> None:
    assert check_state_commit.is_churn([]) is False


def test_main_warn_only_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    # HEAD is a real commit; warn-only must never fail the build.
    code = check_state_commit.main(["--rev", "HEAD", "--warn-only"])
    assert code == 0


def test_main_requires_a_selector() -> None:
    with pytest.raises(SystemExit):
        check_state_commit.main([])