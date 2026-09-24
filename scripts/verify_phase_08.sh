#!/usr/bin/env bash
# Phase 8 acceptance protocol (V11 8.D) - POSIX/WSL2 twin.
# Deliverable: the SDLC pipeline runs chunks in parallel across isolated git
# worktrees, merges them without losing a write, surfaces integration conflicts,
# bounds every retry loop, halts at the HITL gate, contains the Critic, respects
# the prompt budget, and recovers in-flight worktrees from a checkpoint.
#
# The protocol drives two real surfaces:
#   * `python -m dev_harness.engine.cli` (8.20) for the serial baseline, DAG
#     validation and the critic drill;
#   * `scripts/p8_acceptance_driver.py` (8.19) for the parallel/isolation/merge,
#     conflict, bounded-retry, HITL, budget and worktree-checkpoint proofs. The
#     driver composes the real engine parts (WorkerWorkspace 8.8, Developer 8.10,
#     Tester 8.11, Integrator 8.9, HitlGate 8.15, engine.context 8.16) because the
#     CLI's `--max-parallel > 1` path reuses the lowest free worker slot and so
#     cannot bind a distinct worktree per concurrently-running chunk.
#
# Every step is offline (`--mock`); no live provider call is made. P8 needs no
# AF_UNIX, so the .ps1 twin runs the SAME steps on Windows PowerShell.
#
# Step 13 asserts the reviewer-signed reports/phase_08_acceptance.json exists and
# is ACCEPTED; it does NOT create it (the implementer may not sign its own phase).
set -euo pipefail
cd "$(dirname "$0")/.."

DRIVER="scripts/p8_acceptance_driver.py"
REPORT="reports/phase_08_acceptance.json"
MUT_REPORT="reports/mutation_report.json"
WS_ROOT="${TMPDIR:-/tmp}/dev-harness-p8"

# Fresh, deterministic git repo per step: a stale worktree from a prior run would
# make WorkerWorkspace.bind raise WorktreeExistsError.
init_repo() {
    rm -rf "$1"
    mkdir -p "$1"
    ( cd "$1" && git init -q -b main \
        && printf 'a = 1\n' > tracked.py \
        && git add tracked.py \
        && git -c user.email=p8@test -c user.name=p8 commit -q -m "p8 init" )
}

rm -rf "$WS_ROOT"
mkdir -p "$WS_ROOT"

echo "[P8] step 1: serial baseline (run --max-parallel 1 --mock; all chunks COMPLETED)"
WS="$WS_ROOT/serial"; init_repo "$WS"
serial="$(python -m dev_harness.engine.cli run --workspace "$WS" \
    --requirement fixtures/req_simple.md --max-parallel 1 --mock --trace 2>/dev/null)"
echo "$serial"
grep -q "c1: COMPLETED" <<<"$serial" || { echo "serial chunk not COMPLETED: $serial" >&2; exit 1; }
grep -q "gate: RUNNING" <<<"$serial" || { echo "serial gate not RUNNING: $serial" >&2; exit 1; }
python -c "
import json, sys
data = json.load(open(sys.argv[1], encoding='utf-8'))
if not isinstance(data, list) or not data:
    raise SystemExit('run artifact is not a non-empty envelope list')
for env in data:
    if not {'type', 'seq', 'payload'} <= set(env):
        raise SystemExit(f'envelope missing keys: {env}')
print(f'  run artifact schema-valid ({len(data)} envelopes)')
" "$WS/reports/parallel_trace.json" || { echo "run artifact not schema-valid" >&2; exit 1; }

echo "[P8] step 2: DAG correctness (topological order; cycle rejected naming both ids)"
dag="$(python -m dev_harness.engine.cli plan --workspace "$WS" \
    --requirement fixtures/req_diamond.md --mock --print-dag)"
echo "  order: $(tr '\n' ' ' <<<"$dag")"
python -c "
import sys
order = sys.argv[1].split()
idx = {c: i for i, c in enumerate(order)}
deps = {'c2': ['c1'], 'c3': ['c1'], 'c4': ['c1'], 'c5': ['c2', 'c3', 'c4']}
for cid, ds in deps.items():
    for dep in ds:
        if not idx[dep] < idx[cid]:
            raise SystemExit(f'{dep} not before {cid}')
if order != ['c1', 'c2', 'c3', 'c4', 'c5']:
    raise SystemExit(f'unexpected order: {order}')
print('  topological order valid')
" "$dag" || { echo "DAG order invalid" >&2; exit 1; }
set +e
cycle="$(python -m dev_harness.engine.cli plan --workspace "$WS" \
    --requirement fixtures/req_cycle.md --mock --print-dag 2>&1)"
cycle_code=$?
set -e
echo "  cycle -> exit=$cycle_code"
[ "$cycle_code" -ne 0 ] || { echo "cycle fixture was not rejected" >&2; exit 1; }
grep -q "Cycle detected" <<<"$cycle" || { echo "cycle message missing: $cycle" >&2; exit 1; }
grep -q "c1" <<<"$cycle" || { echo "cycle did not name c1: $cycle" >&2; exit 1; }
grep -q "c2" <<<"$cycle" || { echo "cycle did not name c2: $cycle" >&2; exit 1; }

echo "[P8] step 3: parallel execution (peak concurrency exactly 3; no chunk before deps)"
WS="$WS_ROOT/parallel"; init_repo "$WS"
par="$(python "$DRIVER" parallel --workspace "$WS" \
    --requirement fixtures/req_diamond.md --max-parallel 3)"
echo "  $par"
python -c "
import json, sys
from pathlib import Path
sys.path.insert(0, '.')
from dev_harness.engine.cli import parse_chunks
data = json.loads(sys.argv[1])
if data['peak'] != 3:
    raise SystemExit(f\"peak {data['peak']} != 3\")
if data['chunks'] != ['c1', 'c2', 'c3', 'c4', 'c5']:
    raise SystemExit(f\"chunks {data['chunks']}\")
frames = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
start, end = {}, {}
for i, f in enumerate(frames):
    (start if f['phase'] == 'start' else end)[f['chunk']] = i
chunks = {c.chunk_id: c for c in parse_chunks(Path('fixtures/req_diamond.md').read_text(encoding='utf-8'))}
for cid, chunk in chunks.items():
    for dep in chunk.dependencies:
        if not start[cid] > end[dep]:
            raise SystemExit(f'{cid} started before {dep} finished')
print(f\"  peak={data['peak']}; dependency order valid ({len(frames)} frames)\")
" "$par" "$WS/reports/parallel_trace.json" || { echo "parallel execution proof failed" >&2; exit 1; }

echo "[P8] step 4: isolation proof (3 worktrees + primary clean throughout)"
python -c "
import json, sys
d = json.loads(sys.argv[1])
if d['worktrees'] != 3:
    raise SystemExit(f\"worktrees {d['worktrees']} != 3\")
for key in ('primary_clean_before', 'primary_clean_mid', 'primary_clean_after'):
    if not d[key]:
        raise SystemExit(f'primary dirty: {key}')
print('  3 worktrees; primary clean before/mid/after')
" "$par" || { echo "isolation proof failed" >&2; exit 1; }

echo "[P8] step 5: merge proof (all chunk changes; one commit per chunk; no lost writes)"
python -c "
import json, sys
d = json.loads(sys.argv[1])
if not d['merged']:
    raise SystemExit('merged file missing from primary')
if d['commits'] != len(d['chunks']):
    raise SystemExit(f\"commits {d['commits']} != chunks {len(d['chunks'])}\")
if d['lost_writes']:
    raise SystemExit(f\"lost writes: {d['lost_writes']}\")
print(f\"  merged; {d['commits']} commits; 0 lost writes\")
" "$par" || { echo "merge proof failed" >&2; exit 1; }

echo "[P8] step 6: conflict surfacing (IntegrationConflict names file + both ids; primary unmodified)"
WS="$WS_ROOT/conflict"; init_repo "$WS"
con="$(python "$DRIVER" conflict --workspace "$WS" --requirement fixtures/req_conflict.md)"
echo "  $con"
python -c "
import json, sys
d = json.loads(sys.argv[1])
for key in ('conflict', 'names_file', 'names_both_chunks', 'head_unmodified', 'primary_clean'):
    if not d[key]:
        raise SystemExit(f'conflict proof failed: {key}')
print('  conflict names file + both chunks; primary unmodified')
" "$con" || { echo "conflict surfacing failed" >&2; exit 1; }

echo "[P8] step 7: retry + escalation (3x then Architect; e2e ceiling -> HITL; terminal FAILED)"
WS="$WS_ROOT/failing"; init_repo "$WS"
fail="$(python "$DRIVER" failing --workspace "$WS" \
    --requirement fixtures/req_failing.md 2>/dev/null)"
echo "  $fail"
python -c "
import json, sys
d = json.loads(sys.argv[1])
if d['status'] != 'FAILED':
    raise SystemExit(f\"status {d['status']} != FAILED\")
if not d['terminal'] or d['gate'] != 'STOPPED':
    raise SystemExit(f\"not terminal: gate={d['gate']} terminal={d['terminal']}\")
if d['e2e_retry_count'] != 2:
    raise SystemExit(f\"e2e_retry_count {d['e2e_retry_count']} != 2 (unbounded?)\")
print(f\"  terminal FAILED; e2e_retry_count={d['e2e_retry_count']} (bounded)\")
" "$fail" || { echo "bounded-retry proof failed" >&2; exit 1; }
python -c "
from dev_harness.engine.routing import (
    ARCHITECT_NODE, E2E_CEILING, INNER_LOOP_CEILING, FailureKind, route,
)
inner = route({'inner_loop_retry_count': INNER_LOOP_CEILING - 1}, failure_kind=FailureKind.INNER_LOOP)
if inner.next_node != 'developer':
    raise SystemExit(f'inner loop below ceiling routed to {inner.next_node}')
at_ceiling = route({'inner_loop_retry_count': INNER_LOOP_CEILING}, failure_kind=FailureKind.INNER_LOOP)
if at_ceiling.next_node != ARCHITECT_NODE:
    raise SystemExit(f'inner loop at ceiling routed to {at_ceiling.next_node}')
e2e = route({'e2e_retry_count': E2E_CEILING}, failure_kind=FailureKind.E2E)
if not e2e.escalate_to_hitl or e2e.terminal is None or e2e.terminal.value != 'STOPPED':
    raise SystemExit('e2e ceiling did not escalate to HITL/STOPPED')
print(f'  inner loop retries {INNER_LOOP_CEILING}x then Architect; e2e ceiling -> HITL')
" || { echo "retry routing proof failed" >&2; exit 1; }

echo "[P8] step 8: HITL gate (halts at approval; checkpoint persisted; approve advances one node)"
hitl="$(python "$DRIVER" hitl --workspace "$WS")"
echo "  $hitl"
python -c "
import json, sys
d = json.loads(sys.argv[1])
if not d['halted']:
    raise SystemExit('gate did not halt at approval')
if not d['checkpoint_persisted']:
    raise SystemExit('no checkpoint persisted at the halt')
if d['nodes_advanced'] != 1:
    raise SystemExit(f\"advanced {d['nodes_advanced']} nodes, expected 1\")
print('  halted; checkpoint persisted; approve advanced exactly one node')
" "$hitl" || { echo "HITL gate proof failed" >&2; exit 1; }

echo "[P8] step 9: critic containment (CriticScopeViolation; diff confined to tui_state)"
drill="$(python -m dev_harness.engine.cli critic-drill --workspace "$WS" --mock)"
echo "  $drill"
grep -q "CriticScopeViolation" <<<"$drill" || { echo "no CriticScopeViolation: $drill" >&2; exit 1; }
grep -q "diff: \['tui_state'\]" <<<"$drill" || { echo "diff not confined to tui_state: $drill" >&2; exit 1; }

echo "[P8] step 10: budget compliance (prompt <= context_window - max_output; trace capped)"
bud="$(python "$DRIVER" budget --workspace "$WS")"
echo "  $bud"
python -c "
import json, sys
d = json.loads(sys.argv[1])
if not d['prompt_fits']:
    raise SystemExit('prompt exceeded context_window - max_output')
if not d['bounded'] or d['trace_lines'] > 51:
    raise SystemExit(f\"trace not capped: {d['trace_lines']} lines\")
if not (d['first_frame'] and d['last_frame']):
    raise SystemExit('trace cap dropped the first or last frame')
print(f\"  budget={d['budget']}; trace capped at {d['trace_lines']} lines (first+last kept)\")
" "$bud" || { echo "budget compliance failed" >&2; exit 1; }

echo "[P8] step 11: worktree checkpoint proof (capture in-flight, restart, restore each worktree)"
WS="$WS_ROOT/checkpoint"; init_repo "$WS"
ck="$(python "$DRIVER" checkpoint --workspace "$WS")"
echo "  $ck"
python -c "
import json, sys
d = json.loads(sys.argv[1])
for key in ('head_matches', 'file_restored', 'primary_clean'):
    if not d[key]:
        raise SystemExit(f'checkpoint restore failed: {key}')
print('  worktree restored field-for-field; primary clean')
" "$ck" || { echo "worktree checkpoint proof failed" >&2; exit 1; }

echo "[P8] step 12: mutation gate (engine focus set >= 80%; 0 survivors in readiness + worktree binding)"
# PLATFORM LIMIT (plan R2 class): mutmut refuses to run on native Windows
# ("To run mutmut on Windows, please use the WSL"). Detect that and defer the
# gate explicitly rather than reporting a false failure; on WSL2/POSIX the gate
# runs for real and a surviving focus-set mutant still fails the protocol.
# Capture first: under `set -o pipefail` the pipeline would inherit mutmut's
# non-zero exit and the `if` would take the else branch even when grep matched.
MUTMUT_PROBE="$(python -m mutmut run --paths-to-mutate dev_harness.engine.dag 2>&1 || true)"
if grep -qi "use the WSL" <<<"$MUTMUT_PROBE"; then
    echo "  DEFERRED: mutmut requires WSL2/POSIX (plan R2); run this step there."
else
    python scripts/mutation_gate.py \
        --packages engine.dag,engine.worker_pool,engine.worker_workspace,engine.integrator
    [ -f "$MUT_REPORT" ] || { echo "mutation report missing: $MUT_REPORT" >&2; exit 1; }
    python -c "
import json
data = json.load(open('$MUT_REPORT', encoding='utf-8'))
for mod in ('engine.dag', 'engine.worker_pool', 'engine.worker_workspace', 'engine.integrator'):
    entry = data.get(mod)
    if entry is None or entry.get('score') is None:
        raise SystemExit(f'{mod}: no mutation score')
    if entry['score'] < 80.0:
        raise SystemExit(f\"{mod}: score {entry['score']} < 80.0\")
# 0 survivors in the readiness rule (worker_pool) and the worktree binding
# (worker_workspace): a surviving mutant there is a rejection criterion.
for mod in ('engine.worker_pool', 'engine.worker_workspace'):
    if data[mod]['score'] != 100.0:
        raise SystemExit(f\"{mod}: survivors remain (score {data[mod]['score']})\")
print('  engine focus set >= 80%; 0 survivors in readiness + worktree binding')
" || { echo "mutation gate failed" >&2; exit 1; }
fi

echo "[P8] step 13: emit acceptance report + human sign-off"
[ -f "$REPORT" ] || { echo "acceptance report missing (8.D reviewer must sign): $REPORT" >&2; exit 1; }
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
" || { echo "human sign-off missing" >&2; exit 1; }

echo "P8 acceptance OK"
