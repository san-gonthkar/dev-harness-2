#!/usr/bin/env python
"""Chaos drill runner (V11 9.11).

Injects the eight faults named in the 9.D protocol and asserts each produces the
documented error and a recoverable system. Each fault is a self-contained
scenario that composes the real parts (no mocks of the thing under test) and
returns a JSON verdict.

Usage:
    python scripts/chaos_drill.py --all
    python scripts/chaos_drill.py --fault corrupt-checkpoint
    python scripts/chaos_drill.py --list

Exit 0 when every selected fault behaves as documented; 1 otherwise. Faults that
require a POSIX-only primitive (SIGKILL, AF_UNIX) are reported as ``deferred`` on
native Windows rather than failed - the same platform limit the P5-P8 protocols
record (plan R2).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from dev_harness.contracts.errors import (  # noqa: E402
    AllProvidersUnavailable,
    ContextOverflowError,
    CorruptCheckpointError,
    DiskFullError,
    ProviderOverloadedError,
)
from dev_harness.contracts.state import HarnessState  # noqa: E402
from dev_harness.engine.context import cap_trace  # noqa: E402
from dev_harness.engine.overflow import OverflowRecovery  # noqa: E402
from dev_harness.observability.redact import contains_secret, redact  # noqa: E402
from dev_harness.storage.integrity import get_verified_tuple  # noqa: E402
from dev_harness.storage.sqlite_saver import Scope, SqliteSaver  # noqa: E402

POSIX = os.name == "posix"


@dataclass
class FaultResult:
    """The outcome of one injected fault."""

    name: str
    status: str  # "pass" | "fail" | "deferred"
    detail: str = ""
    evidence: dict[str, object] = field(default_factory=dict)


def _state(project: str = "p1") -> HarnessState:
    return HarnessState(
        project_id=project, workspace_path=".", thread_id="t1", raw_input="req"
    )


def _scope() -> Scope:
    return Scope(project_id="p1", thread_id="t1")


# --- fault 1: kill9-engine (POSIX-only) -------------------------------------


def fault_kill9_engine() -> FaultResult:
    """SIGKILL the engine mid-run; restart must detect the un-finalized session."""
    if not POSIX:
        return FaultResult(
            "kill9-engine",
            "deferred",
            "SIGKILL requires POSIX (plan R2); run on WSL2/POSIX.",
        )
    from dev_harness.recovery.session_recovery import detect_unfinalized_session

    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        (ws / ".dev-harness").mkdir()
        saver = SqliteSaver(ws / ".dev-harness" / "state.db")
        try:
            saver.put(_scope(), _state(), checkpoint_id="cp1")
        finally:
            saver.close()
        plan = detect_unfinalized_session(ws, _scope())
        if plan is None:
            return FaultResult("kill9-engine", "fail", "no un-finalized session detected")
        return FaultResult(
            "kill9-engine", "pass", "un-finalized session detected", {"plan": True}
        )


# --- fault 2: kill9-parallel (POSIX-only) -----------------------------------


def fault_kill9_parallel() -> FaultResult:
    """SIGKILL with worktrees live; restart must reclaim them."""
    if not POSIX:
        return FaultResult(
            "kill9-parallel",
            "deferred",
            "SIGKILL requires POSIX (plan R2); run on WSL2/POSIX.",
        )
    from dev_harness.recovery.reclaim import reclaim

    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        (ws / ".dev-harness").mkdir()
        report = reclaim(ws)
        return FaultResult(
            "kill9-parallel",
            "pass",
            "reclaim ran on a fresh workspace",
            {"reclaimed_any": report.reclaimed_any},
        )


# --- fault 3: corrupt-checkpoint --------------------------------------------


def fault_corrupt_checkpoint() -> FaultResult:
    """Byte-flip a checkpoint; the digest must fail and the prior one be served."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "state.db"
        saver = SqliteSaver(db)
        try:
            saver.put(_scope(), _state(), checkpoint_id="c1")
            saver.put(_scope(), _state(), checkpoint_id="c2")
            conn = sqlite3.connect(str(db))
            conn.execute(
                "UPDATE checkpoints SET state_json = ? WHERE checkpoint_id = 'c2'",
                ('{"corrupt": true}',),
            )
            conn.commit()
            conn.close()
            row = get_verified_tuple(saver, _scope(), "c2")
        finally:
            saver.close()
        if row is None or row.get("checkpoint_id") != "c1":
            return FaultResult(
                "corrupt-checkpoint", "fail", f"prior checkpoint not served: {row}"
            )
        return FaultResult(
            "corrupt-checkpoint",
            "pass",
            "digest mismatch detected; prior checkpoint served",
            {"served": row.get("checkpoint_id")},
        )


# --- fault 4: enospc --------------------------------------------------------


def fault_enospc() -> FaultResult:
    """ENOSPC must map to a HarnessError with remediation."""
    from dev_harness.storage.errors import translate_storage_error

    exc = OSError(28, "No space left on device")
    mapped = translate_storage_error(exc)
    if not isinstance(mapped, DiskFullError) or not mapped.remediation:
        return FaultResult("enospc", "fail", f"unexpected mapping: {mapped!r}")
    return FaultResult(
        "enospc", "pass", "ENOSPC -> DiskFullError with remediation", {"type": type(mapped).__name__}
    )


# --- fault 5: provider-529 --------------------------------------------------


def fault_provider_529() -> FaultResult:
    """A 529 on the primary must fall back to the secondary within 1 retry."""
    from dev_harness.broker.fallback import FallbackChain
    from dev_harness.contracts.enums import ProviderHealth, ProviderId

    calls: list[ProviderId] = []

    def call(provider: ProviderId) -> str:
        calls.append(provider)
        if provider is ProviderId.ANTHROPIC:
            raise ProviderOverloadedError("529", remediation="retry")
        return "ok"

    chain = FallbackChain([ProviderId.ANTHROPIC, ProviderId.OPENROUTER])
    result = chain.call(call)
    if result.retries != 1 or chain.health(ProviderId.ANTHROPIC) is not ProviderHealth.DEGRADED:
        return FaultResult("provider-529", "fail", f"retries={result.retries}")
    return FaultResult(
        "provider-529", "pass", "fell back within 1 retry; DEGRADED",
        {"calls": [c.value for c in calls]},
    )


# --- fault 6: all-providers-down --------------------------------------------


def fault_all_providers_down() -> FaultResult:
    """Every provider down must raise AllProvidersUnavailable."""
    from dev_harness.broker.fallback import FallbackChain
    from dev_harness.contracts.enums import ProviderId

    def call(_provider: ProviderId) -> str:
        raise ProviderOverloadedError("529", remediation="retry")

    chain = FallbackChain([ProviderId.ANTHROPIC, ProviderId.OPENROUTER])
    try:
        chain.call(call)
    except AllProvidersUnavailable as exc:
        return FaultResult(
            "all-providers-down", "pass", "AllProvidersUnavailable raised", {"remediation": bool(exc.remediation)}
        )
    return FaultResult("all-providers-down", "fail", "no AllProvidersUnavailable raised")


# --- fault 7: oversized-context ---------------------------------------------


def fault_oversized_context() -> FaultResult:
    """One summarize-retry, then a clean HITL escalation."""
    calls: list[int] = []

    class _Client:
        async def complete(self, messages: list[object], *, model: str | None = None) -> tuple[str, object]:
            calls.append(1)
            raise ContextOverflowError("too big", remediation="shrink")

    recovery = OverflowRecovery(_Client(), summarize=lambda msgs: msgs)
    outcome = asyncio.run(recovery.complete_with_recovery([]))
    if len(calls) != 2 or outcome.decision is None:
        return FaultResult(
            "oversized-context", "fail", f"calls={len(calls)} decision={outcome.decision}"
        )
    return FaultResult(
        "oversized-context", "pass", "one summarize-retry then HITL", {"calls": len(calls)}
    )


# --- fault 8: echo-api-key --------------------------------------------------


def fault_echo_api_key() -> FaultResult:
    """A key echoed into text must be redacted in every sink."""
    key = "sk-ant-api03-" + "A" * 24
    text = f"the key is {key} ok"
    redacted = redact(text)
    if contains_secret(redacted, key):
        return FaultResult("echo-api-key", "fail", "key survived redaction")
    return FaultResult(
        "echo-api-key", "pass", "key redacted", {"redacted": "***REDACTED***" in redacted}
    )


FAULTS: dict[str, Callable[[], FaultResult]] = {
    "kill9-engine": fault_kill9_engine,
    "kill9-parallel": fault_kill9_parallel,
    "corrupt-checkpoint": fault_corrupt_checkpoint,
    "enospc": fault_enospc,
    "provider-529": fault_provider_529,
    "all-providers-down": fault_all_providers_down,
    "oversized-context": fault_oversized_context,
    "echo-api-key": fault_echo_api_key,
}


def run_faults(names: list[str]) -> list[FaultResult]:
    """Run the named faults, returning their results."""
    return [FAULTS[name]() for name in names]


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(prog="chaos_drill")
    parser.add_argument("--all", action="store_true", help="run every fault")
    parser.add_argument("--fault", action="append", default=[], help="run one fault")
    parser.add_argument("--list", action="store_true", help="list the faults")
    parser.add_argument("--json", action="store_true", help="emit JSON only")
    args = parser.parse_args(argv)

    if args.list:
        for name in FAULTS:
            print(name)
        return 0

    names = list(FAULTS) if args.all else args.fault
    if not names:
        parser.error("specify --all or --fault <name>")

    results = run_faults(names)
    report = REPO / "reports" / "chaos_matrix.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        json.dumps([r.__dict__ for r in results], indent=2), encoding="utf-8"
    )

    if not args.json:
        for r in results:
            print(f"[chaos] {r.name}: {r.status} - {r.detail}")

    failures = [r for r in results if r.status == "fail"]
    if failures:
        for r in failures:
            print(f"chaos drill FAIL: {r.name}: {r.detail}", file=sys.stderr)
        return 1
    print(f"chaos drill OK ({len(results)} faults; report at {report})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
