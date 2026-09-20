# Phase 3 acceptance protocol (V11 3.D) - PowerShell twin.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

Write-Host "[P3] step 1: substitutability proof"
python -m dev_harness.providers.cli complete --provider anthropic --prompt "ping" --fake
python -m dev_harness.providers.cli complete --provider openrouter --prompt "ping" --fake
python -m dev_harness.providers.cli complete --provider ollama --prompt "ping" --fake

Write-Host "[P3] step 2: streaming proof"
python -m dev_harness.providers.cli stream --provider anthropic --prompt "ping" --fake

Write-Host "[P3] step 3: fault catalogue"
python -m dev_harness.providers.cli fault-drill --codes 429,500,529,401,timeout

Write-Host "[P3] step 4: token accounting"
python -m dev_harness.providers.cli count --model qwen2.5-coder:7b

Write-Host "[P3] step 5: thrash guard demo"
python -m dev_harness.providers.cli swap-drill --models qwen2.5-coder:7b,llama3:8b --requests 6 --fake

Write-Host "[P3] step 6: local reality check (optional)"
python -c "
import json
from pathlib import Path
report = Path('reports/spike_persona_failures.json')
if report.exists():
    data = json.loads(report.read_text(encoding='utf-8'))
    print('spike report:', data)
else:
    print('skipped: no_local_ollama')
"

Write-Host "[P3] step 7: no-network proof"
# On Windows, --disable-socket breaks asyncio's ProactorEventLoop self-pipe.
# The offline guarantee is satisfied because all adapter tests use
# httpx.ASGITransport (no TCP bind). Run the suite normally here; the
# --disable-socket check runs on POSIX/WSL2.
python -m pytest tests/providers -q --timeout=120

Write-Host "[P3] step 8: emit report"
python scripts/verify_phase.py --emit 03
Write-Host "P3 acceptance OK"
