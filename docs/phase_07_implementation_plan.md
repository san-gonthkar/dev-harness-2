# Phase 7 Implementation Plan — Hermes TUI Core Subsystem

**Status:** planned (not started) · **Lane:** C · **Est:** 35.0h, 13 tasks
**Prerequisites:** 5.4, 5.8, 2.6, 0.3 (all green: P6 closed 2026-09-23)
**Objective:** Render the four-panel dashboard against a live engine with a 20 Hz throttle,
bounded memory, and no event-loop blocking.
**Gate:** `scripts/verify_phase_07.sh` -> `reports/phase_07_acceptance.json` `ACCEPTED`.
**Authoritative contract:** `requirements/Dev_Harness_Implementation_Plan_V11_Final.md` §7.A–§7.D.

---

## 1. Chunking Decision

Evidence from P6 (recorded in `memory.md` S16/S22/S23 vs S24/S25 + 6.8a–6.8d):

| Brief shape | Outcome |
| :--- | :--- |
| Multi-task batch (6.4–6.6, 6.8+6.9) | Empty return, no artifact (3x) |
| Single task (6.9a, 6.9b, 6.8a–d) | Succeeded every time |

**Rule for P7: one task per dispatch.** P7 is the largest phase (13 tasks). Each dispatch brief is
bounded and self-contained; the orchestrator verifies the commit before dispatching the next task.
No multi-task briefs.

Every dispatch brief MUST carry:
- Exactly one task ID and its targeted file(s).
- Budget: <= 25 tool calls, <= 25 minutes wall-clock.
- Output contract: write file -> validate -> `git commit` (with `Task-Id:` trailer) -> `git push` -> append `memory.md`.
- Hard rules: **bounded** waits only (no unbounded `while true`); no `time.sleep()` in tests (frozen clock); smoke lane only; do not investigate mutmut; stop-and-report on a loop.

---

## 2. Dispatch Order (dependency waves)

Subagents run serially. Order is topological **and** risk-first: the three logic-bearing modules
(`bridge`, `throttle`, `render` — the 95/90 contract) are dispatched early, right after the shell.

| # | Task | Deliverable | Prereq | Validation (PR tier) |
| :--- | :--- | :--- | :--- | :--- |
| D1 | 7.1 | `HermesApp` shell + CSS grid, four regions | 5.4 | `pytest tests/tui/test_layout.py -q` |
| D2 | 7.7 | IPC→UI bridge (`call_from_thread`/`post_message`), SNAPSHOT-first | 7.1, 2.6 | `pytest tests/tui/test_bridge.py -q` |
| D3 | 7.8 | 20 Hz coalescing throttle | 7.7 | `pytest tests/tui/test_throttle.py -q` |
| D4 | 7.3 | `#execution-canvas`: RichLog + Sparkline | 7.1 | `pytest tests/tui/test_execution_canvas.py -q` |
| D5 | 7.10 | Safe renderer (Markdown + diff, markup escaping) | 7.3 | `pytest tests/tui/test_render.py -q` |
| D6 | 7.4 | Scrollback cap + spill to run artifact file | 7.3, 0.15 | `pytest tests/tui/test_scrollback.py -q` (fast part) |
| D7 | 7.2 | `#repo-manager`: DirectoryTree + DataTable | 7.1, 1.9 | `pytest tests/tui/test_repo_manager.py -q` |
| D8 | 7.5 | `#model-registry`: model, p50/p95, TPM, USD | 7.1, 4.10 | `pytest tests/tui/test_model_registry.py -q` |
| D9 | 7.13 | Recorded `METRICS_UPDATE` replay feed | 7.5 | `pytest tests/support/test_metrics_replay.py -q` |
| D10 | 7.6 | `#critic-bar`: Input + PAUSE/RESUME/STOP + HITL | 7.1, 0.3 | `pytest tests/tui/test_critic_bar.py -q` |
| D11 | 7.9 | Keybindings: Ctrl+C -> PAUSE, Ctrl+Q -> confirm modal | 7.6 | `pytest tests/tui/test_bindings.py -q` |
| D12 | 7.11 | CLI `dev-harness [--workspace] [--self-check]` | 7.1, 5.6 | `dev-harness --workspace ./tmp/ws --self-check` |
| D13 | 7.12 | `scripts/verify_phase_07.sh` (+ `.ps1` twin) | 0.18 | `bash scripts/verify_phase_07.sh` |
| GATE | 7.D | reviewer-agent acceptance review | D1–D13 | `reports/phase_07_acceptance.json` |

**Gate ordering:** 7.12 is a tracked requirement from the start, but its brief is dispatched only
after the panels exist. The reviewer sign-off is the final step.

---

## 3. Per-Task Acceptance Criteria (from §7.B)

- **7.1** — 4 panel IDs (`#repo-manager`, `#execution-canvas`, `#model-registry`, `#critic-bar`) resolve under `run_test()`; a 100x30 render matches a **reviewed** snapshot baseline committed in this task's PR.
- **7.2** — `GIT_STATUS_UPDATE{branch:"feat/x",dirty:3}` updates cells within 100ms (pilot, no sleep).
- **7.3** — 500 `AGENT_TOKEN_STREAM` events reassemble exactly; Sparkline length == `TEST_PROGRESS` count.
- **7.4** — `-m slow`: 200k lines -> RichLog <= `max_lines`; RSS growth < 100 MB; spilled lines retrievable in order.
- **7.5** — no feed -> `latency: —`; with feed -> p95 to 1 dp, USD to 4 dp; `MODEL_CONFIG_CHANGE` updates the active-model cell.
- **7.6** — `#btn-pause` emits exactly one `INTERRUPT_REQUEST{PAUSE}`.
- **7.7** — 5,000 cross-thread events: 0 `NoActiveAppError`, 0 dropped control events; `SNAPSHOT` applied before deltas on attach.
- **7.8** — `-m timing`: 2s / 10k tokens -> <= 44 `RichLog.write` calls; max loop iteration < 50ms.
- **7.9** — `ctrl+c` leaves the app running and emits PAUSE; `ctrl+q` opens the modal.
- **7.10** — `[bold red]` renders literally; no `MarkupError`.
- **7.11** — exit 0; prints socket, DB, broker + engine health; non-git dir -> exit 2 `NotAGitRepository`.
- **7.12** — exit 0; acceptance JSON `ACCEPTED`.
- **7.13** — replay emits the recorded feed with original values and timing.

---

## 4. Coverage Contract (§7.C)

| Scope | Line | Branch | Enforced by |
| :--- | :--- | :--- | :--- |
| `tui/` overall | 75% | 65% | `.coveragerc` `[coverage:report:tui]` + `coverage_gate.py` |
| `tui/bridge.py`, `tui/throttle.py`, `tui/render.py` | 95% | 90% | Plan 7.C — verify per-module (like P6 `signals.py`), **not** in the gate table |

The split is deliberate: widget wiring is pilot/snapshot-tested (75/65), while thread marshalling,
coalescing math, and markup escaping carry real logic and must meet 95/90. The 7.12 script and the
reviewer must assert the three-module figures explicitly.

---

## 5. Invariants to Respect

- **`tui/` and `engine/` never import each other** — they communicate only over IPC events. `tui/bridge.py` consumes `Envelope`s via the IPC client (`ipc/client.py`), **not** `engine.fanout`. For unit tests, inject an envelope-source callable or use `ipc/queue.BackpressureQueue`; do not import `engine/` from `tui/`.
- One marker per test (`unit` / `integration` / `negative` / `timing` / `slow`); unmarked tests fail collection.
- No `time.sleep()` in tests — use `tests/support/clock.py` (frozen clock) and `pilot.pause()`.
- Branch coverage via `--cov-branch`; every `# pragma: no cover` needs a same-line justification.
- No new dependencies without a ponytail-minimalism justification. Snapshot comparison is hand-rolled over Textual's render API, not a new plugin.
- New test package `tests/tui/` needs `__init__.py` (the 6.9a `test_cli` basename-collision lesson).
- No bare `raise Exception`/`RuntimeError`; all errors subclass `HarnessError` with a non-empty `remediation`.

---

## 6. Test Lane Policy

**Smoke lane only, always.** Never run `make test-full`, `make coverage`, `make ci`, or
`make test-nightly` without the user's explicit permission in the current message.

- NIGHTLY rows are tracked requirements, not smoke-lane work: **7.4** (`-m slow`) and **7.8** (`-m timing`) are deferred to a user-authorized nightly run.
- Baseline snapshots must be reviewed in the PR that introduces them — never commit an unreviewed baseline.
- Smoke validation per task: `pytest tests/tui/<file>.py -q` plus, at the end, `scripts/test_lane.ps1 smoke`.

---

## 7. Platform Notes

Unlike P5/P6, the TUI is pure Python and **runs on native Windows** (Textual `run_test()` is headless,
no display, no AF_UNIX needed for pilot tests). So:

- Panel/bridge/throttle tests run live in the smoke lane on this host.
- The §7.D live protocol (steps 1–8) needs a real daemon + IPC + `StubWorkload`; step 2/8 need
  streaming. 7.12 must decide whether the live run is possible on Windows (best case) or needs WSL2
  (as P5/P6 did) — design the `.sh` as the real protocol and the `.ps1` as either a live runner **or**
  a platform-limit twin, whichever the 7.12 brief confirms.
- `reports/throttle_trace.json` (step 2) is produced by the throttle measurement.

---

## 8. Anti-Loop Guardrails (carried from P6)

1. One task per dispatch; verify the commit before the next dispatch.
2. Bounded waits only; a fake for a drain loop MUST have a terminating condition.
3. Never poll to wait — `runSubagent` is blocking; when it returns, the invocation is over.
4. On empty return: verify state once, record, re-dispatch the remainder in a fresh single-task session.
5. On two consecutive failures of the same task: escalate to the user (do not loop).

---

## 9. Documented Checklist (task -> file -> done)

- [ ] 7.1 `tui/app.py`, `tui/app.tcss` + `tests/tui/test_layout.py` (+`__init__.py`)
- [ ] 7.2 `tui/panels/repo_manager.py` + `tests/tui/test_repo_manager.py`
- [ ] 7.3 `tui/panels/execution_canvas.py` + `tests/tui/test_execution_canvas.py`
- [ ] 7.4 `tui/scrollback.py` + `tests/tui/test_scrollback.py`
- [ ] 7.5 `tui/panels/model_registry.py` + `tests/tui/test_model_registry.py`
- [ ] 7.6 `tui/panels/critic_bar.py` + `tests/tui/test_critic_bar.py`
- [ ] 7.7 `tui/bridge.py` + `tests/tui/test_bridge.py`
- [ ] 7.8 `tui/throttle.py` + `tests/tui/test_throttle.py`
- [ ] 7.9 `tui/bindings.py` + `tests/tui/test_bindings.py`
- [ ] 7.10 `tui/render.py` + `tests/tui/test_render.py`
- [ ] 7.11 `cli.py` (root) + `tests/tui/test_cli.py`
- [ ] 7.12 `scripts/verify_phase_07.sh` (+ `.ps1`)
- [ ] 7.13 `tests/support/metrics_replay.py` + `tests/support/test_metrics_replay.py`
- [ ] 7.D reviewer sign-off -> `reports/phase_07_acceptance.json` ACCEPTED