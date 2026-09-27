# Phase 10 release gate (V11 10.D) - PowerShell twin.
# Runs the SAME 9 steps as scripts/verify_phase_10.sh on Windows PowerShell.
# Steps 2 (nightly), 4 (live Ollama), 5 (cold VM) and 7 (rollback rehearsal)
# are DEFERRED on this host (infrastructure, not platform); every other step
# runs anywhere.
#
# Step 9 asserts the human-signed reports/phase_10_acceptance.json exists and is
# ACCEPTED; it does NOT create it (the implementer may not sign its own phase).
$ErrorActionPreference = "Continue"
Set-Location (Join-Path $PSScriptRoot "..")

$Report = "reports/phase_10_acceptance.json"
$PyCheck = Join-Path $env:TEMP "p10-check.py"

Write-Host "[P10] step 1: phase report audit (all 11 reports present, ACCEPTED, commit-pinned)"
python scripts/verify_phase.py --audit-all
if ($LASTEXITCODE -ne 0) { Write-Error "phase report audit failed"; exit 1 }

Write-Host "[P10] step 2: full nightly (10.1-10.4 + mutation gates on trunk)"
Write-Host "  DEFERRED: requires the nightly CI runner (timing/slow/e2e + mutation)."

Write-Host "[P10] step 3: graph-node coverage (every compiled node executed >=1x)"
python scripts/generate_traceability.py --graph-coverage
if ($LASTEXITCODE -ne 0) { Write-Error "graph-node coverage failed"; exit 1 }

Write-Host "[P10] step 4: real-model run (live local Ollama, Qwen 2.5 Coder 7B)"
Write-Host "  DEFERRED: requires a live local Ollama (10.4, NIGHTLY)."

Write-Host "[P10] step 5: cold-machine install (fresh VM, docs/runbook.md, < 10 min)"
Write-Host "  DEFERRED: requires a fresh VM and a human operator."

Write-Host "[P10] step 6: traceability (TDD section -> task -> test, no gaps)"
python scripts/generate_traceability.py --check
if ($LASTEXITCODE -ne 0) { Write-Error "traceability check failed"; exit 1 }

Write-Host "[P10] step 7: rollback rehearsal (docs/rollback.md, release tag -> prior tag)"
Write-Host "  DEFERRED: requires a human-timed rehearsal on a scratch clone."

Write-Host "[P10] step 8: release checklist (docs/release_checklist.md)"
foreach ($doc in @("docs/release_checklist.md", "docs/rollback.md", "docs/runbook.md", "docs/traceability.md")) {
    if (-not (Test-Path $doc)) { Write-Error "release doc missing: $doc"; exit 1 }
}
Write-Host "  release docs present (checklist, rollback, runbook, traceability)"

Write-Host "[P10] step 9: tag and sign (acceptance JSON ACCEPTED with a human signed_by)"
python scripts/coverage_gate.py --errors
if ($LASTEXITCODE -ne 0) { Write-Error "error-reachability gate failed"; exit 1 }
if (-not (Test-Path $Report)) { Write-Error "acceptance report missing (10.D reviewer must sign): $Report"; exit 1 }
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
print(f"  verdict ACCEPTED; signed_by={signed!r}")
'@
Set-Content -Path $PyCheck -Value $code -Encoding utf8
python $PyCheck $Report
if ($LASTEXITCODE -ne 0) { Write-Error "acceptance report invalid"; exit 1 }

Write-Host "P10 acceptance OK"
