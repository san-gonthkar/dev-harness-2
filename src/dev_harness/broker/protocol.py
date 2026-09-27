"""Broker wire protocol: length-prefixed JSON messages (V11 4.8).

The broker speaks its own request/response protocol (HEALTH, RESERVE,
COMMIT, RELEASE, METRICS) over the host-scoped socket. This is separate
from the engine/TUI event vocabulary (EventType, 9 members) — the broker
is a host-scoped daemon, not an engine peer.
"""

from __future__ import annotations

import struct
from typing import Any, BinaryIO

from pydantic import BaseModel, ConfigDict

_PREFIX = struct.Struct(">I")
PREFIX_LEN = _PREFIX.size
MAX_BROKER_FRAME = 1 * 1024 * 1024  # 1 MiB


class BrokerMessage(BaseModel):
    """A broker request or reply."""

    model_config = ConfigDict(extra="forbid")

    op: str  # HEALTH | RESERVE | COMMIT | RELEASE | METRICS
    ok: bool = True
    data: dict[str, Any] = {}


def encode(msg: BrokerMessage) -> bytes:
    """Encode a broker message as a length-prefixed JSON frame."""
    body = msg.model_dump_json().encode("utf-8")
    frame = _PREFIX.pack(len(body)) + body
    if len(frame) > MAX_BROKER_FRAME:
        raise ValueError(
            f"broker frame of {len(frame)} bytes exceeds {MAX_BROKER_FRAME}"
        )
    return frame


def decode_frame(data: bytes) -> BrokerMessage:
    """Decode a single complete frame (prefix + body)."""
    if len(data) < PREFIX_LEN:
        raise ValueError("frame shorter than 4-byte prefix")
    (length,) = _PREFIX.unpack_from(data)
    if length > MAX_BROKER_FRAME:
        raise ValueError(f"frame body of {length} bytes exceeds ceiling")
    if len(data) < PREFIX_LEN + length:
        raise ValueError(
            f"declared {length} body bytes but only {len(data) - PREFIX_LEN} available"
        )
    body = data[PREFIX_LEN : PREFIX_LEN + length]
    return BrokerMessage.model_validate_json(body)


def _read_exactly(stream: BinaryIO, n: int) -> bytes:
    """Read exactly ``n`` bytes from a file-like object or a socket.

    Sockets expose ``recv`` (which may return short reads); file-like objects
    expose ``read``. Both are supported so the same framing works over a
    ``socket.socket`` and over an in-memory buffer.
    """
    recv = getattr(stream, "recv", None)
    if recv is not None:
        chunks: list[bytes] = []
        remaining = n
        while remaining > 0:
            chunk = recv(remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)
    return stream.read(n)


def read_frame(stream: BinaryIO) -> BrokerMessage:
    """Read one frame from a binary stream, blocking until complete."""
    prefix = _read_exactly(stream, PREFIX_LEN)
    if not prefix:
        raise ValueError("EOF before any frame")
    if len(prefix) < PREFIX_LEN:
        raise ValueError("EOF inside the length prefix")
    (length,) = _PREFIX.unpack(prefix)
    if length > MAX_BROKER_FRAME:
        raise ValueError(f"declared body of {length} bytes exceeds ceiling")
    body = _read_exactly(stream, length)
    if len(body) < length:
        raise ValueError(f"EOF inside frame body: {len(body)} of {length} bytes")
    return BrokerMessage.model_validate_json(body)
