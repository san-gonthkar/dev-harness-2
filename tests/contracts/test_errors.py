"""Error taxonomy completeness tests (V11 0.4)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from dev_harness.contracts.errors import HarnessError

pytestmark = pytest.mark.unit

SRC_ROOT = Path(__file__).resolve().parents[2] / "src"


def _all_harness_error_subclasses() -> list[type[HarnessError]]:
    """Collect every HarnessError subclass reachable from the errors module."""
    import inspect

    from dev_harness.contracts import errors as errors_mod

    found: list[type[HarnessError]] = []
    for _, obj in inspect.getmembers(errors_mod, inspect.isclass):
        if issubclass(obj, HarnessError) and obj is not HarnessError:
            found.append(obj)
    return found


def test_every_subclass_has_remediation() -> None:
    for cls in _all_harness_error_subclasses():
        # Instantiate with a message; remediation must be non-empty.
        err = cls("boom")
        assert err.remediation, f"{cls.__name__} has empty remediation"
        assert "remediation" in str(err)


def test_remediation_required() -> None:
    with pytest.raises(ValueError, match="remediation"):
        HarnessError("boom", remediation="")  # type: ignore[arg-type]


def test_no_bare_raise_exception_in_src() -> None:
    """AST scan: no bare raise Exception/RuntimeError anywhere in src/."""
    offenders: list[str] = []
    for py in SRC_ROOT.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise) and node.exc is None:
                offenders.append(f"{py.relative_to(SRC_ROOT)}:{node.lineno}")
            if isinstance(node, ast.Raise) and node.exc is not None:
                name = None
                if isinstance(node.exc, ast.Call):
                    name = getattr(node.exc.func, "id", None)
                elif isinstance(node.exc, ast.Name):
                    name = node.exc.id
                if name in {"Exception", "RuntimeError"}:
                    offenders.append(f"{py.relative_to(SRC_ROOT)}:{node.lineno}")
    assert offenders == [], f"bare raises found: {offenders}"


def test_illegal_transition_carries_pair() -> None:
    from dev_harness.contracts.enums import CriticCommand, ExecutionState
    from dev_harness.contracts.errors import IllegalTransitionError

    err = IllegalTransitionError(
        "illegal",
        state=ExecutionState.STOPPED,
        command=CriticCommand.RESUME,
        remediation="Send START to begin a new session.",
    )
    assert err.state == ExecutionState.STOPPED
    assert err.command == CriticCommand.RESUME
    assert err.remediation
