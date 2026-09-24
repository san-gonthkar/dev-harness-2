# Phase 9 Implementation Plan — Error Handling, Recovery & Edge Cases

**Plan:** `requirements/Dev_Harness_Implementation_Plan_V11_Final.md` §9.A-§9.D (authoritative).
**Gate:** `scripts/verify_phase_09.sh` -> `reports/phase_09_acceptance.json` `ACCEPTED`.
**Lane:** A/B/C/D (no human sign-off required). **Est:** 29.0h, 11 tasks (9.1-9.11).
**Prereqs:** Phases 1-8. P8 is reviewer-ACCEPTED (human sign-off pending); P9 does not depend on the P8 signature.

## 1. Objective

Every failure mode named in the audit becomes observable, recoverable, and covered by a negative test.

## 2. Dispatch Order (topological, risk-first)

| # | Task | Deliverable | Prereq | Validation |
| :-- | :-- | :-- | :-- | :-- |
| D1 | 9.4 | `storage/integrity.py` (corrupt-checkpoint detection) | 1.4 | `pytest tests/storage/test_integrity.py -q` |
| D2 | 9.2 | `recovery/reclaim.py` (stale artifact reclamation) | 2.3, 1.8, 1.10 | `pytest tests/recovery/test_reclaim.py -q` |
| D3 | 9.1 | `recovery/session_recovery.py` (crash recovery) | 8.18, 1.12, 8.21b | `pytest tests/recovery/test_session_recovery.py -q -m e2e` |
| D4 | 9.3 | `broker/fallback.py` (provider fallback chain) | 4.9, 3.3 | `pytest tests/broker/test_fallback.py -q` |
| D5 | 9.6 | `vcs/edge_cases.py` (git edge cases) | 1.12, 1.10 | `pytest tests/vcs/test_edge_cases.py -q` |
| D6 | 9.8 | `storage/errors.py` (resource exhaustion) | 1.1, 0.4 | `pytest tests/storage/test_errors.py -q` |
| D7 | 9.9 | `engine/overflow.py` (context-overflow recovery) | 8.16, 3.3 | `pytest tests/engine/test_overflow.py -q` |
| D8 | 9.5 | `tui/responsive.py` (terminal degradation) | 7.1 | `pytest tests/tui/test_responsive.py -q` |
| D9 | 9.7 | `tui/render.py` + `observability/redact.py` (canvas redaction) | 0.14, 7.10 | `pytest tests/tui/test_canvas_redaction.py -q` |
| D10 | 9.10 | `docs/rollback.md` (phase rollback procedures) | — | manual rehearsal (REL) |
| D11 | 9.11 | `scripts/verify_phase_09.sh` + `scripts/chaos_drill.py` | 0.18 | `python scripts/chaos_drill.py --all` |
| GATE | 9.D | reviewer-agent acceptance review | D1-D11 | `reports/phase_09_acceptance.json` |

**Chunking decision:** ONE task per dispatch (evidence: multi-task batches failed 3x in P6; single tasks succeeded 21x in P7/P8). Each brief = 1 task, <=25 tool calls, bounded waits, commit+push+memory per task.

## 3. Coverage Contract (§9.C)

| Scope | Line | Branch | Mutation | Enforced by |
| :-- | :-- | :-- | :-- | :-- |
| `recovery/` | 90% | 85% | — | `.coveragerc` + `coverage_gate.py` |
| `storage/integrity.py` | 100% | 95% | >=85% | Plan 9.C — verify per-module |
| **Error reachability** | — | — | — | `python scripts/coverage_gate.py --errors` — every `HarnessError` subclass reachable by >=1 `negative` test |

The error-reachability gate is the phase's most important metric.

## 4. Invariants to Respect

- `tui/` and `engine/` never import each other — IPC only.
- All provider calls go through the broker client; the AST guard forbids direct adapter calls from engine code.
- Enums in `contracts/enums.py` are canonical — no state string literals.
- Every exception subclasses `HarnessError` with a non-empty `remediation`.
- One marker per test; unmarked tests fail collection.
- No `time.sleep()` in tests — frozen clock (`tests/support/clock.py`).
- No live network calls — `MockLLM` / `FakeProviderServer`.
- Branch coverage via `--cov-branch`; every `# pragma: no cover` needs a same-line justification.
- No new dependencies without a ponytail-minimalism justification.

## 5. Test Lane Policy

**Smoke lane only, always.** Never run `make test-full`, `make coverage`, `make ci`, or `make test-nightly` without the user's explicit permission in the current message. NIGHTLY rows (9.1 `-m e2e`, 9.11 chaos drill) are tracked requirements, deferred to a user-authorized nightly run.

## 6. Platform Notes

- P9 is mostly pure Python. `recovery/reclaim.py` deals with sockets/locks/worktrees — AF_UNIX is POSIX-only, so socket reclamation may need a platform-limit twin (plan R2).
- `chaos_drill.py` injects faults; `kill9-*` faults need POSIX signals (SIGKILL) — likely WSL2/POSIX for those steps.
- `storage/integrity.py`, `vcs/edge_cases.py`, `engine/overflow.py`, `tui/responsive.py` are pure Python and run on native Windows.

## 7. Anti-Loop Guardrails (carried from P6/P7/P8)

1. One task per dispatch; verify the commit before the next dispatch.
2. Bounded waits only; a fake for a drain loop MUST have a terminating condition.
3. Never poll to wait — `runSubagent` is blocking; when it returns, the invocation is over.
4. On empty return: verify state once, record, re-dispatch the remainder in a fresh single-task session.
5. On two consecutive failures of the same task: escalate to the user (do not loop).

## 8. Documented Checklist (task -> file -> done)

- [ ] 9.1 `recovery/session_recovery.py` + `tests/recovery/test_session_recovery.py`
- [ ] 9.2 `recovery/reclaim.py` + `tests/recovery/test_reclaim.py`
- [ ] 9.3 `broker/fallback.py` + `tests/broker/test_fallback.py`
- [ ] 9.4 `storage/integrity.py` + `tests/storage/test_integrity.py`
- [ ] 9.5 `tui/responsive.py` + `tests/tui/test_responsive.py`
- [ ] 9.6 `vcs/edge_cases.py` + `tests/vcs/test_edge_cases.py`
- [ ] 9.7 `tui/render.py` + `observability/redact.py` + `tests/tui/test_canvas_redaction.py`
- [ ] 9.8 `storage/errors.py` + `tests/storage/test_errors.py`
- [ ] 9.9 `engine/overflow.py` + `tests/engine/test_overflow.py`
- [ ] 9.10 `docs/rollback.md`
- [ ] 9.11 `scripts/verify_phase_09.sh` + `scripts/chaos_drill.py`
- [ ] 9.D reviewer sign-off -> `reports/phase_09_acceptance.json` ACCEPTED
