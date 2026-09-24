"""Differential test selector tests (V11 8.12).

Validation matrix (8.B, acceptance is exact):

(a) given changed source files and a dependency graph, the selector returns the
    EXACT dependent test set - asserted with set equality, not a superset;
(b) when the dependency graph is UNAVAILABLE, it falls back to the FULL suite
    and LOGS the reason - asserted on the result and on the captured log record.

The graph is injected, so the tests supply a small synthetic graph and simulate
an unavailable graph (``None`` and a raising loader). One test builds the graph
from real files on disk via the ``ast`` analyzer. No network; no ``time.sleep``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from dev_harness.engine.testing.differential import (
    SelectionResult,
    build_dependency_graph,
    make_loader,
    path_to_module,
    select_tests,
)

# A synthetic graph: test node id -> the modules it transitively imports.
GRAPH: dict[str, frozenset[str]] = {
    "tests/engine/test_tester.py": frozenset(
        {"dev_harness.engine.nodes.tester", "dev_harness.contracts.state"}
    ),
    "tests/engine/test_dag.py": frozenset({"dev_harness.engine.dag"}),
    "tests/storage/test_store.py": frozenset({"dev_harness.storage.store"}),
    "tests/test_config.py": frozenset({"dev_harness.config"}),
}

ALL_TESTS: tuple[str, ...] = tuple(sorted(GRAPH))


def _loader(
    graph: dict[str, frozenset[str]] | None,
) -> Callable[[], Mapping[str, frozenset[str]] | None]:
    """A loader returning a fixed graph (or ``None`` for unavailable)."""

    def load() -> dict[str, frozenset[str]] | None:
        return graph

    return load


@pytest.mark.unit
def test_selects_exact_dependent_tests_for_changed_module() -> None:
    """A changed module selects exactly the tests that import it (set equality)."""
    result = select_tests(
        ["src/dev_harness/engine/dag.py"],
        loader=_loader(GRAPH),
        all_tests=ALL_TESTS,
    )
    assert isinstance(result, SelectionResult)
    assert set(result.tests) == {"tests/engine/test_dag.py"}
    assert result.full_suite is False
    assert result.reason is None


@pytest.mark.unit
def test_selects_union_for_multiple_changed_modules() -> None:
    """Several changed modules select the union of their dependents, exactly."""
    result = select_tests(
        ["src/dev_harness/engine/dag.py", "src/dev_harness/storage/store.py"],
        loader=_loader(GRAPH),
        all_tests=ALL_TESTS,
    )
    assert set(result.tests) == {
        "tests/engine/test_dag.py",
        "tests/storage/test_store.py",
    }
    assert result.full_suite is False


@pytest.mark.unit
def test_no_dependents_selects_empty_set() -> None:
    """A changed module no test imports selects nothing (not the full suite)."""
    result = select_tests(
        ["src/dev_harness/engine/unrelated.py"],
        loader=_loader(GRAPH),
        all_tests=ALL_TESTS,
    )
    assert result.tests == ()
    assert result.full_suite is False


@pytest.mark.negative
def test_unavailable_graph_falls_back_to_full_suite_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unavailable graph runs the FULL suite and records the reason."""
    with caplog.at_level(logging.WARNING):
        result = select_tests(
            ["src/dev_harness/engine/dag.py"],
            loader=_loader(None),
            all_tests=ALL_TESTS,
        )
    assert result.full_suite is True
    assert set(result.tests) == set(ALL_TESTS)
    assert result.reason is not None
    assert "unavailable" in result.reason
    assert any("unavailable" in record.message for record in caplog.records)


@pytest.mark.negative
def test_raising_loader_is_treated_as_unavailable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A loader that raises is unavailable: full suite + logged reason."""

    def boom() -> dict[str, frozenset[str]] | None:
        raise RuntimeError("graph build failed")

    with caplog.at_level(logging.WARNING):
        result = select_tests(
            ["src/dev_harness/engine/dag.py"], loader=boom, all_tests=ALL_TESTS
        )
    assert result.full_suite is True
    assert set(result.tests) == set(ALL_TESTS)
    assert result.reason is not None
    assert "RuntimeError" in result.reason
    assert any("unavailable" in record.message for record in caplog.records)


@pytest.mark.unit
def test_path_to_module_normalizes_src_and_init() -> None:
    """Paths normalize to dotted modules, dropping ``src/`` and ``__init__``."""
    assert path_to_module("src/dev_harness/engine/dag.py") == "dev_harness.engine.dag"
    assert path_to_module("src/dev_harness/engine/__init__.py") == "dev_harness.engine"
    assert path_to_module(Path("dev_harness/config.py")) == "dev_harness.config"


@pytest.mark.unit
def test_build_dependency_graph_follows_transitive_imports(tmp_path: Path) -> None:
    """The ast analyzer maps a test to the modules it transitively imports."""
    src = tmp_path / "src" / "pkg"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "leaf.py").write_text("VALUE = 1\n", encoding="utf-8")
    (src / "mid.py").write_text("from pkg.leaf import VALUE\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_mid.py").write_text(
        "from pkg.mid import VALUE\n\n\ndef test_value() -> None:\n    assert VALUE\n",
        encoding="utf-8",
    )
    (tests / "test_other.py").write_text(
        "import pkg\n\n\ndef test_ok() -> None:\n    assert pkg\n", encoding="utf-8"
    )

    graph = build_dependency_graph(tests, tmp_path / "src")

    assert "pkg.leaf" in graph["tests/test_mid.py"]
    assert "pkg.mid" in graph["tests/test_mid.py"]
    assert "pkg.leaf" not in graph["tests/test_other.py"]

    result = select_tests(
        ["src/pkg/leaf.py"],
        loader=make_loader(tests, tmp_path / "src"),
        all_tests=tuple(sorted(graph)),
    )
    assert set(result.tests) == {"tests/test_mid.py"}


@pytest.mark.negative
def test_make_loader_returns_none_when_roots_missing(tmp_path: Path) -> None:
    """A missing tests/src root yields ``None`` so the caller falls back."""
    loader = make_loader(tmp_path / "absent-tests", tmp_path / "absent-src")
    assert loader() is None
