---
description: "Run the Dev Harness V11 plan autonomously end-to-end. Use when you want the orchestrator to execute every phase (P0-P10) without stopping: dispatch implementing agents, enforce review and coverage gates, recover from failures, and close phases until the entire plan is complete."
name: "orchestrate-phase"
argument-hint: "Run the full plan autonomously (or specify a starting phase, e.g. 'start at P0')"
agent: "phase-orchestrator"
tools: [read, search, execute, todo, agent]
---

Run the Dev Harness V11 plan **autonomously, end-to-end** from `requirements/Dev_Harness_Implementation_Plan_V11_Final.md`. Do not stop until all 11 phases are complete.

**Load the `phase-orchestrator` skill and follow it exactly** — it is the canonical spec (resume, loop, failure recovery, gates, sessions, commit/push, report). This prompt adds nothing to it.

## Resume First — Never Restart

1. **Locate** — `memory.md` (phase table, task log, `Current Status`, session registry), `progress.md` (Resume Point), `git log`/`git status`.
2. **Verify** — touched package's tests pass (smoke lane), `git status` clean, prereqs green.
3. **Record** — append a session entry to `memory.md`; update `progress.md`.

Never re-dispatch done tasks, never re-open closed phases, never re-run signed gates.

## Non-Negotiable Invariants

- **Resume, never restart.**
- **Dispatch; never implement.** `reviewer-agent` signs phases; 5/8/10 need a human.
- **Quality gates** (all three): every validation row green · coverage contract met · acceptance protocol signed.
- **Failure recovery**: retry -> re-dispatch -> escalate. Never loop forever.
- **Test lane**: smoke only (`make test` / `scripts/test_lane.ps1 smoke`). The full suite (`make test-full`, `make coverage`, `make ci`, `make test-nightly`) runs only on the user's explicit instruction in the current message — a phase gate is a *requirement to track*, not permission. Put this in every subagent brief.
- **Dispatch tool**: use the `agent` tool. If absent, do NOT retry or substitute — record `blocked - no dispatch tool`, report, stop.
- **Guardrail**: abort on repeated tool-input errors, 8+ meta-only actions, or a no-output cycle; report and log.
- **Output before long work**: status line to `memory.md` within ~10 tool calls, then every <=30 tool calls.
- **Mirror `progress.md`** every state change; **commit + push** with a `Task-Id: {id}` trailer.

## Report

resume point (phase/task, last commit, verified OK) · current phase/task · phase state · implementing agent + why · brief delivered · agent result + validation status · gate verdict · progress (X of 11 closed) · session state · `memory.md`/`progress.md` updates · git state (hash, pushed?).
