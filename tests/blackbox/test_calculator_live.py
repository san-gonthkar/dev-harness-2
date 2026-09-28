"""Real-LLM end-to-end: the harness builds a calculator (blackbox, live).

This is the honest end-to-end test: a **real** provider is called through the
broker, the SDLC graph runs, and the harness produces code in a scratch git
workspace. Nothing is mocked.

It is **opt-in** and never runs by default:

* marked ``live`` (its own lane — never the smoke lane);
* skipped unless ``DEV_HARNESS_OPENROUTER_API_KEY`` is set;
* the model is ``DEV_HARNESS_LIVE_MODEL`` (default ``openrouter-default``);
* the broker must be running (fail-closed), or ``allow_unbrokered`` set.

Run it with::

    pytest tests/blackbox/test_calculator_live.py -q -m live

Cost: one run per invocation. The broker's budget caps apply.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from dev_harness.live import run_live

#: The requirement the harness is asked to build.
REQUIREMENT = Path(__file__).resolve().parents[2] / "fixtures" / "req_calculator.md"
#: The model to use; override with DEV_HARNESS_LIVE_MODEL.
DEFAULT_MODEL = "openrouter-default"


def _api_key_present() -> bool:
    return bool(os.environ.get("DEV_HARNESS_OPENROUTER_API_KEY"))


def _make_workspace(tmp_path: Path) -> Path:
    """A scratch git repository for the harness to build in."""
    ws = tmp_path / "calc"
    ws.mkdir()
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    subprocess.run(["git", "-C", str(ws), "config", "user.email", "t@t.t"], check=True)
    subprocess.run(["git", "-C", str(ws), "config", "user.name", "t"], check=True)
    (ws / "README.md").write_text("calculator demo\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(ws), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(ws), "commit", "-qm", "init"], check=True)
    return ws


@pytest.mark.live
def test_harness_builds_a_calculator_with_a_real_model(tmp_path: Path) -> None:
    """The harness runs the SDLC graph against a real provider.

    Asserts the run completes and the workspace changed. It does **not** assert
    the generated code is correct — a real model is non-deterministic, and
    asserting on its output would make this test flaky. Correctness of the
    generated artifact is a separate, human-reviewed check.
    """
    if not _api_key_present():
        pytest.skip("DEV_HARNESS_OPENROUTER_API_KEY is not set")

    ws = _make_workspace(tmp_path)
    before = subprocess.run(
        ["git", "-C", str(ws), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    exit_code = run_live(
        workspace=str(ws),
        requirement=str(REQUIREMENT),
        provider="openrouter",
        model=os.environ.get("DEV_HARNESS_LIVE_MODEL", DEFAULT_MODEL),
        config_path=None,
        endpoint=os.environ.get("DEV_HARNESS_BROKER_ENDPOINT"),
        max_parallel=1,
    )

    assert exit_code == 0, "the live run did not complete"

    # The run must have produced something in the workspace.
    status = subprocess.run(
        ["git", "-C", str(ws), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    after = subprocess.run(
        ["git", "-C", str(ws), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert status.strip() or after != before, "the run produced no changes"


@pytest.mark.live
def test_generated_calculator_tests_pass(tmp_path: Path) -> None:
    """If the harness wrote tests, they pass.

    Skips when the model produced no test files — that is a quality signal, not
    a harness failure, and asserting on it would make the suite flaky.
    """
    if not _api_key_present():
        pytest.skip("DEV_HARNESS_OPENROUTER_API_KEY is not set")

    ws = _make_workspace(tmp_path)
    exit_code = run_live(
        workspace=str(ws),
        requirement=str(REQUIREMENT),
        provider="openrouter",
        model=os.environ.get("DEV_HARNESS_LIVE_MODEL", DEFAULT_MODEL),
        config_path=None,
        endpoint=os.environ.get("DEV_HARNESS_BROKER_ENDPOINT"),
        max_parallel=1,
    )
    assert exit_code == 0

    test_files = list(ws.rglob("test_*.py"))
    if not test_files:
        pytest.skip("the model produced no test files")

    result = subprocess.run(
        ["python", "-m", "pytest", "-q", str(ws)],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert result.returncode == 0, (
        f"generated tests failed:\n{result.stdout}\n{result.stderr}"
    )
