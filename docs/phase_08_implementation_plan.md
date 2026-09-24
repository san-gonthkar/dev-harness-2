# Phase 8 Implementation Plan — SDLC Pipeline Engine & Parallel Worker Pool

**Plan:** `requirements/Dev_Harness_Implementation_Plan_V11_Final.md` §8.A-§8.D (authoritative).
**Gate:** `scripts/verify_phase_08.sh` -> `reports/phase_08_acceptance.json` `ACCEPTED` with a **human** `signed_by`.
**Lane:** D. **Est:** 69.5h, 21 tasks (8.1-8.21b). **Prereqs:** 1.5, 1.10, 5.5, 6.2, 3.1, 0.5, 7.6 — all green (P0-P7 closed).

## 1. Objective

Assemble the Groomer -> Architect -> Developer -> Tester -> Critic graph with bounded retries, HITL gates, context budgeting, and parallel chunk execution over isolated worktrees.

## 2. Dispatch Order (topological, risk-first)

| # | Task | Deliverable | Prereq | Validation |
| :-- | :-- | :-- | :-- | :-- |
| D1 | 8.1 | `engine/personas/*.md` (5 roles) | 0.5 | `pytest tests/engine/test_personas.py -q` |
| D2 | 8.2 | `engine/personas/validators.py` | 8.1 | `pytest tests/engine/test_persona_validators.py -q` |
| D3 | 8.3 | `engine/state.py` (channels/reducers) | 0.5, 1.5 | `pytest tests/engine/test_state_reducers.py -q` |
| D4 | 8.4 | `engine/nodes/groomer.py` | 8.2, 8.3 | `pytest tests/engine/test_groomer.py -q` |
| D5 | 8.5 | `engine/nodes/architect.py` | 8.4 | `pytest tests/engine/test_architect.py -q` |
| D6 | 8.6 | `engine/dag.py` (RISK) | 8.5 | `pytest tests/engine/test_dag.py -q` |
| D7 | 8.7 | `engine/worker_pool.py` (RISK) | 8.6, 1.10 | `pytest tests/engine/test_worker_pool.py -q` |
| D8 | 8.8 | `engine/worker_workspace.py` (RISK) | 8.7, 1.10 | `pytest tests/engine/test_worker_workspace.py -q` |
| D9 | 8.9 | `engine/integrator.py` (RISK) | 8.8 | `pytest tests/engine/test_integrator.py -q` |
| D10 | 8.10 | `engine/nodes/developer.py` | 8.8, 6.4 | `pytest tests/engine/test_developer.py -q` |
| D11 | 8.11 | `engine/nodes/tester.py` | 8.10 | `pytest tests/engine/test_tester.py -q` |
| D12 | 8.12 | `engine/testing/differential.py` | 8.11 | `pytest tests/engine/test_differential.py -q` |
| D13 | 8.13 | `engine/routing.py` | 8.12 | `pytest tests/engine/test_routing.py -q` |
| D14 | 8.14 | `engine/nodes/critic.py` | 8.3, 6.2 | `pytest tests/engine/test_critic_scope.py -q` |
| D15 | 8.15 | `engine/hitl.py` | 8.14, 7.6 | `pytest tests/engine/test_hitl.py -q` |
| D16 | 8.16 | `engine/context.py` | 3.8, 3.2 | `pytest tests/engine/test_context.py -q` |
| D17 | 8.17 | `engine/classifier.py` | 8.13 | `pytest tests/engine/test_classifier.py -q` |
| D18 | 8.18 | `engine/pipeline.py` | 8.4-8.17 | `pytest tests/engine/test_sdlc_pipeline.py -q` |
| D19 | 8.20 | `engine/cli.py` | 8.18 | `pytest tests/engine/test_cli.py -q` |
| D20 | 8.21a | `storage/migrations/0002_worktree_state.sql` + `storage/checkpoint_binding.py` | 1.11, 1.2 | `pytest tests/storage/test_migration_0002.py -q` |
| D21 | 8.21b | worktree state capture+restore | 8.21a, 8.8 | `pytest tests/engine/test_worker_checkpoint.py -q` |
| D22 | 8.19 | `scripts/verify_phase_08.sh` (+ `.ps1` twin) | 0.18 | `bash scripts/verify_phase_08.sh` |
| GATE | 8.D | reviewer-agent + **human** sign-off | D1-D22 | `reports/phase_08_acceptance.json` |

**Chunking decision:** ONE task per dispatch (evidence: multi-task batches failed 3x in P6; single tasks succeeded 8x in P7). Each brief = 1 task, <=25 tool calls, bounded waits, commit+push+memory per task.

## 3. Coverage Contract (§8.C)

| Scope | Line | Branch | Mutation | Enforced by |
| :-- | :-- | :-- | :-- | :-- |
| `engine/` (nodes, pipeline) | 88% | 80% | — | `.coveragerc [coverage:report:engine]` + `coverage_gate.py` |
| `engine/dag.py`, `worker_pool.py`, `worker_workspace.py`, `integrator.py` | 95% | 90% | >=80% | Plan 8.C — verify per-module; mutation gate |
| `engine/routing.py`, `classifier.py` | 95% | 90% | — | Plan 8.C — verify per-module |

Mutation focus set: `engine/dag.py`, `worker_pool.py`, `worker_workspace.py`, `integrator.py` (>=80%, 0 survivors in readiness check + worktree binding).

## 4. Invariants to Respect

- `tui/` and `engine/` never import each other — IPC only.
- All provider calls go through the broker client; the AST guard test forbids direct adapter calls from engine code.
- All state flows through `HarnessState`; Pydantic v2 models only.
- Enums in `contracts/enums.py` are canonical — no state string literals.
- Every exception subclasses `HarnessError` with a non-empty `remediation`.
- One marker per test (`unit`/`integration`/`negative`/`e2e`); unmarked tests fail collection.
- No `time.sleep()` in tests — frozen clock (`tests/support/clock.py`).
- No live network calls — `MockLLM` / `FakeProviderServer`.
- Branch coverage via `--cov-branch`; every `# pragma: no cover` needs a same-line justification.
- No new dependencies without a ponytail-minimalism justification.

## 5. Test Lane Policy

**Smoke lane only, always.** Never run `make test-full`, `make coverage`, `make ci`, or `make test-nightly` without the user's explicit permission in the current message. Smoke validation per task: `pytest tests/engine/<file>.py -q` plus, at the end, `scripts/test_lane.ps1 smoke`.

## 6. Platform Notes

- P8 is pure Python + git worktrees; the graph runs with `MockLLM` (no network). Worktree operations use the real `git` binary (available on Windows).
- The §8.D live protocol (steps 1-13) uses `python -m dev_harness.engine.cli ... --mock`; it does NOT need AF_UNIX. It MAY run on native Windows — 8.19 must decide live-on-Windows vs WSL2 twin.
- `reports/parallel_trace.json` (step 3) is produced by the worker-pool trace.

## 7. Anti-Loop Guardrails (carried from P6/P7)

1. One task per dispatch; verify the commit before the next dispatch.
2. Bounded waits only; a fake for a drain loop MUST have a terminating condition.
3. Never poll to wait — `runSubagent` is blocking; when it returns, the invocation is over.
4. On empty return: verify state once, record, re-dispatch the remainder in a fresh single-task session.
5. On two consecutive failures of the same task: escalate to the user (do not loop).

## 8. Documented Checklist (task -> file -> done)

- [ ] 8.1 `engine/personas/*.md` + `tests/engine/test_personas.py`
- [ ] 8.2 `engine/personas/validators.py` + `tests/engine/test_persona_validators.py`
- [ ] 8.3 `engine/state.py` + `tests/engine/test_state_reducers.py`
- [ ] 8.4 `engine/nodes/groomer.py` + `tests/engine/test_groomer.py`
- [ ] 8.5 `engine/nodes/architect.py` + `tests/engine/test_architect.py`
- [ ] 8.6 `engine/dag.py` + `tests/engine/test_dag.py`
- [ ] 8.7 `engine/worker_pool.py` + `tests/engine/test_worker_pool.py`
- [ ] 8.8 `engine/worker_workspace.py` + `tests/engine/test_worker_workspace.py`
- [ ] 8.9 `engine/integrator.py` + `tests/engine/test_integrator.py`
- [ ] 8.10 `engine/nodes/developer.py` + `tests/engine/test_developer.py`
- [ ] 8.11 `engine/nodes/tester.py` + `tests/engine/test_tester.py`
- [ ] 8.12 `engine/testing/differential.py` + `tests/engine/test_differential.py`
- [ ] 8.13 `engine/routing.py` + `tests/engine/test_routing.py`
- [ ] 8.14 `engine/nodes/critic.py` + `tests/engine/test_critic_scope.py`
- [ ] 8.15 `engine/hitl.py` + `tests/engine/test_hitl.py`
- [ ] 8.16 `engine/context.py` + `tests/engine/test_context.py`
- [ ] 8.17 `engine/classifier.py` + `tests/engine/test_classifier.py`
- [ ] 8.18 `engine/pipeline.py` + `tests/engine/test_sdlc_pipeline.py`
- [ ] 8.19 `scripts/verify_phase_08.sh` (+ `.ps1`)
- [ ] 8.20 `engine/cli.py` + `tests/engine/test_cli.py`
- [ ] 8.21a `storage/migrations/0002_worktree_state.sql` + `tests/storage/test_migration_0002.py`
- [ ] 8.21b worktree state capture + `tests/engine/test_worker_checkpoint.py`
- [ ] 8.D reviewer + human sign-off -> `reports/phase_08_acceptance.json` ACCEPTED
