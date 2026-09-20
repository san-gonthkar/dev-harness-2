"""Storage CLI round-trip tests (V11 1.15)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from dev_harness.contracts.state import HarnessState
from tests.support.workspace import make_workspace

pytestmark = pytest.mark.unit

CLI = [sys.executable, "-m", "dev_harness.storage.cli"]


def _run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [*CLI, *args], capture_output=True, text=True, check=False, cwd=cwd
    )


def test_put_get_round_trip(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    state = HarnessState(project_id="p1", workspace_path=str(ws), thread_id="t1", raw_input="hello")
    state_file = tmp_path / "state.json"
    state_file.write_text(state.model_dump_json(), encoding="utf-8")
    r = _run("put", "--workspace", str(ws), "--file", str(state_file), "--project", "p1", "--thread", "t1")
    assert r.returncode == 0, r.stderr
    r2 = _run("get", "--workspace", str(ws), "--project", "p1", "--thread", "t1", "--latest")
    assert r2.returncode == 0
    got = json.loads(r2.stdout)
    assert got == json.loads(state.model_dump_json())


def test_restore_checks_out_stored_hash(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    # Create a second commit.
    (ws / "f.txt").write_text("v2", encoding="utf-8")
    subprocess.run(["git", "-C", str(ws), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(ws), "commit", "-m", "second"], check=True, capture_output=True)
    first = subprocess.run(
        ["git", "-C", str(ws), "rev-parse", "HEAD~1"], check=True, capture_output=True, text=True
    ).stdout.strip()
    r = _run("restore", "--workspace", str(ws), "--to", first)
    assert r.returncode == 0, r.stderr
    head = subprocess.run(
        ["git", "-C", str(ws), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    assert head == first


def test_dirty_restore_exit2(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    (ws / "dirty.txt").write_text("x", encoding="utf-8")
    r = _run("restore", "--workspace", str(ws), "--to", "HEAD")
    assert r.returncode == 2
    assert "dirty" in r.stderr.lower() or "DirtyWorktree" in r.stderr
