"""Command surface tests (V11 5.3).

Validation matrix: all 5 commands return typed responses; unknown command ->
UnknownCommandError, connection stays open.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from dev_harness.contracts.enums import ExecutionState
from dev_harness.contracts.errors import UnknownCommandError
from dev_harness.engine.commands import (
    AttachCommand,
    AttachResponse,
    CommandHandler,
    DetachCommand,
    DetachResponse,
    ErrorResponse,
    ShutdownCommand,
    ShutdownResponse,
    StartSessionCommand,
    StartSessionResponse,
    StatusCommand,
    StatusResponse,
)
from dev_harness.engine.session import SessionManager

pytestmark = pytest.mark.unit


@pytest.fixture
def handler(tmp_path: Path) -> CommandHandler:
    return CommandHandler(SessionManager())


@pytest.mark.unit
def test_start_session_returns_typed_response(handler: CommandHandler, tmp_path: Path) -> None:
    """START_SESSION returns a StartSessionResponse with thread_id and state."""
    resp = handler.handle(StartSessionCommand(workspace=str(tmp_path)))
    assert isinstance(resp, StartSessionResponse)
    assert resp.ok is True
    assert resp.thread_id
    assert resp.state == ExecutionState.READY


@pytest.mark.unit
def test_attach_returns_typed_response(handler: CommandHandler, tmp_path: Path) -> None:
    """ATTACH returns an AttachResponse with the session's thread_id."""
    handler.handle(StartSessionCommand(workspace=str(tmp_path)))
    resp = handler.handle(AttachCommand(workspace=str(tmp_path)))
    assert isinstance(resp, AttachResponse)
    assert resp.ok is True
    assert resp.thread_id
    assert resp.state == ExecutionState.READY


@pytest.mark.unit
def test_detach_returns_typed_response(handler: CommandHandler, tmp_path: Path) -> None:
    """DETACH returns a DetachResponse with the session's thread_id."""
    handler.handle(StartSessionCommand(workspace=str(tmp_path)))
    resp = handler.handle(DetachCommand(workspace=str(tmp_path)))
    assert isinstance(resp, DetachResponse)
    assert resp.ok is True
    assert resp.thread_id


@pytest.mark.unit
def test_status_returns_typed_response(handler: CommandHandler, tmp_path: Path) -> None:
    """STATUS returns a StatusResponse with state and thread_id."""
    handler.handle(StartSessionCommand(workspace=str(tmp_path)))
    resp = handler.handle(StatusCommand(workspace=str(tmp_path)))
    assert isinstance(resp, StatusResponse)
    assert resp.ok is True
    assert resp.thread_id
    assert resp.state == ExecutionState.READY


@pytest.mark.unit
def test_shutdown_returns_typed_response(handler: CommandHandler, tmp_path: Path) -> None:
    """SHUTDOWN returns a ShutdownResponse and invokes the callback."""
    called: list[bool] = []

    def _on_shutdown() -> None:
        called.append(True)

    h = CommandHandler(SessionManager(), on_shutdown=_on_shutdown)
    resp = h.handle(ShutdownCommand(workspace=str(tmp_path)))
    assert isinstance(resp, ShutdownResponse)
    assert resp.ok is True
    assert called == [True]


@pytest.mark.unit
def test_status_without_session_returns_ready(handler: CommandHandler, tmp_path: Path) -> None:
    """STATUS with no session reports READY and no thread_id."""
    resp = handler.handle(StatusCommand(workspace=str(tmp_path)))
    assert isinstance(resp, StatusResponse)
    assert resp.thread_id is None
    assert resp.state == ExecutionState.READY


@pytest.mark.unit
def test_attach_without_session_returns_error(handler: CommandHandler, tmp_path: Path) -> None:
    """ATTACH with no session returns an ErrorResponse (connection stays open)."""
    resp = handler.handle(AttachCommand(workspace=str(tmp_path)))
    assert isinstance(resp, ErrorResponse)
    assert resp.ok is False
    assert resp.remediation


@pytest.mark.unit
def test_detach_without_session_returns_error(handler: CommandHandler, tmp_path: Path) -> None:
    """DETACH with no session returns an ErrorResponse (connection stays open)."""
    resp = handler.handle(DetachCommand(workspace=str(tmp_path)))
    assert isinstance(resp, ErrorResponse)
    assert resp.ok is False


@pytest.mark.unit
def test_second_start_session_returns_error(handler: CommandHandler, tmp_path: Path) -> None:
    """A second START_SESSION returns an ErrorResponse naming the live thread_id."""
    first = handler.handle(StartSessionCommand(workspace=str(tmp_path)))
    assert isinstance(first, StartSessionResponse)
    second = handler.handle(StartSessionCommand(workspace=str(tmp_path)))
    assert isinstance(second, ErrorResponse)
    assert first.thread_id in second.error


@pytest.mark.unit
def test_unknown_command_raises_unknown_command_error(
    handler: CommandHandler, tmp_path: Path
) -> None:
    """An unknown command raises UnknownCommandError (a HarnessError)."""
    with pytest.raises(UnknownCommandError):
        handler.handle(object())  # type: ignore[arg-type]
    # The handler is still usable — the connection stays open.
    resp = handler.handle(StatusCommand(workspace=str(tmp_path)))
    assert isinstance(resp, StatusResponse)


@pytest.mark.unit
def test_unknown_command_error_is_harness_error() -> None:
    """UnknownCommandError subclasses HarnessError with remediation."""
    from dev_harness.contracts.errors import HarnessError

    assert issubclass(UnknownCommandError, HarnessError)
    assert UnknownCommandError.remediation


@pytest.mark.unit
def test_command_discriminated_union_rejects_unknown() -> None:
    """The Command union rejects an unknown command tag."""
    from pydantic import TypeAdapter

    from dev_harness.engine.commands import Command

    adapter = TypeAdapter(Command)
    with pytest.raises(ValidationError):
        adapter.validate_python({"command": "BOGUS", "workspace": "/tmp/w"})


@pytest.mark.unit
def test_response_discriminated_union_rejects_unknown() -> None:
    """The CommandResponse union rejects an unknown response tag."""
    from pydantic import TypeAdapter

    from dev_harness.engine.commands import CommandResponse

    adapter = TypeAdapter(CommandResponse)
    with pytest.raises(ValidationError):
        adapter.validate_python({"command": "BOGUS", "ok": True})


@pytest.mark.unit
def test_commands_are_typed_models() -> None:
    """Every command model is a Pydantic model with a Literal tag."""
    for cmd in (
        StartSessionCommand(workspace="/tmp/w"),
        AttachCommand(workspace="/tmp/w"),
        DetachCommand(workspace="/tmp/w"),
        StatusCommand(workspace="/tmp/w"),
        ShutdownCommand(workspace="/tmp/w"),
    ):
        assert cmd.command in {
            "START_SESSION",
            "ATTACH",
            "DETACH",
            "STATUS",
            "SHUTDOWN",
        }