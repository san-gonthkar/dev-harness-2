"""Blackbox TUI tests in a Cypress/Selenium style.

The real ``dev-harness`` process runs in a real PTY; these tests only observe
its rendered screen. Read them as user stories::

    tui = launch(workspace)
    tui.should_see("field").press("ctrl+c").should_still_be_running()

Marked ``e2e`` (NIGHTLY tier) — each test spawns a real process. Run with::

    pytest tests/blackbox -q -m e2e
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests.blackbox.dsl import launch

pytestmark = pytest.mark.e2e


def _make_workspace(tmp_path: Path) -> Path:
    """A minimal git repository for the TUI to open."""
    ws = tmp_path / "ws"
    ws.mkdir()
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    subprocess.run(["git", "-C", str(ws), "config", "user.email", "t@t.t"], check=True)
    subprocess.run(["git", "-C", str(ws), "config", "user.name", "t"], check=True)
    (ws / "README.md").write_text("blackbox\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(ws), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(ws), "commit", "-qm", "init"], check=True)
    return ws


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return _make_workspace(tmp_path)


@pytest.mark.e2e
def test_renders_the_four_panel_dashboard(workspace: Path, tmp_path: Path) -> None:
    """The dashboard renders all four panels and the workspace tree."""
    with launch(workspace, cast_path=tmp_path / "render.cast") as tui:
        tui.should_see("field").should_see("value")
        tui.should_see("README")


@pytest.mark.e2e
def test_degrades_below_80x24(workspace: Path) -> None:
    """Below 80x24 the layout degrades to the execution canvas only (9.5).

    The canvas is empty until it receives events, so the assertion is on what
    is *absent*: the repo tree and the model-registry fields are not rendered.
    """
    with launch(workspace, cols=60, rows=20) as tui:
        # The canvas paints a bordered box; wait for any frame to appear.
        tui.session.wait_for(
            lambda s: any(line.strip() for line in s.screen_lines()),
            timeout=30.0,
            message="no frame rendered",
        )
        tui.should_not_see("README")
        tui.should_not_see("provider")


@pytest.mark.e2e
def test_ctrl_c_requests_pause_without_exiting(workspace: Path) -> None:
    """ctrl+c requests a PAUSE; the app keeps running (7.9)."""
    with launch(workspace) as tui:
        tui.should_see("field")
        tui.press("ctrl+c").should_still_be_running()


@pytest.mark.e2e
def test_ctrl_q_confirm_quit(workspace: Path) -> None:
    """ctrl+q opens a confirm modal; confirming exits the app (7.9).

    The modal is button-driven: the first button (Yes) is focused, so Enter
    confirms.
    """
    with launch(workspace) as tui:
        tui.should_see("field")
        tui.press("ctrl+q").should_see("Quit Hermes?")
        tui.press("enter").should_have_exited()


@pytest.mark.e2e
def test_ctrl_q_cancel_keeps_running(workspace: Path) -> None:
    """ctrl+q then Escape leaves the app running (7.9)."""
    with launch(workspace) as tui:
        tui.should_see("field")
        tui.press("ctrl+q").should_see("Quit Hermes?")
        tui.press("escape").should_still_be_running()
        tui.should_not_see("Quit Hermes?")


@pytest.mark.e2e
def test_records_a_replayable_cast(workspace: Path, tmp_path: Path) -> None:
    """The session is recorded as an asciinema v2 cast with real frames."""
    cast = tmp_path / "demo.cast"
    with launch(workspace, cast_path=cast) as tui:
        tui.should_see("field")

    lines = cast.read_text(encoding="utf-8").strip().splitlines()
    header = json.loads(lines[0])
    assert header["version"] == 2
    assert header["width"] == 100
    assert header["height"] == 30
    frames = [json.loads(line) for line in lines[1:]]
    assert frames, "no frames recorded"
    assert all(frame[1] == "o" for frame in frames)


@pytest.mark.e2e
def test_screenshot_captures_the_screen(workspace: Path, tmp_path: Path) -> None:
    """A screen capture can be written to disk for review."""
    shot = tmp_path / "shots" / "dashboard.txt"
    with launch(workspace) as tui:
        tui.should_see("field")
        tui.screenshot(shot)
    assert shot.exists()
    assert "field" in shot.read_text(encoding="utf-8")
