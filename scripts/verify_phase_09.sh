#!/usr/bin/env bash
# Phase 9 acceptance protocol (V11 9.D) - POSIX/WSL2 twin.
# Deliverable: a system that survives a scripted chaos drill and recovers without
# manual filesystem surgery.
# Scope boundary: no new features - every step exercises existing handling.
# Permitted stubs: MockLLM; fault injection via FakeProviderServer and chaos_drill.py.
#
# Steps 2/3 (kill9-*) need SIGKILL, which native Windows lacks; the chaos drill
# reports them as `deferred` there (plan R2, the same platform limit P5-P8
# record). Every other step runs on any host.
set -euo pipefail
cd "$(dirname "$0")/.."

REPORT="reports/phase_09_acceptance.json"
CHAOS="reports/chaos_matrix.json"

echo "[P9] step 1: error reachability (every HarnessError subclass has a negative test)"
python scripts/coverage_gate.py --errors

echo "[P9] step 2: kill-9 during a run (restart detects the un-finalized session)"
python scripts/chaos_drill.py --fault kill9-engine

echo "[P9] step 3: kill-9 with worktrees live (stale worktrees reclaimed)"
python scripts/chaos_drill.py --fault kill9-parallel

echo "[P9] step 4: corrupt a checkpoint (digest mismatch; prior checkpoint served)"
python scripts/chaos_drill.py --fault corrupt-checkpoint

echo "[P9] step 5: disk full (actionable banner; DB not corrupted)"
python scripts/chaos_drill.py --fault enospc

echo "[P9] step 6: provider outage (fall back within 1 retry; DEGRADED)"
python scripts/chaos_drill.py --fault provider-529

echo "[P9] step 7: total provider outage (AllProvidersUnavailable; resumable)"
python scripts/chaos_drill.py --fault all-providers-down

echo "[P9] step 8: context overflow (one summarize-retry, then HITL)"
python scripts/chaos_drill.py --fault oversized-context

echo "[P9] step 9: secret leak attempt (key redacted in every sink)"
python scripts/chaos_drill.py --fault echo-api-key

echo "[P9] step 10: rollback rehearsal (docs/rollback.md, Phase 1)"
[ -f docs/rollback.md ] || { echo "rollback doc missing: docs/rollback.md" >&2; exit 1; }
WS="${TMPDIR:-/tmp}/dev-harness-p9-rollback"
rm -rf "$WS"
mkdir -p "$WS/.dev-harness"
python -m dev_harness.storage.migrate --workspace "$WS" --up
python -m dev_harness.storage.migrate --workspace "$WS" --down
python -c "
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
names = {r[0] for r in conn.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")}
if 'checkpoints' in names:
    raise SystemExit('rollback left the checkpoints table behind')
print('  rollback clean; checkpoints table removed')
" "$WS/.dev-harness/state.db"

echo "[P9] step 11: emit report (fault matrix attached)"
[ -f "$CHAOS" ] || { echo "chaos matrix missing: $CHAOS" >&2; exit 1; }
[ -f "$REPORT" ] || { echo "acceptance report missing (9.D reviewer must sign): $REPORT" >&2; exit 1; }
grep -q '"verdict": "ACCEPTED"' "$REPORT" || { echo "verdict not ACCEPTED: $REPORT" >&2; exit 1; }
python -c "
import json
data = json.load(open('$REPORT', encoding='utf-8'))
if not data.get('signed_by'):
    raise SystemExit('acceptance report has no signed_by')
print(f\"  verdict ACCEPTED; signed_by={data['signed_by']!r}\")
"

echo "P9 acceptance OK"
