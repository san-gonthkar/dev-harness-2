---
name: phase-orchestrator
description: 'Autonomous phase orchestration for the Dev Harness. Use when running the V11 plan end-to-end: loop through every phase (P0-P10) autonomously, dispatch implementing agents, enforce review and coverage gates, recover from failures, and close phases until the entire plan is complete. Every invocation resumes from the last known step, updates memory.md, mirrors progress into progress.md, and commits/pushes at meaningful intervals.'
user-invocable: true
---

# Phase Orchestrator

## When to Use

- Running the V11 plan end-to-end, autonomously, until all phases are complete
- Starting, advancing, or closing a phase
- Dispatching a task to an implementing agent
- Recovering from a failed task or gate

## State Files

| File | Role | Rule |
| :--- | :--- | :--- |
| `memory.md` | Authoritative shared memory: phase table, task log, `Current Status`, session registry | Read **only `## Current Status` + `## Phase Tracking`** (~60 lines) — never the whole file; append-only; **commit once per phase close**, not per task |
| `memory_archive.md` | Closed-phase history (P0–P6 session records) | Read only when a closed phase's detail is needed; never edit |
| `progress.md` | Human monitoring dashboard; the `AUTO:DASHBOARD` region is **machine-owned** | **Never hand-edit** — run `python scripts/gen_dashboard.py` (reads `memory.md` + git). `--check` fails on a stale dashboard. Commit with the phase close |

## Context Economy (the #1 speed lever)

Every subagent is a fresh session with zero shared memory. If the brief does not carry the context, the subagent spends its whole budget re-reading the codebase — this is the observed "reading files over and over" failure. Rules:

1. **Paste exact API signatures into the brief.** For every module the task touches, include the class/function signatures, field names, and enum values the subagent needs. Target: the subagent reads **0–2 files**, not 15.
2. **Quote the plan row, do not cite it.** Run `python scripts/extract_phase_plan.py --phase N --task X.Y` and paste the JSON into the brief. Never write "read the plan" — it is 961 lines.
3. **Name the pattern file, do not list a reading list.** One "mirror `scripts/verify_phase_05.sh`" beats a 15-file list.
4. **Cap the read sweep.** A brief must state: *"read at most 3 files before writing your first line."*
5. **Orchestrator reads only `## Current Status`** on resume — not all of `memory.md`.

## Process Tooling (use these, do not do it by hand)

| Tool | Purpose | When |
| :--- | :--- | :--- |
| `scripts/extract_phase_plan.py --phase N --task X.Y` | Paste-ready plan row JSON | Before writing any brief |
| `scripts/check_brief.py --file brief.json` | Validate a brief has the required fields | Before every dispatch |
| `scripts/dispatch_log.py start/end` | Record dispatch telemetry | Every dispatch |
| `scripts/dispatch_log.py phase-report --phase N` | Per-phase budget rollup | Phase close |
| `scripts/failure_policy.py --class <name>` | The prescribed response for a failure | On any failure |
| `scripts/check_traceability.py --phase N` | Task -> file -> test mapping | Phase close |
| `scripts/dispatch_plan.py --phase N --done ...` | Ready set + parallel-safe grouping | Phase open / replan |
| `scripts/check_state_commit.py --range A..B` | Detect bookkeeping-churn commits | Phase close |

**Dispatch loop (mechanical):** extract the plan row -> write the brief -> `check_brief` -> `dispatch_log start` -> dispatch -> verify the commit -> `dispatch_log end --outcome ...` -> on failure, `failure_policy --class ...` and follow it.

## Commit & Push Protocol

- Commit **code** after each task (or batch of 2-3), each gate, and each phase close.
- Message: short summary + `Task-Id: {id}` trailer (enforced by `scripts/check_task_trailer.py`).
- **State-file commits are batched once per phase** (see State-File Batching). Never a standalone `memory`/`progress`-only commit per task.
- Push to `origin`/`main` after every commit/batch. If push fails, record it in `progress.md` and continue — never block the loop on push.
- Never commit broken state or stray artifacts (`*.log`, `coverage.json`, temp files).

## State-File Batching (anti-churn)

`memory.md` and `progress.md` were the two most-edited files in the repo (71 edits / 109 commits), and 11 commits existed only to "fix stale dashboard rows". Both costs are now designed out:

- **`progress.md` is generated** — run `python scripts/gen_dashboard.py` to rewrite the `AUTO:DASHBOARD` region from `memory.md` + git. Never hand-edit inside the markers. Run `--check` in a gate to fail a stale dashboard.
- **Append to `memory.md` freely** (it is the append-only log), but **commit it once per phase close**, not per task. Same for the regenerated `progress.md`.
- Per-task `memory.md` appends are scratch state; they do not each need a commit. The phase-close commit carries the durable record.
- **Never** create a commit whose only purpose is to fix a stale dashboard row — regenerate instead.

## Resume Protocol

On every invocation: **locate -> verify -> record -> continue.** Never restart.

1. **Locate** — read **only `## Current Status` + `## Phase Tracking`** in `memory.md` (~60 lines), then `git log --oneline -5` and `git status --short`. Do NOT read the whole `memory.md` or `progress.md`.
2. **Cross-check** — if sources disagree, trust `memory.md` and reconcile the others.
3. **Verify** — touched package's tests pass (smoke lane), `git status` clean, prereqs green.
4. **Record** — append a session entry to `memory.md`; update `progress.md`'s Resume Point.

- **Closed phases** — never re-open, never re-verify, never re-dispatch.
- **Done tasks** — never re-dispatch.
- **In-progress phase** — start at the first task not `done`.
- **Mid-task checkpoint** — resume from its exact next step.
- **Blocked phase** — recover (retry -> re-dispatch -> escalate) or ask the user; do not skip past it.
- **Broken resume point** (tests fail, inconsistent state) — treat as a failure and recover; do not silently skip ahead.

## Session Management

A session is one invocation with a finite context window. The orchestrator keeps session state; `memory.md` holds the registry.

- **Orchestrator session** — rotate after 3-5 dispatches or on context pressure. Checkpoint `memory.md` first; the fresh session resumes from `Current Status`.
- **Subagent session** — checkpoint at ~70% context (done / remaining / exact next step); close with the result.
- **Handoff brief** — pass phase, task, last checkpoint, next step. Never paste conversation history; `memory.md` is the history.
- **Budget** — every brief carries a tool-call and wall-clock budget; exceeding it means checkpoint and return, not push on.

## Test Lane Policy (absolute)

- The smoke lane is the only lane the orchestrator or its subagents run: `make test` / `scripts/test_lane.ps1 smoke`.
- The full suite (`make test-full`, `make coverage`, `make ci`, `make test-nightly`, mutation, nightly e2e) runs **only** on the user's explicit instruction in the current message.
- A phase gate or coverage contract is a *requirement to track*, not permission: record it as `pending - requires user-authorized full-suite run` and ask the user.
- Every dispatch brief MUST state the smoke-only rule. A subagent that ran the full suite unauthorized is a lane-policy breach — log it and do not count that run as evidence.
- Permission is per-run; never carry it forward.
- Exempt (no permission needed): `make test`, `make test-smoke`, `scripts/test_lane.* smoke`, `pytest tests/<path> -q` for the package in work.
- A `PreToolUse` hook (`.github/hooks/test-lane-guard.json`) prompts the user on full-suite commands.

## Loop Guardrail (hard stop)

Abort immediately on any signal:
1. **Identical repeat** — the same tool, same input, same output **twice**. A third repeat is forbidden. (Polling `git log`/`git status` to wait is the classic self-loop.)
2. The same tool-input validation error repeats twice consecutively (or 3 times in one turn window).
3. 8+ consecutive meta-only actions (listing, fetching context, reading logs, permission retries) with no output-producing action.
4. A full cycle with no dispatch, no `memory.md` append, no `progress.md` mirror, and no commit attempt.

**Never poll to wait** — no tool makes another agent progress; if you are waiting, you are looping.

**This is enforced, not just documented**: a `PreToolUse` hook (`.github/hooks/stall-guard.json` -> `scripts/check_stall.py`) counts consecutive identical `(tool_name, tool_input)` calls and returns `ask` on the 3rd and `deny` on the 5th. A normal edit/test/edit cycle resets the counter. If the guard fires, it is a real loop — stop, produce output, or change the call.

On abort: stop the loop; report to the user (trigger, last 5 actions, why no output, exact next safe step); record it in `memory.md` + `progress.md`. This outranks the autonomous loop and failure-retry logic.

### A subagent return is terminal

`runSubagent` is blocking: when it returns, the invocation is over — nothing is still running.

- **Final report** → verify output, continue.
- **Partial/mid-task fragment, or no report** → the session ended early. Do NOT wait or poll. Verify state **once**, record done vs. missing, and **re-dispatch the remainder in a fresh session** (a fresh `runSubagent` with a resume brief).

### Subagent liveness

- `runSubagent` (the `agent` tool) is **blocking** — it returns when the subagent finishes, so there is nothing to poll. Liveness belongs to the subagent (its brief mandates a heartbeat).
- **After it returns**: confirm real output (`git log`, `git status --short`). A report with no commit and no file change is a failed dispatch.
- **Abort/rollback** if: no commit and no file write; the subagent reported a loop; or it exceeded its budget without checkpointing.
- **No dispatch tool in the toolset**: do NOT retry, do NOT hunt substitutes, do NOT implement the tasks yourself. Record `blocked - no dispatch tool`, report in one turn, stop. The `agent` alias is granted only to a **root** session — a subagent never receives it; re-run as the root agent.
- **Every brief MUST include**: a session budget, a heartbeat contract (`memory.md` append or commit every <=30 min), and a stop-and-report rule.

### Output-before-long-work

1. Write a one-line status (resume point + next action) to `memory.md` within the first ~10 tool calls.
2. Append a progress line every <=30 tool calls or <=10 minutes.
3. Read at most ~15 files before producing an artifact.

## Autonomous Loop

On every invocation: locate -> verify -> record.

```
while any phase is not closed:
    phase = next phase whose prereqs are green and state != closed
    if blocked: recover or escalate; continue
    open phase (in progress)                  # memory.md + progress.md
    for each batch of 3-5 tasks (dependency order, from resume point):
        dispatch with brief + memory.md; smoke-lane validation
        if failed: recover
        update memory.md (regenerate progress.md); commit code
    verify phase gate (acceptance protocol + reviewer-agent)
    close phase; commit code + state files; push; advance Current Status
```

## Failure Recovery

1. **Retry** — re-dispatch the same task with a corrective brief (failure output, exact command, what must change). Log it.
2. **Re-dispatch** — fresh invocation of the implementing agent with the full failure history. Log it.
3. **Escalate** — set the phase `blocked`, record the history and the decision needed, ask the user. Never loop forever.

## Quality Gates (all three, every phase)

1. Every task's validation row green (`X.B`: exact command + success criteria).
2. Coverage contract met (`X.C` line/branch/mutation; coverage gate exits 0).
3. Acceptance protocol signed (`scripts/verify_phase_{NN}.sh` end-to-end + `reviewer-agent`; phases 5/8/10 also need a human signature).

Gates 1-3 need the full suite — gate them `pending - requires user-authorized full-suite run` (see Test Lane Policy) instead of running it. Never advance past a phase whose gate is not verified.

**Complete** when all 11 phases are `closed` in `memory.md`; then stop and report.

## Phase Tracking

| State | Meaning | Set when |
| :--- | :--- | :--- |
| `not started` | Prereqs not green; nothing dispatched | Phase created |
| `in progress` | At least one task started | First task dispatched |
| `blocked` | A task failed or a gate is unmet; needs a decision | Escalation after 3 failures |
| `closed` | Tasks green + coverage contract + signed acceptance protocol | Phase gate verified |

1. **Open** — set `in progress` on first dispatch; mirror to `progress.md`.
2. **Per task** — update the task log (`pending` -> `done`/`failed`) and `Current Status`; mirror test counts. Regenerate `progress.md` with `scripts/gen_dashboard.py`; do **not** commit it per task (batch at phase close).
3. **Blocked** — only after 3 failed attempts; record history + decision needed.
4. **Close** — only when all three gates hold; record the acceptance report path + `signed_by` + commit, set `closed`, advance `Current Status`, mirror, commit + push.
5. **Archive on close** — move the closed phase's session records from `memory.md` to `memory_archive.md` so the live file stays under ~250 lines. Keep in `memory.md`: the phase table row, `Current Status`, and a one-line close summary. Never archive an open phase.

## Dispatch Procedure

### 1. Analyse the phase
Read the resume point first. Then the phase's `X.A` (tasks), `X.B` (validation), `X.C` (coverage), `X.D` (acceptance) from the V11 plan. Determine the lane (A/B/C/D).

### 2. Choose the implementing agent

| Phase | Lane | Agent |
| :--- | :--- | :--- |
| P0-P4, P6-P9 | A/B/C/D | `python-developer` |
| P5, P8 | D | `python-developer` (human sign-off) |
| P10 | All | `release-agent` |
| Any phase gate | - | `reviewer-agent` (independent) |

### 3. Write the brief

**Batch size is set by context, not by count.** A batch of 3–5 tasks is fine **only if the brief carries the inline context** (see Context Economy). A batch whose brief says "read these 15 files" fails — observed 3× in P6 (empty returns). When in doubt, dispatch **one task** with a fully self-contained brief; a single task with inline signatures beats a batch with a reading list.

**Mandatory brief template** (every dispatch):

```
TASK: <one task ID + deliverable>            # or a small batch, each with its own row
PLAN ROW (pasted, not cited):
  X.A: <deliverable + targeted files>
  X.B: <exact command> -> <success criteria>
  X.C: <line/branch/mutation numbers>
API SURFACE (pasted signatures the task needs):
  <class/function signatures, field names, enum values>
PATTERN FILE: <one file to mirror, e.g. scripts/verify_phase_05.sh>
HARD RULES: bounded waits only; no time.sleep() in tests; smoke lane only;
  do not investigate mutmut; read at most 3 files before writing your first line.
BUDGET: <= 25 tool calls, <= 25 minutes.
OUTPUT CONTRACT: write -> validate -> commit (Task-Id trailer) -> push -> append memory.md.
STOP-AND-REPORT: on a loop or budget exhaustion, commit what you have and report.
```

Also include: skills to load; current `memory.md` context (paste `## Current Status`); heartbeat contract; **smoke-lane-only rule**; **"cover branches in the task's own test file — never a trailing `*gaps*` file"**.

### 4. Invoke
Dispatch with the `agent` tool (`runSubagent`) — blocking; verify output (commit/file change) after it returns, not just the report. Pass the brief and `memory.md`. If unavailable, follow the no-dispatch-tool rule.

### 5. Verify the phase gate
Run `scripts/verify_phase_{NN}.sh`; dispatch `reviewer-agent` for sign-off (the implementer may not sign its own phase); obtain the human signature for phases 5/8/10; record the verdict.

### 6. Close and continue
Confirm all three gates; set `closed`; record report path + `signed_by` + commit; advance `Current Status`. Do not start a phase whose prereqs are not green.

## Report

resume point (phase/task, last commit, verified OK) · current phase/task · phase state · implementing agent + why · brief delivered · agent result + validation status · phase gate verdict · progress (X of 11 closed) · session state (ID, context est., rotation due?) · `memory.md` updated (what) · `progress.md` updated (what) · git state (hash, pushed?) · `karpathy-understanding-first` contract.
