"""Deterministic rig self-tests (V11 0.12)."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from tests.support.clock import FrozenClock

pytestmark = pytest.mark.unit


def test_frozen_clock_advances_only_on_tick() -> None:
    clock = FrozenClock(start=100.0)
    assert clock.monotonic() == 100.0
    assert clock.monotonic() == 100.0  # no drift
    clock.tick(5.0)
    assert clock.monotonic() == 105.0


def test_frozen_clock_install_restore() -> None:
    clock = FrozenClock(start=200.0)
    clock.install()
    try:
        assert time.monotonic() == 200.0
        clock.tick(1.0)
        assert time.monotonic() == 201.0
    finally:
        clock.restore()
    # Real clock restored.
    assert time.monotonic() > 0


def test_tmp_workspace_is_git_repo(tmp_workspace: Path) -> None:
    r = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_workspace, capture_output=True, text=True, check=False,
    )
    assert r.returncode == 0
    assert len(r.stdout.strip()) == 40


def test_tmp_workspace_files(tmp_path: Path) -> None:
    from tests.support.workspace import make_workspace

    ws = make_workspace(tmp_path, files={"src/a.py": "x = 1"})
    assert (ws / "src" / "a.py").read_text(encoding="utf-8") == "x = 1"
