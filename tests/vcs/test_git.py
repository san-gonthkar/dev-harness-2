"""Git adapter tests (V11 1.9)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dev_harness.contracts.errors import NoCommitsError
from dev_harness.vcs.git import GitAdapter

pytestmark = pytest.mark.unit


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


def test_head_sha_matches_rev_parse(tmp_workspace: Path) -> None:
    adapter = GitAdapter(tmp_workspace)
    expected = subprocess.run(
        ["git", "-C", str(tmp_workspace), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert adapter.head_sha() == expected


def test_active_branch(tmp_workspace: Path) -> None:
    adapter = GitAdapter(tmp_workspace)
    assert adapter.active_branch() == "main"


def test_dirty_transitions(tmp_workspace: Path) -> None:
    adapter = GitAdapter(tmp_workspace)
    assert adapter.is_dirty() is False
    (tmp_workspace / "new.txt").write_text("x", encoding="utf-8")
    assert adapter.is_dirty() is True
    assert adapter.uncommitted_count() == 1


def test_no_commits_raises(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-b", "main")
    adapter = GitAdapter(tmp_path)
    with pytest.raises(NoCommitsError):
        adapter.head_sha()
