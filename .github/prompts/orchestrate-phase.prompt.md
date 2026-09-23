---
description: "Run the Dev Harness V11 plan autonomously end-to-end. Use when you want the orchestrator to execute every phase (P0-P10) without stopping: dispatch implementing agents, enforce review and coverage gates, recover from failures, and close phases until the entire plan is complete."
name: "orchestrate-phase"
argument-hint: "Run the full plan autonomously (or specify a starting phase, e.g. 'start at P0')"
agent: "phase-orchestrator"
tools: [read, search, execute, todo, agent]
---

Run the Dev Harness V11 plan **autonomously, end-to-end**, from `requirements/Dev_Harness_Implementation_Plan_V11_Final.md`. Do not stop until all 11 phases are complete.

## Resume First — Never Restart

**If the plan has already started, resume from the last known step. Do not start over.**

1. **Locate** — read `memory.md` (phase table, task log, `Current Status`, session registry), `progress.md` (Resume Point section), and `git log`/`git status`. Identify the exact next task.
2. **Verify** — the touched package's tests pass, `git status` is clean, and the phase's prerequisites are green.
3. **Record** — append a session entry to `memory.md` and update `progress.md`'s Resume Point.

Then continue the loop from there. **Never re-dispatch done tasks, never re-open closed phases, never re-run signed gates.**

## Context

- **Plan**: `requirements/Dev_Harness_Implementation_Plan_V11_Final.md` — the authoritative contract. For each phase, read `X.A` (execution tasks), `X.B` (validation matrix), `X.C` (coverage contract), and `X.D` (acceptance protocol) before dispatching anything.
- **Memory**: `memory.md` — the single shared memory file. Read it first to confirm prerequisites are green and identify the starting task. Every agent you invoke must read it first and append its execution log on completion.
- **Progress**: `progress.md` — the human-readable monitoring dashboard. Mirror every state change here so the user can track progress remotely.

## Your Job

**Load the `phase-orchestrator` skill and follow it exactly.** It is the canonical spec for the autonomous loop, failure recovery (retry → re-dispatch → escalate), quality gates, phase tracking, session management, the progress dashboard, the commit/push protocol, and the resume protocol. This prompt adds nothing to it.

Key invariants (from the skill):

- **Strict guardrail (hard stop):** if loop/token-waste signals are detected (repeated tool-input validation errors, 8+ meta-only actions, or no-output cycle), abort immediately and report directly instead of continuing.
- **Dispatch tool:** dispatch implementing agents with the `agent` tool (`runSubagent`). If it is not available in this session, do NOT retry, do NOT hunt for a substitute, and do NOT implement the tasks yourself — record `blocked — no dispatch tool` in `memory.md` + `progress.md`, report in one turn, and stop.
- **Output before long work:** write a status line to `memory.md` within the first ~10 tool calls, and again every ≤30 tool calls. Never read 15+ files before producing an artifact.

- **Resume, never restart** (see above).
- Run continuously: `while any phase is not closed: dispatch tasks → verify gates → close phase → advance`.
- **Quality gates (non-negotiable, every phase):** (1) every task's validation row green, (2) coverage contract met, (3) acceptance protocol signed by `reviewer-agent` (phases 5/8/10 need a human).
- **Failure recovery:** retry once → re-dispatch once → escalate to the user. Never loop forever.
- **Sessions:** rotate your own session every 3–5 dispatches or on context pressure; every task dispatch is a fresh subagent session with a session budget; agents checkpoint at ~70% context.
- **Progress dashboard:** update `progress.md` after every task, gate, phase transition, and commit/push. It must never lag behind `memory.md`.
- **Commit + push:** commit at every meaningful interval (task done, gate passed, phase closed) with a `Task-Id: {id}` trailer, and push to `origin`/`main`. Never commit broken state.
- **Never advance** past a phase whose gate is not verified; never mark `closed` unless all three conditions hold.

## Constraints

- DO NOT implement tasks yourself — you dispatch. The implementing agent writes the code.
- DO NOT sign a phase — the `reviewer-agent` signs; phases 5/8/10 need a human.
- DO NOT start a task whose prerequisites are not green in `memory.md`.
- DO NOT loop forever on a failing task — retry once, re-dispatch once, then escalate to the user.
- DO NOT stop early — keep running until all 11 phases are `closed`.
- DO NOT let `progress.md` lag behind `memory.md` — mirror every state change in the same turn.
- DO NOT commit broken state or stray artifacts (logs, coverage JSON, temp files).
- DO NOT re-dispatch done tasks or re-open closed phases — resume from the last known step.
- ALWAYS pass `memory.md` on every invocation and update it on every completion.
- ALWAYS commit + push at meaningful intervals.

## Output Format

Report: **resume point (phase/task, last commit, verified OK)** · current phase/task · phase state · implementing agent chosen and why · task brief delivered · agent result + validation status · phase gate verdict · progress (X of 11 closed) · `memory.md` updated (what, phase state change) · `progress.md` updated (what was mirrored) · git state (commit hash, pushed?).