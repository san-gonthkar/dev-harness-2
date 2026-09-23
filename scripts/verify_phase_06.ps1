# Phase 6 acceptance protocol (V11 6.D) - PowerShell twin.
# PLATFORM LIMIT: the interrupt subsystem kills a hostile process tree with
# killpg and verifies it with pgrep/ps (plan R2: "POSIX-only (AF_UNIX, flock,
# killpg). WSL2 is supported; native Windows would fork the transport layer.").
# Native Windows has no killpg/pgrep/ps, so the 6.D protocol cannot run here.
#
# Run the acceptance protocol on a POSIX host / WSL2 instead:
#     bash scripts/verify_phase_06.sh
# This stub exists so `python scripts/verify_phase.py --phase 06` fails with
# a clear, actionable message instead of a confusing process-kill error.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

Write-Host "[P6] platform limit: interrupt protocol requires POSIX (killpg/pgrep/ps)." -ForegroundColor Yellow
Write-Host "[P6] Run: bash scripts/verify_phase_06.sh on WSL2 or a POSIX host." -ForegroundColor Yellow
Write-Host "[P6] See plan R2 and docs/adr/0001-ipc-transport.md." -ForegroundColor Yellow
exit 1
