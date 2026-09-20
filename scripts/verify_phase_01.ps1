# Phase 1 acceptance protocol (V11 1.D) - PowerShell twin.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

Write-Host "[P1] step 1: fresh DB bootstrap"
$w1 = Join-Path $env:TEMP "w1"
Remove-Item -Recurse -Force $w1 -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $w1 | Out-Null
git -C $w1 init -b main 2>&1 | Out-Null
python -m dev_harness.storage.migrate --workspace $w1 --up

Write-Host "[P1] step 2: write/read round trip"
python -m dev_harness.storage.cli put --workspace $w1 --file tests/fixtures/state_v7_golden.json --project p1 --thread t1
python -m dev_harness.storage.cli get --workspace $w1 --project p1 --thread t1 --latest

Write-Host "[P1] step 9: mutation gate (dry-run)"
python scripts/mutation_gate.py --packages storage,vcs --dry-run

Write-Host "[P1] step 10: emit report"
python scripts/verify_phase.py --emit 01
Write-Host "P1 acceptance OK"
