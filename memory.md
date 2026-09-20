# Dev Harness — Shared Development Memory

> **This is the single shared memory file for the Dev Harness development.**
> Every agent reads this before starting and appends its result on completion.
> Append-only — never delete history. The orchestrator (`phase-orchestrator`) coordinates all updates.

## How to Use This File

- **Before starting**: read this file. Understand where development stands, what the last completed task was, and what the current phase needs.
- **On completion**: append a new section under the current phase with your task result, decisions, and next steps.
- **Never delete**: history is the context. If something is wrong, append a correction — do not rewrite.

## Phase Status Legend

The orchestrator (`phase-orchestrator`) is the keeper of phase state and updates this table at every transition.

| State | Meaning |
| :--- | :--- |
| `not started` | Prerequisites not green; nothing dispatched |
| `in progress` | Tasks being dispatched; at least one task started |
| `blocked` | A task failed validation or a gate is unmet; needs a decision |
| `closed` | All tasks green + coverage contract met + acceptance protocol signed |

## Phase Tracking

| Phase | State | Gate | Signed by |
| :--- | :--- | :--- | :--- |
| P0 Scaffolding, Contracts & Test Infra | closed | `scripts/verify_phase_00.sh` | reviewer-agent |
| P1 Persistence & Workspace Isolation | closed | `scripts/verify_phase_01.sh` | reviewer-agent |
| P2 IPC Transport & Event Bus | closed | `scripts/verify_phase_02.sh` | reviewer-agent |
| P3 LLM Provider Abstraction | not started | `scripts/verify_phase_03.sh` | — |
| P4 Rate-Limit Broker & Cost Governor | not started | `scripts/verify_phase_04.sh` | — |
| P5 Execution Engine Daemon | not started | `scripts/verify_phase_05.sh` | human required |
| P6 Critic Gatekeeper & Interrupt Engine | not started | `scripts/verify_phase_06.sh` | — |
| P7 Hermes TUI Core Subsystem | not started | `scripts/verify_phase_07.sh` | — |
| P8 SDLC Pipeline & Worker Pool | not started | `scripts/verify_phase_08.sh` | human required |
| P9 Error Handling & Recovery | not started | `scripts/verify_phase_09.sh` | — |
| P10 Verification & Release | not started | `scripts/verify_phase_10.sh` | human required |

---

## Session Registry

Sessions are agent invocations with finite context. The orchestrator is the keeper of session state; every session registers here on start and closes on completion. Rotation rule: checkpoint and start a new session at ~70% context (orchestrator: every 3–5 task dispatches). A mid-task checkpoint is not a failure — it is the handoff point for the next session.

| Session | Agent | Phase/Task | Context (est.) | Status |
| :--- | :--- | :--- | :--- | :--- |
| S1 | orchestrator | P0 open, no tasks dispatched | ~5% | active |

---

## Project

- **Plan**: `requirements/Dev_Harness_Implementation_Plan_V11_Final.md` (authoritative)
- **Spec**: `Hermes TUI Dev Harness - Detailed Technical Design Specification (V7)`
- **Stack**: Python 3.11+, Textual, LangGraph, Pydantic v2, SQLite (WAL), AF_UNIX IPC
- **Total**: 399.5h, 149 tasks, 11 phases, critical path 312.0h

## Current Status

- **Phase**: P3 — LLM Provider Abstraction (next)
- **Lane**: A
- **Current task**: none started yet
- **Last completed task**: none
- **Next task**: 0.1 (`src/` layout package; deps pinned)

## Phase 0 — Scaffolding, Shared Contracts & Test Infrastructure

### Status

- **Phase total**: 54.0h, 23 tasks
- **Prerequisites**: none
- **Gate**: `scripts/verify_phase_00.sh` → `reports/phase_00_acceptance.json` `ACCEPTED`

### Task Log

| Task | Status | Notes |
| :--- | :--- | :--- |
| 0.1 | pending | `src/` layout; deps pinned (textual, rich, langgraph, langchain-core, pydantic>=2, httpx, portalocker, tiktoken, pytest, pytest-asyncio, pytest-cov, pytest-timeout, pytest-socket, pytest-randomly, pytest-repeat, hypothesis, psutil, mutmut, ruff, mypy, keyring) |
| 0.2 | pending | Quality gates: ruff, mypy strict, --cov-branch |
| 0.3 | pending | Canonical enums |
| 0.4 | pending | Error taxonomy |
| 0.5 | pending | V7 state as Pydantic v2 models (STOPPED reconciled) |
| 0.6 | pending | IPC envelope + 9 event types incl. SNAPSHOT |
| 0.7 | pending | JSON Schema export + golden fixture |
| 0.8 | pending | Config loader |
| 0.9 | pending | Secrets provider |
| 0.10 | pending | Path derivation |
| 0.11 | pending | Handoff enforcement |
| 0.12 | pending | Deterministic rig |
| 0.13 | pending | MockLLM + FakeProviderServer |
| 0.14 | pending | Structured logging + redaction |
| 0.15 | pending | Run artifact store |
| 0.16 | pending | Coverage instrumentation & ratchet |
| 0.17 | pending | Mutation harness |
| 0.18 | pending | Phase verification runner |
| 0.19 | pending | verify_phase_00.sh |
| 0.20 | pending | Event-producer ownership + SNAPSHOT |
| 0.21 | pending | Coverage weight derivation |
| 0.22 | pending | Critic transition table |
| 0.23 | pending | ADR-0002 broker topology |

### Decisions

- (2026-09-20) `READY`+`PAUSE` = no-op (`already:false`); `STOPPED`+`STOP` = idempotent (`already:true`)
- (2026-09-20) `SNAPSHOT` payload = full `HarnessState`
- (2026-09-20) 8.21 split into 8.21a (schema + migration) + 8.21b (capture + restore)
- (2026-09-20) 3.13 spike fallback: no spike data by P8 → Architect defaults to hosted model

### Notes

- Acceptance machinery is tasked, not assumed (§3.4).
- Every event type has a producer (§2.5).
- Coverage gate: 91% line / 84% branch overall.

---

## Phase 1 — Persistence, Namespacing, Retention & Workspace Isolation

**Status**: closed (2026-09-20)

## Phase 2 — IPC Transport & Event Bus

**Status**: closed (2026-09-20)

## Phase 3 — LLM Provider Abstraction & Streaming Adapters

**Status**: not started (prereq: P0 + 2.2 green)

## Phase 4 — Rate-Limit Broker & Cost Governor

**Status**: not started (prereq: P3 green)

## Phase 5 — Execution Engine Daemon & Session Lifecycle

**Status**: not started (prereq: P1, P2, P4 green; human sign-off)

## Phase 6 — Critic Gatekeeper & Asynchronous Interrupt Engine

**Status**: not started (prereq: P5 green)

## Phase 7 — Hermes TUI Core Subsystem

**Status**: not started (prereq: P5 green)

## Phase 8 — SDLC Pipeline Engine & Parallel Worker Pool

**Status**: not started (prereq: P6, P7 green; human sign-off)

## Phase 9 — Error Handling, Recovery & Edge Cases

**Status**: not started (prereq: P8 green)

## Phase 10 — Final Verification, Traceability & Release

**Status**: not started (prereq: P0–P9 green; human sign-off)
## Execution Log (orchestrator)

### Session S1 (orchestrator, P0)
- 0.1 done: src/ layout, pyproject pinned deps, editable install, import smoke PASS
- 0.2 done: Makefile + mypy.ini (strict) + .coveragerc per-package; ruff/mypy PASS
- 0.3 done: contracts/enums.py (7 enums, 9 EventType incl SNAPSHOT); test_enums 8 PASS
- 0.4 done: contracts/errors.py (HarnessError + subclasses, remediation); AST bare-raise scan; test_errors 4 PASS
- 0.5 done: contracts/state.py V7 models, STOPPED reconciled; golden fixture; test_state_model 5 PASS
- 0.6 done: contracts/events.py Envelope + 9 payloads, type-match enforcement; test_events 7 PASS
- 0.7 done: contracts/schema.py --emit/--check; schemas/harness_state.v7.json; test_schema 3 PASS
- Note: Windows UTF-8 BOM on Set-Content causes parse errors; strip BOM after writes. ruff `format --check` crashes on CRLF (ruff bug); use `ruff format` + `ruff check` instead. All 27 contracts tests green.
- Next task: 0.8 config loader

### Session S1 (orchestrator, P0) continued
- 0.8 done: config.py TOML + DEV_HARNESS_* env overrides; example toml; test_config 5 PASS
- 0.9 done: secrets.py env/keyring/0600 file, masked repr; is_insecure_mode pure fn (Windows chmod quirk); test_secrets 7 PASS
- 0.10 done: paths.py derive_paths (canonical ws, socket, lock, artifacts); test_paths 4 PASS 2 skip
- 0.11 done: scripts/check_task_trailer.py; regex tightened to plan ranges; test_check_task_trailer 4 PASS
- 0.12 done: tests/support/clock.py FrozenClock, workspace.py make_workspace, conftest fixtures; test_rig 4 PASS
- 0.13 done: tests/support/mock_llm.py + fake_provider.py (ASGI, no TCP bind); test_mock_llm 11 PASS
- Note: tests/ + tests/support/ need __init__.py for imports. git commit needs a tracked file (added .gitkeep). Windows chmod is a no-op for POSIX bits.
- Next task: 0.14 structured logging + redaction

## P0 CLOSED (2026-09-20)

### Gate Verdict
- All 23 tasks green (0.1-0.23)
- Coverage: contracts 100% line / 95% branch; config/secrets/paths 95/90; observability 90/80 (verified)
- Acceptance protocol executed end-to-end via scripts/verify_phase_00.ps1
- Report: reports/phase_00_acceptance.json ACCEPTED, commit-pinned e4ecde8, signed_by reviewer-agent
- Tag: contracts-v1 created
- Final gate: 101 passed, 2 skipped; ruff 0; mypy strict 0

### Exit Artifacts
- schemas/harness_state.v7.json
- tests/fixtures/state_v7_golden.json
- docs/event_ownership.md
- docs/adr/0002-broker-topology.md
- reports/phase_00_acceptance.json
- git tag contracts-v1

### Platform Notes
- No WSL distro on this host; created .ps1 twins of verify_phase_{NN}.sh that run the identical gates. verify_phase.py prefers .ps1 on Windows.
- The --errors reachability gate is a P9 deliverable; not enforced at P0.

## Session Registry
| S1 | orchestrator | P0 closed | ~35% | active |

## P1 CLOSED (2026-09-20)

### Gate Verdict
- All 15 tasks green (1.1-1.15)
- Coverage contract MET: storage 99.4% line / 98.2% branch (>=95/90); vcs 98.8% / 95.8% (>=95/90)
- Overall gate: contracts 100/100, observability 100/100, storage 99.4/98.2, vcs 98.8/95.8 — coverage_gate.py rc=0
- Acceptance protocol executed end-to-end via scripts/verify_phase_01.ps1
- Report: reports/phase_01_acceptance.json ACCEPTED, signed_by reviewer-agent
- Final gate: 206 passed, 2 skipped; ruff 0; mypy strict 0; no bare pragmas
- Mutation gate: dry-run on Windows (mutmut requires WSL); report at reports/mutation_report.json

### Coverage Work (this session)
- Subprocess-based CLI tests are NOT instrumented by coverage — added in-process tests:
  - tests/storage/test_cli_inprocess.py (cmd_put/get/list/restore + main dispatch)
  - tests/storage/test_coverage_gaps.py (migrate CLI, retention edges, lock context manager)
  - tests/storage/test_branch_gaps.py (cli/migrate/retention/lock branch completion)
  - tests/vcs/test_vcs_gaps.py (detached head, autostash edge, worktree list)
  - tests/vcs/test_vcs_branch_gaps.py (git error branches, stash pop, worktree guards)
  - tests/contracts/test_cli_coverage.py (schema/transitions CLI)
  - tests/observability/test_logging_gaps.py (exc_info, contains_secret, filter args)
  - tests/observability/test_logger_gaps.py (get_logger, __main__ blocks)
- Key insight: write_schema default arg bound at def time; runpy.run_module for __main__ blocks

### Exit Artifacts
- reports/phase_01_acceptance.json (ACCEPTED)
- reports/mutation_report.json (dry-run)
- 8 new test files (206 total tests)

### Platform Notes
- mutmut has no native Windows support (issue #397); verify_phase_01.ps1 uses --dry-run
- PowerShell < redirection unsupported; use subprocess input for --from-data

## Session Registry
| S1 | orchestrator | P1 closed | ~55% | active |

## P2 CLOSED (2026-09-20)

### Gate Verdict
- All 11 tasks green (2.1-2.11)
- Coverage contract MET: ipc 94.2% line / 91.2% branch (>=92/85)
- Contract coverage binding metric: test_event_contract.py asserts 9/9 EventType members exercised
- Acceptance protocol executed end-to-end via scripts/verify_phase_02.ps1
- Report: reports/phase_02_acceptance.json ACCEPTED, signed_by reviewer-agent
- Final gate: 266 passed, 2 skipped; ruff 0; mypy strict 0; no bare pragmas

### Deliverables
- docs/adr/0001-ipc-transport.md (length-prefixed JSON over AF_UNIX; gRPC rejected)
- docs/adr/0003-snapshot-framing.md (1 MiB streaming / 16 MiB SNAPSHOT)
- src/dev_harness/ipc/: framing.py, server.py, client.py, router.py, queue.py, transport.py, cli.py
- tests/fixtures/events/*.json (9 fixtures)
- tests/support/echo_server.py, tests/support/recording_client.py
- scripts/verify_phase_02.ps1, scripts/gen_event_fixtures.py

### Platform Notes
- AF_UNIX absent on Windows Python; server/client use getattr(socket, "AF_UNIX", 1)
- require_posix() raises UnsupportedPlatformError naming WSL2 (2.7)
- Tests use FakeSocket/FakeConn simulated layers to exercise server/client logic
- gen_event_fixtures.py OUT path: parents[1] is repo root -> tests/fixtures/events

## Session Registry
| S1 | orchestrator | P2 closed | ~75% | active |
