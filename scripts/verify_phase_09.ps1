# Phase 9 acceptance protocol (V11 9.D) - PowerShell twin.
# Runs the SAME 11 steps as scripts/verify_phase_09.sh on Windows PowerShell.
# P9 needs no AF_UNIX; the chaos drill reports the two SIGKILL faults as
# `deferred` on native Windows (plan R2), so this is a full twin, not a stub.
#
# Step 11 asserts the reviewer-signed reports/phase_09_acceptance.json exists and
# is ACCEPTED; it does NOT create it (the implementer may not sign its own phase).
$ErrorActionPreference = "Continue"
Set-Location (Join-Path $PSScriptRoot "..")

$Report = "reports/phase_09_acceptance.json"
$Chaos = "reports/chaos_matrix.json"

Write-Host "[P9] step 1: error reachability (every HarnessError subclass has a negative test)"
python scripts/coverage_gate.py --errors
if ($LASTEXITCODE -ne 0) { Write-Error "error-reachability gate failed"; exit 1 }

Write-Host "[P9] step 2: kill-9 during a run (restart detects the un-finalized session)"
python scripts/chaos_drill.py --fault kill9-engine
if ($LASTEXITCODE -ne 0) { Write-Error "kill9-engine fault failed"; exit 1 }

Write-Host "[P9] step 3: kill-9 with worktrees live (stale worktrees reclaimed)"
python scripts/chaos_drill.py --fault kill9-parallel
if ($LASTEXITCODE -ne 0) { Write-Error "kill9-parallel fault failed"; exit 1 }

Write-Host "[P9] step 4: corrupt a checkpoint (digest mismatch; prior checkpoint served)"
python scripts/chaos_drill.py --fault corrupt-checkpoint
if ($LASTEXITCODE -ne 0) { Write-Error "corrupt-checkpoint fault failed"; exit 1 }

Write-Host "[P9] step 5: disk full (actionable banner; DB not corrupted)"
python scripts/chaos_drill.py --fault enospc
if ($LASTEXITCODE -ne 0) { Write-Error "enospc fault failed"; exit 1 }

Write-Host "[P9] step 6: provider outage (fall back within 1 retry; DEGRADED)"
python scripts/chaos_drill.py --fault provider-529
if ($LASTEXITCODE -ne 0) { Write-Error "provider-529 fault failed"; exit 1 }

Write-Host "[P9] step 7: total provider outage (AllProvidersUnavailable; resumable)"
python scripts/chaos_drill.py --fault all-providers-down
if ($LASTEXITCODE -ne 0) { Write-Error "all-providers-down fault failed"; exit 1 }

Write-Host "[P9] step 8: context overflow (one summarize-retry, then HITL)"
python scripts/chaos_drill.py --fault oversized-context
if ($LASTEXITCODE -ne 0) { Write-Error "oversized-context fault failed"; exit 1 }

Write-Host "[P9] step 9: secret leak attempt (key redacted in every sink)"
python scripts/chaos_drill.py --fault echo-api-key
if ($LASTEXITCODE -ne 0) { Write-Error "echo-api-key fault failed"; exit 1 }

Write-Host "[P9] step 10: rollback rehearsal (docs/rollback.md, Phase 1)"
if (-not (Test-Path "docs/rollback.md")) { Write-Error "rollback doc missing"; exit 1 }
$ws = Join-Path $env:TEMP "dev-harness-p9-rollback"
Remove-Item -Recurse -Force $ws -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force (Join-Path $ws ".dev-harness") | Out-Null
python -m dev_harness.storage.migrate --workspace $ws --up
python -m dev_harness.storage.migrate --workspace $ws --down
$code = @'
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
if 'checkpoints' in names:
    raise SystemExit('rollback left the checkpoints table behind')
print('  rollback clean; checkpoints table removed')
'@
$PyCheck = Join-Path $env:TEMP "p9-check.py"
Set-Content -Path $PyCheck -Value $code -Encoding utf8
python $PyCheck (Join-Path $ws ".dev-harness/state.db")
if ($LASTEXITCODE -ne 0) { Write-Error "rollback rehearsal failed"; exit 1 }

Write-Host "[P9] step 11: emit report (fault matrix attached)"
if (-not (Test-Path $Chaos)) { Write-Error "chaos matrix missing: $Chaos"; exit 1 }
if (-not (Test-Path $Report)) { Write-Error "acceptance report missing (9.D reviewer must sign): $Report"; exit 1 }
if (-not (Select-String -Path $Report -Pattern '"verdict": "ACCEPTED"' -Quiet)) {
    Write-Error "verdict not ACCEPTED: $Report"; exit 1
}
$code = @'
import json, sys
data = json.load(open(sys.argv[1], encoding='utf-8'))
if not data.get('signed_by'):
    raise SystemExit('acceptance report has no signed_by')
print(f"  verdict ACCEPTED; signed_by={data['signed_by']!r}")
'@
Set-Content -Path $PyCheck -Value $code -Encoding utf8
python $PyCheck $Report
if ($LASTEXITCODE -ne 0) { Write-Error "acceptance report invalid"; exit 1 }

Write-Host "P9 acceptance OK"
