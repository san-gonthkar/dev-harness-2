"""Framing codec: 4-byte BE length prefix + UTF-8 JSON (V11 2.2).

Per-type max frame sizes (ADR-0003): 1 MiB for streaming/control types,
16 MiB for SNAPSHOT. The limit is enforced on the encoded frame length.
"""

from __future__ import annotations

import struct
from typing import BinaryIO

from dev_harness.contracts.enums import EventType
from dev_harness.contracts.errors import HarnessError
from dev_harness.contracts.events import Envelope

# 4-byte big-endian unsigned length prefix.
_PREFIX = struct.Struct(">I")
PREFIX_LEN = _PREFIX.size  # 4

# Per-type max frame sizes (ADR-0003).
MAX_STREAM_FRAME = 1 * 1024 * 1024  # 1 MiB
MAX_SNAPSHOT_FRAME = 16 * 1024 * 1024  # 16 MiB
# Absolute ceiling: no frame may exceed this, regardless of type.
GLOBAL_MAX_FRAME = MAX_SNAPSHOT_FRAME


class FrameError(HarnessError):
    """Base class for framing errors."""


class FrameTooLargeError(FrameError):
    """A frame exceeds its type's maximum size."""


class IncompleteFrameError(FrameError):
    """The stream ended before a full frame was read."""


def max_frame_for(event_type: EventType) -> int:
    """The maximum encoded frame size for an event type (ADR-0003)."""
    if event_type == EventType.SNAPSHOT:
        return MAX_SNAPSHOT_FRAME
    return MAX_STREAM_FRAME


def encode(envelope: Envelope) -> bytes:
    """Encode an envelope as a length-prefixed JSON frame.

    Raises FrameTooLargeError if the encoded frame exceeds the type's limit.
    """
    body = envelope.model_dump_json().encode("utf-8")
    frame = _PREFIX.pack(len(body)) + body
    limit = max_frame_for(envelope.type)
    if len(frame) > limit:
        raise FrameTooLargeError(
            f"frame of {len(frame)} bytes exceeds {limit} for {envelope.type.value}",
            remediation="Reduce the payload size or use a SNAPSHOT frame for large state.",
        )
    return frame


def decode_frame(data: bytes) -> Envelope:
    """Decode a single complete frame (prefix + body) into an Envelope.

    Raises FrameTooLargeError if the body exceeds the global ceiling, and
    IncompleteFrameError if the data is shorter than the declared length.
    """
    if len(data) < PREFIX_LEN:
        raise IncompleteFrameError(
            f"frame shorter than {PREFIX_LEN}-byte prefix",
            remediation="Read a full frame before decoding.",
        )
    (length,) = _PREFIX.unpack_from(data)
    if length > GLOBAL_MAX_FRAME:
        raise FrameTooLargeError(
            f"frame body of {length} bytes exceeds global ceiling {GLOBAL_MAX_FRAME}",
            remediation="Reject the frame; the peer sent an oversized body.",
        )
    if len(data) < PREFIX_LEN + length:
        raise IncompleteFrameError(
            f"declared {length} body bytes but only {len(data) - PREFIX_LEN} available",
            remediation="Read the full frame before decoding.",
        )
    body = data[PREFIX_LEN : PREFIX_LEN + length]
    return Envelope.model_validate_json(body)


def read_frame(stream: BinaryIO) -> Envelope:
    """Read one frame from a binary stream, blocking until complete.

    Raises IncompleteFrameError on EOF mid-frame and FrameTooLargeError when
    the declared length exceeds the global ceiling.
    """
    prefix = stream.read(PREFIX_LEN)
    if not prefix:
        raise IncompleteFrameError(
            "EOF before any frame",
            remediation="The peer closed the connection.",
        )
    if len(prefix) < PREFIX_LEN:
        raise IncompleteFrameError(
            "EOF inside the length prefix",
            remediation="The peer closed mid-prefix.",
        )
    (length,) = _PREFIX.unpack(prefix)
    if length > GLOBAL_MAX_FRAME:
        raise FrameTooLargeError(
            f"declared body of {length} bytes exceeds global ceiling {GLOBAL_MAX_FRAME}",
            remediation="Reject the frame; the peer sent an oversized body.",
        )
    body = stream.read(length)
    if len(body) < length:
        raise IncompleteFrameError(
            f"EOF inside frame body: {len(body)} of {length} bytes",
            remediation="The peer closed mid-frame.",
        )
    return Envelope.model_validate_json(body)
