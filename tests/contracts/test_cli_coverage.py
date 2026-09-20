"""In-process CLI coverage for schema.py and transitions.py (V11 0.7/0.22)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from dev_harness.contracts import schema, transitions

pytestmark = pytest.mark.unit


class TestSchemaCli:
    def test_emit_writes_schema(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "schema.json"
        # write_schema's default arg is bound at def time, so call it directly
        # with the target path (covers the write path).
        schema.write_schema(target)
        assert target.exists()
        # And the CLI --emit path writes to the committed location.
        monkeypatch.setattr(sys, "argv", ["schema", "--emit"])
        assert schema.main() == 0

    def test_check_matches(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["schema", "--check"])
        assert schema.main() == 0
        assert "matches" in capsys.readouterr().out

    def test_check_missing_returns_1(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        missing = tmp_path / "nope.json"
        monkeypatch.setattr(schema, "SCHEMA_PATH", missing)
        monkeypatch.setattr(sys, "argv", ["schema", "--check"])
        assert schema.main() == 1
        assert "missing" in capsys.readouterr().err

    def test_check_drift_returns_1(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        drifted = tmp_path / "drifted.json"
        drifted.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(schema, "SCHEMA_PATH", drifted)
        monkeypatch.setattr(sys, "argv", ["schema", "--check"])
        assert schema.main() == 1
        assert "drift" in capsys.readouterr().err

    def test_no_args_returns_2(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["schema"])
        assert schema.main() == 2
        assert "usage" in capsys.readouterr().out


class TestTransitionsCli:
    def test_table_flag_returns_0(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["transitions", "--table"])
        assert transitions.main() == 0
        out = capsys.readouterr().out
        assert (
            "READY" in out and "RUNNING" in out and "PAUSED" in out and "STOPPED" in out
        )

    def test_no_args_returns_2(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["transitions"])
        assert transitions.main() == 2
        assert "usage" in capsys.readouterr().out

    def test_transition_table_shape(self) -> None:
        table = transitions.transition_table()
        assert set(table) == {"READY", "RUNNING", "PAUSED", "STOPPED"}
        for cmds in table.values():
            assert set(cmds) == {"START", "PAUSE", "RESUME", "STOP"}
