"""Final IPC branch completion tests."""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

from dev_harness.contracts.enums import EventType
from dev_harness.contracts.events import (
    AgentTokenStreamPayload,
    Envelope,
    FileChangePayload,
)
from dev_harness.ipc import cli
from dev_harness.ipc.framing import GLOBAL_MAX_FRAME, FrameTooLargeError, decode_frame
from dev_harness.ipc.queue import BackpressureQueue

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "events"


def _env() -> Envelope:
    return Envelope(
        type=EventType.FILE_CHANGE,
        payload=FileChangePayload(
            type="FILE_CHANGE", path="/a", change_type="modified"
        ),
    )


def _token(seq: int) -> Envelope:
    return Envelope(
        type=EventType.AGENT_TOKEN_STREAM,
        payload=AgentTokenStreamPayload(type="AGENT_TOKEN_STREAM", seq=seq, token="x"),
    )


class TestCliBranches:
    def test_send_all_mismatch(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A mismatched echo reply exits 1."""
        from dev_harness.ipc import client as client_mod
        from dev_harness.ipc import framing

        class FakeConn:
            def __init__(self) -> None:
                self._buf = b""

            def sendall(self, data: bytes) -> None:
                pass

            def recv(self, n: int) -> bytes:
                return b""

            def read(self, n: int) -> bytes:
                return b""

            def close(self) -> None:
                pass

        class FakeClient:
            def __init__(self, *args: object, **kwargs: object) -> None:
                self._conn = FakeConn()

            def send(self, env: Envelope) -> None:
                pass

            def close(self) -> None:
                pass

        monkeypatch.setattr(client_mod, "IpcClient", FakeClient)
        # Echo a DIFFERENT envelope to force a mismatch.
        wrong = _env()
        wrong.payload.path = "/different"  # type: ignore[attr-defined]

        def fake_read_frame(conn: object) -> Envelope:
            return wrong

        monkeypatch.setattr(framing, "read_frame", fake_read_frame)
        rc = cli.cmd_send_all("/tmp/w", str(FIXTURES))
        assert rc == 1
        assert "mismatches" in capsys.readouterr().err

    def test_coverage_check_unexercised(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A fixture dir with a wrong-type file exits 1 with 'unexercised'."""
        partial = tmp_path / "partial"
        partial.mkdir()
        for event_type in EventType:
            src = FIXTURES / f"{event_type.value.lower()}.json"
            (partial / src.name).write_text(
                src.read_text(encoding="utf-8"), encoding="utf-8"
            )
        # Replace snapshot.json content with a FILE_CHANGE envelope.

        fc = _env()
        (partial / "snapshot.json").write_text(fc.model_dump_json(), encoding="utf-8")
        rc = cli.cmd_coverage_check("/tmp/w", str(partial))
        assert rc == 1
        assert "unexercised" in capsys.readouterr().err

    def test_flood_control_drop_returns_1(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A control drop makes flood exit 1."""

        class FullQueue:
            def __init__(self) -> None:
                self.dropped_frames = 0

            def put(self, env: Envelope) -> bool:
                return False  # always full

        monkeypatch.setattr(cli, "BackpressureQueue", FullQueue)
        rc = cli.cmd_flood(10)
        assert rc == 1
        assert "control drops" in capsys.readouterr().out

    def test_main_send_all_dispatch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from dev_harness.ipc import cli as cli_mod

        monkeypatch.setattr(cli_mod, "cmd_send_all", lambda ws, fx: 0)
        monkeypatch.setattr(sys, "argv", ["cli", "send-all", "--workspace", "/tmp/w"])
        assert cli.main() == 0

    def test_main_coverage_dispatch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from dev_harness.ipc import cli as cli_mod

        monkeypatch.setattr(cli_mod, "cmd_coverage_check", lambda ws, fx: 0)
        monkeypatch.setattr(
            sys, "argv", ["cli", "coverage-check", "--workspace", "/tmp/w"]
        )
        assert cli.main() == 0


class TestQueueBranches:
    def test_control_full_no_droppable_returns_false(self) -> None:
        """A control event when full of control events returns False."""
        q = BackpressureQueue(capacity=2)
        q.put(_env())
        q.put(_env())
        assert q.put(_env()) is False  # no droppable to evict


class TestFramingBranches:
    def test_decode_oversized_body(self) -> None:
        """decode_frame rejects a body exceeding the global ceiling."""
        prefix = struct.pack(">I", GLOBAL_MAX_FRAME + 1)
        with pytest.raises(FrameTooLargeError):
            decode_frame(prefix + b"x" * 10)
