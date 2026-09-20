"""Command surface: START_SESSION, ATTACH, DETACH, STATUS, SHUTDOWN (V11 5.3).

Commands and responses are typed Pydantic models discriminated on a
``command`` Literal tag. The daemon's IPC handler decodes command frames,
dispatches them through CommandHandler, and returns a typed response frame.
An unknown command raises UnknownCommandError but the connection stays open
(the handler returns an error response rather than closing the socket).
"""

from __future__ import annotations

import struct
from collections.abc import Callable
from typing import Annotated, BinaryIO, Literal

from pydantic import BaseModel, ConfigDict, Field

from dev_harness import __version__
from dev_harness.contracts.enums import ExecutionState
from dev_harness.contracts.errors import SessionExistsError, UnknownCommandError
from dev_harness.engine.session import SessionManager

# --- commands ---------------------------------------------------------------


class StartSessionCommand(BaseModel):
    """Create a session for the workspace."""

    model_config = ConfigDict(extra="forbid")

    command: Literal["START_SESSION"] = "START_SESSION"
    workspace: str


class AttachCommand(BaseModel):
    """Attach a client to the active session."""

    model_config = ConfigDict(extra="forbid")

    command: Literal["ATTACH"] = "ATTACH"
    workspace: str


class DetachCommand(BaseModel):
    """Detach a client from the active session."""

    model_config = ConfigDict(extra="forbid")

    command: Literal["DETACH"] = "DETACH"
    workspace: str


class StatusCommand(BaseModel):
    """Report the session's status."""

    model_config = ConfigDict(extra="forbid")

    command: Literal["STATUS"] = "STATUS"
    workspace: str


class ShutdownCommand(BaseModel):
    """Shut the engine daemon down gracefully."""

    model_config = ConfigDict(extra="forbid")

    command: Literal["SHUTDOWN"] = "SHUTDOWN"
    workspace: str


Command = Annotated[
    StartSessionCommand
    | AttachCommand
    | DetachCommand
    | StatusCommand
    | ShutdownCommand,
    Field(discriminator="command"),
]


# --- responses --------------------------------------------------------------


class StartSessionResponse(BaseModel):
    """A session was created."""

    model_config = ConfigDict(extra="forbid")

    command: Literal["START_SESSION"] = "START_SESSION"
    ok: bool = True
    thread_id: str
    state: ExecutionState


class AttachResponse(BaseModel):
    """A client attached to the session."""

    model_config = ConfigDict(extra="forbid")

    command: Literal["ATTACH"] = "ATTACH"
    ok: bool = True
    thread_id: str
    state: ExecutionState


class DetachResponse(BaseModel):
    """A client detached from the session."""

    model_config = ConfigDict(extra="forbid")

    command: Literal["DETACH"] = "DETACH"
    ok: bool = True
    thread_id: str


class StatusResponse(BaseModel):
    """The session's current status."""

    model_config = ConfigDict(extra="forbid")

    command: Literal["STATUS"] = "STATUS"
    ok: bool = True
    thread_id: str | None
    state: ExecutionState
    version: str = ""


class ShutdownResponse(BaseModel):
    """The daemon will shut down."""

    model_config = ConfigDict(extra="forbid")

    command: Literal["SHUTDOWN"] = "SHUTDOWN"
    ok: bool = True


class ErrorResponse(BaseModel):
    """A command failed; the connection stays open."""

    model_config = ConfigDict(extra="forbid")

    command: Literal["ERROR"] = "ERROR"
    ok: bool = False
    error: str
    remediation: str = ""


CommandResponse = Annotated[
    StartSessionResponse
    | AttachResponse
    | DetachResponse
    | StatusResponse
    | ShutdownResponse
    | ErrorResponse,
    Field(discriminator="command"),
]


# --- framing ----------------------------------------------------------------

_PREFIX = struct.Struct(">I")
PREFIX_LEN = _PREFIX.size
MAX_COMMAND_FRAME = 1 * 1024 * 1024  # 1 MiB


def encode_command(command: Command) -> bytes:
    """Encode a command as a length-prefixed JSON frame."""
    body = command.model_dump_json().encode("utf-8")
    frame = _PREFIX.pack(len(body)) + body
    if len(frame) > MAX_COMMAND_FRAME:
        raise ValueError(f"command frame of {len(frame)} bytes exceeds {MAX_COMMAND_FRAME}")
    return frame


def encode_response(response: CommandResponse) -> bytes:
    """Encode a response as a length-prefixed JSON frame."""
    body = response.model_dump_json().encode("utf-8")
    frame = _PREFIX.pack(len(body)) + body
    if len(frame) > MAX_COMMAND_FRAME:
        raise ValueError(f"response frame of {len(frame)} bytes exceeds {MAX_COMMAND_FRAME}")
    return frame


def decode_command_frame(data: bytes) -> Command:
    """Decode a single complete command frame (prefix + body)."""
    if len(data) < PREFIX_LEN:
        raise ValueError("frame shorter than 4-byte prefix")
    (length,) = _PREFIX.unpack_from(data)
    if length > MAX_COMMAND_FRAME:
        raise ValueError(f"frame body of {length} bytes exceeds ceiling")
    if len(data) < PREFIX_LEN + length:
        raise ValueError(
            f"declared {length} body bytes but only {len(data) - PREFIX_LEN} available"
        )
    body = data[PREFIX_LEN : PREFIX_LEN + length]
    return _command_from_json(body)


def decode_response_frame(data: bytes) -> CommandResponse:
    """Decode a single complete response frame (prefix + body)."""
    if len(data) < PREFIX_LEN:
        raise ValueError("frame shorter than 4-byte prefix")
    (length,) = _PREFIX.unpack_from(data)
    if length > MAX_COMMAND_FRAME:
        raise ValueError(f"frame body of {length} bytes exceeds ceiling")
    if len(data) < PREFIX_LEN + length:
        raise ValueError(
            f"declared {length} body bytes but only {len(data) - PREFIX_LEN} available"
        )
    body = data[PREFIX_LEN : PREFIX_LEN + length]
    return _response_from_json(body)


def read_command_frame(stream: BinaryIO) -> Command:
    """Read one command frame from a binary stream, blocking until complete."""
    prefix = stream.read(PREFIX_LEN)
    if not prefix:
        raise ValueError("EOF before any frame")
    if len(prefix) < PREFIX_LEN:
        raise ValueError("EOF inside the length prefix")
    (length,) = _PREFIX.unpack(prefix)
    if length > MAX_COMMAND_FRAME:
        raise ValueError(f"declared body of {length} bytes exceeds ceiling")
    body = stream.read(length)
    if len(body) < length:
        raise ValueError(f"EOF inside frame body: {len(body)} of {length} bytes")
    return _command_from_json(body)


def read_response_frame(stream: BinaryIO) -> CommandResponse:
    """Read one response frame from a binary stream, blocking until complete."""
    prefix = stream.read(PREFIX_LEN)
    if not prefix:
        raise ValueError("EOF before any frame")
    if len(prefix) < PREFIX_LEN:
        raise ValueError("EOF inside the length prefix")
    (length,) = _PREFIX.unpack(prefix)
    if length > MAX_COMMAND_FRAME:
        raise ValueError(f"declared body of {length} bytes exceeds ceiling")
    body = stream.read(length)
    if len(body) < length:
        raise ValueError(f"EOF inside frame body: {len(body)} of {length} bytes")
    return _response_from_json(body)


def _command_from_json(body: bytes) -> Command:
    """Validate a command body against the Command union."""
    import json

    from pydantic import TypeAdapter

    data = json.loads(body)
    return TypeAdapter(Command).validate_python(data)


def _response_from_json(body: bytes) -> CommandResponse:
    """Validate a response body against the CommandResponse union."""
    import json

    from pydantic import TypeAdapter

    data = json.loads(body)
    return TypeAdapter(CommandResponse).validate_python(data)


# --- handler ----------------------------------------------------------------


class CommandHandler:
    """Dispatch commands against the session manager.

    ``on_shutdown`` is invoked when SHUTDOWN is handled so the daemon can
    begin its graceful drain.
    """

    def __init__(
        self,
        sessions: SessionManager,
        *,
        on_shutdown: Callable[[], None] | None = None,
    ) -> None:
        self._sessions = sessions
        self._on_shutdown = on_shutdown

    def handle(self, command: Command) -> CommandResponse:
        """Dispatch one command to its typed handler."""
        if isinstance(command, StartSessionCommand):
            return self._start_session(command)
        if isinstance(command, AttachCommand):
            return self._attach(command)
        if isinstance(command, DetachCommand):
            return self._detach(command)
        if isinstance(command, StatusCommand):
            return self._status(command)
        if isinstance(command, ShutdownCommand):
            return self._shutdown()
        raise UnknownCommandError(
            f"unknown command {command!r}",
            remediation="Send one of the documented commands; the connection stays open.",
        )

    def _start_session(
        self, command: StartSessionCommand
    ) -> StartSessionResponse | ErrorResponse:
        try:
            session = self._sessions.new_session(command.workspace)
        except SessionExistsError as exc:
            return ErrorResponse(
                error=str(exc),
                remediation=exc.remediation,
            )
        return StartSessionResponse(
            thread_id=session.thread_id,
            state=session.state.tui_state.critic_gatekeeper_status,
        )

    def _attach(self, command: AttachCommand) -> AttachResponse | ErrorResponse:
        session = self._sessions.get(command.workspace)
        if session is None:
            return ErrorResponse(
                error=f"no session for workspace {command.workspace}",
                remediation="Start a session first.",
            )
        return AttachResponse(
            thread_id=session.thread_id,
            state=session.state.tui_state.critic_gatekeeper_status,
        )

    def _detach(self, command: DetachCommand) -> DetachResponse | ErrorResponse:
        session = self._sessions.get(command.workspace)
        if session is None:
            return ErrorResponse(
                error=f"no session for workspace {command.workspace}",
                remediation="Start a session first.",
            )
        return DetachResponse(thread_id=session.thread_id)

    def _status(self, command: StatusCommand) -> StatusResponse:
        session = self._sessions.get(command.workspace)
        if session is None:
            return StatusResponse(
                thread_id=None,
                state=ExecutionState.READY,
                version=__version__,
            )
        return StatusResponse(
            thread_id=session.thread_id,
            state=session.state.tui_state.critic_gatekeeper_status,
            version=__version__,
        )

    def _shutdown(self) -> ShutdownResponse:
        if self._on_shutdown is not None:
            self._on_shutdown()
        return ShutdownResponse()
