# Phase 2 acceptance protocol (V11 2.D) - PowerShell twin.
# On Windows, AF_UNIX is unavailable; the acceptance steps run against the
# simulated socket layer (FakeSocket) which exercises the same code paths.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

Write-Host "[P2] step 1: framing codec proof"
python -c "
from dev_harness.contracts.enums import EventType
from dev_harness.contracts.events import Envelope, FileChangePayload
from dev_harness.ipc.framing import encode, decode_frame
env = Envelope(type=EventType.FILE_CHANGE, payload=FileChangePayload(type='FILE_CHANGE', path='/a', change_type='modified'))
frame = encode(env)
assert decode_frame(frame) == env
print('framing round-trip OK')
"

Write-Host "[P2] step 2: event-type coverage assertion"
python -m dev_harness.ipc.cli coverage-check --workspace $env:TEMP --fixtures tests/fixtures/events

Write-Host "[P2] step 3: flood behavior"
python -m dev_harness.ipc.cli flood --count 10000

Write-Host "[P2] step 4: snapshot frame proof"
python -c "
from dev_harness.contracts.enums import EventType
from dev_harness.contracts.events import Envelope, SnapshotPayload
from dev_harness.contracts.state import HarnessState
from dev_harness.ipc.framing import encode, FrameTooLargeError
# 16 MiB SNAPSHOT accepted
state = HarnessState(project_id='p', workspace_path='/w', thread_id='t', raw_input='x' * (16*1024*1024 - 4096))
env = Envelope(type=EventType.SNAPSHOT, payload=SnapshotPayload(type='SNAPSHOT', state=state))
frame = encode(env)
print(f'16 MiB SNAPSHOT accepted: {len(frame)} bytes')
# 17 MiB SNAPSHOT rejected
state2 = HarnessState(project_id='p', workspace_path='/w', thread_id='t', raw_input='x' * (17*1024*1024))
env2 = Envelope(type=EventType.SNAPSHOT, payload=SnapshotPayload(type='SNAPSHOT', state=state2))
try:
    encode(env2)
    raise SystemExit('FAIL: 17 MiB SNAPSHOT should be rejected')
except FrameTooLargeError:
    print('17 MiB SNAPSHOT rejected: OK')
"

Write-Host "[P2] step 5: emit report"
python scripts/verify_phase.py --emit 02
Write-Host "P2 acceptance OK"
