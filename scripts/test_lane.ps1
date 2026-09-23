#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Run a Dev Harness test lane.
.DESCRIPTION
    smoke    - FAST lane for regular/daily runs. Excludes NIGHTLY markers
               (timing/slow/e2e), coverage-padding `*gaps*` files, spikes, and
               meta-tests (tests/tooling). Use this as the default inner loop.
    full     - the ENTIRE suite. Run manually / on demand (phase gates).
    nightly  - only the NIGHTLY tier (timing/slow/e2e).
    coverage - full suite with branch coverage (the `make ci` coverage step).
.EXAMPLE
    ./scripts/test_lane.ps1 smoke
    ./scripts/test_lane.ps1 full tests/engine -x
#>
[CmdletBinding()]
param(
    [ValidateSet('smoke', 'full', 'nightly', 'coverage')]
    [string]$Lane = 'smoke',

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ExtraArgs = @()
)

$ErrorActionPreference = 'Stop'

switch ($Lane) {
    'smoke' {
        $pytestArgs = @(
            'tests', '-q',
            '-m', 'not timing and not slow and not e2e',
            '--ignore-glob=**/*gaps*.py',
            '--ignore=tests/spikes',
            '--ignore=tests/tooling',
            '--timeout=120'
        )
    }
    'full' { $pytestArgs = @('tests', '-q') }
    'nightly' { $pytestArgs = @('tests', '-q', '-m', 'timing or slow or e2e') }
    'coverage' {
        $pytestArgs = @('tests', '-q', '--cov=dev_harness', '--cov-branch', '--cov-report=term-missing')
    }
}

Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    Write-Host "test lane: $Lane" -ForegroundColor Cyan
    & python -m pytest @pytestArgs @ExtraArgs
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}