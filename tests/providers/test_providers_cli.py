"""Providers CLI tests (V11 3.12)."""

from __future__ import annotations

import sys

import pytest

from dev_harness.providers import cli
from dev_harness.providers.errors import fault_table

pytestmark = pytest.mark.unit


def test_fault_drill_matches_errors_table(capsys: pytest.CaptureFixture[str]) -> None:
    codes = ",".join(str(row["code"]) for row in fault_table())
    rc = cli._fault_drill(codes)
    assert rc == 0
    out = capsys.readouterr().out
    for row in fault_table():
        assert row["error"] in out


def test_count_sample(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli._count("qwen2.5-coder:7b", None)
    assert rc == 0
    assert "estimate=" in capsys.readouterr().out


def test_count_file(
    tmp_path: pytest.TempPathFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    import pathlib

    f = pathlib.Path(tmp_path) / "sample.txt"
    f.write_text("hello world", encoding="utf-8")
    rc = cli._count("qwen2.5-coder:7b", str(f))
    assert rc == 0
    assert "chars=11" in capsys.readouterr().out


def test_swap_drill(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli._swap_drill("qwen2.5-coder:7b,llama3:8b", 6, True)
    assert rc == 0
    out = capsys.readouterr().out
    assert "load events" in out


def test_build_parser_subcommands() -> None:
    parser = cli.build_parser()
    for name in ("complete", "stream", "fault-drill", "count", "swap-drill"):
        with pytest.raises(SystemExit):
            parser.parse_args([name, "--help"])


def test_main_complete_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_complete(provider: str, prompt: str, fake: bool) -> int:
        return 0

    monkeypatch.setattr(cli, "_complete", fake_complete)
    monkeypatch.setattr(
        sys,
        "argv",
        ["cli", "complete", "--provider", "anthropic", "--prompt", "ping", "--fake"],
    )
    assert cli.main() == 0


def test_main_fault_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["cli", "fault-drill", "--codes", "429"])
    assert cli.main() == 0


def test_main_count_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["cli", "count", "--model", "qwen2.5-coder:7b"])
    assert cli.main() == 0


def test_main_swap_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys, "argv", ["cli", "swap-drill", "--models", "a,b", "--requests", "4"]
    )
    assert cli.main() == 0


def test_main_unknown_command(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeArgs:
        command = "bogus"

    class FakeParser:
        def parse_args(self) -> FakeArgs:
            return FakeArgs()

    monkeypatch.setattr(cli, "build_parser", lambda: FakeParser())
    assert cli.main() == 2
