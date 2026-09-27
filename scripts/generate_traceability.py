#!/usr/bin/env python
"""Traceability generator: TDD section -> task -> test, plus graph-node coverage.

Two acceptance checks for V11 phase 10:

* ``--check`` - every TDD V7 section 1-6 maps to **>=1 plan task** and **>=1
  passing test** (plan §14), and every task id it names exists in the plan.
  Exit 1 on **any** gap.
* ``--graph-coverage`` - every node of the compiled SDLC graph (``build_graph``)
  executed **>=1x** across the E2E runs, read from ``reports/graph_trace.json``
  (written by the 10.1/10.2 runs via ``tests.support.graph_trace``). Exit 1 on
  any unexecuted node.

Without a flag the generator regenerates ``docs/traceability.md``.

"Passing test" is structural: the section's mapped test token resolves to an
existing test module that defines >=1 ``test_`` function, and - when a JUnit XML
report is supplied with ``--results`` - that module has no failures/errors in it.
The authoritative pass/fail is the phase's nightly gate (10.D steps 1-2); this
generator is its traceability index, not a substitute for running the suite.

Usage:
    python scripts/generate_traceability.py               # regenerate the doc
    python scripts/generate_traceability.py --check        # gap gate
    python scripts/generate_traceability.py --graph-coverage
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

# Allow `python scripts/generate_traceability.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.check_traceability import TaskTrace, parse_phase

REPO = Path(__file__).resolve().parents[1]
PLAN = REPO / "requirements" / "Dev_Harness_Implementation_Plan_V11_Final.md"
TDD_SPEC = REPO / "requirements" / "Hermes TUI Dev Harness - Detailed Technical Design Specification V7.md"
DOC = REPO / "docs" / "traceability.md"
GRAPH_TRACE = REPO / "reports" / "graph_trace.json"
TESTS = REPO / "tests"

SECTIONS: dict[int, str] = {
    1: "System Architecture & Foundation",
    2: "Hermes TUI Event Loop & UI Subsystem Specifications",
    3: "Universal Critic Gatekeeper & Async Thread Cancellation",
    4: "Persistence, Isolation & Multi-Project State Database",
    5: "Centralized API Rate Limiting Protocol",
    6: "Complete System State Schema (V7)",
}

_TDD_HEADING_RE = re.compile(r"^##\s*\*{0,2}(\d)\.\s*(.+?)\*{0,2}\s*$")
_SEC14_ROW_RE = re.compile(r"^\|\s*§([0-9.]+)\s*\|(.*)\|\s*$")
_TASK_ROW_RE = re.compile(r"^\|\s*(\d+\.\d+[a-z]?)\s*\|")
_TASK_RE = re.compile(r"\d+\.\d+[a-z]?")
_RANGE_RE = re.compile(r"^\s*(\d+\.\d+[a-z]?)\s*[–-]\s*(\d+\.\d+[a-z]?)\s*$")
_BACKTICK_RE = re.compile(r"`([^`]+)`")
_TEST_DEF_RE = re.compile(r"^\s*(?:async\s+)?def test_", re.MULTILINE)


@dataclass
class Requirement:
    """One row of plan §14: a TDD requirement, its tasks and verifying tests."""

    anchor: str
    requirement: str
    tasks: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    test_files: list[str] = field(default_factory=list)
    passing_tests: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)

    @property
    def section(self) -> int:
        """The TDD top-level section this row belongs to (1-6)."""
        return int(self.anchor.split(".")[0])

    @property
    def ok(self) -> bool:
        """True when the row has no unresolved gap (missing/failing test, task)."""
        return not self.gaps


def _cells(line: str) -> list[str]:
    """Split a markdown table row into its cells."""
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def tdd_sections() -> dict[int, str]:
    """TDD V7 sections 1-6 (title read from the spec, falling back to ``SECTIONS``)."""
    found: dict[int, str] = {}
    for line in TDD_SPEC.read_text(encoding="utf-8").splitlines():
        match = _TDD_HEADING_RE.match(line)
        if match is None:
            continue
        number = int(match.group(1))
        if number in SECTIONS and number not in found:
            found[number] = match.group(2).strip()
    return {number: found.get(number, title) for number, title in SECTIONS.items()}


def plan_task_ids() -> set[str]:
    """Every task id defined in the V11 plan's X.A tables."""
    ids: set[str] = set()
    for line in PLAN.read_text(encoding="utf-8").splitlines():
        match = _TASK_ROW_RE.match(line)
        if match is not None:
            ids.add(match.group(1))
    return ids


def plan_traces() -> dict[str, TaskTrace]:
    """Every plan task's trace, keyed by id, via ``check_traceability`` (reused)."""
    phases = {task.split(".")[0] for task in plan_task_ids()}
    traces: dict[str, TaskTrace] = {}
    for phase in sorted(phases):
        for trace in parse_phase(phase):
            traces[trace.task_id] = trace
    return traces


def _expand_tasks(cell: str) -> list[str]:
    """Expand a §14 Tasks cell (ranges and comma lists) into task ids."""
    ids: list[str] = []
    for part in cell.split(","):
        part = part.strip()
        span = _RANGE_RE.match(part)
        if span is not None:
            start, end = span.group(1), span.group(2)
            major_a, minor_a = start.split(".")[0], start.split(".")[1]
            major_b, minor_b = end.split(".")[0], end.split(".")[1]
            if major_a == major_b:
                for offset in range(int(minor_a), int(minor_b) + 1):
                    ids.append(f"{major_a}.{offset}")
                continue
            ids.append(start)
            continue
        if _TASK_RE.fullmatch(part):
            ids.append(part)
    return ids


def _resolve_tests(token: str) -> list[Path]:
    """Resolve a §14 test token to existing test modules under ``tests/``.

    A token may be a repo-relative path, a bare filename, or a glob (e.g.
    ``tests/fixtures/events/*.json``); all three resolve against the repo.
    """
    if "*" in token or "?" in token:
        return sorted(REPO.glob(token))
    if "/" in token:
        candidate = REPO / token
        return [candidate] if candidate.exists() else []
    return sorted(TESTS.rglob(token))


def _has_test_function(path: Path) -> bool:
    """True when ``path`` defines at least one ``test_`` function."""
    try:
        return _TEST_DEF_RE.search(path.read_text(encoding="utf-8")) is not None
    except OSError:
        return False


def _junit_failed_modules(results: Path | None) -> set[str]:
    """Module paths that contain a failure/error, from an optional JUnit XML."""
    if results is None or not results.exists():
        return set()
    failed: set[str] = set()
    root = ElementTree.parse(results).getroot()
    for case in root.iter("testcase"):
        if case.find("failure") is None and case.find("error") is None:
            continue
        classname = case.get("classname", "")
        file_name = case.get("file") or classname.replace(".", "/")
        if file_name:
            failed.add(Path(file_name).name)
    return failed


def _looks_like_file(token: str) -> bool:
    """True when a §14 test token names a file rather than a prose annotation."""
    return "/" in token or token.endswith((".py", ".sh", ".ps1", ".json", ".md"))


def parse_section14(results: Path | None = None) -> list[Requirement]:
    """Parse plan §14 into :class:`Requirement` rows with gap detection."""
    text = PLAN.read_text(encoding="utf-8")
    section = text.split("## 14. Traceability", 1)[1].split("## 15.", 1)[0]
    failed_modules = _junit_failed_modules(results)

    requirements: list[Requirement] = []
    for line in section.splitlines():
        match = _SEC14_ROW_RE.match(line)
        if match is None:
            continue
        cells = _cells(line)
        req = Requirement(
            anchor=match.group(1),
            requirement=cells[1] if len(cells) > 1 else "",
            tasks=_expand_tasks(cells[2] if len(cells) > 2 else ""),
        )
        for token in _BACKTICK_RE.findall(cells[3] if len(cells) > 3 else ""):
            req.tests.append(token)
            resolved = _resolve_tests(token)
            if not resolved:
                if _looks_like_file(token):
                    req.gaps.append(f"test not found: {token}")
                continue
            req.test_files.extend(path.relative_to(REPO).as_posix() for path in resolved)
            if any(_has_test_function(path) for path in resolved):
                req.passing_tests.append(token)
                if Path(token).name in failed_modules:
                    req.gaps.append(f"test reported failing: {token}")

        if not req.tasks:
            req.gaps.append("no task mapped")
        requirements.append(req)
    return requirements


def _static_graph_nodes() -> list[str]:
    """Names of every node in the compiled SDLC graph, minus START/END."""
    from dev_harness.contracts.llm import Message, Usage
    from dev_harness.engine.pipeline import PipelineConfig, build_graph
    from dev_harness.engine.worker_workspace import WorkerWorkspace

    class _NullClient:
        async def complete(
            self, messages: list[Message], *, model: str | None = None
        ) -> tuple[str, Usage]:
            return "{}", Usage(input_tokens=0, output_tokens=0)

    with tempfile.TemporaryDirectory() as tmp:
        config = PipelineConfig(
            client=_NullClient(), workspace=WorkerWorkspace(Path(tmp)), chunks=[]
        )
        graph: Any = build_graph(config)
        names = graph.get_graph().nodes
    return sorted(name for name in names if name not in ("__start__", "__end__"))


def _executed_graph_nodes() -> list[str]:
    """Node names recorded across the E2E runs (empty when no trace exists)."""
    if not GRAPH_TRACE.exists():
        return []
    import json

    data = json.loads(GRAPH_TRACE.read_text(encoding="utf-8"))
    return sorted(set(data.get("executed_nodes", [])))


def check_graph_coverage() -> list[str]:
    """Return the nodes that are in the compiled graph but never executed."""
    return sorted(set(_static_graph_nodes()) - set(_executed_graph_nodes()))


def _render_doc(
    requirements: list[Requirement], executed: list[str], static: list[str]
) -> str:
    """Render ``docs/traceability.md`` from the parsed rows and trace."""
    lines = [
        "# Traceability: TDD Section -> Task -> Test",
        "",
        "> Generated by `scripts/generate_traceability.py` - do not hand-edit.",
        "> Source: `requirements/Dev_Harness_Implementation_Plan_V11_Final.md` §14,",
        "> the V7 TDD spec §1-6, and `reports/graph_trace.json`.",
        "",
        "## Section coverage",
        "",
        "| TDD § | Section | Requirements | Tasks | Tests | Status |",
        "| :--- | :--- | :--- | :--- | :--- | :--- |",
    ]
    for number, title in tdd_sections().items():
        rows = [req for req in requirements if req.section == number]
        tasks = sorted({task for req in rows for task in req.tasks})
        tests = sorted({token for req in rows for token in req.tests})
        complete = bool(tasks) and all(req.ok for req in rows)
        status = "OK" if complete else "GAP"
        lines.append(
            f"| §{number} | {title} | {len(rows)} | "
            f"{len(tasks)} | {len(tests)} | {status} |"
        )

    lines += ["", "## Detail", ""]
    for number, title in tdd_sections().items():
        lines += [f"### §{number} - {title}", ""]
        lines += [
            "| Req | Requirement | Tasks | Verifying tests | Status |",
            "| :--- | :--- | :--- | :--- | :--- |",
        ]
        for req in [r for r in requirements if r.section == number]:
            tasks = ", ".join(req.tasks)
            tests = ", ".join(f"`{token}`" for token in req.tests)
            status = "OK" if req.ok else f"GAP: {'; '.join(req.gaps)}"
            lines.append(
                f"| §{req.anchor} | {req.requirement} | {tasks} | {tests} | {status} |"
            )
        lines.append("")

    lines += [
        "## Graph-node coverage (10.C)",
        "",
        "Every node of the compiled SDLC graph must execute at least once across",
        "the 10.1 + 10.2 E2E runs (`python scripts/generate_traceability.py --graph-coverage`).",
        "",
        "| Node | Executed |",
        "| :--- | :--- |",
    ]
    executed_set = set(executed)
    for node in static:
        lines.append(f"| `{node}` | {'yes' if node in executed_set else 'NO'} |")
    lines += [
        "",
        f"Executed nodes: {', '.join(f'`{node}`' for node in executed) or '_(none recorded)_'}",
        "",
        "## Regenerating",
        "",
        "```sh",
        "python scripts/generate_traceability.py            # rewrite this file",
        "python scripts/generate_traceability.py --check     # exit 1 on any gap",
        "python scripts/generate_traceability.py --graph-coverage",
        "```",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(prog="generate_traceability")
    parser.add_argument(
        "--check", action="store_true", help="exit 1 on any TDD-section gap"
    )
    parser.add_argument(
        "--graph-coverage",
        action="store_true",
        help="exit 1 if any compiled graph node never executed",
    )
    parser.add_argument(
        "--results",
        type=Path,
        default=None,
        help="optional JUnit XML to upgrade the passing-test check to true pass/fail",
    )
    args = parser.parse_args(argv)

    if args.graph_coverage:
        static = _static_graph_nodes()
        executed = _executed_graph_nodes()
        missing = sorted(set(static) - set(executed))
        for node in static:
            print(f"[{'OK ' if node in set(executed) else 'GAP'}] {node}")
        if missing:
            print(
                f"\ngraph-node coverage FAIL: {len(missing)} unexecuted node(s): "
                f"{', '.join(missing)}",
                file=sys.stderr,
            )
            return 1
        print(f"\ngraph-node coverage OK: {len(static)} nodes all executed")
        return 0

    requirements = parse_section14(args.results)
    traces = plan_traces() if args.check else {}
    plan_ids = set(traces) or plan_task_ids()

    if args.check:
        gaps: list[str] = []
        for number in tdd_sections():
            rows = [req for req in requirements if req.section == number]
            tasks = sorted({task for req in rows for task in req.tasks})
            tests = sorted({token for req in rows for token in req.passing_tests})
            if not rows:
                gaps.append(f"TDD section {number} has no §14 requirement rows")
            if not tasks:
                gaps.append(f"TDD section {number} maps to no task")
            if not tests:
                gaps.append(f"TDD section {number} maps to no passing test")
            for task in tasks:
                matched = [
                    pid
                    for pid in plan_ids
                    if pid == task or pid.startswith(f"{task}.")
                ]
                if not matched:
                    gaps.append(
                        f"TDD section {number}: task '{task}' is not in the plan"
                    )
                    continue
                for pid in matched:
                    trace = traces.get(pid)
                    if trace is not None and not trace.ok:
                        gaps.append(
                            f"TDD section {number}: task '{pid}' missing "
                            f"{'; '.join(trace.missing)}"
                        )
        for req in requirements:
            for gap in req.gaps:
                gaps.append(f"§{req.anchor}: {gap}")
        if gaps:
            for gap in gaps:
                print(f"[GAP] {gap}", file=sys.stderr)
            print(
                f"\ntraceability FAIL: {len(gaps)} gap(s) across TDD sections 1-6",
                file=sys.stderr,
            )
            return 1
        print("traceability OK: TDD sections 1-6 each map to >=1 task and >=1 test")
        return 0

    DOC.parent.mkdir(parents=True, exist_ok=True)
    DOC.write_text(
        _render_doc(requirements, _executed_graph_nodes(), _static_graph_nodes()),
        encoding="utf-8",
    )
    print(f"wrote {DOC.relative_to(REPO).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
