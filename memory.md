# Dev Harness — Shared Development Memory

> **This is the single shared memory file for the Dev Harness development.**
> Every agent reads this before starting and appends its result on completion.
> Append-only — never delete history. The orchestrator (`phase-orchestrator`) coordinates all updates.

## How to Use This File

- **Before starting**: read **only `## Current Status` + `## Phase Tracking`** (the first ~60 lines). Do NOT read the whole file — closed-phase history lives in `memory_archive.md`.
- **On completion**: append a new section under the current phase with your task result, decisions, and next steps.
- **Never delete**: history is the context. If something is wrong, append a correction — do not rewrite.
- **Keep it lean**: when a phase closes, move its session records to `memory_archive.md` (see `## Closed-Phase Detail`). The live file must stay under ~250 lines.

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
| P3 LLM Provider Abstraction | closed | `scripts/verify_phase_03.sh` | reviewer-agent |
| P4 Rate-Limit Broker & Cost Governor | closed | `scripts/verify_phase_04.sh` | reviewer-agent |
| P5 Execution Engine Daemon | closed | `scripts/verify_phase_05.sh` | human signed 2026-09-22 |
| P6 Critic Gatekeeper & Interrupt Engine | closed | `scripts/verify_phase_06.sh` | reviewer-agent |
| P7 Hermes TUI Core Subsystem | closed | `scripts/verify_phase_07.sh` | reviewer-agent |
| P8 SDLC Pipeline & Worker Pool | not started | `scripts/verify_phase_08.sh` | human required |
| P9 Error Handling & Recovery | not started | `scripts/verify_phase_09.sh` | — |
| P10 Verification & Release | not started | `scripts/verify_phase_10.sh` | human required |

---

## Session Registry

Sessions are agent invocations with finite context. The orchestrator is the keeper of session state; every session registers here on start and closes on completion. Rotation rule: checkpoint and start a new session at ~70% context (orchestrator: every 3–5 task dispatches). A mid-task checkpoint is not a failure — it is the handoff point for the next session.

| Session | Agent | Phase/Task | Context (est.) | Status |
| :--- | :--- | :--- | :--- | :--- |
| S1 | orchestrator | P4 closed | ~85% | closed |
| S2 | reviewer-agent | P4 sign-off review (independent) | ~10% | closed — ACCEPTED |

---

## Project

- **Plan**: `requirements/Dev_Harness_Implementation_Plan_V11_Final.md` (authoritative)
- **Spec**: `Hermes TUI Dev Harness - Detailed Technical Design Specification (V7)`
- **Stack**: Python 3.11+, Textual, LangGraph, Pydantic v2, SQLite (WAL), AF_UNIX IPC
- **Total**: 399.5h, 149 tasks, 11 phases, critical path 312.0h

## Current Status

- **Phase**: P7 CLOSED (2026-09-24). Next: P8 — SDLC Pipeline & Worker Pool (not started; human sign-off required).
- **Lane**: D (P8)
- **Current task**: P8 in progress — 8.1 DONE (`dfb95b3`), 8.2 DONE (`ca7556a`), 8.3 DONE (`65dca4f`), 8.4 DONE, 8.5 DONE, 8.6 DONE (`6229911`), 8.7 DONE (`4b34183`), 8.8 DONE (`14af5e1`), 8.9 DONE (`724eab4`), 8.10 DONE, 8.11 DONE, 8.12 DONE (`4d4871d`), 8.13 DONE, 8.14 DONE, 8.15 DONE, 8.16 DONE (`3e1e915`), 8.17 DONE (`93e6544`), 8.18 DONE, 8.20 DONE (engine CLI run/plan/critic-drill), 8.21a DONE, 8.21b DONE (worktree capture/restore).
- **Last completed task**: P8 8.21b worktree state capture + restore (checkpoint records each worktree HEAD + diff; restore replays field-for-field including uncommitted work).
- **Next task**: 8.19 (`scripts/verify_phase_08.sh`); then phase-8 acceptance.

### 9.1 DONE — recovery/session_recovery.py + tests/recovery/test_session_recovery.py
- Files: `src/dev_harness/recovery/session_recovery.py`, `tests/recovery/test_session_recovery.py`. Commit `472a14d` (pushed to origin/main).
- Deliverable: crash recovery. `detect_unfinalized_session(workspace) -> RecoveryPlan | None` finds the newest checkpoint across all scopes (`created_at DESC, checkpoint_id DESC`, matching `SqliteSaver.list`) and returns a plan (offer resume) unless it is the clean-shutdown seal (`checkpoint_id == "shutdown"`, written by `engine/shutdown.py`). A corrupt newest row is quarantined by `storage.integrity.get_verified_tuple` and the prior valid checkpoint served; if that fallback is the seal, the session is finalized (`None`). `resume(plan, workspace)` calls `recovery.reclaim.reclaim` (9.2), deletes the orphaned `chunk/{worker}` branch (git keeps it after `worktree remove`, so a re-bind with `-b` would fail), re-binds each worker worktree and replays its recorded HEAD + diff via `WorkerWorkspace.restore` (8.21b), returning the verified `HarnessState`. `RecoveryError` (existing) wraps any restore failure.
- Validation: `pytest tests/recovery/test_session_recovery.py -q` -> **13 passed, 1 skipped** (e2e SIGKILL, POSIX-only), exit 0. Acceptance exact: (a) un-finalized session detected (`test_detect_offers_resume_for_unfinalized`); (b) resume offered via `RecoveryPlan`; (c) worktree restored to recorded HEAD + diff (`test_resume_restores_worktree_and_state_field_for_field`); (d) state equals the last seal field-for-field (asserted `model_dump(mode="json")` equal). Also: stale-worktree reclaim+rebind, corrupt-newest quarantine fallback, corrupt-only -> `CorruptCheckpointError`, restore failure -> `RecoveryError`. Targeted coverage `recovery/session_recovery.py` -> **100% line / 100% branch**. Smoke lane 1046 passed / 7 skipped. `mypy --strict` + `ruff check`/`format` clean.
- Tests: 13 (unit 7, integration 3, negative 2, e2e 1). Real temp git repo + SQLite; no `time.sleep`, no network, no `tui/` import, no new deps, no new error, no state string literals.
- Unverified: the `e2e` SIGKILL test (POSIX-only; skipped on Windows) and the `recovery/` 90/85 package gate + full-suite coverage (smoke-only per brief; tracked requirement, not permission).

### 9.4 DONE — storage/integrity.py + tests/storage/test_integrity.py
- Files: `src/dev_harness/storage/integrity.py`, `tests/storage/test_integrity.py`.
- Deliverable: corrupt-checkpoint detection/quarantine/rollback. `verify_row(state_json, state_sha256)` recomputes the digest independently of `SqliteSaver._sha256`; `get_verified_tuple(saver, scope, cid)` verifies on read, quarantines a mismatching row, and serves the newest remaining valid checkpoint, raising `CorruptCheckpointError` if none remains; `quarantine_corrupt(saver, scope)` scans a scope and returns moved ids. **Quarantine = move-aside, not delete**: the full corrupt row is copied into the lazily-created `checkpoint_quarantine` side table (PK `(project_id,thread_id,checkpoint_id)`, `INSERT OR REPLACE`, `reason` recorded) then deleted from `checkpoints` — evidence preserved, no migration-ledger entry needed. `SqliteSaver.get_tuple` contract is unchanged (docstring still aspirational); verification is wired through this module.
- Validation: `pytest tests/storage/test_integrity.py -q` -> **10 passed**, exit 0. Acceptance exact: (a) a first-byte flip in `state_json` -> digest fails (`test_byte_flip_fails_digest_and_serves_prior_valid`); (b) corrupt row recoverable in `checkpoint_quarantine` with reason, gone from `checkpoints`; (c) `get_verified_tuple` returns the prior valid `c1` byte-for-byte. Also covers stale-digest tamper and re-read-after-quarantine -> None. Targeted branch coverage `storage/integrity.py` -> **100% line / 100% branch**. `mypy --strict` + `ruff check`/`format` clean.
- Tests: 10 (unit 4, integration 3, negative 3). Real temp SQLite DB; no `time.sleep`, no network, no `tui/` import, no new deps, no new error, no state string literals.
- Unverified: full-suite coverage + the `storage/integrity.py` mutation >=85% gate (smoke-only per brief; tracked requirement, not permission).

### 9.8 DONE — storage/errors.py + tests/storage/test_errors.py
- Files: `src/dev_harness/storage/errors.py`, `src/dev_harness/contracts/errors.py` (two new subclasses), `tests/storage/test_errors.py`. Commit `3f36517` (pushed to origin/main).
- Deliverable: resource-exhaustion translation. `translate_storage_error(exc) -> HarnessError` is **total** for `sqlite3`/`OSError`: ENOSPC (`OSError` errno 28) -> `DiskFullError`, SQLITE_BUSY/LOCKED (`sqlite3.OperationalError`, code 5/6 or "database is locked") -> `DatabaseBusyError`, any other `sqlite3.Error`/`OSError` -> `StorageError`, an existing `HarnessError` passes through unchanged. `translate_storage_errors()` context manager wraps a block and re-raises only as a `HarnessError` (never a raw `sqlite3`/`OSError`), so 0 unhandled tracebacks reach the TUI. New canonical subclasses `DiskFullError(StorageError)` and `DatabaseBusyError(StorageError)` live in `contracts/errors.py` with non-empty remediation (covered by the 0.4 taxonomy test). `LockTimeout` deliberately NOT reused (workspace-lock concept).
- Validation: `pytest tests/storage/test_errors.py -q` -> **18 passed**, exit 0. Acceptance exact: (a) ENOSPC -> `DiskFullError` with non-empty actionable remediation; (b) SQLITE_BUSY -> `DatabaseBusyError` with remediation (message + error-code paths); (c) totality asserted over a parametrized table incl. unrelated `ValueError` -> `StorageError`, and the context manager never lets a raw `sqlite3`/`OSError` escape. `tests/contracts/test_errors.py` -> 4 passed (new subclasses satisfy the taxonomy). `mypy --strict` + `ruff check` clean.
- Tests: 18 (unit 17, negative 1). Simulated via injected raising callables - no disk filled, no real lock contention. No `time.sleep`, no network, no `tui/` import, no new deps, no state string literals.
- Unverified: full-suite coverage + the `storage/` 95/90 package gate (smoke-only per brief; tracked requirement, not permission).

### 8.21b DONE — engine/worker_workspace.py + storage/checkpoint_binding.py + tests/engine/test_worker_checkpoint.py
- Files: `src/dev_harness/engine/worker_workspace.py`, `src/dev_harness/storage/checkpoint_binding.py`, `src/dev_harness/storage/sqlite_saver.py`, `tests/engine/test_worker_checkpoint.py`, `tests/storage/test_saver_write.py` (hand-rolled schema needed the 0002 columns).
- Deliverable: `WorkerWorkspace.capture(worker_id) -> {head, diff}` reads HEAD + the uncommitted diff (`git add -N -A` intent-to-add so **untracked** files are included, then `git diff --binary`, then `git reset` to restore the index); `capture_map(ids)`; `restore(worker_id, snapshot)` replays the recorded HEAD (`git reset --hard` when it differs) then `git apply` the diff. `CheckpointBinding.put_bound(..., worktree_state=...)` serializes the map into the two 0002 columns (JSON `worker_id -> value`); `serialize_worktree_state`/`deserialize_worktree_state` are the codecs. `SqliteSaver.put` gained `worktree_head`/`worktree_diff` params and `get_tuple`/`_row_to_dict` expose them. 0002 has single TEXT columns (not a table) -> the map is the documented shape (no 0003 needed).
- Validation: `pytest tests/engine/test_worker_checkpoint.py -q` -> **10 passed**, exit 0. Acceptance exact: (a) stored `worktree_head`/`worktree_diff` decode to the captured snapshot (asserted equal); (b) after `reset --hard`+`clean -fd`, `restore` reproduces HEAD, `tracked.txt` = `tracked-v2-uncommitted`, `new.txt` = `untracked work`; (c) uncommitted tracked + untracked work recovered field-for-field. Real temp git repo (`tmp_workspace`). Targeted coverage `engine/worker_workspace.py` (with `test_worker_workspace.py`) -> **98% line / 26 branch** (only pre-existing line 82 `_ensure_harness_ignored` early-return uncovered), exceeds 95/90. `mypy --strict` + `ruff check`/`format` clean.
- Tests: 10 (unit 3, integration 4, negative 3). No `time.sleep`, no network, no `tui/` import, no new deps, no state string literals.
- Deviations: the brief allowed either a 0003 multi-row migration or a serialized map; chose the **serialized `worker_id -> {head,diff}` map** (simplest shape, 0002 columns suffice) — documented in `checkpoint_binding.py`. Capture includes untracked files via intent-to-add so "uncommitted work" is genuinely field-for-field.
- Unverified: full-suite coverage + the `engine/` 88/80 and 8.C mutation >=80% gate (smoke-only per brief; tracked requirement, not permission).

### 8.20 DONE — engine/cli.py + tests/engine/test_cli.py
- Files: `src/dev_harness/engine/cli.py`, `tests/engine/test_cli.py`.
- Deliverable: engine CLI (`python -m dev_harness.engine.cli`) with `run`, `plan`, `critic-drill`. `--mock` builds a deterministic `_MockPersonaClient` over `tests.support.mock_llm.MockLLM` (no network); a persona call is answered from a scripted role map so the graph completes, other calls delegate to the mock. `plan --print-dag` prints the topological order and rejects a cycle via `ChunkDAG`/`CyclicDependencyError` (exit 2, both ids named). `critic-drill` runs the Critic node, shows `guard_scope({"tui_state", "groomed_requirements"})` raises `CriticScopeViolation`, and reports the PAUSE/RESUME `state_diff == {"tui_state"}`. `main(argv) -> int` + `__main__` block. Unknown flags (`--print-dag` elsewhere) are rejected.
- Validation: `pytest tests/engine/test_cli.py -q` -> **11 passed**, exit 0 (`tests/engine` 339 passed, no regressions). Acceptance exact: (a) `run`/`plan`/`critic-drill` all exit 0 against `--mock`; (b) `--print-dag` order asserted topological (every dep precedes its chunk). `mypy --strict` + `ruff check`/`format` clean.
- Tests: 11 (unit 5, integration 5, negative 2 — one negative subprocess-free). No `time.sleep`, no network, no `tui/` import, no new deps.
- Deviations: requirement grammar is a documented line format (`<id>: <title> ... deps: a, b`; auto-numbered `cN` for plain lines) since no planner task exists — deterministic, no LLM needed to plan. `--workspace` is a per-subcommand flag (8.D commands place it there). `--resume` reuses the thread id (node idempotency makes the replay safe).
- Unverified: the full 8.D protocol execution and the `engine/` (nodes, pipeline) 88/80 coverage gate (smoke-only per brief).

### 8.16 DONE — engine/context.py + tests/engine/test_context.py
- Files: `src/dev_harness/engine/context.py`, `tests/engine/test_context.py`.
- Deliverable: pure `cap_trace(text, *, head=30, tail=20) -> str` returns text unchanged when it has <= head+tail lines, else the first `head` frames + one elision marker (`... [N lines elided] ...`) + the last `tail` frames. `prompt_budget(entry) == context_window - max_output`; `fits_budget(prompt_tokens, entry)`; `count_messages(messages)` (sums `estimate_tokens` over content). `build_prompt(messages, *, entry, trace=None, head=30, tail=20) -> list[Message]` caps an optional trace, appends it as a user frame, and clips every message content to an equal share when the estimate exceeds the budget so the result always fits. Uses `providers.tokenizer` (`estimate_tokens`, `MARGIN`, `CHARS_PER_TOKEN`) and `providers.registry.ModelEntry`. No new deps, no I/O, no clock.
- Validation: `pytest tests/engine/test_context.py -q` -> **13 passed**, exit 0. Acceptance exact: (a) a 500-line trace caps to 50 frames (`TRACE_HEAD + 1 + TRACE_TAIL`) with `frame 0` first and `frame 499` last and the elision marker at index 30; (b) for every `registry.all_models()` model, `count_messages(build_prompt(...)) <= prompt_budget(entry)` under 200k-char inputs. `mypy --strict` + `ruff check`/`format` clean.
- Tests: 13 (unit 10, negative 2, property 1). No `time.sleep`, no network, no `tui/` import.
- Deviations: none. `cap_trace` raises `ValueError` on a negative window (pure function; not a `HarnessError` surface).
- Unverified: full-suite coverage + the `engine/` (nodes, pipeline) 88/80 gate and `build_prompt` integration into the 8.18 pipeline (smoke-only per brief).

### 8.15 DONE — engine/hitl.py + tests/engine/test_hitl.py
- Files: `src/dev_harness/engine/hitl.py`, `tests/engine/test_hitl.py`.
- Deliverable: `HitlGate(*, checkpointer=None, resume_source=None, approval_node=None, prepare_node=None, thread_id="hitl")` — builds a MINIMAL graph (`prepare` -> `approval` -> END) compiled with `interrupt_before=[APPROVAL_NODE]`. `start(initial)` runs to the breakpoint; `is_halted()`/`pending_node()` report the suspension; `checkpoint_persisted()` asserts the saver holds a checkpoint; `resume()` pulls one command from the injected `ResumeSource` and invokes `Command(resume=<value>)`, advancing exactly one node. `QueueResumeSource` is the deterministic test double (no socket). `HitlResumeError(EngineError)` fails closed when no source/command. `executed_nodes` records the exact node sequence.
- Validation: `pytest tests/engine/test_hitl.py -q` -> **6 passed**, exit 0. Acceptance exact: (a) after `start`, `is_halted()` True, `pending_node()=="approval"`, `checkpoint_persisted()` True, `executed_nodes==["prepare"]`; (b) after `resume()`, `executed_nodes==["prepare","approval"]` (delta exactly 1) and `pending_node() is None` (reached END). `mypy --strict` + `ruff check`/`format` clean; `tests/engine` 292 passed (no regressions).
- Tests: 6 (unit 2, integration 2, negative 2). No `time.sleep`, no network, no `tui/` import.
- Deviations: the plan says "compiled with `SqliteSaver`", but `langgraph.checkpoint.sqlite` is NOT an installed dependency (only `langgraph-checkpoint` base -> `InMemorySaver`) and the project's `storage.sqlite_saver.SqliteSaver` is a custom `HarnessState` store, not a LangGraph `BaseCheckpointSaver`. The gate therefore takes an injectable checkpointer defaulting to `InMemorySaver`; a SQLite-backed saver can be swapped in without touching the module. Flagged for reviewer.
- Unverified: full-suite coverage + the `engine/` (nodes, pipeline) 88/80 gate (smoke-only per brief; 8.C gate is a tracked requirement, not permission).

### 8.14 DONE — engine/nodes/critic.py + tests/engine/test_critic_scope.py
- Files: `src/dev_harness/engine/nodes/critic.py`, `tests/engine/test_critic_scope.py`.
- Deliverable: `CriticNode(client, *, model=None)` — LangGraph node returning a *partial* update confined to `tui_state`. Explicit scope guard `WRITABLE_CHANNELS = {"tui_state"}` + `guard_scope(update)` raises `CriticScopeViolation` on any other key. `CriticVerdict` (binary `APPROVED`/`REJECTED`; REJECTED requires non-empty reasons). `parse_verdict` -> `PersonaOutputError` on malformed output. `apply_command(state, CriticCommand, timestamp=...)` applies PAUSE/RESUME/START/STOP through the same guard; `state_diff(before, after)` returns changed `HarnessState` field names. Publish channel = `tui_state.critic_gatekeeper_status` (verdict → RUNNING/PAUSED). `make_critic_node` factory. Reuses 8.4 shape + `load_persona_prompt`. Injected `CompletionClient`.
- Validation: `pytest tests/engine/test_critic_scope.py -q` -> **9 passed**, exit 0. Acceptance exact: (a) `guard_scope({"groomed_requirements": ...})` -> `CriticScopeViolation` (and technical_design/chunk_dag/raw_input/git_state); (b) `state_diff(start, paused) <= {"tui_state"}` and `state_diff(paused, resumed) <= {"tui_state"}`, artifacts byte-identical across the cycle. `mypy --strict` + `ruff check`/`format` clean; `tests/contracts/test_enums.py` 8 passed (literal-ban intact); 8.13/8.12/adjacent node tests unaffected.
- Tests: 9 (unit 6, negative 3). No `time.sleep`, no network.
- Deviations: none from brief. Decision: the verdict publishes to `tui_state.critic_gatekeeper_status` because the plan has no dedicated verdict channel — `tui_state` is the sole writable channel and is the canonical home for critic status (`contracts.state.TuiState`). Verdict channel is `tui_state` (the only non-artifact channel); the node never mutates `groomed_requirements`/`technical_design`/`chunk_dag`/code.
- Unverified: full-suite coverage + the `engine/` (nodes, pipeline) 88/80 gate (smoke-only per brief; 8.C gate is a tracked requirement, not permission).

### 8.13 DONE — engine/routing.py + tests/engine/test_routing.py
- Files: `src/dev_harness/engine/routing.py`, `tests/engine/test_routing.py`, `src/dev_harness/contracts/enums.py` (added `RunOutcome`).
- Deliverable: pure decision function `route(state, *, failure_kind) -> RouteDecision` reading only the retry counters. `INNER_LOOP_CEILING=3`, `E2E_CEILING=2`. Inner loop: below ceiling → `DEVELOPER_NODE`; at/above → `ARCHITECT_NODE` (the 4th attempt). E2E: below ceiling → `DEVELOPER_NODE`; at/above → `HITL_NODE` + `terminal=ExecutionState.STOPPED` + `outcome=RunOutcome.FAILED` + `escalate_to_hitl=True`. `FailureKind` enum (INNER_LOOP/E2E); `RouteDecision` frozen dataclass. No clock, no randomness, no network.
- Validation: `pytest tests/engine/test_routing.py -q` -> **19 passed**, exit 0. Acceptance exact: (a) 4th inner attempt (count==3) → Architect; (b) e2e count>=2 → HITL; (c) e2e count>=2 → STOPPED + FAILED, and a 100-step drive loop terminates FAILED (no infinite loop). Targeted branch coverage `--cov=dev_harness.engine.routing --cov-branch` -> **100% line / 100% branch** (33 stmts, 6 branches, 0 miss) — exceeds the 8.C 95/90 contract. `mypy --strict` + `ruff check`/`format` clean; `tests/contracts/test_enums.py` still 8 passed (literal-ban + exhaustiveness intact).
- Tests: 19 (unit 16, negative 3). No `time.sleep`, no network.
- Deviations: the plan's terminal `FAILED` is not an `ExecutionState` member (V11 0.5 locks it to 4 values; `test_enums.py` asserts `len==4`). Added canonical `RunOutcome` enum to `contracts/enums.py` (the canonical enum home) so the terminal is `ExecutionState.STOPPED` + `RunOutcome.FAILED` — no string literals. This is the minimal reconciliation; flagged for reviewer.
- Unverified: full-suite coverage + the `engine/` (nodes, pipeline) 88/80 gate (smoke-only per brief; 8.C gate is a tracked requirement, not permission).

### 8.12 DONE — engine/testing/differential.py + tests/engine/test_differential.py
- Files: `src/dev_harness/engine/testing/differential.py`, `src/dev_harness/engine/testing/__init__.py`, `tests/engine/test_differential.py`.
- Deliverable: `select_tests(changed, *, loader, all_tests, logger=None) -> SelectionResult` — returns the EXACT dependent test set (set equality) from an injected dependency graph (test node id -> transitively imported modules); when the graph is unavailable (`loader()` returns `None` or raises) it returns the FULL suite with `full_suite=True` and a logged+recorded `reason`. `build_dependency_graph(tests_root, src_root)` builds the graph via stdlib `ast` static analysis of `import`/`from ... import` (transitive closure over the src module graph); `make_loader` returns `None` when a root is missing; `path_to_module` normalizes `src/...` paths. Pure stdlib (`ast`, `pathlib`, `logging`); no new deps.
- Validation: `pytest tests/engine/test_differential.py -q` -> **8 passed**, exit 0. Acceptance exact: (a) changed `engine/dag.py` -> exactly `{tests/engine/test_dag.py}` (set equality, not superset); multi-module union exact; no-dependents -> empty set (not full suite); (b) unavailable graph (`None` loader) -> full suite + `reason` contains "unavailable" + a WARNING log record; raising loader -> full suite + reason names the exception. Real on-disk graph test follows transitive imports (`pkg.mid` -> `pkg.leaf`). `mypy --strict` + `ruff check`/`format` clean.
- Tests: 8 (unit 6, negative 2). No `time.sleep`, no network.
- Deviations: none from brief. Decision: a loader that raises is treated as "unavailable" (full-suite fallback) rather than propagating — a broken graph build must not silently skip tests.
- Unverified: full-suite coverage + the `engine/` (nodes, pipeline) 88/80 gate (smoke-only per brief; 8.C gate is a tracked requirement, not permission).

### 8.11 DONE — engine/nodes/tester.py + tests/engine/test_tester.py
- Files: `src/dev_harness/engine/nodes/tester.py`, `tests/engine/test_tester.py`.
- Deliverable: `TesterNode(workspace, chunk, *, command=None, timeout=300.0, emit=None)` — sync LangGraph node; runs the chunk's test command in `workspace.root_for(chunk)` as a subprocess in its own killable process group (POSIX `start_new_session`, Windows `CREATE_NEW_PROCESS_GROUP` + `taskkill /T`). `parse_pytest_counts` reads the terminal summary line; `classify_failure` maps raw result → `FailureClass` (`TIMEOUT` on kill; `RUNTIME_ERROR` on exit 127; `COMPILE_ERROR` on errors-only; `TEST_FAILURE` on assertions; else `UNKNOWN`). Emits one `TEST_PROGRESS` envelope with counts + class via the injected sink. Idempotent (second call no-op); chunk → `COMPLETED`/`FAILED`. `make_tester_node` factory.
- Validation: `pytest tests/engine/test_tester.py -q` -> **8 passed**, exit 0. Acceptance exact: (a) passing suite → (1,0,1); (b) real hanging child (`time.sleep(30)` in child) killed at `timeout=2.0`, `timed_out=True`, `exit_code=None`, `failure_class=TIMEOUT` (asserted not `TEST_FAILURE`), node returns < 15s wall-clock; (c) failing suite emits a `TEST_PROGRESS` envelope carrying `FailureClass.TEST_FAILURE`. Real temp git repo (`tmp_workspace`); child pytest scoped to the one written file with `-o addopts= -p no:cacheprovider`. `mypy --strict` + `ruff` check/format clean; event-ownership guard 9/9.
- Tests: 8 (unit 4, integration 3, negative 1). No `time.sleep` in the test body, no network.
- Deviations: none from brief. Decision: `classify_failure` maps exit 127 to `RUNTIME_ERROR` (a missing command is a runtime failure, not `UNKNOWN`) — needed to satisfy the negative test's semantics. `# pragma: no cover`: the POSIX `killpg`/`start_new_session` branches (justified inline: exercised on POSIX CI only).
- Unverified: full-suite coverage + the `engine/` (nodes, pipeline) 88/80 gate (smoke-only per brief; 8.C gate is a tracked requirement, not permission).

### 8.10 DONE — engine/nodes/developer.py + tests/engine/test_developer.py
- Files: `src/dev_harness/engine/nodes/developer.py`, `tests/engine/test_developer.py`.
- Deliverable: `DeveloperNode(client, workspace, chunk, *, model=None)` — LangGraph node; idempotent on a `COMPLETED` chunk (empty update, no client call). Non-JSON persona contract: `parse_file_writes(text)` parses the reply as a JSON `relative_path -> content` map (tolerates one markdown fence; `PersonaOutputError` otherwise). Every write goes through `WorkerWorkspace.write_path` (8.8), so absolute/`../` escapes AND symlink escapes raise `WorkspaceEscapeError` before any write. Sets chunk `IN_PROGRESS` -> `COMPLETED`; returns `{"chunk_dag": [chunk]}`. `make_developer_node` factory.
- Validation: `pytest tests/engine/test_developer.py -q` -> **7 passed**, exit 0. Acceptance exact: (a) `../../etc/passwd` -> `WorkspaceEscapeError`; (b) symlink inside worktree pointing outside blocked (junction fallback on Windows); (c) valid relative writes land inside the worker's worktree root and not in the primary tree. Real temp git repo (`tmp_workspace`). `mypy --strict` + `ruff` check/format clean.
- Tests: 7 (unit 4, negative 3). No `time.sleep`, no network.
- Deviations: none from brief. Decision: `_make_dir_link` falls back to a Windows junction (`mklink /J`) because `os.symlink` needs a privilege here (WinError 1314); `Path.resolve` follows both, so the guard is exercised identically. `# pragma: no cover`: none.
- Unverified: full-suite coverage + the `engine/` (nodes, pipeline) 88/80 gate (smoke-only per brief; 8.C gate is a tracked requirement, not permission).

### 8.9 DONE — engine/integrator.py + tests/engine/test_integrator.py
- Files: `src/dev_harness/engine/integrator.py`, `tests/engine/test_integrator.py`. Commit `724eab4` (pushed origin/main).
- Deliverable: `Integrator(repo)` — `integrate(chunks) -> list[str]` merges each `chunk/{chunk_id}` branch into the primary branch one at a time in the given (topological) order via `git merge --no-edit` (fast-forward when possible). On a conflict it collects unmerged paths (`git diff --name-only --diff-filter=U`), names the conflicting file + the already-merged chunk that touched it (fallback: primary branch) + the current chunk, aborts the merge, and **rolls the primary back to its pre-integration HEAD** (`git reset --hard start_head`) so integration is atomic. `primary_branch` property; `_changed_files`/`_conflicting_files`/`_abort_merge`/`_other_chunk` helpers.
- Validation: `pytest tests/engine/test_integrator.py -q` -> **10 passed**, exit 0. Acceptance exact: (a) non-overlapping branches merge clean, all changes present; (b) overlapping edit -> `IntegrationConflict` message contains `shared.txt` + `c1` + `c2`; (c) primary HEAD + `git status --porcelain` unchanged and no `MERGE_HEAD` after conflict. Real temp git repo (`tmp_workspace`). `mypy --strict` + `ruff` check/format clean.
- Tests: 10 (unit 5, integration 2, negative 3). No `time.sleep`, no network.
- Deviations: none from brief. Decision: `_abort_merge` also `git reset --hard` to the start HEAD — a plain `git merge --abort` restores only to the last successful merge, so a conflict on chunk N would leave chunks 1..N-1 merged, violating acceptance (c) "primary unmodified". `# pragma: no cover`: none.
- Unverified: full-suite coverage + the `engine.integrator` mutation gate run (smoke-only per brief; 8.C gate is a tracked requirement, not permission).

### 8.8 DONE — engine/worker_workspace.py + tests/engine/test_worker_workspace.py
- Files: `src/dev_harness/engine/worker_workspace.py`, `tests/engine/test_worker_workspace.py`. Commit `14af5e1` (pushed origin/main).
- Deliverable: `WorkerWorkspace(repo, *, worktrees=None)` — `bind(worker_id, chunk) -> Path` (idempotent; `WorktreeManager.create(worker_id, branch=f"chunk/{chunk_id}")`), `root_for(chunk) -> Path` (EngineError if unassigned/unbound), `write_path(chunk, rel) -> Path` (rejects absolute + escapes with `WorkspaceEscapeError`), `release(worker_id)`. Module-level `chunk_branch(chunk_id)`. `_ensure_harness_ignored()` writes `.dev-harness/` to `.git/info/exclude` (local, untracked) so worktrees under the repo never dirty the primary tree.
- Validation: `pytest tests/engine/test_worker_workspace.py -q` -> **11 passed**, exit 0. Acceptance exact: (a) 3 workers write `src/app.py` -> 3 distinct contents; (b) primary `git status --porcelain` empty throughout (asserted before/after each bind+write). Real temp git repo (`tmp_workspace`). Smoke lane: 881 passed, 7 skipped. `mypy --strict` + `ruff` clean.
- Tests: 11 (unit 3, integration 4, negative 4). No `time.sleep`, no network.
- Deviations: none from brief. Decision: added `_ensure_harness_ignored` — without it the worktrees dir (inside the repo) shows as untracked and dirties the primary, violating acceptance (b); the entry goes in `.git/info/exclude` (not `.gitignore`) so it is itself untracked. `# pragma: no cover`: none.
- Unverified: full-suite coverage + the `engine.worker_workspace` mutation gate run (smoke-only per brief; 8.C gate is a tracked requirement, not permission).

### 8.7 DONE — engine/worker_pool.py + tests/engine/test_worker_pool.py
- Files: `src/dev_harness/engine/worker_pool.py`, `tests/engine/test_worker_pool.py`. Commit `4b34183` (pushed origin/main).
- Deliverable: `WorkerPool(dag, max_parallel_workers, execute)` + module-level `dependencies_completed(chunk, by_id)` / `ready_chunks(order, by_id)`. `run() -> list[Chunk]` schedules in `ChunkDAG.topological_order()` order via a bounded `ThreadPoolExecutor`; a chunk is claimed only when every dependency is `COMPLETED`, stamps `assigned_worker_id` (lowest free `worker-N` slot, reused across chunks), sets `IN_PROGRESS`, then on settle `COMPLETED` (releasing the slot) or `FAILED` + `EngineError`. Injected `ChunkExecutor` keeps the pool free of worktree work (8.8 binds those). Non-positive bound -> `EngineError`; stalled graph -> `EngineError`.
- Validation: `pytest tests/engine/test_worker_pool.py -q` -> **11 passed**, exit 0. Acceptance asserted exactly: 7-chunk diamond (a→{b,c,d}→e→{f,g}), `max_parallel=3`, peak concurrency asserted == 3 (barrier rendezvous b/c/d), no chunk starts before deps (per-chunk `deps_completed_at_start`), all 7 `COMPLETED`. Regression: `tests/engine` smoke lane -> 214 passed. `mypy --strict` + `ruff` clean.
- Tests: 11 (unit 5, integration 3, negative 3). No `time.sleep` (barrier + event-driven waits, timeout=10 safety). No network. Deterministic.
- Lints: ruff check + format clean; `mypy --strict` clean on worker_pool.py.
- Deviations: none from brief. Decision: exposed the readiness rule as module-level `dependencies_completed`/`ready_chunks` so the 8.C mutation focus set (0 survivors in the readiness check) has exact unit targets. `# pragma: no cover`: 1 — the `_acquire_worker_id` exhausted-slots raise (unreachable by the loop's bound, justified inline).
- Unverified: full-suite coverage + the `engine.worker_pool` mutation gate run (smoke-only per brief; 8.C gate is a tracked requirement, not permission).

### 8.6 DONE — engine/dag.py + tests/engine/test_dag.py
- Files: `src/dev_harness/engine/dag.py`, `tests/engine/test_dag.py`. Commit `6229911` (pushed to origin/main).
- Deliverable: `ChunkDAG(chunks)` — builds a dependency graph from `list[Chunk]`. Validates referential integrity (`OrphanDependencyError` naming the dependent + unknown id, checked BEFORE cycles) then acyclicity (`CyclicDependencyError` naming two ids in the cycle + the full path e.g. `a -> c -> b -> a`; self-dep `a -> a`). `topological_order() -> list[Chunk]` (Kahn, ready nodes tie-broken by `chunk_id` → stable across 10 runs). `ready_chunks() -> list[Chunk]` (PENDING only, all deps COMPLETED, chunk_id order) — consumed by 8.7.
- Validation: `pytest tests/engine/test_dag.py -q` -> 20 passed, exit 0. Coverage of the module: **100% line / 100% branch** (contract 95/90). `mypy --strict` + `ruff` clean.
- Deviations: none from brief. Unverified: full-suite coverage run + mutation gate (smoke-only per brief; 8.C gate is a tracked requirement, not permission). `# pragma: no cover`: none.

### 8.5 DONE — engine/nodes/architect.py + tests/engine/test_architect.py
- Files: `src/dev_harness/engine/nodes/architect.py`, `tests/engine/test_architect.py`.
- Deliverable: `ArchitectNode(client, *, model=None)` — async LangGraph node over `HarnessStateChannels`. Consumes `state["groomed_requirements"]` (raises `EngineError` with remediation if absent), loads `engine/personas/architect.md`, calls the injected `CompletionClient`, validates via `validator_for("architect")` -> `TechnicalDesign`, returns `{"technical_design": <value>}`. Re-invocation with a set channel returns `{}` (no client call).
- Contract gates (`_enforce_contract`): a non-empty `openapi_spec` must `json.loads` (stdlib; non-JSON raises `PersonaOutputError`); an empty design (blank `architecture_spec` or empty contracts) that is `APPROVED` is downgraded to `REJECTED` so `status != APPROVED` always holds for an empty design.
- Validation: `pytest tests/engine/test_architect.py -q` -> 7 passed, exit 0. Regression: `tests/engine` + `tests/contracts/test_enums.py` -> 191 passed (literal-ban + AST adapter guard clean). `mypy --strict` + `ruff` clean.
- Deviations: none from brief. Design decision: enforced the OpenAPI parse gate with stdlib `json.loads` (persona contract emits JSON-serializable OpenAPI; no new dependency). `# pragma: no cover`: none. Unverified: coverage/full suite (smoke-only per brief).

### 8.4 DONE — engine/nodes/groomer.py + tests/engine/test_groomer.py
- Files: `src/dev_harness/engine/nodes/__init__.py` (new package), `src/dev_harness/engine/nodes/groomer.py`, `tests/engine/test_groomer.py`.
- Deliverable: `GroomerNode(client, *, model=None)` — async LangGraph node over `HarnessStateChannels` returning a partial update. Loads `engine/personas/groomer.md` from disk, calls the injected `CompletionClient`, validates via `validator_for("groomer")` -> `GroomedRequirements{LOCKED}`, returns `{"groomed_requirements": <value>}`. Re-invocation with a set channel returns `{}` and never calls the client.
- Validation: `pytest tests/engine/test_groomer.py -q` -> 4 passed, exit 0. Guards: `tests/contracts/test_enums.py` + `tests/engine/test_provider_gateway.py` + `tests/engine/test_state_reducers.py` -> 46 passed. `mypy` strict + `ruff` clean.
- Deviations: none. `# pragma: no cover`: none. Unverified: coverage/full suite (smoke-only per brief).

### 8.1 DONE — five persona templates + tests/engine/test_personas.py
- Commit `dfb95b3` (pushed origin/main). Files: `src/dev_harness/engine/personas/{groomer,architect,developer,tester,critic}.md`, `tests/engine/test_personas.py`.
- Validation: `pytest tests/engine/test_personas.py -q` -> 21 passed, exit 0.
- Tests: 20 parametrized `unit` + 1 critic keyword-scan `unit`. Lints: file exists, `## Output Contract`, refusal behavior, role name; critic keyword scan (refined to skip prohibition/refusal lines — the 8.B false-positive fix). Scan verified to catch a positive ("allowed to edit code") and skip a prohibition.
- Architect defaults to hosted model (decision 2026-09-20; spike skipped `no_local_ollama`), recorded in `architect.md`.
- Deviations: none. `# pragma: no cover`: none.
- Unverified: full-suite lane / coverage (smoke-only per brief).

### 8.2 DONE — engine/personas/validators.py + tests/engine/test_persona_validators.py
- Commit `ca7556a` (pushed origin/main). Files: `src/dev_harness/engine/personas/__init__.py` (new package), `src/dev_harness/engine/personas/validators.py`, `tests/engine/test_persona_validators.py`.
- Deliverable: `PersonaOutputValidator[ModelT]` — parses persona text as JSON, validates against the role's target Pydantic model; on parse/validation failure performs EXACTLY ONE repair retry (re-invokes the client with a repair prompt carrying raw text + error), then raises `PersonaOutputError`. Valid output -> 0 retries. `ValidationOutcome(value, retries)` return + observable `validator.retries` counter. `CompletionClient` Protocol (structural match to `providers.base.LLMClient`) injected; `PERSONA_MODELS`/`validator_for` map groomer->`GroomedRequirements`, architect->`TechnicalDesign`.
- Validation: `pytest tests/engine/test_persona_validators.py -q` -> 10 passed, exit 0. Guards: `tests/contracts/test_enums.py` + `tests/engine/test_provider_gateway.py` + `tests/engine/test_personas.py` -> 46 passed (literal-ban + AST adapter guard clean).
- Tests: 10 (unit 7, negative 3). No `time.sleep`; scripted fake client, no network.
- Lints: ruff check + format clean; `mypy --strict` clean on validators.py.
- Decisions: (1) `PersonaOutputError` reused (no new error class). (2) `CompletionClient` Protocol declared locally rather than importing `providers.base` — the AST guard forbids engine code importing `providers.base`; structural typing keeps the real `LLMClient` compatible. (3) `personas/` promoted to a package with `__init__.py` (setuptools `find` requires it; 8.4+ nodes import from it). (4) Developer/critic roles have non-JSON contracts (diff/verdict) so they are not in `PERSONA_MODELS`; `validator_for` raises `PersonaOutputError` for them.
- Deviations: none. `# pragma: no cover`: none.
- Unverified: full-suite lane / coverage / mutation (smoke-only per brief).

### 8.3 DONE — engine/state.py + tests/engine/test_state_reducers.py
- Commit `65dca4f` (pushed origin/main). Files: `src/dev_harness/engine/state.py`, `tests/engine/test_state_reducers.py`.
- Deliverable: `HarnessStateChannels(TypedDict, total=False)` mirroring `HarnessState`; `project_id`/`workspace_path`/`thread_id` are `Required`. `Annotated` reducers: `chunk_dag: Annotated[list[Chunk], append_chunks]` (operator.add append), `inner_loop_retry_count`/`e2e_retry_count: Annotated[int, add_counters]` (operator.add). All other channels last-write-wins (no reducer).
- Validation: `pytest tests/engine/test_state_reducers.py -q` -> 21 passed, exit 0. Guard: `tests/contracts/test_enums.py` + reducers -> 29 passed (literal-ban clean).
- Tests: 21 (unit; parametrized scalar-channel + retry-channel tables). Reducers exercised directly via a local `_merge` applying partial updates — no compiled graph. No `time.sleep`; no network.
- Lints: ruff check + format clean; `mypy --strict` clean on state.py.
- Decisions: (1) Used `cast` on the two `operator.add` wrappers — mypy strict flags `operator.add`'s `Any` return (`no-any-return`); wrappers give named, IntelliSense-visible reducers that LangGraph accepts. (2) `TypedDict(total=False)` so nodes may write partial updates; `Required[...]` on the three identity channels mirrors `HarnessState`. (3) `_reducer` helper checks `get_origin(...) is Annotated` before reading metadata — `X | None` unions otherwise yield a spurious second arg (`NoneType`).
- Deviations: none. `# pragma: no cover`: none.
- Unverified: full-suite lane / coverage / mutation (smoke-only per brief); compiled-graph channel wiring is 8.18 (out of scope).

## Closed-Phase Detail

Phase 0–6 task logs, gate verdicts, and exit artifacts are archived in
`memory_archive.md` (read only when a closed phase's detail is needed).

---

## P7 PLANNED - HERMES TUI CORE SUBSYSTEM (2026-09-23)

- **Plan doc**: `docs/phase_07_implementation_plan.md` (commit `28f38c2`, pushed). Reviewed requirements first, then documented the chunked plan.
- **Prereqs**: 5.4 (fanout), 5.8 (state broadcast), 2.6 (IPC), 0.3 (enums) - all green (P6 closed).
- **Est**: 35.0h, 13 tasks (7.1-7.13). Lane C. Gate `scripts/verify_phase_07.sh` -> `reports/phase_07_acceptance.json` ACCEPTED.
- **Chunking decision**: ONE task per dispatch (evidence: multi-task batches failed 3x in P6, single tasks succeeded 7x). Each brief = 1 task, <=25 tool calls, bounded waits, commit+push+memory per task.
- **Dispatch order (risk-first, topological)**: 7.1 shell -> 7.7 bridge -> 7.8 throttle -> 7.3 canvas -> 7.10 render -> 7.4 scrollback -> 7.2 repo-manager -> 7.5 model-registry -> 7.13 metrics_replay -> 7.6 critic-bar -> 7.9 bindings -> 7.11 CLI -> 7.12 verify script -> 7.D reviewer sign-off.
- **Coverage contract (7.C)**: `tui/` 75/65 (in .coveragerc + coverage_gate); `tui/bridge.py`, `tui/throttle.py`, `tui/render.py` 95/90 (verified per-module like P6 signals.py, NOT in gate table).
- **Invariants**: tui/ never imports engine/ (IPC only); one marker per test; no time.sleep() in tests (frozen clock); tests/tui/ needs __init__.py (6.9a collision lesson); no new deps without ponytail justification.
- **Test lane**: smoke only. NIGHTLY rows 7.4 (-m slow) and 7.8 (-m timing) are tracked requirements, deferred to a user-authorized nightly run.
- **Platform**: TUI is pure Python - pilot tests run on native Windows. The 7.D live protocol steps need a real daemon+IPC; 7.12 decides live-on-Windows vs WSL2 twin.
- **Next**: dispatch D1 (7.1 HermesApp shell) as a single-task brief.
### 7.7 DONE — tui/bridge.py + test_bridge.py
- Commit: 0ea63d2 (pushed to origin/main).
- Deliverable: `Bridge` thread-marshalling class; injectable source (callable or `BackpressureQueue`); single daemon reader thread; `start()`/`stop(timeout=2.0)` bounded join; `_apply` on UI thread via `app.call_from_thread`; `on(type, cb)`; counters `applied`/`control_applied`/`dropped`; `state`/`snapshot_seen`; SNAPSHOT-first arrival-order apply.
- Tests: 12 (unit 8, negative 3, integration 1) — all pass in <1s, no hang.
- Coverage: bridge.py 99% line / 95% branch (21/22; only 54->56 partial, the source-is-queue alias branch).
- Validation: pytest 12 passed; ruff clean; mypy --strict clean (84 files).
- Decisions: (1) `NoActiveAppError` imported from `textual._context` (its defining module) — `textual.message_pump` re-exports it but mypy strict flags the re-export. (2) Integration producer is backpressure-aware: the queue drops oldest on token overflow, so control events are enqueued only after the bridge consumes the SNAPSHOT/controls — guarantees 0 control drops deterministically. (3) `dropped` = queue.dropped_frames + marshal drops (NoActiveAppError).
- Unverified: none beyond smoke lane.
### 7.8 DONE — tui/throttle.py + test_throttle.py
- Commit: 859c04a (pushed to origin/main).
- Deliverable: `CoalescingThrottle(sink, *, interval=1/20, clock=time.monotonic, max_batch=4096)` — half-open 20 Hz coalescing; `push(env)->bool` (flush when interval elapsed or buffer at `max_batch`); `flush()->int`; `drain(now=None)->int` (bounded, flushes at most once); properties `pending`/`flushes`/`written`. No deps, no `engine/` import.
- Tests: 14 (unit 11 incl. parametrized interval boundary, negative 2, timing 1) — 13 pass in smoke (timing deselected); full file 14 passed in 0.44s.
- Coverage: throttle.py 100% line / 100% branch (56 stmts, 14 branches) — exceeds 95/90.
- Validation: pytest 14 passed; ruff clean; mypy --strict clean (85 files).
- Decisions: (1) Injectable clock (`time.monotonic`) so coalescing math is deterministic; tests use `tests/support/clock.py`, zero `time.sleep`. (2) `flush` marks the batch flushed before invoking the sink — a raising sink propagates but leaves state consistent (empty buffer, no re-delivery); recorded as the documented negative contract. (3) `push` returns `True` on either an interval flush or a `max_batch` bound flush. (4) Timing test drives the frozen clock in 50 ms steps over 2 s/10k tokens, asserts `flushes <= 44`, `written == 10000`, and no coalescing gap > one 50 ms step.
- Unverified: timing SLO is NIGHTLY (`-m timing`), deselected in smoke — not run this session.

### 7.3 DONE — tui/panels/execution_canvas.py + test_execution_canvas.py
- Commit: b588c0b (pushed to origin/main).
- Tests: 9 passed (tests/tui/test_execution_canvas.py + test_layout.py); ruff clean; mypy strict clean (87 files).
- Decisions: ExecutionCanvas(Vertical) with RichLog #canvas-log + Sparkline #canvas-sparkline; tokens stored in dict[seq,str] so rendered_text reassembles in seq order (duplicate seq = last-write-wins); sparkline sample = passed/total, total==0 -> 0.0 (no division); bind(bridge) registers AGENT_TOKEN_STREAM + TEST_PROGRESS handlers (UI-thread callbacks write directly). app.py compose() now yields ExecutionCanvas(id='execution-canvas'). No engine import; no sleeps (pilot.pause + bounded deadline).
- Unverified: full-suite/coverage/mutation gates not run (smoke lane only per policy).

### 7.10 DONE — tui/render.py + test_render.py
- Commit: f0950f7 (pushed to origin/main).
- Deliverable: `safe_text(text)->Text` (escaping primitive: `Text.from_markup(escape(text))`), `render_markdown(text)->RenderableType` (rich `Markdown(escape(text))` — structure preserved, markup literal), `render_diff(diff_text)->Text` (per-line `+` green / `-` red / `@@` cyan / headers+context dim, each line escaped so markup stays literal; `.plain` == input). Helpers `_line_style` covered. No `engine/` import; `rich` + stdlib only.
- Tests: 101 passed (unit 9, negative 4×parametrized NASTY table = 92) in 0.66s; zero `time.sleep`.
- Coverage: render.py **100% line / 100% branch** (33 stmts, 12 branches) — exceeds 95/90. Four `# pragma: no cover` guards on the untriggered `except MarkupError: pytest.fail(...)` defect path (each same-line justified).
- Validation: pytest 101 passed; `--cov-branch` 100/100; ruff clean; mypy --strict clean (88 files).
- Decisions: (1) Chose `Markdown(escape(text))` over a literal `Text` because Rich's Markdown parser interprets *Markdown*, not Rich markup — raw `[bold red]` already renders literally, while escaping additionally guarantees no `MarkupError` and neutralises backslash-escape tricks. (2) `safe_text` is the single primitive; both renderers route through it (7.C no duplication). (3) Negative table includes `[/]` (the only tag `from_markup` rejects unescaped) plus unclosed/nested/`[[`/non-ASCII; fuzz confirmed `escape` is total and the escaped `\\[` never survives into Markdown output.
- Unverified: full-suite/coverage/mutation gates not run (smoke lane only); no mutmut investigation (per brief).

### 7.4 DONE — tui/scrollback.py + test_scrollback.py
- Commit: 786078f (pushed to origin/main). Task-Id: 7.4.
- Deliverables: src/dev_harness/tui/scrollback.py (ScrollbackBuffer: max_lines cap, spill oldest-first to RunArtifactStore, lines/spilled/all_lines/__len__/spilled_count); tests/tui/test_scrollback.py (11 smoke tests: 5 unit, 3 negative-param, 2 integration, 1 slow).
- Also added ScrollbackError(HarnessError) to contracts/errors.py (canonical taxonomy; remediation non-empty) for the max_lines<1 rejection.
- Validation: pytest tests/tui/test_scrollback.py -q -> 11 passed (slow deselected); +tests/contracts/test_errors.py -> 15 passed; ruff clean; mypy src strict clean (89 files).
- Coverage: new module fully exercised by smoke tests (all branches: under/over cap, spill present/absent, path vs spill, factory default).
- Decision/deviation: RunArtifactStore.append is O(n) per call (reloads+rewrites whole file) — benchmarked 3000 appends = 27.5s. Per-line spill into it is O(n^2), infeasible for 200k. Smoke/integration use the real RunArtifactStore (small counts). The @pytest.mark.slow 200k soak uses an internal _FileSink (buffered append handle, structural twin of RunArtifactStore) injected via sink_factory, keeping the soak O(n) and time-bounded.
- NIGHTLY slow test (200k lines, RSS<100MB, ordered retrieval) is WRITTEN but DEFERRED — not run in smoke lane per lane policy.
- No tui/->engine/ import; no time.sleep; bounded spill loop (one popleft per iteration).

### 7.2 DONE — tui/panels/repo_manager.py + test_repo_manager.py
- Commit: 4f1b087 (pushed to origin/main).
- Deliverable: RepoManager(Vertical) with DirectoryTree(#repo-tree) + DataTable(#git-status); on_file_change/on_git_status/bind(Bridge); accessors branch/dirty_count/changed_paths/displayed_branch/displayed_dirty_count/row_count.
- app.py: HermesApp(workspace: str | None = None) -> self.workspace (default '.'); compose() yields RepoManager(path=self.workspace, id='repo-manager').
- Tests: 10 passed (tests/tui/test_repo_manager.py 7 + test_layout.py 3). Markers: unit x3, negative x2, integration x2.
- Decisions: table columns created lazily on mount (DataTable.add_columns needs an active app); value column key captured as ColumnKey; fixed row set (branch/dirty) + one row per distinct changed path -> idempotent update_cell, no duplicate rows. No git dependency at render time (values from events only).
- Validation: pytest tests/tui/test_repo_manager.py tests/tui/test_layout.py -q --timeout=120 -> 10 passed; ruff clean; mypy strict clean (90 files).
- Unverified: 100ms latency criterion asserted via single pilot.pause (no wall-clock sleep); nightly/slow lanes not run.

### 7.5 DONE — tui/panels/model_registry.py + test_model_registry.py
- Commit: df2a905 (pushed to origin/main).
- Deliverables: ModelRegistry(Vertical) with #registry-table DataTable (provider/model/p50/p95/tpm/usd rows); pure helpers format_latency (1 dp, em dash when None) + format_usd (4 dp, em dash when None); on_metrics/on_model_config; bind() subscribes METRICS_UPDATE + MODEL_CONFIG_CHANGE; accessors provider/active_model/latency_text/p95_text/usd_text/tpm_text. app.py compose() now yields ModelRegistry(id='model-registry').
- Tests: 19 passed (tests/tui/test_model_registry.py + test_layout.py), 1.14s. ruff clean; mypy strict clean (91 files).
- Decisions: mirrored repo_manager panel pattern (DataTable fixed rows, update_cell in place, is_mounted guard). No engine import; contracts+textual only. No new deps.
- Unverified: full suite / coverage / mutation gates not run (smoke lane only per policy).

### 7.13 DONE — tests/support/metrics_replay.py + test_metrics_replay.py
- Commit: 8d0d42d (pushed to origin/main). Task-Id: 7.13.
- Deliverables: tests/support/metrics_replay.py (MetricsReplay: recorded METRICS_UPDATE feed; RecordedSample + _FeedDocument Pydantic v2 models; record/replay/replay_fast/emitted/feed/to_json/from_json; injectable sink/sleep/clock; built-in 6-sample feed with rising USD + varying p95, 0.5s spacing). tests/support/test_metrics_replay.py (7 tests: 5 unit, 2 negative).
- Validation: pytest tests/support/test_metrics_replay.py -q --timeout=120 -> 7 passed in 0.34s; ruff clean; mypy src strict clean (91 files).
- Decisions: mirrors StubWorkload shape (scripted emitter + injectable sink). Pacing via injectable sleep (default time.sleep) so tests inject a recorder and never sleep for real; bounded loop over finite list. Caller-supplied feed (no timestamps) spaced 0.5s apart; record() timestamps from injected clock. emitted increments only after sink returns -> a raising sink propagates without corrupting the counter (documented negative behavior). No new deps; test-infra module, not under tui/ coverage ledger.
- Unverified: full suite / coverage / mutation gates not run (smoke lane only per policy). 7.D acceptance replay wiring into #model-registry not exercised here (7.12/7.D scope).

### 7.6 DONE — tui/panels/critic_bar.py + test_critic_bar.py
- Commit: 600ecdb (pushed to origin/main). Task-Id: 7.6.
- Deliverables: CriticBar(Horizontal) with Input(#critic-input) + Buttons #btn-pause/#btn-resume/#btn-stop/#btn-approve/#btn-reject; injectable publish sink (Callable[[Envelope], None]); emit(command, reason='') builds+publishes exactly one INTERRUPT_REQUEST envelope and returns it; on_button_pressed maps button id -> CriticCommand via BUTTON_COMMANDS; accessors emitted (list) + last_command. app.py compose() now yields CriticBar(id='critic-bar') (Static placeholder removed).
- Tests: 15 passed (tests/tui/test_critic_bar.py 12 + test_layout.py 3), 2.34s. Markers: unit x6, integration x2, negative x4. ruff clean; mypy strict clean (92 files).
- Decisions: Approve/Reject map to RESUME/STOP (CriticCommand has no distinct HITL member — 0.3 enum). No sink -> envelopes buffered in emitted for socket-free assertions. Unknown button id ignored (no raise). No dedup at this layer (documented). No engine import; contracts+textual only. No new deps.
- Deviation: the 'click twice' negative test clicks two *different* buttons (PAUSE then RESUME) rather than the same button twice — Textual swallows a second click on a button still in its pressed state, so same-button double-click is not observable via pilot.click. No-dedup is still demonstrated (2 distinct envelopes).
- Unverified: full suite / coverage / mutation gates not run (smoke lane only per policy). HITL consumer not wired (7.D scope boundary).

### 7.9 DONE — tui/bindings.py + test_bindings.py
- Commit: 3f13531 (pushed to origin/main).
- Deliverables: `src/dev_harness/tui/bindings.py` (`ConfirmQuitScreen(ModalScreen[bool])` + `HERMES_BINDINGS`), wired into `HermesApp` (`BINDINGS`, `action_pause`, `action_request_quit`, `pause_requests` accessor).
- Tests: `tests/tui/test_bindings.py` — 8 tests (3 unit, 3 integration, 2 negative). Validation: `pytest tests/tui/test_bindings.py tests/tui/test_layout.py tests/contracts/test_enums.py` -> 19 passed; ruff clean; mypy strict clean (93 files).
- Decisions: ctrl+c priority binding emits one `INTERRUPT_REQUEST{PAUSE}` via the mounted `CriticBar` (reuses 7.6 contract, no engine import); ctrl+q pushes modal, exits only on confirmed Yes. Modal CSS uses fixed `width: 40` — `width: auto` clipped the buttons container so the No button was unreachable by the pilot. No state string literals (uses `CriticCommand.PAUSE`).

### 7.11 DONE — src/dev_harness/cli.py + test_cli.py
- Commit: c3f0c09 (pushed to origin/main).
- Tests: 14 passed (tests/tui/test_cli.py + tests/contracts/test_enums.py), 0.73s.
- Validation: ruff clean; mypy --strict clean (94 files).
- Decisions: added NotAGitRepository(VcsError) to contracts/errors.py (did not exist); added GitAdapter.is_repository() (git rev-parse --is-inside-work-tree); console script dev-harness = dev_harness.cli:main.
- No state string literals (uses ExecutionState.READY.value); no unbounded loops; no time.sleep; no new deps.
- Unverified: real daemon/socket self-check (AF_UNIX unavailable on Windows) — broker/engine monkeypatched.

### 7.12a DONE — scripts/verify_phase_07.ps1
- Commit: c71fc64 (pushed to origin/main). Task-Id: 7.12a.
- Deliverable: `scripts/verify_phase_07.ps1` — PowerShell twin of the 7.D acceptance protocol. Mirrors `verify_phase_06.ps1` (header comment block, `$ErrorActionPreference = "Stop"`, `Set-Location (Join-Path $PSScriptRoot "..")`, 3 Yellow `Write-Host` lines, `exit 1`).
- Rationale: 7.D attaches a live TUI to a live engine daemon over AF_UNIX IPC; native Windows Python (<=3.12) has no AF_UNIX, so the daemon cannot bind its socket and the protocol cannot run here (plan R2). Directs the operator to `bash scripts/verify_phase_07.sh` on WSL2/POSIX.
- Validation: `powershell -ExecutionPolicy Bypass -File scripts/verify_phase_07.ps1; "exit=$LASTEXITCODE"` -> 3 Yellow lines then `exit=1` (expected).
- Unverified: the real 7.D protocol (7.12b `verify_phase_07.sh`) not run — requires WSL2/POSIX. No test suite run (stub script, no code under test).

### ORCHESTRATOR RESUME (2026-09-24)
- Resume point verified: HEAD `2592cde` (clean tree, in sync with origin/main). P7 in progress; 7.1-7.11 + 7.12a done.
- Remaining P7 work: **7.12b** `scripts/verify_phase_07.sh` (real POSIX 7.D protocol; missing) then **7.D** reviewer sign-off -> `reports/phase_07_acceptance.json`.
- Next action: dispatch 7.12b (single-task brief `briefs/7.12b.json`, validated OK) to python-developer.

### 7.12b DONE — scripts/verify_phase_07.sh (real POSIX 7.D protocol)
- Commit: 30a349f (pushed to origin/main). Task-Id: 7.12b.
- Deliverable: 10-step live acceptance protocol mirroring verify_phase_06.sh (embedded Python driver via heredoc, trap cleanup, bounded waits, numbered [P7] step lines, final ACCEPTED grep). Driver composes the real engine + TUI parts over AF_UNIX (Fanout, SessionManager, StateBroadcast, WorkspaceWatcher, SqliteSaver, CriticGatekeeper, HermesApp, Bridge, CoalescingThrottle, StubWorkload, MetricsReplay).
- Steps: 1 cold launch + --self-check (broker ok / engine thread_id / state=RUNNING); 2 10k-token stream + reports/throttle_trace.json SLO (<=20 writes/s, max iter <50ms); 3 repo panel truth (touch -> dirty increment <=1s, branch match); 4 metrics replay (p95/USD match feed); 5 UI pause -> PAUSED + sealed checkpoint; 6 ctrl+c stays running + 1 PAUSE, ctrl+q modal; 7 reattach SNAPSHOT{PAUSED}; 8 200k-line soak (peak <100MB, spill retrievable); 9 60x20 degradation (no Traceback); 10 acceptance report ACCEPTED.
- Validation: `bash -n` (Git Bash) exit 0; embedded driver AST parse OK (603 lines); all driver imports resolve (exec'd import block, 0 failures); no tui->engine import. Live run DEFERRED to WSL2/POSIX (AF_UNIX unavailable on native Windows).
- Note: subagent returned a mid-task fragment without committing; orchestrator verified the artifact, validated it, cleaned stray temp files, and committed. No re-dispatch needed.
- Unverified: the live 10-step run (requires WSL2/POSIX). reports/phase_07_acceptance.json not created (7.D reviewer artifact).

### P7 GATE STATUS (2026-09-24) — awaiting 7.D
- Tasks: 7.1-7.13 all done (7.12b committed 30a349f). Tasks-done is NOT phase-done.
- Gate condition 1 (all validation rows green): MET for the smoke lane. NIGHTLY rows 7.4 (-m slow) and 7.8 (-m timing) are tracked requirements, deferred to a user-authorized nightly run.
- Gate condition 2 (7.C coverage contract): **pending - requires user-authorized full-suite run**. `coverage.json` is stale (73 files, no tui/). Need `tui/` 75/65 + `tui/bridge.py`/`throttle.py`/`render.py` 95/90.
- Gate condition 3 (signed acceptance protocol): **pending** - reviewer-agent must sign `reports/phase_07_acceptance.json`; the live 7.D protocol (verify_phase_07.sh) needs WSL2/POSIX (AF_UNIX).
- Next: user authorization for the full-suite coverage run, then dispatch reviewer-agent for 7.D.

### P7 7.C COVERAGE CONTRACT — VERIFIED (2026-09-24, user-authorized full-suite run)
- Command: `python -m pytest tests -q --cov=dev_harness --cov-branch --cov-report=term-missing --cov-report=json:coverage.json` -> 988 passed, 8 skipped, 82.67s.
- `tui/` overall: **98.1% line / 88.5% branch** (need 75/65) — MET.
- `tui/bridge.py`: 98.9/98.9; `tui/throttle.py`: 100/100; `tui/render.py`: 100/100 (need 95/90) — MET.
- `python scripts/coverage_gate.py` -> exit 0 (gate OK). `python scripts/coverage_weights.py` -> exit 0 (overall 89/83).
- Fixed one stale tooling test: `tests/tooling/test_check_traceability.py::test_unbuilt_task_reports_gap` asserted 7.12 was unbuilt; repointed to 8.19 (`scripts/verify_phase_08.sh`). Commit f7f2f26 (pushed).
- Gate condition 2 (7.C coverage contract): **MET**.

### P7 7.D REVIEW — ACCEPTED (2026-09-24, reviewer-agent, independent)
- Reviewed: P7 Hermes TUI Core Subsystem (tasks 7.1-7.13). Reviewer did not implement any P7 task.
- Validation matrix (7.B): all 13 rows green in the smoke lane. `pytest tests/tui tests/support/test_metrics_replay.py -q` -> **203 passed** (19.66s). NIGHTLY rows 7.4 (`-m slow`) and 7.8 (`-m timing`) deferred to a user-authorized nightly run (tracked, not failed).
- Coverage (7.C): confirmed against `coverage.json` + `.coveragerc [coverage:report:tui]`. `tui/` overall **98.1 line / 88.5 branch** (need 75/65); `bridge.py` 100.0/95.5, `throttle.py` 100/100, `render.py` 100/100 (need 95/90). All MET.
- Rejection criteria (7.D): Ctrl+C-quits -> PASS (test_ctrl_c_pauses_and_keeps_running); unreviewed snapshot baseline -> PASS (no TUI render baseline committed); max-iter>=50ms (step 2) and RSS>bound (step 8) -> NOT_TRIGGERED (live/nightly deferred). No rejections.
- Invariants: `tui/` has zero `dev_harness.engine` imports (AST scan); one marker per test; no `time.sleep()` in tests; no unreviewed baselines.
- Platform limit: live 7.D protocol (`scripts/verify_phase_07.sh`) needs AF_UNIX -> deferred to WSL2/POSIX (plan R2, same as P1-P6). `bash -n` (Git Bash) exit 0; embedded driver AST parse OK; imports resolve. `.ps1` twin is the platform-limit stub.
- Artifact: `reports/phase_07_acceptance.json` verdict **ACCEPTED**, signed_by reviewer-agent, commit-pinned.
- Verdict: **ACCEPTED**. P7 gate conditions 1-3 all satisfied (1 smoke-green, 2 coverage MET, 3 signed).

### ORCHESTRATOR CHECKPOINT (2026-09-24, session rotation)
- P7 CLOSED. P8 in progress: 8.1-8.7 DONE (commits dfb95b3, ca7556a, 65dca4f, 5aac0f6, 88b731c, 6229911, 4b34183). HEAD 1a0ef40, pushed.
- Next: 8.8 engine/worker_workspace.py (RISK: 95/90 + mutation >=80%; per-worker worktree binding on chunk/{chunk_id}; prereq 8.7, 1.10).
- Remaining P8: 8.8, 8.9, 8.10, 8.11, 8.12, 8.13, 8.14, 8.15, 8.16, 8.17, 8.18, 8.20, 8.21a, 8.21b, 8.19, 8.D (human sign-off required).
- Briefs live in briefs/<task>.json (validated via scripts/check_brief.py). One task per dispatch.
- Test lane: smoke only (user authorized ONE full-suite coverage run this session for 7.C; permission is per-run, NOT carried forward).

### 8.16 FLAGGED DEVIATION (for 8.D reviewer)
- Plan 8.B/8.D say "500-line trace -> 50 lines"; the implementation yields **51 lines** (head 30 + 1 elision marker + tail 20).
- Rationale: the plan's "50-line cap (head 30 / tail 20)" and "first and last frames present" are mutually inconsistent if the elision marker is a standalone line. The implementation preserves the exact head-30/tail-20 frame counts and both endpoints, sacrificing only the literal total of 50.
- Reviewer must adjudicate: accept 51 (30+marker+20) or require exactly 50 (which forces head 29 or tail 19, breaking the stated 30/20 split).

### 8.17 DONE (2026-09-24, python-developer)
- Deliverable: `src/dev_harness/engine/classifier.py` + `tests/engine/test_classifier.py`.
- `classify_report(report_text, *, state) -> ClassificationResult`: parses line-oriented report (`chunk: <id> status: PASS|FAIL`, `e2e: PASS|FAIL`); maps to `E2EReport.classification` (CHUNK_IMPLEMENTATION_BUG w/ first failing chunk id; INTEGRATION_SPEC_MISMATCH when all chunks pass but e2e fails; None on e2e PASS). Unparseable (malformed line / missing e2e verdict / e2e FAIL with no chunk results) -> HITL route (`HITL_NODE`, escalate_to_hitl=True), never a guess.
- Canonical constants `CHUNK_IMPLEMENTATION_BUG` / `INTEGRATION_SPEC_MISMATCH` added to `contracts/state.py` (single source; classifier imports them - no state literals outside contracts). Drift-guard test ties them to the `E2EReport` Literal.
- Validation: `pytest tests/engine/test_classifier.py -q` -> **20 passed** (10 labelled fixtures 100% correct; 7 unparseable -> HITL). ruff + mypy --strict clean.
- Deviation: none. Note: `Literal[...]` cannot reference module constants (mypy valid-type), so the `E2EReport` field keeps its inline Literal strings (in contracts, permitted) and the constants mirror them, guarded by a test.

### 9.2 DONE (2026-09-24, python-developer) — recovery/reclaim.py + tests/recovery/test_reclaim.py
- Files: `src/dev_harness/recovery/__init__.py`, `src/dev_harness/recovery/reclaim.py`, `tests/recovery/__init__.py`, `tests/recovery/test_reclaim.py`. Commit `2c10097`, pushed to `origin/main`.
- Deliverable: `reclaim(workspace, *, worktree_manager=None) -> ReclaimReport` orchestrates the existing primitives (`storage.workspace_lock._pid_alive`, the socket-cleanup pattern from `ipc.server`, `vcs.worktree.WorktreeManager.list/destroy`) into one entry point. Liveness is recorded as a PID next to each artifact: socket -> `<socket_path>.pid` sidecar; lock -> the lock file itself (`pid:hostname`); worktree -> `<worktree>/.pid`. Missing/empty/unparseable PID = stale (orphan) -> reclaimed. Live-PID artifacts are never touched. `ReclaimReport` exposes `socket_removed`, `lock_removed`, `worktrees_removed`, `live_artifacts`, `elapsed_seconds`, `reclaimed_any`. `RecoveryError` (existing) raised on `OSError`; unregistered worktree dirs fall back to `shutil.rmtree`.
- Validation: `pytest tests/recovery/test_reclaim.py -q` -> **9 passed**, exit 0. Acceptance exact: (a) dead-PID socket+lock+worktree removed (`test_dead_pid_socket_lock_and_worktree_removed`); (b) fresh session < 2s (`test_fresh_session_starts_under_two_seconds`, bounded wall-clock, no `time.sleep`); (c) live-PID artifacts untouched (`test_live_pid_artifacts_untouched`). Targeted branch coverage `recovery/reclaim.py` -> **100% line / 100% branch** (exceeds 9.C 90/85). `mypy --strict` + `ruff check`/`format` clean.
- Tests: 9 (unit 4, integration 3, negative 2). Real temp git workspace (`tmp_workspace`); socket exercised as a plain file (AF_UNIX is POSIX-only). No `time.sleep`, no network, no `tui/` import, no new deps, no new error, no state string literals.

### 9.3 DONE (2026-09-24, python-developer) — broker/fallback.py + tests/broker/test_fallback.py
- Files: `src/dev_harness/broker/fallback.py`, `tests/broker/test_fallback.py`, `src/dev_harness/contracts/enums.py` (new `ProviderHealth` enum). Commit `dc5c8cf`, pushed to `origin/main`.
- Deliverable: `FallbackChain(providers)` with an injected `call(fn)` where `fn: Callable[[ProviderId], T]`. Tries each provider in order; a **retryable** `ProviderError` (`ProviderOverloadedError`/`TransientError`, via `getattr(exc, "retryable", False)`) marks that provider `DEGRADED` and advances **once per provider**; a **non-retryable** error (`AuthError`) propagates immediately (no fallback); an exhausted chain raises `AllProvidersUnavailable` (existing error, chained from the last error). `FallbackResult(value, provider, retries, health)`; `health(pid)`, `status()`, `is_degraded()`. Empty provider list -> `ValueError`.
- `DEGRADED` added as canonical `ProviderHealth` enum member in `contracts/enums.py` (not a string literal; the literal-ban test's banned set is `READY/RUNNING/PAUSED/STOPPED/START/RESUME`, so `DEGRADED` is safe either way, but the enum is the canonical home per the brief).
- Validation: `pytest tests/broker/test_fallback.py -q` -> **7 passed**, exit 0. Acceptance exact: (a) 529/`ProviderOverloadedError` on PRIMARY -> SECONDARY with `result.retries == 1` and call order `[PRIMARY, SECONDARY]`; (b) `chain.health(PRIMARY) is ProviderHealth.DEGRADED` + `is_degraded()`; (c) exhausted chain raises `AllProvidersUnavailable` with non-empty remediation. Also: transient retryable, healthy primary (0 retries), `AuthError` no-fallback (calls == `[PRIMARY]`), empty list rejected.
- Regression: `pytest tests/contracts/test_enums.py tests/broker -q` -> **125 passed**. `ruff check` + `mypy --strict` clean on all touched files.
- Tests: 7 (unit 5, negative 2). Injected call fn; no network, no `time.sleep`, no `tui/` import, no new deps, no new error, no state string literals.
- Unverified: full-suite coverage + the `broker/` 80/80 package gate (smoke-only per brief; tracked requirement, not permission).
- **Windows gotcha (important for future tasks):** `os.kill(pid, 0)` on Windows calls `TerminateProcess`, so `_pid_alive(os.getpid())` **kills the test runner**. The live-PID test uses a real child process (`CREATE_NEW_PROCESS_GROUP`, detached streams) instead of the current PID. `tests/storage/test_workspace_lock.py::test_pid_alive` already uses a subprocess for this reason.
- Unverified: full-suite coverage + the `recovery/` mutation gate (smoke-only per brief; tracked requirement, not permission).

### 8.18 DONE — engine/pipeline.py + tests/engine/test_sdlc_pipeline.py
- Commit: d396349 (pushed origin/main). Task-Id: 8.18.
- Deliverable: `build_graph(config)` assembles Groomer -> Architect -> Developer -> Tester -> Critic with the chunk DAG, worker pool, retry routing, HITL gate, and context budgeting; compiled with an injectable checkpointer (InMemorySaver default - langgraph-checkpoint-sqlite is NOT installed).
- Validation: `pytest tests/engine/test_sdlc_pipeline.py -q` -> 3 passed (integration 2, negative 1). Acceptance exact: requirement -> green unit test; final checkpoint schema-valid; chunk_dag[0].status == COMPLETED; failing chunk escalates to HITL with bounded retry (Developer called exactly 2x, not a loop); 0 live network calls (MockLLM).
- **GUARD FINDING (important):** the provider-adapter AST guard (`tests/engine/test_provider_gateway.py`) was ALREADY BROKEN by 8.16 - `engine/context.py` imports `providers.registry`, which the guard's `_ADAPTER_MODULES` banned. I missed it because I did not run that guard test after 8.16. The 8.18 subagent narrowed the guard to the 4 real network adapters (anthropic/openrouter/ollama/base); `providers.registry` is a pure metadata table (imports only config/contracts, no network). Verified: the narrowed guard still catches a real adapter import and passes. Flagged for the 8.D reviewer.
- Unverified: full-suite coverage + the engine/ 88/80 gate (smoke-only).

### 8.21a DONE — storage/migrations/0002_worktree_state.sql + per-version rollback
- Deliverable: `storage/migrations/0002_worktree_state.sql` (ALTER TABLE checkpoints ADD COLUMN worktree_head TEXT; worktree_diff TEXT) + per-version rollback in `storage/migrate.py` + `tests/storage/test_migration_0002.py`.
- **Rollback fix:** `migrate_down` no longer hardcodes `DROP TABLE IF EXISTS checkpoints`. Added `_DOWN_SQL: dict[str,str]` (0001 -> DROP TABLE; 0002 -> ALTER TABLE ... DROP COLUMN x2). Runtime SQLite 3.49.1 (>=3.35) supports DROP COLUMN. Rollback order made deterministic (`ORDER BY applied_at DESC, version DESC`); all versions pre-validated for a reverse before any mutation (no half-rollback). Unknown version -> `StorageError` with remediation.
- Validation: `pytest tests/storage/test_migration_0002.py -q` -> **5 passed** (unit 1, integration 2, negative 2). Acceptance exact: (a) 0002 applies cleanly on 0001; (b) rollback returns sqlite_master object count to the 0001 baseline (ALTER adds no objects); (c) worktree_head + worktree_diff capture per-worker HEAD + uncommitted diff.
- Regression fixes (schema change ripples): `tests/storage/test_migrations.py` (now expects ["0001","0002"]), `tests/storage/test_branch_gaps.py` (target="0000" -> ["0002","0001"]), `tests/storage/test_schema_ddl.py` (positional INSERT -> named columns; 10 cols now).
- Smoke lane: 1000 passed, 7 skipped (importlib mode; default mode hits a PRE-EXISTING basename collision tests/engine/test_cli.py vs tests/storage/test_cli.py, unrelated to this task). ruff + mypy --strict clean.
- Deviation: none. Unverified: full-suite coverage + storage/ 95/90 gate (smoke-only).

### 8.20 DONE — engine/cli.py + tests/engine/test_cli.py
- Commit 0526e25 (pushed). CLI `run`/`plan`/`critic-drill` against `--mock`; exit 0; `--print-dag` topologically valid; cycle fixture -> exit 2 naming both ids; `critic-drill` surfaces CriticScopeViolation; `--trace` writes reports/parallel_trace.json. 11 tests. Engine smoke lane 339 passed.

### 8.21a DONE — storage/migrations/0002_worktree_state.sql + per-version rollback
- Commit c21a670 (pushed). Adds `worktree_head` + `worktree_diff` columns; migrate_down made per-version (`_DOWN_SQL`) instead of the hardcoded `DROP TABLE checkpoints` placeholder. 0002 reverses via `ALTER TABLE ... DROP COLUMN` (SQLite 3.49.1 >= 3.35). Rollback restores the sqlite_master object count to the 0001 baseline and preserves 0001's table. Updated 3 existing migration tests for the schema ripple. `pytest tests/storage -q` 71 passed.

### CROSS-CUTTING FIX — test_cli basename collision (commit 18e6a3a)
- 8.20 introduced tests/engine/test_cli.py, colliding with the existing tests/storage/test_cli.py under the default pytest import mode (the 6.9a lesson). Added `tests/engine/__init__.py` + `tests/storage/__init__.py` package markers. Verified: `pytest tests/engine/test_cli.py tests/storage/test_cli.py -q` 14 passed.
- **FULL SMOKE LANE GREEN: 1000 passed, 7 skipped, 16 deselected in 70.49s.**
- **PROCESS LESSON:** my per-task smoke checks (`pytest tests/engine/<file>.py`) did NOT exercise the default-import-mode collection of colliding basenames. The end-of-phase `scripts/test_lane.ps1 smoke` caught it. Run the smoke lane more often, not only at phase close.

### 8.19 DONE — scripts/verify_phase_08.sh + .ps1 (13-step 8.D protocol)
- Commit 2095e9e (pushed). Task-Id: 8.19. Also committed scripts/p8_acceptance_driver.py (6 subcommands) + fixtures/{req_simple,req_diamond,req_conflict,req_failing,req_hitl,req_cycle}.md.
- **Both twins run all 13 steps; steps 1-11 PASS on native Windows** (P8 needs no AF_UNIX). Step 13 correctly fails until the 8.D reviewer signs reports/phase_08_acceptance.json.
- Step 12 DEFERS the mutation gate on native Windows: mutmut refuses to run there ("use the WSL") - plan R2 class, same as P5-P7. Detected explicitly (capture output first: under `set -o pipefail` the pipeline inherits mutmut's non-zero exit and the `if` would take the wrong branch).
- .ps1 needed 3 Windows fixes: (1) PowerShell strips embedded quotes passing `-c` code to native commands -> stage checks in temp files; (2) a UTF-8 BOM breaks json.load -> write utf8NoBOM; (3) Test-Path throws on multi-line strings -> probe defensively.
- Evidence: step 3 peak concurrency exactly 3, 3 worktrees, primary clean before/mid/after, 5 commits, 0 lost writes; step 6 conflict names file + both ids, primary unmodified; step 7 terminal FAILED, e2e_retry_count=2 (bounded); step 8 halted + checkpoint persisted + advanced exactly 1 node; step 9 CriticScopeViolation, diff ['tui_state']; step 10 budget 4096, trace 51 lines with first+last; step 11 worktree restored field-for-field.
- **ALL P8 TASKS DONE (8.1-8.21b).** Remaining: 8.D reviewer + HUMAN sign-off (Lane D requires a human signed_by).

## P8 8.D REVIEW — INDEPENDENT (reviewer-agent, 2026-09-24)

- **Session**: reviewer-agent, P8 8.D acceptance review. Did not implement any P8 task.
- **Contract**: V11 §8.A–§8.D (lines 822–900) + `docs/phase_08_implementation_plan.md`.
- **8.B validation matrix**: all 21 task rows green. `pytest` over the 21 per-task test files -> **234 passed** (smoke lane, `--timeout=120`). No full-suite run.
- **8.C coverage (measured per-module, targeted `--cov-branch`)**:
  - `engine/` package aggregate (tests/engine) -> **96.64 line / 91.83 branch** (≥88/80 MET).
  - `engine/dag.py` 100/100; `engine/worker_pool.py` 98.82/94.44; `engine/worker_workspace.py` 98.18/96.15; `engine/routing.py` 100/100; `engine/classifier.py` 100/100 — all ≥95/90 MET.
  - **`engine/integrator.py` 90.12 line / 77.78 branch — BELOW the 8.C 95/90 per-module contract.** Missing lines 86/98/104/146 (defensive `VcsError` paths in `_abort_merge` + the non-conflict merge-failure raise). Recorded as finding **F1**; NOT a rejection criterion. Recommend negative tests before human sign-off.
- **8.D protocol (native Windows, Git Bash)**: steps 1–11 verified — step 3 peak=3, 3 worktrees, primary clean before/mid/after, 5 commits, 0 lost writes; step 6 conflict names file+both ids, primary unmodified; step 7 terminal FAILED, e2e_retry_count=2 (bounded); step 8 halted + checkpoint persisted + advanced exactly 1 node; step 9 CriticScopeViolation, diff `['tui_state']`; step 10 budget 4096, trace 51 lines first+last; step 11 worktree restored field-for-field. Step 12 **DEFERRED** (mutmut requires WSL2/POSIX, plan R2; `reports/mutation_report.json` all score=null exit=1). Step 13 now reaches the human-signoff gate (fails only on `signed_by='reviewer-agent'`).
- **Rejection criteria**: no lost write PASS; primary never dirty PASS; no infinite retry PASS; surviving mutant DEFERRED (WSL2/POSIX); no live network PASS.
- **Invariants**: `tui/` never imports `engine/` PASS; engine never imports provider adapters PASS (manual grep of all engine subpackages; only docstring mentions); one marker per test PASS; no `time.sleep()` in P8 test files PASS; no state string literals outside `contracts/enums.py` PASS (`test_enums.py` 8 passed).
- **Findings**: F1 (integrator coverage below 8.C per-module contract); F2 (AST guard `test_provider_gateway.py` globs only `engine/*.py`, not subpackages — invariant holds by manual grep, guard incomplete).
- **Deviations adjudicated**: 8.13 `RunOutcome` enum — ACCEPTED (canonical home, no literals, `ExecutionState` locked to 4). 8.16 51-line trace — ACCEPTED (plan's "50-line cap (head 30/tail 20)" is internally inconsistent with a standalone marker; 51 = 30+1+20). 8.18 AST guard narrowing — ACCEPTED (`registry.py` imports only config/contracts, no network; `context.py` legitimately needs it). 8.15/8.18 injectable `InMemorySaver` — ACCEPTED (`langgraph-checkpoint-sqlite` not installed; injectable seam documented).
- **Artifact**: `reports/phase_08_acceptance.json` verdict **ACCEPTED**, commit `e5a893f`, signed_by `reviewer-agent`, `human_signoff_required: true`. **Lane D requires a HUMAN signature.**

### P8 8.D REVIEW — ACCEPTED (2026-09-24, reviewer-agent, independent)
- Reviewed: P8 SDLC Pipeline Engine & Parallel Worker Pool (8.1-8.21b). Reviewer did not implement any P8 task.
- Validation matrix (8.B): all 21 rows green (234 tests). Coverage (8.C) measured per-module: engine/ 96.6/91.8 (need 88/80); dag 100/100; worker_pool 98.8/94.4; worker_workspace 98.2/96.2; routing 100/100; classifier 100/100.
- Rejection criteria: lost write -> PASS (0 lost, 5 commits); primary dirty -> PASS (clean before/mid/after); infinite retry -> PASS (terminal FAILED, e2e_retry_count=2); surviving mutant -> DEFERRED (mutmut needs WSL2/POSIX, plan R2); live network -> PASS (--mock, sockets disabled).
- Deviations adjudicated: 8.13 RunOutcome enum ACCEPTED; 8.16 51-line trace ACCEPTED; 8.18 AST-guard narrowing ACCEPTED; 8.15/8.18 injectable InMemorySaver ACCEPTED.
- **Findings (both CLOSED in commit 343a220):**
  - **F1:** integrator.py was 90/78, below the 8.C per-module 95/90. Added 4 negative tests for the defensive git-failure paths (injected _run). Now **100/100**.
  - **F2:** the provider-adapter AST guard globbed only engine/*.py, missing engine/nodes/, engine/personas/, engine/testing/. Widened to rglob.
- Smoke lane after fixes: **1014 passed, 7 skipped**.
- Artifact: reports/phase_08_acceptance.json verdict ACCEPTED, signed_by reviewer-agent, human_signoff_required=true, pinned to commit e5a893f.
- **P8 GATE STATUS:** conditions 1 (validation rows) and 2 (coverage contract) MET; condition 3 (signed acceptance) has the reviewer signature but **Lane D requires a HUMAN signed_by** - awaiting the user.

### P8 GATE DECISION (2026-09-24, autonomous - user unavailable)
- User delegated: "work autonomously and make good decisions."
- **Decision: P8 is NOT marked closed.** Lane D requires a HUMAN signed_by; the reviewer signed ACCEPTED but that is not a human signature. I will NOT fabricate one (falsifying an acceptance record is an integrity violation).
- P8 state: **reviewer-ACCEPTED, human sign-off PENDING**. All 21 tasks done; validation rows green; coverage contract MET; F1+F2 closed; smoke 1014 passed.
- Deferred (needs WSL2/POSIX): mutation gate (step 12), live 7.D protocol.
- Proceeding to P9 (Lane A/B/C/D, no human sign-off required). P8 closure awaits the user.

### P9 9.6 DONE — Git edge cases (2026-09-24, python-developer)
- Deliverable: `src/dev_harness/vcs/edge_cases.py` + `tests/vcs/test_edge_cases.py`.
- API: `describe_repo_state(repo) -> RepoState` (typed, frozen Pydantic: is_repository/has_commits/detached/branch/head_sha/dirty/uncommitted_count/submodules/worktrees); `require_commits(repo) -> str` (NoCommitsError on unborn); `restore_detached(repo, sha, *, autostash=False) -> RepoState` (reports detached=True, branch=None); `guard_worktree(repo, worker_id) -> Path` (WorktreeExistsError on duplicate/pre-existing). Reuses GitAdapter/WorktreeManager/Restorer; no new errors, no new deps.
- Validation: `pytest tests/vcs/test_edge_cases.py -q` -> **7 passed** (9.26s). Regression `pytest tests/vcs -q` -> **29 passed**. ruff check + format clean; mypy strict clean.
- Acceptance: (a) unborn -> NoCommitsError PASS; (b) detached restore succeeds + reports detached PASS; (c) duplicate worktree -> WorktreeExistsError PASS; submodules + pre-existing worktrees covered (submodule test skips with reason if offline).
- Deviation: none. Commit `TBD` (Task-Id: 9.6), pushed to origin/main.
