# Phase 0 acceptance protocol (V11 0.D) - PowerShell twin for hosts without WSL.
# Runs the same gates as verify_phase_00.sh on Windows.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

Write-Host "[P0] step 2: full gate"
python -m ruff check src tests scripts
python -m mypy src
python -m pytest tests -q

Write-Host "[P0] step 3: contract freeze"
python -m dev_harness.contracts.schema --emit

Write-Host "[P0] step 8: event ownership"
python scripts/check_event_ownership.py

Write-Host "[P0] step 9: transition table"
python -m dev_harness.contracts.transitions --table

Write-Host "[P0] ADR-0002 review gate"
if (-not (Test-Path docs/adr/0002-broker-topology.md)) { throw "missing ADR-0002" }

Write-Host "[P0] step 10: emit report"
python scripts/verify_phase.py --emit 00
Write-Host "P0 acceptance OK"
