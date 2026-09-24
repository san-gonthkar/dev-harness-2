# Phase 8 acceptance protocol (V11 8.D) - PowerShell twin.
# Runs the SAME 13 steps as scripts/verify_phase_08.sh on Windows PowerShell.
# P8 needs no AF_UNIX (unlike P5-P7): the engine CLI and the acceptance driver
# are plain subprocesses, so this is a full twin, not a platform-limit stub.
#
# Deliverable: the SDLC pipeline runs chunks in parallel across isolated git
# worktrees, merges them without losing a write, surfaces integration conflicts,
# bounds every retry loop, halts at the HITL gate, contains the Critic, respects
# the prompt budget, and recovers in-flight worktrees from a checkpoint.
#
# Step 13 asserts the reviewer-signed reports/phase_08_acceptance.json exists and
# is ACCEPTED; it does NOT create it (the implementer may not sign its own phase).
#
# ErrorActionPreference is "Continue", not "Stop": Windows PowerShell 5.1 turns a
# native command's stderr (the langgraph checkpoint warnings) into a terminating
# error under "Stop". Every step instead checks $LASTEXITCODE / its JSON and
# calls `exit 1` explicitly, so a failure still aborts the protocol.
$ErrorActionPreference = "Continue"
Set-Location (Join-Path $PSScriptRoot "..")

$Driver = "scripts/p8_acceptance_driver.py"
$Report = "reports/phase_08_acceptance.json"
$MutReport = "reports/mutation_report.json"
$WsRoot = Join-Path $env:TEMP "dev-harness-p8"

# Fresh, deterministic git repo per step: a stale worktree from a prior run would
# make WorkerWorkspace.bind raise WorktreeExistsError.
function Initialize-Repo($path) {
    Remove-Item -Recurse -Force $path -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force $path | Out-Null
    Push-Location $path
    git init -q -b main
    Set-Content tracked.py "a = 1"
    git add tracked.py
    git -c user.email=p8@test -c user.name=p8 commit -q -m "p8 init"
    Pop-Location
}

Remove-Item -Recurse -Force $WsRoot -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $WsRoot | Out-Null

# Windows PowerShell 5.1 strips embedded double quotes when passing `-c` code to
# a native command, which corrupts any Python check containing a string literal.
# Write the check to a temp file and run it as a script instead.
$PyCheck = Join-Path $env:TEMP "p8-check.py"
$PyJson = Join-Path $env:TEMP "p8-check.json"
function Invoke-PyCheck($code, $json, $extra) {
    Set-Content -Path $PyCheck -Value $code -Encoding utf8
    if ($null -eq $json) {
        python $PyCheck
        return
    }
    # A single-line value that names an existing file is a path (e.g. a trace
    # artifact); anything else is raw data. Test-Path throws on multi-line or
    # otherwise illegal path strings, so probe defensively.
    $isPath = $false
    if ($json -is [string] -and $json -notmatch "[\r\n]") {
        try { $isPath = Test-Path -LiteralPath $json -PathType Leaf } catch { $isPath = $false }
    }
    if ($isPath) {
        $first = $json
    } else {
        # Raw data: PowerShell mangles embedded quotes in argv, so stage it in a
        # temp file. utf8NoBOM: a BOM breaks json.load on the Python side.
        [System.IO.File]::WriteAllText($PyJson, $json, [System.Text.UTF8Encoding]::new($false))
        $first = $PyJson
    }
    if ($null -ne $extra) {
        python $PyCheck $first $extra
    } else {
        python $PyCheck $first
    }
}

Write-Host "[P8] step 1: serial baseline (run --max-parallel 1 --mock; all chunks COMPLETED)"
$ws = Join-Path $WsRoot "serial"; Initialize-Repo $ws
$serial = (python -m dev_harness.engine.cli run --workspace $ws `
    --requirement fixtures/req_simple.md --max-parallel 1 --mock --trace 2>$null) -join "`n"
Write-Host $serial
if ($serial -notmatch "c1: COMPLETED") { Write-Error "serial chunk not COMPLETED: $serial"; exit 1 }
if ($serial -notmatch "gate: RUNNING") { Write-Error "serial gate not RUNNING: $serial"; exit 1 }
$code = @'
import json, sys
data = json.load(open(sys.argv[1], encoding='utf-8'))
if not isinstance(data, list) or not data:
    raise SystemExit('run artifact is not a non-empty envelope list')
for env in data:
    if not {'type', 'seq', 'payload'} <= set(env):
        raise SystemExit(f'envelope missing keys: {env}')
print(f'  run artifact schema-valid ({len(data)} envelopes)')
'@
Invoke-PyCheck $code "$ws/reports/parallel_trace.json"
if ($LASTEXITCODE -ne 0) { Write-Error "run artifact not schema-valid"; exit 1 }

Write-Host "[P8] step 2: DAG correctness (topological order; cycle rejected naming both ids)"
$dag = (python -m dev_harness.engine.cli plan --workspace $ws `
    --requirement fixtures/req_diamond.md --mock --print-dag) -join "`n"
Write-Host "  order: $($dag -replace "`n", ' ')"
$code = @'
import sys
from pathlib import Path
order = Path(sys.argv[1]).read_text(encoding='utf-8').split()
idx = {c: i for i, c in enumerate(order)}
deps = {'c2': ['c1'], 'c3': ['c1'], 'c4': ['c1'], 'c5': ['c2', 'c3', 'c4']}
for cid, ds in deps.items():
    for dep in ds:
        if not idx[dep] < idx[cid]:
            raise SystemExit(f'{dep} not before {cid}')
if order != ['c1', 'c2', 'c3', 'c4', 'c5']:
    raise SystemExit(f'unexpected order: {order}')
print('  topological order valid')
'@
Invoke-PyCheck $code ($dag -join " ")
if ($LASTEXITCODE -ne 0) { Write-Error "DAG order invalid"; exit 1 }
$cycle = (python -m dev_harness.engine.cli plan --workspace $ws `
    --requirement fixtures/req_cycle.md --mock --print-dag 2>&1) -join "`n"
$cycleCode = $LASTEXITCODE
Write-Host "  cycle -> exit=$cycleCode"
if ($cycleCode -eq 0) { Write-Error "cycle fixture was not rejected"; exit 1 }
if ($cycle -notmatch "Cycle detected") { Write-Error "cycle message missing: $cycle"; exit 1 }
if ($cycle -notmatch "c1") { Write-Error "cycle did not name c1: $cycle"; exit 1 }
if ($cycle -notmatch "c2") { Write-Error "cycle did not name c2: $cycle"; exit 1 }

Write-Host "[P8] step 3: parallel execution (peak concurrency exactly 3; no chunk before deps)"
$ws = Join-Path $WsRoot "parallel"; Initialize-Repo $ws
$par = (python $Driver parallel --workspace $ws `
    --requirement fixtures/req_diamond.md --max-parallel 3) -join "`n"
Write-Host "  $par"
$code = @'
import json, sys
from pathlib import Path
sys.path.insert(0, '.')
from dev_harness.engine.cli import parse_chunks
data = json.load(open(sys.argv[1], encoding="utf-8"))
if data['peak'] != 3:
    raise SystemExit(f"peak {data['peak']} != 3")
if data['chunks'] != ['c1', 'c2', 'c3', 'c4', 'c5']:
    raise SystemExit(f"chunks {data['chunks']}")
frames = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
start, end = {}, {}
for i, f in enumerate(frames):
    (start if f['phase'] == 'start' else end)[f['chunk']] = i
chunks = {c.chunk_id: c for c in parse_chunks(Path('fixtures/req_diamond.md').read_text(encoding='utf-8'))}
for cid, chunk in chunks.items():
    for dep in chunk.dependencies:
        if not start[cid] > end[dep]:
            raise SystemExit(f'{cid} started before {dep} finished')
print(f"  peak={data['peak']}; dependency order valid ({len(frames)} frames)")
'@
Invoke-PyCheck $code $par "$ws/reports/parallel_trace.json"
if ($LASTEXITCODE -ne 0) { Write-Error "parallel execution proof failed"; exit 1 }

Write-Host "[P8] step 4: isolation proof (3 worktrees + primary clean throughout)"
$code = @'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
if d['worktrees'] != 3:
    raise SystemExit(f"worktrees {d['worktrees']} != 3")
for key in ('primary_clean_before', 'primary_clean_mid', 'primary_clean_after'):
    if not d[key]:
        raise SystemExit(f'primary dirty: {key}')
print('  3 worktrees; primary clean before/mid/after')
'@
Invoke-PyCheck $code $par
if ($LASTEXITCODE -ne 0) { Write-Error "isolation proof failed"; exit 1 }

Write-Host "[P8] step 5: merge proof (all chunk changes; one commit per chunk; no lost writes)"
$code = @'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
if not d['merged']:
    raise SystemExit('merged file missing from primary')
if d['commits'] != len(d['chunks']):
    raise SystemExit(f"commits {d['commits']} != chunks {len(d['chunks'])}")
if d['lost_writes']:
    raise SystemExit(f"lost writes: {d['lost_writes']}")
print(f"  merged; {d['commits']} commits; 0 lost writes")
'@
Invoke-PyCheck $code $par
if ($LASTEXITCODE -ne 0) { Write-Error "merge proof failed"; exit 1 }

Write-Host "[P8] step 6: conflict surfacing (IntegrationConflict names file + both ids; primary unmodified)"
$ws = Join-Path $WsRoot "conflict"; Initialize-Repo $ws
$con = (python $Driver conflict --workspace $ws --requirement fixtures/req_conflict.md) -join "`n"
Write-Host "  $con"
$code = @'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
for key in ('conflict', 'names_file', 'names_both_chunks', 'head_unmodified', 'primary_clean'):
    if not d[key]:
        raise SystemExit(f'conflict proof failed: {key}')
print('  conflict names file + both chunks; primary unmodified')
'@
Invoke-PyCheck $code $con
if ($LASTEXITCODE -ne 0) { Write-Error "conflict surfacing failed"; exit 1 }

Write-Host "[P8] step 7: retry + escalation (3x then Architect; e2e ceiling -> HITL; terminal FAILED)"
$ws = Join-Path $WsRoot "failing"; Initialize-Repo $ws
$fail = (python $Driver failing --workspace $ws --requirement fixtures/req_failing.md 2>$null) -join "`n"
Write-Host "  $fail"
$code = @'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
if d['status'] != 'FAILED':
    raise SystemExit(f"status {d['status']} != FAILED")
if not d['terminal'] or d['gate'] != 'STOPPED':
    raise SystemExit(f"not terminal: gate={d['gate']} terminal={d['terminal']}")
if d['e2e_retry_count'] != 2:
    raise SystemExit(f"e2e_retry_count {d['e2e_retry_count']} != 2 (unbounded?)")
print(f"  terminal FAILED; e2e_retry_count={d['e2e_retry_count']} (bounded)")
'@
Invoke-PyCheck $code $fail
if ($LASTEXITCODE -ne 0) { Write-Error "bounded-retry proof failed"; exit 1 }
$code = @'
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
'@
Invoke-PyCheck $code
if ($LASTEXITCODE -ne 0) { Write-Error "retry routing proof failed"; exit 1 }

Write-Host "[P8] step 8: HITL gate (halts at approval; checkpoint persisted; approve advances one node)"
$hitl = (python $Driver hitl --workspace $ws) -join "`n"
Write-Host "  $hitl"
$code = @'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
if not d['halted']:
    raise SystemExit('gate did not halt at approval')
if not d['checkpoint_persisted']:
    raise SystemExit('no checkpoint persisted at the halt')
if d['nodes_advanced'] != 1:
    raise SystemExit(f"advanced {d['nodes_advanced']} nodes, expected 1")
print('  halted; checkpoint persisted; approve advanced exactly one node')
'@
Invoke-PyCheck $code $hitl
if ($LASTEXITCODE -ne 0) { Write-Error "HITL gate proof failed"; exit 1 }

Write-Host "[P8] step 9: critic containment (CriticScopeViolation; diff confined to tui_state)"
$drill = (python -m dev_harness.engine.cli critic-drill --workspace $ws --mock) -join "`n"
Write-Host "  $drill"
if ($drill -notmatch "CriticScopeViolation") { Write-Error "no CriticScopeViolation: $drill"; exit 1 }
if ($drill -notmatch "diff: \['tui_state'\]") { Write-Error "diff not confined to tui_state: $drill"; exit 1 }

Write-Host "[P8] step 10: budget compliance (prompt <= context_window - max_output; trace capped)"
$bud = (python $Driver budget --workspace $ws) -join "`n"
Write-Host "  $bud"
$code = @'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
if not d['prompt_fits']:
    raise SystemExit('prompt exceeded context_window - max_output')
if not d['bounded'] or d['trace_lines'] > 51:
    raise SystemExit(f"trace not capped: {d['trace_lines']} lines")
if not (d['first_frame'] and d['last_frame']):
    raise SystemExit('trace cap dropped the first or last frame')
print(f"  budget={d['budget']}; trace capped at {d['trace_lines']} lines (first+last kept)")
'@
Invoke-PyCheck $code $bud
if ($LASTEXITCODE -ne 0) { Write-Error "budget compliance failed"; exit 1 }

Write-Host "[P8] step 11: worktree checkpoint proof (capture in-flight, restart, restore each worktree)"
$ws = Join-Path $WsRoot "checkpoint"; Initialize-Repo $ws
$ck = (python $Driver checkpoint --workspace $ws) -join "`n"
Write-Host "  $ck"
$code = @'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
for key in ('head_matches', 'file_restored', 'primary_clean'):
    if not d[key]:
        raise SystemExit(f'checkpoint restore failed: {key}')
print('  worktree restored field-for-field; primary clean')
'@
Invoke-PyCheck $code $ck
if ($LASTEXITCODE -ne 0) { Write-Error "worktree checkpoint proof failed"; exit 1 }

Write-Host "[P8] step 12: mutation gate (engine focus set >= 80%; 0 survivors in readiness + worktree binding)"
# PLATFORM LIMIT (plan R2 class): mutmut refuses to run on native Windows
# ("To run mutmut on Windows, please use the WSL"). Detect that and defer the
# gate explicitly rather than reporting a false failure; on WSL2/POSIX the gate
# runs for real and a surviving focus-set mutant still fails the protocol.
$mutmutProbe = (python -m mutmut run --paths-to-mutate dev_harness.engine.dag 2>&1) -join "`n"
if ($mutmutProbe -match "use the WSL") {
    Write-Host "  DEFERRED: mutmut requires WSL2/POSIX (plan R2); run this step there."
} else {
    python scripts/mutation_gate.py `
        --packages engine.dag,engine.worker_pool,engine.worker_workspace,engine.integrator
    if ($LASTEXITCODE -ne 0) { Write-Error "mutation gate failed"; exit 1 }
    if (-not (Test-Path $MutReport)) { Write-Error "mutation report missing: $MutReport"; exit 1 }
    $code = @'
import json, sys
data = json.load(open(sys.argv[1], encoding='utf-8'))
for mod in ('engine.dag', 'engine.worker_pool', 'engine.worker_workspace', 'engine.integrator'):
    entry = data.get(mod)
    if entry is None or entry.get('score') is None:
        raise SystemExit(f'{mod}: no mutation score')
    if entry['score'] < 80.0:
        raise SystemExit(f"{mod}: score {entry['score']} < 80.0")
# 0 survivors in the readiness rule (worker_pool) and the worktree binding
# (worker_workspace): a surviving mutant there is a rejection criterion.
for mod in ('engine.worker_pool', 'engine.worker_workspace'):
    if data[mod]['score'] != 100.0:
        raise SystemExit(f"{mod}: survivors remain (score {data[mod]['score']})")
print('  engine focus set >= 80%; 0 survivors in readiness + worktree binding')
'@
    Invoke-PyCheck $code $MutReport
    if ($LASTEXITCODE -ne 0) { Write-Error "mutation gate failed"; exit 1 }
}

Write-Host "[P8] step 13: emit acceptance report + human sign-off"
if (-not (Test-Path $Report)) { Write-Error "acceptance report missing (8.D reviewer must sign): $Report"; exit 1 }
if (-not (Select-String -Path $Report -Pattern '"verdict": "ACCEPTED"' -Quiet)) {
    Write-Error "verdict not ACCEPTED: $Report"; exit 1
}
$code = @'
import json, sys
data = json.load(open(sys.argv[1], encoding='utf-8'))
signed = data.get('signed_by', '')
if not signed:
    raise SystemExit('acceptance report has no signed_by')
if 'agent' in signed.lower():
    raise SystemExit(f'signed_by is not a human: {signed!r}')
print(f'  verdict ACCEPTED; signed_by={signed!r}')
'@
Invoke-PyCheck $code $Report
if ($LASTEXITCODE -ne 0) { Write-Error "human sign-off missing"; exit 1 }

Write-Host "P8 acceptance OK"
