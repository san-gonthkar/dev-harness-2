"""Engine CLI tests (V11 8.20).

Covers the 8.B parity row exactly: ``run``/``plan``/``critic-drill`` complete
against ``--mock`` (exit 0) and ``plan --print-dag`` emits a topologically valid
order. ``main()`` is invoked in-process for speed; one subprocess ``python -m``
check proves the module entry point wires up. No network, no ``time.sleep``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from dev_harness.contracts.errors import CyclicDependencyError
from dev_harness.engine.cli import (
    EXIT_ERROR,
    EXIT_OK,
    critic_drill,
    main,
    parse_chunks,
)
from tests.support.workspace import make_workspace

_SIMPLE = "# A simple requirement\nAdd one to a number.\n"
_DIAMOND = "c1: Base\nc2: Left deps: c1\nc3: Right deps: c1\nc4: Join deps: c2, c3\n"
_CYCLE = "c1: A deps: c2\nc2: B deps: c1\n"


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# -- parse_chunks (unit) ----------------------------------------------------


@pytest.mark.unit
def test_parse_chunks_auto_numbers_plain_lines() -> None:
    """A free-text requirement becomes one auto-numbered chunk per line."""
    chunks = parse_chunks("first line\n\nsecond line\n")
    assert [c.chunk_id for c in chunks] == ["c1", "c2"]
    assert chunks[0].title == "first line"
    assert chunks[1].dependencies == []


@pytest.mark.unit
def test_parse_chunks_reads_ids_and_dependencies() -> None:
    """Structured lines carry their id, title and dependency list."""
    chunks = parse_chunks(_DIAMOND)
    assert [c.chunk_id for c in chunks] == ["c1", "c2", "c3", "c4"]
    assert chunks[3].dependencies == ["c2", "c3"]


# -- plan (unit / negative) -------------------------------------------------


@pytest.mark.unit
def test_plan_print_dag_is_topologically_valid(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``plan --print-dag`` exits 0 and every dependency precedes its chunk."""
    req = _write(tmp_path, "req_diamond.md", _DIAMOND)
    code = main(["plan", "--requirement", str(req), "--mock", "--print-dag"])
    assert code == EXIT_OK

    order = capsys.readouterr().out.split()
    assert sorted(order) == ["c1", "c2", "c3", "c4"]
    chunks = {c.chunk_id: c for c in parse_chunks(_DIAMOND)}
    for index, chunk_id in enumerate(order):
        for dep in chunks[chunk_id].dependencies:
            assert order.index(dep) < index


@pytest.mark.negative
def test_plan_rejects_a_cycle_naming_both_ids(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A cyclic requirement exits 2 and names both ids in the cycle."""
    req = _write(tmp_path, "req_cycle.md", _CYCLE)
    code = main(["plan", "--requirement", str(req), "--mock", "--print-dag"])
    assert code == EXIT_ERROR
    err = capsys.readouterr().err
    assert "CyclicDependencyError" in err
    assert "c1" in err and "c2" in err


@pytest.mark.negative
def test_plan_without_mock_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Omitting ``--mock`` fails closed instead of reaching a live provider."""
    req = _write(tmp_path, "req_simple.md", _SIMPLE)
    code = main(["plan", "--requirement", str(req), "--print-dag"])
    assert code == EXIT_ERROR
    assert "mock" in capsys.readouterr().err.lower()


@pytest.mark.unit
def test_chunk_dag_raises_cyclic_before_cli(tmp_path: Path) -> None:
    """The parser + ChunkDAG surface ``CyclicDependencyError`` directly."""
    from dev_harness.engine.dag import ChunkDAG

    with pytest.raises(CyclicDependencyError):
        ChunkDAG(parse_chunks(_CYCLE))


# -- critic-drill (integration) ---------------------------------------------


@pytest.mark.integration
def test_critic_drill_surfaces_scope_violation() -> None:
    """The drill reports a CriticScopeViolation and a tui_state-only diff."""
    from dev_harness.engine.cli import _mock_persona_client

    result = critic_drill(_mock_persona_client())
    assert "CriticScopeViolation" in result.violation
    assert result.diff == {"tui_state"}


@pytest.mark.integration
def test_critic_drill_command_completes(capsys: pytest.CaptureFixture[str]) -> None:
    """``critic-drill --mock`` exits 0 and prints its evidence."""
    code = main(["critic-drill", "--mock"])
    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert "CriticScopeViolation" in out


# -- run (integration) ------------------------------------------------------


@pytest.mark.integration
def test_run_completes_against_mock(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``run --mock`` reaches exit 0 with every chunk COMPLETED."""
    repo = make_workspace(tmp_path, files={".gitkeep": ""})
    req = _write(tmp_path, "req_simple.md", _SIMPLE)
    code = main(
        [
            "run",
            "--requirement",
            str(req),
            "--workspace",
            str(repo),
            "--max-parallel",
            "1",
            "--mock",
        ]
    )
    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert "c1: COMPLETED" in out


@pytest.mark.integration
def test_run_with_trace_writes_trace_file(tmp_path: Path) -> None:
    """``run --trace`` persists the collected envelopes under reports/."""
    repo = make_workspace(tmp_path, files={".gitkeep": ""})
    req = _write(tmp_path, "req_simple.md", _SIMPLE)
    code = main(
        [
            "run",
            "--requirement",
            str(req),
            "--workspace",
            str(repo),
            "--mock",
            "--trace",
        ]
    )
    assert code == EXIT_OK
    assert (repo / "reports" / "parallel_trace.json").is_file()


# -- module entry point (integration) ---------------------------------------


@pytest.mark.integration
def test_module_entry_point_runs(tmp_path: Path) -> None:
    """``python -m dev_harness.engine.cli plan`` works as a subprocess."""
    repo_root = Path(__file__).resolve().parents[2]
    req = _write(tmp_path, "req_diamond.md", _DIAMOND)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "dev_harness.engine.cli",
            "plan",
            "--requirement",
            str(req),
            "--mock",
            "--print-dag",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == EXIT_OK, proc.stdout + proc.stderr
    assert proc.stdout.split() == ["c1", "c2", "c3", "c4"]
