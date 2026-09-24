# Phase 7 acceptance protocol (V11 7.D) - PowerShell twin.
# PLATFORM LIMIT: the 7.D protocol attaches a live TUI to a live engine daemon
# over AF_UNIX IPC (plan R2: "POSIX-only (AF_UNIX, flock, killpg). WSL2 is
# supported; native Windows would fork the transport layer.").
# Native Windows Python (<=3.12) has no AF_UNIX, so the engine daemon cannot
# bind its socket and the 7.D protocol cannot run here.
#
# Run the acceptance protocol on a POSIX host / WSL2 instead:
#     bash scripts/verify_phase_07.sh
# This stub exists so `python scripts/verify_phase.py --phase 07` fails with
# a clear, actionable message instead of a confusing socket-bind error.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

Write-Host "[P7] platform limit: TUI acceptance protocol requires AF_UNIX (POSIX/WSL2)." -ForegroundColor Yellow
Write-Host "[P7] Run: bash scripts/verify_phase_07.sh on WSL2 or a POSIX host." -ForegroundColor Yellow
Write-Host "[P7] See plan R2 and docs/adr/0001-ipc-transport.md." -ForegroundColor Yellow
exit 1
