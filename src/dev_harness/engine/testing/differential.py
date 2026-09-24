"""Differential test selector with documented full-suite fallback (V11 8.12).

Given the set of source files a chunk changed, the selector returns the
**exact** set of tests that depend on them, so the pipeline runs only the
affected tests instead of the whole suite. Two behaviors are load-bearing
(8.B acceptance is exact):

* with a dependency graph available, the result is the **exact dependent test
  set** - set equality, not a superset (a test is selected iff it transitively
  imports a changed module);
* when the graph is **unavailable**, the selector falls back to the **full
  suite** and **logs the reason** - a silent empty selection would skip tests
  and hide regressions, so the fallback is loud and recorded on the result.

The graph is built from static analysis of the test files' imports (``ast``,
stdlib only): each test file maps to the set of modules it transitively imports.
The loader is injected (``Callable[[], Mapping | None]``) so a caller can supply
a prebuilt graph, a synthetic one, or ``None`` to simulate an unavailable graph.
No network; no new dependencies.
"""

from __future__ import annotations

import ast
import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

_LOG = logging.getLogger(__name__)

# A test node id is the test file's path relative to the tests root, posix-style
# and prefixed with ``tests/`` (e.g. ``tests/engine/test_differential.py``).
_TESTS_PREFIX = "tests/"


def path_to_module(path: str | Path) -> str:
    """Normalize a source path to a dotted module name.

    ``src/dev_harness/engine/nodes/tester.py`` -> ``dev_harness.engine.nodes.tester``.
    A leading ``src/`` segment is dropped, the ``.py`` suffix is stripped, and a
    trailing ``__init__`` collapses to its package.
    """
    parts = Path(path).as_posix().split("/")
    if parts and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _imports_of(tree: ast.AST) -> set[str]:
    """The module names imported by one parsed file.

    ``import a.b`` records ``a.b``. ``from a.b import c`` records both ``a.b``
    and the candidate submodule ``a.b.c`` (a name may be a module or a symbol;
    recording both avoids missing a changed submodule). Relative imports are
    skipped - they cannot be resolved without package context.
    """
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level or node.module is None:
                continue
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
    return modules


def _parse(path: Path) -> ast.AST:
    """Parse a Python file; an unreadable/unparseable file imports nothing."""
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return ast.Module(body=[], type_ignores=[])


def _module_graph(src_root: Path) -> dict[str, set[str]]:
    """Map every module under ``src_root`` to the modules it imports."""
    graph: dict[str, set[str]] = {}
    for py in sorted(src_root.rglob("*.py")):
        module = path_to_module(py.relative_to(src_root))
        graph[module] = _imports_of(_parse(py))
    return graph


def _reachable(start: Iterable[str], module_graph: Mapping[str, set[str]]) -> set[str]:
    """The transitive closure of ``start`` over ``module_graph``."""
    seen: set[str] = set()
    stack = list(start)
    while stack:
        module = stack.pop()
        if module in seen:
            continue
        seen.add(module)
        stack.extend(module_graph.get(module, ()))
    return seen


def build_dependency_graph(
    tests_root: Path, src_root: Path
) -> dict[str, frozenset[str]]:
    """Build ``test node id -> transitively imported modules`` from source.

    Each test file's direct imports are expanded through the module graph built
    from ``src_root``, so a test that imports ``a`` which imports the changed
    ``b`` is selected for ``b``. Test files that fail to parse contribute an
    empty set (they are never silently dropped from the graph).
    """
    module_graph = _module_graph(src_root)
    graph: dict[str, frozenset[str]] = {}
    for py in sorted(tests_root.rglob("*.py")):
        node_id = _TESTS_PREFIX + py.relative_to(tests_root).as_posix()
        direct = _imports_of(_parse(py))
        graph[node_id] = frozenset(_reachable(direct, module_graph))
    return graph


@dataclass(frozen=True)
class SelectionResult:
    """The outcome of one differential selection.

    :param tests: the selected test node ids (sorted); the full suite on fallback.
    :param full_suite: True when the graph was unavailable and everything runs.
    :param reason: why the full suite was selected, or ``None`` on a targeted run.
    """

    tests: tuple[str, ...]
    full_suite: bool
    reason: str | None = None


def select_tests(
    changed: Iterable[str],
    *,
    loader: Callable[[], Mapping[str, frozenset[str]] | None],
    all_tests: Sequence[str] = (),
    logger: logging.Logger | None = None,
) -> SelectionResult:
    """Select the exact dependent tests for ``changed`` source files.

    ``loader`` returns the dependency graph (test node id -> imported modules) or
    ``None`` when it is unavailable; a loader that raises is treated as
    unavailable. On an unavailable graph the full suite (``all_tests``) is
    returned with ``full_suite=True`` and the reason logged and recorded. With a
    graph, a test is selected iff its imported-module set intersects the changed
    modules - the exact set, never a superset.
    """
    log = logger if logger is not None else _LOG
    try:
        graph = loader()
    except Exception as exc:  # noqa: BLE001 - any loader failure means "unavailable"
        graph = None
        detail = f"{type(exc).__name__}: {exc}"
    else:
        detail = "loader returned None"

    if graph is None:
        reason = f"dependency graph unavailable ({detail}); running full suite"
        log.warning("differential selection: %s", reason)
        return SelectionResult(
            tests=tuple(sorted(all_tests)), full_suite=True, reason=reason
        )

    changed_modules = {path_to_module(path) for path in changed}
    selected = sorted(
        node_id for node_id, modules in graph.items() if modules & changed_modules
    )
    return SelectionResult(tests=tuple(selected), full_suite=False, reason=None)


def make_loader(
    tests_root: Path, src_root: Path
) -> Callable[[], Mapping[str, frozenset[str]] | None]:
    """A loader that builds the graph from disk, or ``None`` if it cannot.

    A missing tests or source root yields ``None`` so the caller falls back to
    the full suite rather than selecting nothing.
    """

    def load() -> Mapping[str, frozenset[str]] | None:
        if not tests_root.is_dir() or not src_root.is_dir():
            return None
        return build_dependency_graph(tests_root, src_root)

    return load
