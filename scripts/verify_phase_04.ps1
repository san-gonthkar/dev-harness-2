# Phase 4 acceptance protocol (V11 4.D) - PowerShell twin.
# PLATFORM LIMIT: the broker daemon binds an AF_UNIX socket (plan R2:
# "POSIX-only (AF_UNIX, flock, killpg). WSL2 is supported; native Windows
# would fork the transport layer."). Native Windows Python (<=3.12) has no
# AF_UNIX, so the real daemon cannot start here (bind() -> "bad family").
#
# Run the acceptance protocol on a POSIX host / WSL2 instead:
#     bash scripts/verify_phase_04.sh
# This stub exists so `python scripts/verify_phase.py --phase 04` fails with
# a clear, actionable message instead of a confusing socket error.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

Write-Host "[P4] platform limit: broker daemon requires AF_UNIX (POSIX/WSL2)." -ForegroundColor Yellow
Write-Host "[P4] Run: bash scripts/verify_phase_04.sh on WSL2 or a POSIX host." -ForegroundColor Yellow
Write-Host "[P4] See plan R2 and docs/adr/0002-broker-topology.md." -ForegroundColor Yellow
exit 1