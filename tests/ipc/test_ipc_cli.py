"""IPC CLI tests (V11 2.10)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dev_harness.contracts.enums import EventType
from dev_harness.contracts.events import Envelope
from dev_harness.ipc import cli

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "events"


def test_coverage_check_all_exercised(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.cmd_coverage_check("/tmp/w", str(FIXTURES))
    assert rc == 0
    assert "9/9" in capsys.readouterr().out


def test_coverage_check_missing_fixture(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    rc = cli.cmd_coverage_check("/tmp/w", str(empty))
    assert rc == 1
    assert "missing fixture" in capsys.readouterr().err


def test_coverage_check_unexercised_type(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A fixture dir with a corrupted type exits 1."""
    partial = tmp_path / "partial"
    partial.mkdir()
    for event_type in EventType:
        src = FIXTURES / f"{event_type.value.lower()}.json"
        (partial / src.name).write_text(
            src.read_text(encoding="utf-8"), encoding="utf-8"
        )
    # Remove one fixture so its type is unexercised.
    (partial / "snapshot.json").unlink()
    rc = cli.cmd_coverage_check("/tmp/w", str(partial))
    assert rc == 1
    assert "missing fixture" in capsys.readouterr().err


def test_flood_reports_drops(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.cmd_flood(10000)
    assert rc == 0
    out = capsys.readouterr().out
    assert "token drops" in out
    assert "control drops" in out


def test_flood_small_count(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.cmd_flood(10)
    assert rc == 0


def test_send_all_round_trip(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """send-all round-trips all 9 fixtures through a fake echo client."""
    from dev_harness.ipc import client as client_mod

    sent: list[Envelope] = []

    class FakeConn:
        def __init__(self) -> None:
            self._buf = b""

        def sendall(self, data: bytes) -> None:
            from dev_harness.ipc.framing import decode_frame

            sent.append(decode_frame(data))

        def recv(self, n: int) -> bytes:
            return b""

        def close(self) -> None:
            pass

    class FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self._conn = FakeConn()

        def send(self, env: Envelope) -> None:
            from dev_harness.ipc.framing import encode

            self._conn.sendall(encode(env))

        def close(self) -> None:
            pass

    monkeypatch.setattr(client_mod, "IpcClient", FakeClient)
    # Patch framing.read_frame to echo back the sent envelope.
    from dev_harness.ipc import framing

    def fake_read_frame(conn: object) -> Envelope:
        return sent[-1]

    monkeypatch.setattr(framing, "read_frame", fake_read_frame)
    rc = cli.cmd_send_all("/tmp/w", str(FIXTURES))
    assert rc == 0
    assert "0 mismatches" in capsys.readouterr().out
    assert len(sent) == 9


def test_build_parser_subcommands() -> None:
    parser = cli.build_parser()
    for name in ("send-all", "coverage-check", "flood"):
        with pytest.raises(SystemExit):
            parser.parse_args([name, "--help"])
