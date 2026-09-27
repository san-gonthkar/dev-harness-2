#!/usr/bin/env bash
# Phase 10 release gate (V11 10.D) - POSIX/WSL2 twin.
# Deliverable: a tagged, installable release with a validated runbook and a
# complete traceability report.
# Scope boundary: none - this is the only phase with no stubs on the critical
# path (MockLLM is permitted in 10.1-10.3 for determinism; 10.4 is the
# un-stubbed reality check).
#
# Steps 2 (full nightly), 4 (real local Ollama), 5 (cold-machine install) and
# 7 (rollback rehearsal) need infrastructure this host does not have; the gate
# records them as DEFERRED rather than failing (the same platform/infra limit
# P5-P9 record). Every other step runs anywhere.
set -euo pipefail
cd "$(dirname "$0")/.."

REPORT="reports/phase_10_acceptance.json"

echo "[P10] step 1: phase report audit (all 11 reports present, ACCEPTED, commit-pinned)"
python scripts/verify_phase.py --audit-all

echo "[P10] step 2: full nightly (10.1-10.4 + mutation gates on trunk)"
echo "  DEFERRED: requires the nightly CI runner (timing/slow/e2e + mutation)."

echo "[P10] step 3: graph-node coverage (every compiled node executed >=1x)"
python scripts/generate_traceability.py --graph-coverage

echo "[P10] step 4: real-model run (live local Ollama, Qwen 2.5 Coder 7B)"
echo "  DEFERRED: requires a live local Ollama (10.4, NIGHTLY)."

echo "[P10] step 5: cold-machine install (fresh VM, docs/runbook.md, < 10 min)"
echo "  DEFERRED: requires a fresh VM and a human operator."

echo "[P10] step 6: traceability (TDD section -> task -> test, no gaps)"
python scripts/generate_traceability.py --check

echo "[P10] step 7: rollback rehearsal (docs/rollback.md, release tag -> prior tag)"
echo "  DEFERRED: requires a human-timed rehearsal on a scratch clone."

echo "[P10] step 8: release checklist (docs/release_checklist.md)"
[ -f docs/release_checklist.md ] || { echo "release checklist missing" >&2; exit 1; }
[ -f docs/rollback.md ] || { echo "rollback doc missing" >&2; exit 1; }
[ -f docs/runbook.md ] || { echo "runbook missing" >&2; exit 1; }
[ -f docs/traceability.md ] || { echo "traceability doc missing" >&2; exit 1; }
echo "  release docs present (checklist, rollback, runbook, traceability)"

echo "[P10] step 9: tag and sign (acceptance JSON ACCEPTED with a human signed_by)"
python scripts/coverage_gate.py --errors
[ -f "$REPORT" ] || { echo "acceptance report missing (10.D reviewer must sign): $REPORT" >&2; exit 1; }
grep -q '"verdict": "ACCEPTED"' "$REPORT" || { echo "verdict not ACCEPTED: $REPORT" >&2; exit 1; }
python -c "
import json
data = json.load(open('$REPORT', encoding='utf-8'))
signed = data.get('signed_by', '')
if not signed:
    raise SystemExit('acceptance report has no signed_by')
if 'agent' in signed.lower():
    raise SystemExit(f'signed_by is not a human: {signed!r}')
print(f'  verdict ACCEPTED; signed_by={signed!r}')
"

echo "P10 acceptance OK"
