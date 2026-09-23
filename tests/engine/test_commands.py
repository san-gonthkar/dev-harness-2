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
def test_start_session_returns_typed_response(
    handler: CommandHandler, tmp_path: Path
) -> None:
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
def test_shutdown_returns_typed_response(
    handler: CommandHandler, tmp_path: Path
) -> None:
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
def test_status_without_session_returns_ready(
    handler: CommandHandler, tmp_path: Path
) -> None:
    """STATUS with no session reports READY and no thread_id."""
    resp = handler.handle(StatusCommand(workspace=str(tmp_path)))
    assert isinstance(resp, StatusResponse)
    assert resp.thread_id is None
    assert resp.state == ExecutionState.READY


@pytest.mark.unit
def test_attach_without_session_returns_error(
    handler: CommandHandler, tmp_path: Path
) -> None:
    """ATTACH with no session returns an ErrorResponse (connection stays open)."""
    resp = handler.handle(AttachCommand(workspace=str(tmp_path)))
    assert isinstance(resp, ErrorResponse)
    assert resp.ok is False
    assert resp.remediation


@pytest.mark.unit
def test_detach_without_session_returns_error(
    handler: CommandHandler, tmp_path: Path
) -> None:
    """DETACH with no session returns an ErrorResponse (connection stays open)."""
    resp = handler.handle(DetachCommand(workspace=str(tmp_path)))
    assert isinstance(resp, ErrorResponse)
    assert resp.ok is False


@pytest.mark.unit
def test_second_start_session_returns_error(
    handler: CommandHandler, tmp_path: Path
) -> None:
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


# --- framing round-trips ----------------------------------------------------


@pytest.mark.unit
def test_command_frame_round_trip() -> None:
    """encode_command/decode_command_frame round-trip a command."""
    from dev_harness.engine.commands import (
        decode_command_frame,
        encode_command,
    )

    cmd = StartSessionCommand(workspace="/tmp/w")
    decoded = decode_command_frame(encode_command(cmd))
    assert isinstance(decoded, StartSessionCommand)
    assert decoded.workspace == "/tmp/w"


@pytest.mark.unit
def test_response_frame_round_trip() -> None:
    """encode_response/decode_response_frame round-trip a response."""
    from dev_harness.engine.commands import (
        decode_response_frame,
        encode_response,
    )

    resp = StatusResponse(thread_id="t1", state=ExecutionState.READY)
    decoded = decode_response_frame(encode_response(resp))
    assert isinstance(decoded, StatusResponse)
    assert decoded.thread_id == "t1"
    assert decoded.state == ExecutionState.READY


@pytest.mark.unit
def test_read_command_frame_from_stream() -> None:
    """read_command_frame reads a command from a binary stream."""
    import io

    from dev_harness.engine.commands import encode_command, read_command_frame

    cmd = StatusCommand(workspace="/tmp/w")
    stream = io.BytesIO(encode_command(cmd))
    decoded = read_command_frame(stream)
    assert isinstance(decoded, StatusCommand)


@pytest.mark.unit
def test_read_response_frame_from_stream() -> None:
    """read_response_frame reads a response from a binary stream."""
    import io

    from dev_harness.engine.commands import encode_response, read_response_frame

    resp = ShutdownResponse()
    stream = io.BytesIO(encode_response(resp))
    decoded = read_response_frame(stream)
    assert isinstance(decoded, ShutdownResponse)


@pytest.mark.unit
def test_decode_command_frame_rejects_unknown() -> None:
    """decode_command_frame rejects an unknown command tag."""
    import struct

    from dev_harness.engine.commands import (
        MAX_COMMAND_FRAME,
        PREFIX_LEN,
        decode_command_frame,
    )

    body = b'{"command": "BOGUS", "workspace": "/tmp/w"}'
    frame = struct.pack(">I", len(body)) + body
    assert len(frame) <= MAX_COMMAND_FRAME
    with pytest.raises(ValueError):
        decode_command_frame(frame[:PREFIX_LEN])


@pytest.mark.unit
def test_decode_command_frame_short_prefix() -> None:
    """decode_command_frame rejects a frame shorter than the prefix."""
    from dev_harness.engine.commands import decode_command_frame

    with pytest.raises(ValueError):
        decode_command_frame(b"\x00\x00")


@pytest.mark.unit
def test_decode_command_frame_oversized() -> None:
    """decode_command_frame rejects an oversized body."""
    import struct

    from dev_harness.engine.commands import (
        MAX_COMMAND_FRAME,
        decode_command_frame,
    )

    frame = struct.pack(">I", MAX_COMMAND_FRAME + 1) + b"x"
    with pytest.raises(ValueError):
        decode_command_frame(frame)


@pytest.mark.unit
def test_read_command_frame_eof() -> None:
    """read_command_frame raises on EOF before any frame."""
    import io

    from dev_harness.engine.commands import read_command_frame

    with pytest.raises(ValueError):
        read_command_frame(io.BytesIO(b""))


def test_encode_command_oversized() -> None:
    """encode_command raises when the body exceeds the ceiling."""
    from dev_harness.engine.commands import MAX_COMMAND_FRAME, encode_command

    class _Huge:
        def model_dump_json(self) -> str:
            return "x" * (MAX_COMMAND_FRAME + 1)

    with pytest.raises(ValueError):
        encode_command(_Huge())  # type: ignore[arg-type]


def test_encode_response_oversized() -> None:
    """encode_response raises when the body exceeds the ceiling."""
    from dev_harness.engine.commands import MAX_COMMAND_FRAME, encode_response

    class _Huge:
        def model_dump_json(self) -> str:
            return "x" * (MAX_COMMAND_FRAME + 1)

    with pytest.raises(ValueError):
        encode_response(_Huge())  # type: ignore[arg-type]


def test_decode_response_frame_short_prefix() -> None:
    """decode_response_frame rejects a frame shorter than the prefix."""
    from dev_harness.engine.commands import decode_response_frame

    with pytest.raises(ValueError):
        decode_response_frame(b"\x00\x00")


def test_decode_response_frame_body_truncated() -> None:
    """decode_response_frame rejects a declared body longer than the data."""
    import struct

    from dev_harness.engine.commands import decode_response_frame

    frame = struct.pack(">I", 64) + b"x"
    with pytest.raises(ValueError):
        decode_response_frame(frame)


def test_read_command_frame_eof_in_prefix() -> None:
    """read_command_frame raises on EOF inside the length prefix."""
    import io

    from dev_harness.engine.commands import read_command_frame

    with pytest.raises(ValueError):
        read_command_frame(io.BytesIO(b"\x00\x00"))


def test_read_command_frame_eof_in_body() -> None:
    """read_command_frame raises when the body is shorter than declared."""
    import io
    import struct

    from dev_harness.engine.commands import read_command_frame

    with pytest.raises(ValueError):
        read_command_frame(io.BytesIO(struct.pack(">I", 32) + b"x"))


def test_read_response_frame_eof_in_prefix() -> None:
    """read_response_frame raises on EOF inside the length prefix."""
    import io

    from dev_harness.engine.commands import read_response_frame

    with pytest.raises(ValueError):
        read_response_frame(io.BytesIO(b"\x00\x00"))


def test_read_response_frame_eof_in_body() -> None:
    """read_response_frame raises when the body is shorter than declared."""
    import io
    import struct

    from dev_harness.engine.commands import read_response_frame

    with pytest.raises(ValueError):
        read_response_frame(io.BytesIO(struct.pack(">I", 32) + b"x"))


def test_shutdown_command_without_hook(tmp_path: Path) -> None:
    """SHUTDOWN with no on_shutdown hook still returns a ShutdownResponse."""
    handler = CommandHandler(SessionManager(), on_shutdown=None)
    resp = handler.handle(ShutdownCommand(workspace=str(tmp_path)))
    assert isinstance(resp, ShutdownResponse)
    assert resp.ok is True
