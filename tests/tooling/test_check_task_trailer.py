"""Handoff enforcement tests (V11 0.11)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_task_trailer.py"


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "f").write_text("x", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "base")
    return tmp_path


def _commit(repo: Path, message: str) -> None:
    (repo / "f").write_text("y", encoding="utf-8")
    _git(repo, "add", ".")
    r = _git(repo, "commit", "-m", message)
    assert r.returncode == 0, r.stderr


def _run(repo: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *extra],
        cwd=repo, capture_output=True, text=True,
    )


def test_valid_trailer_exit0(repo: Path) -> None:
    _commit(repo, "task 0.11: x\n\nTask-Id: 0.11")
    r = _run(repo, "--rev", "HEAD")
    assert r.returncode == 0


def test_missing_trailer_exit1(repo: Path) -> None:
    _commit(repo, "task with no trailer")
    r = _run(repo, "--rev", "HEAD")
    assert r.returncode == 1
    assert "missing Task-Id trailer" in r.stderr


def test_invalid_task_id_exit1(repo: Path) -> None:
    _commit(repo, "task 99.9: x\n\nTask-Id: 99.9")
    r = _run(repo, "--rev", "HEAD")
    assert r.returncode == 1
    assert "invalid Task-Id" in r.stderr


def test_subtask_id_ok(repo: Path) -> None:
    _commit(repo, "task 8.21a: x\n\nTask-Id: 8.21a")
    r = _run(repo, "--rev", "HEAD")
    assert r.returncode == 0
