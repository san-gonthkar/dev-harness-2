---
name: phase-orchestrator
description: 'Autonomous phase orchestration for the Dev Harness. Use when running the V11 plan end-to-end: loop through every phase (P0-P10) autonomously, dispatch implementing agents, enforce review and coverage gates, recover from failures, and close phases until the entire plan is complete. Every invocation passes and updates the shared memory.md file, mirrors progress into progress.md, and commits/pushes at meaningful intervals.'
user-invocable: true
---

# Phase Orchestrator

## When to Use

- Running the V11 plan end-to-end, autonomously, until all phases are complete
- Starting, advancing, or closing a phase
- Dispatching a task to an implementing agent
- Recovering from a failed task or gate
- Any handoff between agents

## The Memory Protocol

`memory.md` (at the workspace root) is the **single shared memory file** for this development. Every agent invocation reads it first and updates it on completion.

- **Read before starting**: load `memory.md`, understand where the development stands, what the last completed task was, and what the current phase needs.
- **Update on completion**: append the task result, decisions, and next steps so the next agent starts with full context.
- **Never delete history**: append-only. The file is the exchange medium — losing history loses context.

## The Progress Dashboard Protocol

`progress.md` (at the workspace root) is the **human-readable monitoring view**. The user watches this file to track progress; it must never lag behind `memory.md`.

- **Update after every meaningful interval**: task done, gate result, phase transition, commit/push.
- **Mirror, do not duplicate**: `memory.md` holds the full history; `progress.md` holds the current state — phase table, current phase task progress, quality gates, recent activity, git/push log.
- **Keep it current**: every time you append to `memory.md`, update the corresponding section of `progress.md` in the same turn.
- **Never delete history**: append to the git/push log and recent-activity sections; do not rewrite them.

## The Commit & Push Protocol

The GitHub repository is set up. Commit and push at **regular meaningful intervals** so the user can monitor progress remotely.

- **Commit at every meaningful interval** — at minimum:
  1. After each task's validation passes (or a small batch of 2–3 tasks).
  2. After each quality gate passes (coverage contract, acceptance protocol).
  3. After each phase closes.
  4. After any `memory.md` / `progress.md` update that changes phase state.
- **Commit message convention**: `Task-Id: {id}` trailer (enforced by `scripts/check_task_trailer.py`). Use a short summary line describing what changed, e.g. `P4.1 token bucket + tests` or `Close P4: coverage contract met, acceptance ACCEPTED`.
- **Push after every commit** (or batch of commits) — the remote is `origin`; the branch is `main`. If the push fails (no remote, auth), record it in `progress.md` and continue; do not block the loop on push.
- **Never commit broken state**: only commit when the touched package's tests pass and mypy/ruff are clean for the changed files. A phase-close commit additionally requires the coverage contract and acceptance protocol.
- **Stray artifacts**: do not commit logs (`*.log`), coverage JSON, or temp files. Add them to `.gitignore` if they recur.

## Session Management

A **session** is one agent invocation with a finite context window. The V11 plan is 149 tasks — far more than any single session can hold, so sessions must be rotated before they fill. The orchestrator is the **keeper of session state**; `memory.md` holds the session registry.

### Session Registry

Every session — orchestrator or subagent — registers on start and closes on completion:

| Session | Agent | Phase/Task | Context (est.) | Status |
| :--- | :--- | :--- | :--- | :--- |
| S1 | orchestrator | P0 open | ~5% | active |
| S2 | python-developer | 0.1 | ~25% | closed |

### Rotation Rules

1. **Orchestrator session** — rotate after every 3–5 dispatched tasks, or immediately on context pressure (truncation, slow responses, dropped context). Before rotating: checkpoint `memory.md` (phase state, task log, current task, next step), then start a fresh session that reads `memory.md` and continues from the `Current Status` block. No history is lost — `memory.md` is the history.
2. **Subagent session** — every task dispatch is a fresh session. The implementing agent must:
   - Register the session in `memory.md` on start (session ID, task, context estimate).
   - Write a **mid-task checkpoint** at ~70% context: what is done, what remains, the exact next step.
   - Close the session on completion with the result.
3. **Handoff brief** — when starting a new session, pass a compact brief: current phase, current task, last checkpoint, next step. Do **not** paste the full conversation history; `memory.md` is the history.
4. **Never lose work** — a session that fills mid-task must checkpoint and return control. The orchestrator dispatches a fresh session with the checkpoint as the starting point.
5. **Session budget** — include a session budget in each task brief (estimated tool calls / context). If the agent exceeds it, it checkpoints and returns rather than pushing on.

## Autonomous Execution Loop

Run **continuously** until the plan is complete. Do not stop after one phase.

```
while any phase is not closed:
    phase = next phase whose prerequisites are green and state is not closed
    if phase is blocked:
        recover (see Failure Recovery) or escalate to the user
        continue
    open phase (in progress)          # update memory.md + progress.md
    for each task in phase (in dependency order):
        dispatch to implementing agent with a task brief + memory.md
        verify the task's validation command passes
        if failed: recover (see Failure Recovery)
        update memory.md + progress.md (task state, test counts)
        commit + push at meaningful intervals (see Commit & Push Protocol)
    verify phase gate (acceptance protocol + reviewer-agent sign-off)
    close phase in memory.md + progress.md
    commit + push the phase close
    advance Current Status to the next phase
```

### Failure Recovery

When a task fails validation or a gate is unmet, recover autonomously before escalating:

1. **Retry (1st failure)** — re-dispatch the same task with a corrective brief: the failure output, the exact validation command, and what must change. Log the retry in `memory.md`.
2. **Re-dispatch (2nd failure)** — dispatch to a fresh invocation of the implementing agent with the full failure history, so the task is re-attempted with complete context. Log it.
3. **Escalate (3rd failure)** — set the phase to `blocked` in `memory.md`, record the failure history and the decision needed, and stop to ask the user. Do not loop forever.

### Quality Gates (non-negotiable, every phase)

Before closing any phase, all three must hold:
1. **Every task's validation row green** — the exact command from `X.B` exits 0 with the exact success criteria.
2. **Coverage contract met** — the phase's `X.C` line/branch/mutation targets are met; the coverage gate exits 0.
3. **Acceptance protocol signed** — `scripts/verify_phase_{NN}.sh` runs end-to-end and `reviewer-agent` signs the report. Phases 5/8/10 additionally require a human signature.

If any gate fails, treat it as a failure and recover (retry → re-dispatch → escalate). Never advance past a phase whose gate is not verified.

### Completion Condition

The plan is complete when **all 11 phases are `closed`** in `memory.md`. Only then do you stop and report the final summary.

## Phase Tracking

The orchestrator is the **keeper of phase state**. `memory.md` holds the authoritative phase-status table.

### Phase Lifecycle

| State | Meaning | Set when |
| :--- | :--- | :--- |
| `not started` | Prerequisites not green; nothing dispatched | Phase created |
| `in progress` | Tasks being dispatched; at least one task started | First task dispatched |
| `blocked` | A task failed validation or a gate is unmet; needs a decision | Escalation after 3 failures |
| `closed` | All tasks green + coverage contract met + acceptance protocol signed | Phase gate verified |

### Tracking Rules

1. **Open a phase** — set it to `in progress` when you dispatch the first task. Mirror to `progress.md`.
2. **Update per task** — after each task, update the phase's task log (`pending` → `done`/`failed`) and the `Current Status` block. Mirror to `progress.md` (task state + test counts).
3. **Mark blocked** — only after 3 failed attempts (retry + re-dispatch + escalate). Record the failure history and the decision needed. Mirror to `progress.md`.
4. **Close a phase** — only when all three phase-completion conditions are met:
   - Every task's validation row green.
   - The phase Coverage Contract met.
   - The phase Acceptance Protocol executed end-to-end with a signed report.
   Then set the phase to `closed`, record the acceptance report path + `signed_by` + commit, and advance `Current Status` to the next phase. Mirror to `progress.md` and commit + push.

## Orchestration Loop

### 1. Analyse the Phase

1. Read the phase's `X.A` execution tasks, `X.B` validation matrix, `X.C` coverage contract, and `X.D` acceptance protocol from the V11 plan.
2. Read `memory.md` to confirm prerequisites are green and identify the starting task.
3. Determine the lane (A/B/C/D) and the implementing agent.

### 2. Identify the Implementing Agent

| Phase | Lane | Implementing agent |
| :--- | :--- | :--- |
| P0–P4, P6–P9 | A/B/C/D | `python-developer` |
| P5, P8 | D | `python-developer` (human sign-off required) |
| P10 | All | `release-agent` |
| Any phase gate | — | `reviewer-agent` (independent sign-off) |

### 3. Write the Task Brief

For each task, produce a brief containing:
- Task ID and deliverable (from `X.A`)
- The exact validation command and success criteria (from `X.B`)
- The coverage contract and mutation focus set (from `X.C`)
- The skills the agent must load
- The current `memory.md` context

### 4. Invoke the Agent

Pass the brief **and** the `memory.md` content. The agent must:
1. Read `memory.md` first.
2. Load the required skills.
3. Implement the task.
4. Update `memory.md` with the result.

### 5. Verify the Phase Gate

After all tasks in a phase are green:
1. Run the phase's acceptance protocol (`scripts/verify_phase_{NN}.sh`).
2. Invoke `reviewer-agent` for the independent sign-off (the implementing worker may not sign its own phase).
3. For phases 5, 8, 10: obtain a human signature.
4. Update `memory.md` with the phase verdict.

### 6. Close the Phase

1. Confirm all three phase-completion conditions are met (tasks green + coverage contract + signed acceptance protocol).
2. Set the phase to `closed` in `memory.md`.
3. Record the acceptance report path, `signed_by`, and commit.
4. Advance the `Current Status` block to the next phase.
5. Do not start the next phase until its prerequisites are green.

### 7. Continue the Loop

Move to the next phase whose prerequisites are green. Do not stop until all 11 phases are closed.

## Output Format

Report back with:
- Phase analysed and current task
- Phase state (not started / in progress / blocked / closed)
- Implementing agent chosen and why
- Task brief delivered
- Agent result and validation status
- Phase gate verdict
- Overall progress (X of 11 phases closed)
- Session state (current session ID, context estimate, rotation due?)
- `memory.md` updated (what was appended, phase state change)
- `progress.md` updated (what was mirrored)
- Git state (commit hash, pushed to origin? yes/no)