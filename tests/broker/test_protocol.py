"""Broker wire protocol tests (V11 4.8)."""

from __future__ import annotations

import pytest

from dev_harness.broker.protocol import BrokerMessage, decode_frame, encode, read_frame


class FakeStream:
    """A binary stream backed by bytes."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self, n: int) -> bytes:
        out = self._data[:n]
        self._data = self._data[n:]
        return out


@pytest.mark.unit
def test_encode_decode_roundtrip() -> None:
    msg = BrokerMessage(op="RESERVE", data={"provider": "anthropic", "tokens": 1.0})
    frame = encode(msg)
    decoded = decode_frame(frame)
    assert decoded.op == "RESERVE"
    assert decoded.data["provider"] == "anthropic"


@pytest.mark.unit
def test_read_frame_from_stream() -> None:
    msg = BrokerMessage(op="HEALTH", data={"status": "ok"})
    stream = FakeStream(encode(msg))
    decoded = read_frame(stream)
    assert decoded.op == "HEALTH"
    assert decoded.data["status"] == "ok"


@pytest.mark.unit
def test_short_frame_raises() -> None:
    with pytest.raises(ValueError):
        decode_frame(b"\x00")


@pytest.mark.unit
def test_oversized_frame_raises() -> None:
    import struct

    frame = struct.pack(">I", 2 * 1024 * 1024) + b"x"
    with pytest.raises(ValueError):
        decode_frame(frame)


@pytest.mark.unit
def test_truncated_body_raises() -> None:
    import struct

    frame = struct.pack(">I", 100) + b"short"
    with pytest.raises(ValueError):
        decode_frame(frame)


@pytest.mark.unit
def test_eof_before_frame_raises() -> None:
    with pytest.raises(ValueError):
        read_frame(FakeStream(b""))
