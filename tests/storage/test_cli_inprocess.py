"""In-process storage CLI tests (V11 1.15) — subprocess tests are not
instrumented by coverage, so these call the cmd_* functions directly."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from dev_harness.contracts.state import HarnessState
from dev_harness.storage import cli
from tests.support.workspace import make_workspace

pytestmark = pytest.mark.unit


def _state(ws: Path, project: str = "p1", thread: str = "t1") -> HarnessState:
    return HarnessState(
        project_id=project, workspace_path=str(ws), thread_id=thread, raw_input="hello"
    )


def _write_state(tmp_path: Path, state: HarnessState) -> Path:
    f = tmp_path / "state.json"
    f.write_text(state.model_dump_json(), encoding="utf-8")
    return f


def test_cmd_put_returns_cid(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    state = _state(ws)
    f = _write_state(tmp_path, state)
    cid = cli.cmd_put(str(ws), str(f), "p1", "t1")
    assert cid == 0


def test_cmd_get_round_trip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ws = make_workspace(tmp_path)
    state = _state(ws)
    f = _write_state(tmp_path, state)
    cli.cmd_put(str(ws), str(f), "p1", "t1")
    capsys.readouterr()
    rc = cli.cmd_get(str(ws), "p1", "t1")
    out = capsys.readouterr().out
    assert rc == 0
    assert json.loads(out) == json.loads(state.model_dump_json())


def test_cmd_get_empty_returns_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ws = make_workspace(tmp_path)
    rc = cli.cmd_get(str(ws), "p1", "t1")
    assert rc == 1
    assert "no checkpoints" in capsys.readouterr().err


def test_cmd_list_rows(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ws = make_workspace(tmp_path)
    state = _state(ws)
    f = _write_state(tmp_path, state)
    cli.cmd_put(str(ws), str(f), "p1", "t1")
    capsys.readouterr()
    rc = cli.cmd_list(str(ws), "p1", "t1")
    out = capsys.readouterr().out
    assert rc == 0
    assert "cp_" in out  # checkpoint_id column


def test_cmd_restore_ok(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path)
    (ws / "f.txt").write_text("v2", encoding="utf-8")
    subprocess.run(["git", "-C", str(ws), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(ws), "commit", "-m", "second"],
        check=True,
        capture_output=True,
    )
    first = subprocess.run(
        ["git", "-C", str(ws), "rev-parse", "HEAD~1"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    rc = cli.cmd_restore(str(ws), first)
    assert rc == 0


def test_cmd_restore_dirty_returns_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ws = make_workspace(tmp_path)
    (ws / "dirty.txt").write_text("x", encoding="utf-8")
    rc = cli.cmd_restore(str(ws), "HEAD")
    assert rc == 2
    assert "restore failed" in capsys.readouterr().err


def test_build_parser_subcommands() -> None:
    parser = cli.build_parser()
    # The parser must expose all four subcommands (parse_args raises SystemExit
    # for --help, which proves the subcommand exists).
    for name in ("put", "get", "list", "restore"):
        with pytest.raises(SystemExit):
            parser.parse_args([name, "--help"])


def test_main_put_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = make_workspace(tmp_path)
    state = _state(ws)
    f = _write_state(tmp_path, state)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cli",
            "put",
            "--workspace",
            str(ws),
            "--file",
            str(f),
            "--project",
            "p1",
            "--thread",
            "t1",
        ],
    )
    assert cli.main() == 0


def test_main_get_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = make_workspace(tmp_path)
    state = _state(ws)
    f = _write_state(tmp_path, state)
    cli.cmd_put(str(ws), str(f), "p1", "t1")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cli",
            "get",
            "--workspace",
            str(ws),
            "--project",
            "p1",
            "--thread",
            "t1",
            "--latest",
        ],
    )
    assert cli.main() == 0


def test_main_list_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = make_workspace(tmp_path)
    state = _state(ws)
    f = _write_state(tmp_path, state)
    cli.cmd_put(str(ws), str(f), "p1", "t1")
    monkeypatch.setattr(
        sys,
        "argv",
        ["cli", "list", "--workspace", str(ws), "--project", "p1", "--thread", "t1"],
    )
    assert cli.main() == 0


def test_main_restore_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = make_workspace(tmp_path)
    (ws / "dirty.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        sys, "argv", ["cli", "restore", "--workspace", str(ws), "--to", "HEAD"]
    )
    assert cli.main() == 2  # dirty tree -> exit 2
