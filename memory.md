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
| P7 Hermes TUI Core Subsystem | in progress | `scripts/verify_phase_07.sh` | — |
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

- **Phase**: P7 — Hermes TUI Core Subsystem (in progress; all 13 tasks done except 7.12)
- **Lane**: C
- **Current task**: P7 D13 - 7.12 verify_phase_07 script
- **Last completed task**: P7 7.11 root CLI (`c3f0c09`, 6 passed)
- **Next task**: dispatch 7.12 (single-task brief); then 7.D reviewer sign-off

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
