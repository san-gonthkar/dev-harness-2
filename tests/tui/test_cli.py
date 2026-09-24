"""Root ``dev-harness`` CLI tests (V11 task 7.11).

Unit tests monkeypatch the process/IO boundary (``GitAdapter``,
``BrokerClient``, ``EngineBootstrap``, ``HermesApp``) so no real daemon,
socket, or TUI is started — AF_UNIX is unavailable on Windows. Negative
tests cover the non-git exit-2 path and a ``HarnessError`` from the
bootstrap. No ``time.sleep``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import ClassVar

import pytest

from dev_harness import cli
from dev_harness.broker.protocol import BrokerMessage
from dev_harness.contracts.enums import ExecutionState
from dev_harness.engine.bootstrap import EngineUnreachableError
from dev_harness.engine.commands import StatusResponse
from dev_harness.paths import derive_paths


class _FakeBroker:
    """A broker client stub whose ``health`` never touches a socket."""

    def __init__(self, socket_path: object = None, **_: object) -> None:
        self.socket_path = socket_path
        self.closed = False

    def health(self) -> BrokerMessage:
        return BrokerMessage(op="HEALTH", ok=True, data={"status": "ok"})

    def close(self) -> None:
        self.closed = True


class _FakeBootstrap:
    """An engine bootstrap stub returning a canned ``StatusResponse``."""

    def __init__(self, workspace: object = None, **_: object) -> None:
        self.workspace = workspace

    def ensure_daemon(self, **_: object) -> StatusResponse:
        return StatusResponse(
            thread_id="thread-1",
            state=ExecutionState.READY,
            version="0.0.0",
        )


class _FakeApp:
    """A HermesApp stub recording construction and ``run``."""

    instances: ClassVar[list[_FakeApp]] = []

    def __init__(self, *, workspace: str | None = None) -> None:
        self.workspace = workspace
        self.ran = False
        _FakeApp.instances.append(self)

    def run(self) -> None:
        self.ran = True


def _patch_repo(monkeypatch: pytest.MonkeyPatch, is_repo: bool) -> None:
    monkeypatch.setattr(cli.GitAdapter, "is_repository", lambda self: is_repo)


# --- unit -------------------------------------------------------------------


@pytest.mark.unit
def test_help_exits_zero() -> None:
    """``--help`` parses and exits 0 via argparse."""
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--help"])
    assert excinfo.value.code == 0


@pytest.mark.unit
def test_launches_tui_with_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without ``--self-check`` the TUI is constructed with the workspace."""
    _patch_repo(monkeypatch, True)
    _FakeApp.instances.clear()
    monkeypatch.setattr("dev_harness.tui.app.HermesApp", _FakeApp)

    rc = cli.main(["--workspace", str(tmp_path)])

    assert rc == 0
    assert len(_FakeApp.instances) == 1
    assert _FakeApp.instances[0].workspace == str(tmp_path)
    assert _FakeApp.instances[0].ran is True


@pytest.mark.unit
def test_self_check_prints_paths_and_health(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--self-check`` prints socket/DB/broker/engine and returns 0."""
    _patch_repo(monkeypatch, True)
    monkeypatch.setattr(cli, "BrokerClient", _FakeBroker)
    monkeypatch.setattr(cli, "EngineBootstrap", _FakeBootstrap)

    rc = cli.main(["--workspace", str(tmp_path), "--self-check"])

    out = capsys.readouterr().out
    paths = derive_paths(tmp_path)
    assert rc == 0
    assert str(paths.socket_path) in out
    assert str(paths.state_db) in out
    assert "broker: ok" in out
    assert "engine: thread_id=thread-1" in out


# --- negative ---------------------------------------------------------------


@pytest.mark.unit
def test_non_git_workspace_exits_two(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A non-git workspace exits 2 and prints the NotAGitRepository message."""
    _patch_repo(monkeypatch, False)

    rc = cli.main(["--workspace", str(tmp_path), "--self-check"])

    err = capsys.readouterr().err
    assert rc == 2
    assert "not a git repository" in err


@pytest.mark.unit
def test_harness_error_returns_nonzero_without_traceback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A HarnessError from the bootstrap is reported, not raised."""
    _patch_repo(monkeypatch, True)
    monkeypatch.setattr(cli, "BrokerClient", _FakeBroker)

    class _FailingBootstrap:
        def __init__(self, workspace: object = None, **_: object) -> None:
            self.workspace = workspace

        def ensure_daemon(self, **_: object) -> StatusResponse:
            raise EngineUnreachableError("engine down")

    monkeypatch.setattr(cli, "EngineBootstrap", _FailingBootstrap)

    rc = cli.main(["--workspace", str(tmp_path), "--self-check"])

    err = capsys.readouterr().err
    assert rc != 0
    assert "engine down" in err
    assert "Traceback" not in err


# --- integration ------------------------------------------------------------


@pytest.mark.integration
def test_self_check_in_real_git_repo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--self-check`` returns 0 against a real git repo with a fake bootstrap."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    monkeypatch.setattr(cli, "BrokerClient", _FakeBroker)
    monkeypatch.setattr(cli, "EngineBootstrap", _FakeBootstrap)

    rc = cli.main(["--workspace", str(tmp_path), "--self-check"])

    assert rc == 0
    assert "broker: ok" in capsys.readouterr().out
