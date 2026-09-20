# Event-Producer Ownership Matrix (V11 2.5)

The contract test asserts **9/9** `EventType` members have at least one producer
task and one consumer task, programmatically (`scripts/check_event_ownership.py`).
A member with no producer fails the build.

| EventType | Producer (task) | Consumer(s) | First emitted by phase |
| :--- | :--- | :--- | :--- |
| `FILE_CHANGE` | 5.11 workspace watcher | 7.2 repo-manager | P5 |
| `GIT_STATUS_UPDATE` | 5.11 workspace watcher | 7.2 repo-manager | P5 |
| `AGENT_TOKEN_STREAM` | 3.9 stream bridge | 7.3 execution-canvas | P3 |
| `TEST_PROGRESS` | 8.11 tester node | 7.3 execution-canvas | P8 |
| `MODEL_CONFIG_CHANGE` | 3.7 ollama loader / 5.5 provider gateway | 7.5 model-registry | P3 |
| `INTERRUPT_REQUEST` | 7.6 critic bar, 4.7 kill switch | 6.2 critic commands | P4 |
| `INTERRUPT_ACK` | 6.2 critic commands | 7.6 critic bar | P6 |
| `METRICS_UPDATE` | 4.10 metrics feed | 7.5 model-registry | P4 |
| `SNAPSHOT` | 5.8 state broadcast | 7.7 bridge (late attach) | P5 |

`SNAPSHOT` is the ninth type (added by task 0.20), carrying the full `HarnessState`
(decision 2026-09-20) so a late-attaching client renders exactly what others see.
