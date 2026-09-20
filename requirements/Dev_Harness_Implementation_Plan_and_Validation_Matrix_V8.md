# Hermes TUI Dev Harness — Detailed Implementation Plan & Validation Matrix (V8)

**Supersedes:** `Dev_Harness_Implementation_Plan_and_Validation_Matrix_V7.md`
**Traces to:** `Hermes TUI Dev Harness - Detailed Technical Design Specification (V7)`
**Status:** Execution-ready. No task in this document is marked "validated" until its Validation Matrix row passes in CI.

---

## 0. How to Read This Document

- **Task sizing:** every task is scoped to 1–4 hours of focused implementation by a single agent or engineer.
- **Prerequisites are hard gates.** An agent may not start a task until every listed prerequisite task has a green Validation Matrix row.
- **Deliverable = code.** Every task names the exact file(s) it creates or modifies. A task with no file path is not a task.
- **Compliance is earned, not asserted.** The V7 "100% Validated" column has been removed and replaced with the traceability matrix in §10, which maps TDD sections to task IDs.

### Corrected Phase Dependency Order

V7 sequenced the TUI first. That is inverted: the four panels consume IPC events and broker metrics that did not yet exist, so `CHUNK_01` could only have been built against stubs. Corrected order:

```
Phase 0: Scaffolding & Shared Contracts
    |
    +--> Phase 1: Persistence & Workspace Isolation
    |        |
    +--> Phase 2: IPC Transport & Event Bus
    |        |
    |        +--> Phase 3: TUI Core ------+
    |        |                            |
    |        +--> Phase 4: Critic Gatekeeper
    |        |                            |
    |        +--> Phase 5: Rate Limit Broker
    |                                     |
    |                                     v
    +-----------------------------> Phase 6: SDLC Pipeline Engine
                                          |
                                          v
                                   Phase 7: Error Handling & Edge Cases
                                          |
                                          v
                                   Phase 8: Final Verification & Release
```

---

## Phase 0: Repository Scaffolding, Configuration & Shared Contracts

**Objective:** Establish the single source of truth for every enum, message schema, and path derivation rule so downstream phases compile against contracts rather than against each other's internals.
**Prerequisites:** None.

### Phase 0 Execution Tasks

| Task ID | Task Description & Deliverable | Targeted Files / Components | Prerequisite Tasks |
| :--- | :--- | :--- | :--- |
| 0.1 | Create `src/` layout package, pin Python >=3.11, declare dependencies (textual, rich, langgraph, langchain-core, pydantic>=2, httpx, portalocker, pytest, pytest-asyncio, pytest-cov, ruff, mypy). | `pyproject.toml`, `src/dev_harness/__init__.py` | None |
| 0.2 | Add quality gates: ruff lint+format, mypy `strict = true` on `src/dev_harness/contracts`, pytest with `--cov-fail-under=85`. | `Makefile`, `.github/workflows/ci.yml`, `mypy.ini` | 0.1 |
| 0.3 | Define canonical enums resolving the V7 control-vocabulary conflict (see Audit §A2): `ExecutionState{READY,RUNNING,PAUSED,STOPPED}`, `CriticCommand{START,PAUSE,RESUME,STOP}`, `ChunkStatus`, `PanelId`, `EventType`. | `src/dev_harness/contracts/enums.py` | 0.1 |
| 0.4 | Implement the V7 system state as Pydantic v2 models (`HarnessState`, `GroomedRequirements`, `TechnicalDesign`, `ChunkNode`, `TuiState`, `GitState`, `RateLimitingState`, `E2EReport`). | `src/dev_harness/contracts/state.py` | 0.3 |
| 0.5 | Define the IPC envelope (`Envelope{msg_id, ts, type, project_id, thread_id, payload}`) and typed payloads for `FILE_CHANGE`, `GIT_STATUS_UPDATE`, `AGENT_TOKEN_STREAM`, `TEST_PROGRESS`, `MODEL_CONFIG_CHANGE`, `INTERRUPT_REQUEST`, `INTERRUPT_ACK`. | `src/dev_harness/contracts/events.py` | 0.3 |
| 0.6 | Export JSON Schema for `HarnessState` and commit a golden fixture instance derived from TDD §6. | `src/dev_harness/contracts/schema.py`, `schemas/harness_state.v7.json`, `tests/fixtures/state_v7_golden.json` | 0.4 |
| 0.7 | Implement config loader: TOML file + `DEV_HARNESS_*` env override, with provider blocks (`anthropic`, `openrouter`, `ollama`) carrying `rpm`, `tpm`, `max_concurrency`, `base_url`. | `src/dev_harness/config.py`, `dev-harness.example.toml` | 0.4 |
| 0.8 | Implement path derivation: canonicalize workspace path (resolve symlinks, strip trailing slash, case-normalize on macOS), derive socket path `/tmp/dev-harness-{sha256(path)[:16]}-{uid}.sock` and lock path `.git/dev-harness.lock`. | `src/dev_harness/paths.py` | 0.1 |
| 0.9 | Structured JSON logging with correlation on `project_id`/`thread_id`, plus a secret-redaction filter for `sk-*`, `Bearer *`, and configured API keys. | `src/dev_harness/observability/logging.py` | 0.7 |

### Phase 0 Validation Matrix

| Task ID | Verification Strategy | Test Commands / Validation Script | Success Criteria (Pass/Fail) |
| :--- | :--- | :--- | :--- |
| 0.1 | Build / import smoke | `pip install -e . && python -c "import dev_harness"` | Exit 0; `dev_harness.__version__` resolves. |
| 0.2 | CI gate self-test | `make lint && make typecheck && make test` | ruff 0 findings; mypy 0 errors in `contracts/`; pytest exits 0. |
| 0.3 | Enum exhaustiveness unit test | `pytest tests/contracts/test_enums.py -q` | `set(CriticCommand) == {START,PAUSE,RESUME,STOP}`; `ExecutionState.STOPPED` exists; `test_no_string_literals_outside_enums` asserts `grep -rn "\"PAUSED\"" src/ --include=*.py` returns only `enums.py`. |
| 0.4 | Round-trip serialization | `pytest tests/contracts/test_state_model.py -q` | `HarnessState.model_validate(golden).model_dump(mode="json") == golden`; missing `project_id` raises `ValidationError` with `loc == ("project_id",)`. |
| 0.5 | Payload discrimination | `pytest tests/contracts/test_events.py -q` | Each `EventType` maps to exactly one payload model; unknown `type` raises `ValidationError`; `Envelope` rejects payload/type mismatch. |
| 0.6 | Schema conformance | `pytest tests/contracts/test_schema.py -q` | `jsonschema.validate(golden, harness_state.v7.json)` passes; schema file is byte-identical to regenerated output (drift detection). |
| 0.7 | Config precedence | `pytest tests/test_config.py -q` | Env var `DEV_HARNESS_ANTHROPIC__RPM=10` overrides TOML value 50; absent required key raises `ConfigError` naming the key. |
| 0.8 | Path determinism & collision | `pytest tests/test_paths.py -q` | `/tmp/x/` and `/tmp/x` yield identical socket path; symlinked path resolves to the real path's socket; two distinct paths yield distinct hashes; path length <= 104 bytes (`sun_path` limit). |
| 0.9 | Redaction | `pytest tests/observability/test_redaction.py -q` | Log record containing `sk-ant-api03-XXXX` emits `sk-***REDACTED***`; assertion on captured `caplog.text` contains no raw key substring. |

---

## Phase 1: Persistence, Namespacing & Workspace Isolation

**Objective:** Deliver a multi-tenant checkpoint store that cannot leak state across projects and that binds every checkpoint to a recoverable Git commit.
**Prerequisites:** Phase 0 (0.4, 0.7, 0.8).

### Phase 1 Execution Tasks

| Task ID | Task Description & Deliverable | Targeted Files / Components | Prerequisite Tasks |
| :--- | :--- | :--- | :--- |
| 1.1 | Connection factory applying `PRAGMA journal_mode=WAL`, `PRAGMA synchronous=NORMAL`, `PRAGMA busy_timeout=10000`, `PRAGMA foreign_keys=ON` on every connect. | `src/dev_harness/storage/connection.py` | 0.7 |
| 1.2 | Versioned migration runner with forward and `-- down` rollback sections; records applied versions in `schema_migrations`. | `src/dev_harness/storage/migrate.py` | 1.1 |
| 1.3 | Migration `0001_init`: `checkpoints` table with composite PK `(project_id, thread_id, checkpoint_id)`, columns `state_json`, `git_commit_hash`, `is_paused`, `created_at`; index on `(project_id, thread_id, created_at DESC)`. | `src/dev_harness/storage/migrations/0001_init.sql` | 1.2 |
| 1.4 | `SqliteSaver` implementing the LangGraph `BaseCheckpointSaver` interface (`put`, `put_writes`, `get_tuple`, `list`). | `src/dev_harness/storage/sqlite_saver.py` | 1.3, 0.4 |
| 1.5 | Namespace guard: a query-builder wrapper that raises `UnscopedQueryError` if any SELECT/UPDATE/DELETE omits both `project_id` and `thread_id` predicates. | `src/dev_harness/storage/guards.py` | 1.4 |
| 1.6 | Workspace lock: `portalocker` exclusive lock context manager on `.git/dev-harness.lock`, 10s acquire timeout, PID+hostname written to the lock file for stale detection. | `src/dev_harness/storage/workspace_lock.py` | 0.8 |
| 1.7 | Git adapter: `head_sha()`, `active_branch()`, `is_dirty()`, `uncommitted_count()` via `subprocess` with explicit `cwd` and no shell. | `src/dev_harness/vcs/git.py` | 0.1 |
| 1.8 | Checkpoint↔Git binding: `put()` captures `head_sha()` inside the workspace lock so the state and the hash are atomic with respect to other sessions. | `src/dev_harness/storage/checkpoint_binding.py` | 1.4, 1.6, 1.7 |
| 1.9 | Restore path (missing entirely from V7): `restore(checkpoint_id)` refuses on dirty worktree unless `stash_policy="autostash"`, then `git checkout {sha}` and re-hydrates `HarnessState`. | `src/dev_harness/vcs/restore.py` | 1.8 |
| 1.10 | Concurrency fixture: pytest fixture spawning two isolated temp workspaces, each with its own git repo and DB. | `tests/conftest.py`, `tests/storage/test_multi_project_isolation.py` | 1.4, 1.6 |

### Phase 1 Validation Matrix

| Task ID | Verification Strategy | Test Commands / Validation Script | Success Criteria (Pass/Fail) |
| :--- | :--- | :--- | :--- |
| 1.1 | PRAGMA assertion | `pytest tests/storage/test_connection.py -q` | `SELECT * FROM pragma_journal_mode` returns `wal`; `pragma_synchronous` returns `1`; `pragma_busy_timeout` returns `10000`. |
| 1.2 | Migration up/down idempotence | `pytest tests/storage/test_migrations.py -q` | Applying 0001 twice is a no-op; rollback drops all created objects; `sqlite_master` row count returns to the pre-migration baseline exactly. |
| 1.3 | Constraint enforcement | `pytest tests/storage/test_schema_ddl.py -q` | Duplicate `(project_id, thread_id, checkpoint_id)` insert raises `sqlite3.IntegrityError`; `EXPLAIN QUERY PLAN` for the list query reports `USING INDEX idx_checkpoints_scope`. |
| 1.4 | Interface conformance + round trip | `pytest tests/storage/test_sqlite_saver.py -q` | `get_tuple()` returns the state written by `put()` with field-level equality against the golden fixture; `list(limit=3)` returns newest-first ordering. |
| 1.5 | Negative test on unscoped access | `pytest tests/storage/test_guards.py -q` | `saver.list(config={})` raises `UnscopedQueryError`; a checkpoint written under `proj_A` is **not** returned for `proj_B` (asserted count == 0). |
| 1.6 | Lock contention + stale recovery | `pytest tests/storage/test_workspace_lock.py -q` | Second acquirer in the same workspace blocks then raises `LockTimeout` after 10s (±0.5s); a lock file whose PID is not alive is reclaimed within 1 acquire attempt. |
| 1.7 | Git adapter against fixture repo | `pytest tests/vcs/test_git.py -q` | `head_sha()` matches `git rev-parse HEAD` output; `is_dirty()` is `True` after touching a tracked file and `False` after `git checkout .`. |
| 1.8 | Atomicity under concurrent commit | `pytest tests/storage/test_checkpoint_binding.py -q` | Across 50 interleaved writes, every stored `git_commit_hash` is a real object (`git cat-file -e` exit 0) and matches the HEAD at write time. |
| 1.9 | Destructive-restore guard | `pytest tests/vcs/test_restore.py -q` | Restore on a dirty tree raises `DirtyWorktreeError` and leaves `git status --porcelain` byte-identical; with `autostash`, the stash is reapplied and `git stash list` is empty afterwards. |
| 1.10 | Cross-project isolation (replaces V7's "proving zero blocking") | `pytest tests/storage/test_multi_project_isolation.py -q` | Two workspaces perform 200 writes each concurrently: 0 `database is locked` exceptions, 0 rows of A visible in B, and wall-clock duration < 2x the single-workspace baseline. |

---

## Phase 2: IPC Transport & Event Bus

**Objective:** Provide a framed, typed, backpressure-safe channel between TUI sessions and the execution engine, so panels and the gatekeeper are built against a real wire contract.
**Prerequisites:** Phase 0 (0.5, 0.8, 0.9).

### Phase 2 Execution Tasks

| Task ID | Task Description & Deliverable | Targeted Files / Components | Prerequisite Tasks |
| :--- | :--- | :--- | :--- |
| 2.1 | Resolve the TDD's "gRPC/Unix socket" ambiguity: record an ADR selecting length-prefixed JSON over `AF_UNIX` (gRPC rejected — adds a codegen toolchain for a single-host, low-fanout channel). | `docs/adr/0001-ipc-transport.md` | None |
| 2.2 | Framing codec: 4-byte big-endian length prefix + UTF-8 JSON body, max frame 1 MiB, `FrameTooLargeError` on overflow. | `src/dev_harness/ipc/framing.py` | 2.1, 0.5 |
| 2.3 | Async Unix socket server via `asyncio.start_unix_server`; creates the socket with mode `0600`, unlinks stale socket files on bind. | `src/dev_harness/ipc/server.py` | 2.2, 0.8 |
| 2.4 | Client with connect-retry (exponential backoff, cap 5s) and half-open detection. | `src/dev_harness/ipc/client.py` | 2.2 |
| 2.5 | Pub/sub router: subscribe handlers by `EventType`, fan-out to N subscribers, isolate handler exceptions. | `src/dev_harness/ipc/router.py` | 2.3 |
| 2.6 | Backpressure queue: bounded `asyncio.Queue(maxsize=2048)`; `AGENT_TOKEN_STREAM` uses drop-oldest with a dropped-frame counter, control events never drop. | `src/dev_harness/ipc/queue.py` | 2.5 |
| 2.7 | Platform gate: POSIX detected at import; on Windows raise `UnsupportedPlatformError` naming WSL2 as the supported path (V7 silently assumed POSIX via `fcntl`). | `src/dev_harness/ipc/transport.py` | 2.3 |

### Phase 2 Validation Matrix

| Task ID | Verification Strategy | Test Commands / Validation Script | Success Criteria (Pass/Fail) |
| :--- | :--- | :--- | :--- |
| 2.1 | Document review gate | `test -f docs/adr/0001-ipc-transport.md` | ADR contains Decision, Rejected Alternatives, and Consequences sections; reviewed by lead. |
| 2.2 | Property-based codec test | `pytest tests/ipc/test_framing.py -q` | Hypothesis round-trip over 500 random envelopes is lossless; a 2 MiB frame raises `FrameTooLargeError`; a truncated stream raises `IncompleteFrameError` rather than hanging. |
| 2.3 | Socket permissions & stale cleanup | `pytest tests/ipc/test_server.py -q` | `oct(os.stat(sock).st_mode)[-3:] == "600"`; binding over a leftover socket file succeeds without `EADDRINUSE`. |
| 2.4 | Reconnect behavior | `pytest tests/ipc/test_client.py -q` | Client survives a server restart and delivers the next message; retry intervals are non-decreasing and capped at 5s. |
| 2.5 | Fan-out + handler isolation | `pytest tests/ipc/test_router.py -q` | 3 subscribers each receive 1 copy; a handler raising `RuntimeError` does not prevent the other 2 from receiving, and the error is logged once. |
| 2.6 | Backpressure under flood | `pytest tests/ipc/test_queue.py -q` | 10,000 token events into a 2048 queue: process RSS growth < 50 MB, `dropped_frames > 0`, and 0 `INTERRUPT_REQUEST` events dropped. |
| 2.7 | Platform guard | `pytest tests/ipc/test_transport.py -q` | Simulated `sys.platform="win32"` raises `UnsupportedPlatformError` whose message contains "WSL2". |

---

## Phase 3: Hermes TUI Core Subsystem

**Objective:** Render the four-panel dashboard against live IPC events with a 20 Hz throttle and no blocking of the Textual event loop.
**Prerequisites:** Phase 2 (2.5, 2.6), Phase 0 (0.3).

### Phase 3 Execution Tasks

| Task ID | Task Description & Deliverable | Targeted Files / Components | Prerequisite Tasks |
| :--- | :--- | :--- | :--- |
| 3.1 | `HermesApp(textual.app.App)` shell with a CSS grid declaring the four regions and a minimum viable size. | `src/dev_harness/tui/app.py`, `src/dev_harness/tui/app.tcss` | 2.5 |
| 3.2 | `#repo-manager`: `DirectoryTree` + `DataTable` (V7 omitted `DataTable`), bound to `FILE_CHANGE` / `GIT_STATUS_UPDATE`; shows branch, uncommitted diff count, workspace path, session list. | `src/dev_harness/tui/panels/repo_manager.py` | 3.1, 1.7 |
| 3.3 | `#execution-canvas`: `RichLog` + `Sparkline` (V7 omitted `Sparkline`), consuming `AGENT_TOKEN_STREAM` and `TEST_PROGRESS`. | `src/dev_harness/tui/panels/execution_canvas.py` | 3.1 |
| 3.4 | `#model-registry`: `OptionList` + `Static`, rendering active provider, latency p50/p95, and token burn rate from `MODEL_CONFIG_CHANGE` and broker metrics (consumes the Phase 5 feed; ships behind a "no data" placeholder until 5.9 lands). | `src/dev_harness/tui/panels/model_registry.py` | 3.1 |
| 3.5 | `#critic-bar`: `Input` + PAUSE/RESUME/STOP `Button`s + HITL Approve/Reject gate buttons; publishes `INTERRUPT_REQUEST`. | `src/dev_harness/tui/panels/critic_bar.py` | 3.1, 0.3 |
| 3.6 | IPC→UI bridge: background worker draining the bounded queue and marshalling to widgets via `App.call_from_thread` / `post_message`. | `src/dev_harness/tui/bridge.py` | 2.6, 3.1 |
| 3.7 | 20 Hz coalescing throttle: `set_interval(0.05)` flushing an accumulated token buffer as a single write. | `src/dev_harness/tui/throttle.py` | 3.6 |
| 3.8 | Keybindings: `Ctrl+C` remapped as a priority binding to PAUSE (Textual's default is quit — a real trap V7 inherited from TDD §3.1), quit moved to `Ctrl+Q` with a confirm modal. | `src/dev_harness/tui/bindings.py` | 3.5 |
| 3.9 | Safe renderer: Markdown + unified-diff colorizer that escapes Rich markup in model output. | `src/dev_harness/tui/render.py` | 3.3 |
| 3.10 | CLI entrypoint `dev-harness [--workspace PATH]` wiring config, paths, IPC client, and app. | `src/dev_harness/cli.py`, `pyproject.toml` `[project.scripts]` | 3.1, 0.7 |

### Phase 3 Validation Matrix

| Task ID | Verification Strategy | Test Commands / Validation Script | Success Criteria (Pass/Fail) |
| :--- | :--- | :--- | :--- |
| 3.1 | Headless mount test | `pytest tests/tui/test_layout.py -q` | Under `async with app.run_test()`, `app.query_one("#repo-manager")` and the other three IDs all resolve; snapshot of the 100x30 render matches the committed baseline. |
| 3.2 | Event-driven update | `pytest tests/tui/test_repo_manager.py -q` | Injecting `GIT_STATUS_UPDATE{branch:"feat/x", dirty:3}` updates the DataTable cell to `feat/x` and `3` within 100ms. |
| 3.3 | Stream consumption | `pytest tests/tui/test_execution_canvas.py -q` | 500 token events produce a RichLog whose concatenated text equals the input exactly; Sparkline data length == number of `TEST_PROGRESS` events. |
| 3.4 | Placeholder + populated states | `pytest tests/tui/test_model_registry.py -q` | With no broker feed, Static renders `latency: —`; with a metrics event, renders `p95` to 1 decimal place. |
| 3.5 | Command publication | `pytest tests/tui/test_critic_bar.py -q` | Clicking `#btn-pause` emits exactly one `INTERRUPT_REQUEST` envelope with `payload.command == CriticCommand.PAUSE`. |
| 3.6 | Thread-safety | `pytest tests/tui/test_bridge.py -q` | 5,000 events pushed from a non-UI thread produce 0 `NoActiveAppError` and 0 dropped control events. |
| 3.7 | Throttle rate (replaces V7's "without UI blockages") | `pytest tests/tui/test_throttle.py -q` | Over a 2s flood of 10,000 tokens, `RichLog.write` call count <= 44 (20 Hz + 10% tolerance) and the UI event loop's max single-iteration latency < 50ms. |
| 3.8 | Keybinding override | `pytest tests/tui/test_bindings.py -q` | `await pilot.press("ctrl+c")` leaves `app.is_running is True` and emits a PAUSE command; `ctrl+q` opens the confirm modal. |
| 3.9 | Markup injection safety | `pytest tests/tui/test_render.py -q` | Model output containing `[bold red]` renders as literal text, not styled; no `MarkupError` raised. |
| 3.10 | CLI smoke | `dev-harness --workspace ./tmp/ws --self-check` | Exit 0; prints resolved socket path and DB path; `--workspace` pointing at a non-git directory exits 2 with `NotAGitRepository`. |

---

## Phase 4: Critic Gatekeeper & Asynchronous Interrupt Engine

**Objective:** Guarantee sub-second, leak-free interruption of agent tasks and their subprocess trees, with a sealed checkpoint on every pause.
**Prerequisites:** Phase 2 (2.3, 2.5), Phase 1 (1.8), Phase 0 (0.3).

### Phase 4 Execution Tasks

| Task ID | Task Description & Deliverable | Targeted Files / Components | Prerequisite Tasks |
| :--- | :--- | :--- | :--- |
| 4.1 | `CriticGatekeeper` state machine with an explicit legal-transition table over `ExecutionState`; illegal transitions raise `IllegalTransitionError`. | `src/dev_harness/core/critic.py` | 0.3 |
| 4.2 | Command handler mapping `CriticCommand` → transition, idempotent (PAUSE while PAUSED is a no-op returning `INTERRUPT_ACK{already:true}`). | `src/dev_harness/core/critic_commands.py` | 4.1, 2.5 |
| 4.3 | Task registry tracking live `asyncio.Task` handles per `thread_id`; `cancel_all()` awaits cancellation with a 1s join. | `src/dev_harness/core/task_registry.py` | 4.1 |
| 4.4 | Subprocess group manager: all external runners spawned with `start_new_session=True`; registry of PGIDs per thread. | `src/dev_harness/core/process_group.py` | 4.3 |
| 4.5 | Signal escalation: `killpg(SIGINT)` → 3.0s grace → `killpg(SIGKILL)`; reap with `waitpid` to prevent zombies. | `src/dev_harness/core/signals.py` | 4.4 |
| 4.6 | Pause seal: on entering PAUSED, write a checkpoint with `is_paused=True`, `last_interrupt_timestamp`, and the bound `git_commit_hash`. | `src/dev_harness/core/pause_seal.py` | 4.2, 1.8 |
| 4.7 | Latency instrumentation: histogram of interrupt-request → all-tasks-cancelled duration, exported on `TEST_PROGRESS`-adjacent metrics channel. | `src/dev_harness/core/metrics.py` | 4.5 |

### Phase 4 Validation Matrix

| Task ID | Verification Strategy | Test Commands / Validation Script | Success Criteria (Pass/Fail) |
| :--- | :--- | :--- | :--- |
| 4.1 | Transition table exhaustive test | `pytest tests/core/test_critic_state.py -q` | All 16 (state, command) pairs asserted; `RESUME` from `STOPPED` raises `IllegalTransitionError`; no transition leaves an undefined state. |
| 4.2 | Idempotency | `pytest tests/core/test_critic_commands.py -q` | Two consecutive PAUSE commands yield one state change and two ACKs, the second with `already == True`. |
| 4.3 | Task cancellation | `pytest tests/core/test_task_registry.py -q` | 20 sleeping tasks all reach `task.cancelled() is True`; `asyncio.all_tasks()` returns only the test task after `cancel_all()`. |
| 4.4 | Process group creation | `pytest tests/core/test_process_group.py -q` | `os.getpgid(child.pid) != os.getpgid(0)`; registry contains exactly one PGID per spawned runner. |
| 4.5 | Orphan elimination (the V7 flagged risk, now a test) | `pytest tests/core/test_signals.py -q` | A runner that spawns 3 grandchildren and ignores SIGINT: after escalation, `pgrep -g {pgid}` returns empty within 4.0s and `psutil` reports 0 zombies. |
| 4.6 | Seal correctness | `pytest tests/core/test_pause_seal.py -q` | Latest checkpoint has `is_paused is True`, `last_interrupt_timestamp` within 1s of the request, and `git_commit_hash == git rev-parse HEAD`. |
| 4.7 | Sub-second SLO (replaces V7's unmeasured "sub-second") | `pytest tests/core/test_interrupt_latency.py -q` | Over 50 trials, p95 interrupt-to-cancelled < 500ms and max < 1000ms; results written to `reports/interrupt_latency.json`. |

---

## Phase 5: Centralized Rate Limiting Broker & Provider Adapters

**Objective:** Prevent HTTP 429s on hosted providers and memory saturation on local inference, with a shared broker that degrades safely when absent.
**Prerequisites:** Phase 2 (2.3, 2.4), Phase 0 (0.7).

### Phase 5 Execution Tasks

| Task ID | Task Description & Deliverable | Targeted Files / Components | Prerequisite Tasks |
| :--- | :--- | :--- | :--- |
| 5.1 | Token bucket primitive using `time.monotonic()`, fractional refill, thread-safe acquire with timeout. | `src/dev_harness/broker/bucket.py` | 0.1 |
| 5.2 | Provider policy registry loading RPM/TPM/max_concurrency per provider from config. | `src/dev_harness/broker/policies.py` | 5.1, 0.7 |
| 5.3 | Token estimator: `estimate(prompt, max_output)` using the provider tokenizer where available, else a 4-chars/token heuristic with a +15% safety margin (V7 required TPM but assigned no estimator). | `src/dev_harness/broker/estimator.py` | 5.2 |
| 5.4 | Reservation protocol `reserve → commit(actual_tokens) | release`, with a 120s reservation TTL so crashed clients cannot leak capacity. | `src/dev_harness/broker/reservation.py` | 5.3 |
| 5.5 | Local-inference limiter: `ollama` governed by a concurrency semaphore + queue depth (a local server emits no 429; RPM/TPM is the wrong control — see Audit §A5). | `src/dev_harness/broker/local_limiter.py` | 5.2 |
| 5.6 | Backoff: `t_wait = min(max_backoff, base * 2**attempt) + U(0, jitter)`, honoring a `Retry-After` header when present. | `src/dev_harness/broker/backoff.py` | 5.1 |
| 5.7 | `dev-harness-broker` daemon: single-instance lock, IPC endpoint, `HEALTH` command, graceful shutdown draining reservations. | `src/dev_harness/broker/daemon.py` | 5.4, 5.5, 2.3 |
| 5.8 | Broker client SDK with explicit fail-closed policy: if the broker is unreachable, requests are refused (not silently unlimited) unless `allow_unbrokered=true`. | `src/dev_harness/broker/client.py` | 5.7, 2.4 |
| 5.9 | Metrics feed publishing p50/p95 latency and tokens-per-minute burn to `MODEL_CONFIG_CHANGE` consumers (completes Task 3.4). | `src/dev_harness/broker/metrics_feed.py` | 5.7, 3.4 |

### Phase 5 Validation Matrix

| Task ID | Verification Strategy | Test Commands / Validation Script | Success Criteria (Pass/Fail) |
| :--- | :--- | :--- | :--- |
| 5.1 | Deterministic clock test | `pytest tests/broker/test_bucket.py -q` | With a frozen monotonic clock, a 50-capacity bucket grants exactly 50 in the first window and 0 more until refill; no drift after 10 simulated minutes. |
| 5.2 | Policy loading | `pytest tests/broker/test_policies.py -q` | Unknown provider raises `UnknownProviderError`; `ollama` policy exposes `max_concurrency` and no `rpm`. |
| 5.3 | Estimator bounds | `pytest tests/broker/test_estimator.py -q` | Estimate >= actual tokenizer count for 20 corpus samples (0 underestimates); overhead <= 30%. |
| 5.4 | Reservation leak prevention | `pytest tests/broker/test_reservation.py -q` | A client killed after `reserve` releases capacity at TTL+1s; `commit` with actual < reserved returns the difference to the bucket. |
| 5.5 | Local saturation control | `pytest tests/broker/test_local_limiter.py -q` | With `max_concurrency=2`, 10 concurrent ollama calls show max in-flight == 2 at every sample; queue depth reported accurately. |
| 5.6 | Backoff distribution | `pytest tests/broker/test_backoff.py -q` | Sequence for attempts 0..5 is non-decreasing before jitter and capped at `max_backoff`; `Retry-After: 30` overrides the computed value. |
| 5.7 | Daemon lifecycle | `pytest tests/broker/test_daemon.py -q` | Second daemon instance exits 3 with `AlreadyRunning`; `HEALTH` returns `{status:"ok"}` in < 50ms; SIGTERM drains within 5s with 0 orphaned reservations. |
| 5.8 | Ceiling adherence under load (replaces V7's untoleranced claim) | `pytest tests/broker/test_rate_limiter_load.py -q` | 100 concurrent reservations against a 50 RPM policy: grants in any rolling 60s window <= 50, 0 unhandled exceptions, and with the broker stopped the client raises `BrokerUnavailableError` rather than proceeding. |
| 5.9 | End-to-end metrics render | `pytest tests/broker/test_metrics_feed.py -q` | `#model-registry` Static shows a numeric p95 within 1s of a simulated call batch. |

---

## Phase 6: Agent SDLC Pipeline Engine (LangGraph Orchestrator)

**Objective:** Assemble the Groomer → Architect → Developer → Tester → Critic graph over the V7 state schema with bounded retries, HITL gates, and context truncation.
**Prerequisites:** Phase 1 (1.4), Phase 4 (4.2), Phase 5 (5.8), Phase 0 (0.4).

### Phase 6 Execution Tasks

| Task ID | Task Description & Deliverable | Targeted Files / Components | Prerequisite Tasks |
| :--- | :--- | :--- | :--- |
| 6.1 | Persona prompt templates for the five roles, each with an explicit output contract and refusal behavior (V7 required "persona validation" but shipped no personas). | `src/dev_harness/engine/personas/{groomer,architect,developer,tester,critic}.md` | 0.4 |
| 6.2 | LangGraph channel definitions and reducers (append-only for `chunk_dag`, last-write-wins for `tui_state`, counter reducers for retries). | `src/dev_harness/engine/state.py` | 0.4, 1.4 |
| 6.3 | Groomer node: raw input → `groomed_requirements` with `status=LOCKED` and `locked_at_timestamp`. | `src/dev_harness/engine/nodes/groomer.py` | 6.1, 6.2 |
| 6.4 | Architect node: groomed requirements → `technical_design` including `openapi_spec` and `db_schema` contract strings. | `src/dev_harness/engine/nodes/architect.py` | 6.3 |
| 6.5 | Chunk DAG builder + topological validator with cycle detection and orphan-dependency detection. | `src/dev_harness/engine/dag.py` | 6.4 |
| 6.6 | Developer node: consumes one `chunk_id`, emits file writes through a workspace-scoped writer that refuses paths outside the workspace root. | `src/dev_harness/engine/nodes/developer.py` | 6.5, 4.4 |
| 6.7 | Tester node + differential runner: executes only tests affected by the chunk's changed files, returns structured pass/fail. | `src/dev_harness/engine/nodes/tester.py`, `src/dev_harness/engine/testing/differential.py` | 6.6 |
| 6.8 | Retry router: `inner_loop_retry_count` ceiling 3 → escalate to Architect; `e2e_retry_count` ceiling 2 → escalate to HITL (V7 carried the counters but defined no ceilings). | `src/dev_harness/engine/routing.py` | 6.7 |
| 6.9 | Critic node as a strict binary gate: may write only `tui_state.critic_gatekeeper_status` and `is_paused`; any other field write raises `CriticScopeViolation`. | `src/dev_harness/engine/nodes/critic.py` | 6.2, 4.2 |
| 6.10 | HITL gate via `interrupt_before` on approval nodes, resumed by a `Command(resume=...)` originating from `#critic-bar`. | `src/dev_harness/engine/hitl.py` | 6.9, 3.5 |
| 6.11 | Context manager: stack traces capped at 50 lines (head 30 / tail 20) plus a total prompt-token budget derived from the active model's context window. | `src/dev_harness/engine/context.py` | 5.3 |
| 6.12 | E2E failure classifier routing `CHUNK_IMPLEMENTATION_BUG` → Developer and `INTEGRATION_SPEC_MISMATCH` → Architect. | `src/dev_harness/engine/classifier.py` | 6.8 |
| 6.13 | Graph assembly and compile with `SqliteSaver` as checkpointer; exported factory `build_graph(config)`. | `src/dev_harness/engine/pipeline.py` | 6.3–6.12 |

### Phase 6 Validation Matrix

| Task ID | Verification Strategy | Test Commands / Validation Script | Success Criteria (Pass/Fail) |
| :--- | :--- | :--- | :--- |
| 6.1 | Contract lint | `pytest tests/engine/test_personas.py -q` | Each persona file declares an `## Output Contract` section; none contains instructions permitting the Critic to modify code. |
| 6.2 | Reducer semantics | `pytest tests/engine/test_state_reducers.py -q` | Two parallel `chunk_dag` appends yield 2 entries (no clobber); concurrent `inner_loop_retry_count` increments yield exactly +2. |
| 6.3 | Node output schema | `pytest tests/engine/test_groomer.py -q` | With a mocked LLM, output validates against `GroomedRequirements`; `status == "LOCKED"`; a second invocation on locked requirements is a no-op. |
| 6.4 | Design contract presence | `pytest tests/engine/test_architect.py -q` | `technical_design.interface_contracts.openapi_spec` parses as valid YAML/JSON; empty design sets `status != "APPROVED"`. |
| 6.5 | DAG validation | `pytest tests/engine/test_dag.py -q` | A cyclic fixture raises `CyclicDependencyError` naming both chunk IDs; a dependency on an unknown chunk raises `OrphanDependencyError`; topological order is stable across runs. |
| 6.6 | Path traversal guard | `pytest tests/engine/test_developer.py -q` | A write to `../../etc/passwd` raises `WorkspaceEscapeError`; legitimate writes land under the workspace root only. |
| 6.7 | Differential selection | `pytest tests/engine/test_differential.py -q` | Changing one module selects only its dependent test files (asserted set equality against fixture); full-suite fallback triggers when the dependency graph is unavailable. |
| 6.8 | Retry ceilings | `pytest tests/engine/test_routing.py -q` | Forced failures route to Architect on the 4th inner-loop attempt, never loop infinitely, and terminate with `status=FAILED` after the e2e ceiling. |
| 6.9 | Zero-drift enforcement | `pytest tests/engine/test_critic_scope.py -q` | A Critic node attempting to write `groomed_requirements` raises `CriticScopeViolation`; a full state diff after a PAUSE/RESUME cycle shows changes confined to `tui_state`. |
| 6.10 | HITL round trip | `pytest tests/engine/test_hitl.py -q` | Graph halts at the approval node with a persisted checkpoint; a resume Command from a simulated button click advances exactly one node. |
| 6.11 | Truncation | `pytest tests/engine/test_context.py -q` | A 500-line trace is reduced to 50 lines containing both the first and last frames; total prompt tokens <= model context minus `max_output`. |
| 6.12 | Classifier routing | `pytest tests/engine/test_classifier.py -q` | 10 labelled fixture reports route to the correct node with 100% accuracy; an unparseable report routes to HITL rather than guessing. |
| 6.13 | End-to-end graph (replaces V7's single vague E2E claim) | `pytest tests/engine/test_sdlc_pipeline.py -q` | Mock SDLC flow from raw requirement to green unit test completes; final checkpoint in SQLite validates against `harness_state.v7.json` and `chunk_dag[0].status == "COMPLETED"`. |

---

## Phase 7: Error Handling, Recovery & Edge Cases

**Objective:** Make every failure mode identified in the audit observable, recoverable, and covered by a negative test.
**Prerequisites:** Phases 1–6.

### Phase 7 Execution Tasks

| Task ID | Task Description & Deliverable | Targeted Files / Components | Prerequisite Tasks |
| :--- | :--- | :--- | :--- |
| 7.1 | Crash recovery: on startup, detect an un-finalized session and offer resume-from-last-checkpoint. | `src/dev_harness/recovery/session_recovery.py` | 6.13, 1.9 |
| 7.2 | Stale artifact reclamation for orphaned sockets and lock files after an unclean exit. | `src/dev_harness/recovery/reclaim.py` | 2.3, 1.6 |
| 7.3 | Provider fallback chain (e.g. `anthropic → openrouter → ollama`) with an explicit degradation notice on `#model-registry`. | `src/dev_harness/broker/fallback.py` | 5.8 |
| 7.4 | Corrupt checkpoint detection: checksum over `state_json`, quarantine on mismatch, roll back to the previous valid checkpoint. | `src/dev_harness/storage/integrity.py` | 1.4 |
| 7.5 | Terminal degradation: below 80x24, collapse to a single-panel view rather than crashing on layout. | `src/dev_harness/tui/responsive.py` | 3.1 |
| 7.6 | Git edge cases: detached HEAD, unborn branch (no commits), and submodule presence handled explicitly. | `src/dev_harness/vcs/edge_cases.py` | 1.9 |
| 7.7 | Secret redaction applied to the execution canvas as well as logs. | `src/dev_harness/tui/render.py`, `src/dev_harness/observability/redact.py` | 0.9, 3.9 |
| 7.8 | Resource exhaustion: disk-full and `SQLITE_BUSY` produce an actionable error banner instead of a traceback. | `src/dev_harness/storage/errors.py` | 1.1 |

### Phase 7 Validation Matrix

| Task ID | Verification Strategy | Test Commands / Validation Script | Success Criteria (Pass/Fail) |
| :--- | :--- | :--- | :--- |
| 7.1 | Kill-and-resume | `pytest tests/recovery/test_session_recovery.py -q` | SIGKILL mid-run, restart: state equals the last sealed checkpoint field-for-field, and the worktree SHA matches `git_commit_hash`. |
| 7.2 | Reclamation | `pytest tests/recovery/test_reclaim.py -q` | A stale socket + lock from a dead PID are removed and a fresh session starts within 2s; a live PID's artifacts are left untouched. |
| 7.3 | Fallback chain | `pytest tests/broker/test_fallback.py -q` | Primary returning 529 routes to secondary within 1 retry; `#model-registry` shows `DEGRADED`; exhausting the chain raises `AllProvidersUnavailable`. |
| 7.4 | Corruption handling | `pytest tests/storage/test_integrity.py -q` | A byte-flipped `state_json` is quarantined (row moved to `checkpoints_quarantine`) and `get_tuple()` returns the prior valid checkpoint. |
| 7.5 | Small-terminal render | `pytest tests/tui/test_responsive.py -q` | `run_test(size=(60,20))` mounts without exception and `app.query("#execution-canvas")` is the only visible panel. |
| 7.6 | Git edge fixtures | `pytest tests/vcs/test_edge_cases.py -q` | Unborn branch yields `NoCommitsError` (not a `CalledProcessError`); detached HEAD restore succeeds and reports the detached state. |
| 7.7 | Canvas redaction | `pytest tests/tui/test_canvas_redaction.py -q` | A streamed token sequence containing an API key renders redacted; the raw key appears in neither the widget buffer nor any log file. |
| 7.8 | Exhaustion simulation | `pytest tests/storage/test_errors.py -q` | Simulated `ENOSPC` and `SQLITE_BUSY` each produce a `HarnessError` with a remediation hint; 0 unhandled tracebacks surface to the TUI. |

---

## Phase 8: Final Verification, Traceability & Release

**Objective:** Prove the assembled system against the TDD end to end and gate the release on measurable thresholds.
**Prerequisites:** Phases 0–7 fully green.

### Phase 8 Execution Tasks

| Task ID | Task Description & Deliverable | Targeted Files / Components | Prerequisite Tasks |
| :--- | :--- | :--- | :--- |
| 8.1 | E2E scenario: one requirement → groomed → designed → chunked → implemented → tested, with a scripted mid-run PAUSE and RESUME. | `tests/e2e/test_full_sdlc.py` | 7.* |
| 8.2 | Two-project concurrent soak: 30 minutes, both workspaces active, shared broker. | `tests/e2e/test_concurrent_soak.py` | 8.1 |
| 8.3 | Coverage and typing gate wired into CI as a merge blocker. | `.github/workflows/ci.yml` | 0.2 |
| 8.4 | Schema conformance regression against `harness_state.v7.json` for every checkpoint produced during the E2E run. | `tests/e2e/test_schema_conformance.py` | 8.1 |
| 8.5 | Traceability matrix generator mapping each TDD section to the implementing task IDs and their test files. | `scripts/generate_traceability.py`, `docs/traceability.md` | 8.1 |
| 8.6 | Packaging, `README`, operator runbook (start/stop broker, recover a wedged session). | `README.md`, `docs/runbook.md` | 8.1 |

### Phase 8 Validation Matrix

| Task ID | Verification Strategy | Test Commands / Validation Script | Success Criteria (Pass/Fail) |
| :--- | :--- | :--- | :--- |
| 8.1 | Full-system E2E | `pytest tests/e2e/test_full_sdlc.py -q --timeout=900` | Run completes; PAUSE seals a checkpoint with `is_paused=True`; RESUME continues from that exact checkpoint; final chunk status `COMPLETED`; 0 orphan processes (`pgrep -g` empty). |
| 8.2 | Soak | `pytest tests/e2e/test_concurrent_soak.py -q --timeout=2400` | 0 `database is locked` errors; 0 cross-project state rows; RSS growth < 15% over 30 min; broker grant rate within each provider ceiling. |
| 8.3 | CI gate | `make ci` | Coverage >= 85% overall and >= 95% for `core/`, `storage/`, `broker/`; mypy 0 errors; ruff 0 findings. |
| 8.4 | Schema regression | `pytest tests/e2e/test_schema_conformance.py -q` | 100% of emitted checkpoints validate; any added field fails the test until the schema and golden fixture are updated together. |
| 8.5 | Traceability completeness | `python scripts/generate_traceability.py --check` | Every TDD section 2–6 maps to >= 1 task ID and >= 1 passing test; exit 1 on any unmapped section. |
| 8.6 | Runbook dry run | Manual: follow `docs/runbook.md` on a clean machine | A new operator reaches a running four-panel TUI with a live broker in < 10 minutes without consulting source code. |

---

## 9. Engineering Assessment & Open Risks

| # | Risk | Impact | Mitigation / Owning Task |
| :--- | :--- | :--- | :--- |
| R1 | `git checkout` on restore is destructive to uncommitted work. | Data loss | Dirty-tree guard + autostash (1.9); operator warning in runbook (8.6). |
| R2 | Qwen 2.5 Coder 7B context window is small relative to accumulated SDLC state. | Silent truncation, degraded output | Total-budget enforcement, not just the 50-line trace cap (6.11); per-model budget in config (0.7). |
| R3 | `/tmp` sockets on a shared host are hijackable via symlink races. | Privilege/session hijack | uid-suffixed path, `0600` mode, stale-file unlink (0.8, 2.3). |
| R4 | Broker is a single point of failure for all providers. | Full stall | Explicit fail-closed default with documented `allow_unbrokered` escape hatch (5.8); health endpoint (5.7). |
| R5 | POSIX-only primitives (`fcntl`, `AF_UNIX`) block native Windows. | Platform limit | Declared unsupported with WSL2 guidance rather than silently failing (2.7). |
| R6 | Differential test selection can miss transitively affected tests. | False green | Documented full-suite fallback + full suite required in Phase 8 gates (6.7, 8.1). |
| R7 | LLM-authored code executed in-process. | Arbitrary execution | Workspace-root write guard (6.6); subprocess isolation and killable process groups (4.4). |

## 10. Traceability: TDD Section → Implementing Tasks

| TDD Section | Requirement | Implementing Tasks | Verifying Tests |
| :--- | :--- | :--- | :--- |
| §2.1 | Async event loop / worker separation | 3.1, 3.6 | `test_layout.py`, `test_bridge.py` |
| §2.1 | 20 Hz render throttle | 3.7 | `test_throttle.py` |
| §2.2 | Four-panel widget protocol (incl. `DataTable`, `Sparkline`) | 3.2–3.5 | `test_repo_manager.py`, `test_execution_canvas.py`, `test_model_registry.py`, `test_critic_bar.py` |
| §2.2 | Panel event subscriptions | 0.5, 2.5 | `test_events.py`, `test_router.py` |
| §3.1 | Interrupt dispatch & Ctrl+C trigger | 3.8, 4.2 | `test_bindings.py`, `test_critic_commands.py` |
| §3.1 | Task cancellation & SIGINT→SIGKILL | 4.3, 4.5 | `test_task_registry.py`, `test_signals.py` |
| §3.1 | Checkpoint seal on pause | 4.6 | `test_pause_seal.py` |
| §3.2 | Gatekeeper state machine | 4.1 | `test_critic_state.py` |
| §4.1 | WAL + synchronous=NORMAL | 1.1 | `test_connection.py` |
| §4.1 | `(project_id, thread_id)` namespacing | 1.3, 1.5 | `test_schema_ddl.py`, `test_guards.py` |
| §4.2 | Workspace-scoped sockets | 0.8, 2.3 | `test_paths.py`, `test_server.py` |
| §4.2 | Directory-scoped locks | 1.6 | `test_workspace_lock.py` |
| §4.2 | Git checkpoint + restore | 1.8, 1.9 | `test_checkpoint_binding.py`, `test_restore.py` |
| §5.1 | Token bucket / RPM / TPM | 5.1–5.4 | `test_bucket.py`, `test_rate_limiter_load.py` |
| §5.1 | Local inference saturation | 5.5 | `test_local_limiter.py` |
| §5.1 | Exponential backoff + jitter | 5.6 | `test_backoff.py` |
| §6 | Unified V7 state schema | 0.4, 0.6, 6.2 | `test_state_model.py`, `test_schema.py` |
| §6 | `chunk_dag` structure | 6.5 | `test_dag.py` |
| §6 | Retry counters & e2e classification | 6.8, 6.12 | `test_routing.py`, `test_classifier.py` |
