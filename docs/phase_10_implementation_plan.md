# Phase 10 Implementation Plan — Final Verification, Traceability & Release

**Plan:** `requirements/Dev_Harness_Implementation_Plan_V11_Final.md` §10.A-§10.D (authoritative).
**Gate:** `scripts/verify_phase_10.sh` -> `reports/phase_10_acceptance.json` `ACCEPTED` with a **human** `signed_by`.
**Lane:** All — **human sign-off required**. **Est:** 27.0h, 9 tasks (10.1-10.9).
**Prereqs:** Phases 0-9 green. P9 CLOSED; P8 reviewer-ACCEPTED (human sign-off pending).

## 1. Objective

Prove the assembled system against the TDD and gate release on measurable thresholds.

## 2. Dispatch Order

| # | Task | Deliverable | Prereq | Validation |
| :-- | :-- | :-- | :-- | :-- |
| D1 | 10.1 | `tests/e2e/test_full_sdlc.py` (requirement -> tested chunk, mid-run PAUSE/RESUME) | 9.* | `pytest tests/e2e/test_full_sdlc.py -q --timeout=900` (NIGHTLY) |
| D2 | 10.2 | `tests/e2e/test_parallel_sdlc.py` (3-wide pool through merge) | 10.1 | `pytest tests/e2e/test_parallel_sdlc.py -q --timeout=1200` (NIGHTLY) |
| D3 | 10.6 | `tests/e2e/test_schema_conformance.py` | 10.1 | `pytest tests/e2e/test_schema_conformance.py -q` |
| D4 | 10.7 | `scripts/generate_traceability.py` + `docs/traceability.md` | 10.1 | `python scripts/generate_traceability.py --check` |
| D5 | 10.5 | `.github/workflows/ci.yml` + `nightly.yml` | 0.2, 0.16 | `time make ci` (PR < 10 min) |
| D6 | 10.8 | `README.md` + `docs/runbook.md` + packaging | 10.1, 9.10 | manual dry run |
| D7 | 10.3 | `tests/e2e/test_concurrent_soak.py` (30-min soak) | 10.1 | `pytest ... --timeout=2400` (NIGHTLY) |
| D8 | 10.4 | `tests/e2e/test_local_profile.py` + `profiles/local.toml` (live Ollama) | 10.1, 3.7 | `pytest ... -q -m slow` (NIGHTLY) |
| D9 | 10.9 | `scripts/verify_phase_10.sh` + `docs/release_checklist.md` | 0.18 | `bash scripts/verify_phase_10.sh` |
| GATE | 10.D | reviewer + **human** sign-off + tag `v1.0.0` | D1-D9 | `reports/phase_10_acceptance.json` |

**Chunking decision:** ONE task per dispatch. Each brief = 1 task, <=25 tool calls, bounded waits, commit+push+memory per task.

## 3. Coverage Contract (§10.C)

| Scope | Line | Branch | Notes |
| :-- | :-- | :-- | :-- |
| Whole repo | 91% | 84% | Weighted aggregate of §2.3, verified at release |
| Mutation focus set | per ledger | per ledger | Re-run at release on the merged trunk |
| **E2E graph-node coverage** | — | — | Every node in the compiled graph executed >=1x across 10.1 + 10.2, asserted programmatically from the run trace |

The graph-node coverage assertion exists because a pipeline can hit 91% line coverage while an entire node never runs in any E2E test.

## 4. Invariants to Respect

- All prior-phase invariants hold (hexagonal layering, canonical enums, HarnessError taxonomy, one marker per test, no time.sleep, no live network except 10.4).
- 10.4 is the ONLY un-stubbed reality check (live local Ollama); 10.1-10.3 use MockLLM for determinism.

## 5. Test Lane Policy

**Smoke lane only, always.** Never run `make test-full`, `make coverage`, `make ci`, or `make test-nightly` without the user's explicit permission in the current message. NIGHTLY rows (10.1-10.4) are tracked requirements, deferred to a user-authorized nightly run.

## 6. Platform Notes

- 10.1/10.2/10.6/10.7/10.8/10.9 are pure Python + git; they run on native Windows.
- 10.3 (30-min soak) and 10.4 (live Ollama) are NIGHTLY and need a long-running host / a local model.
- 10.5 (CI workflows) is YAML; the PR budget (< 10 min) is measured on the standard runner.
- 10.D step 5 (cold-machine install) needs a fresh VM; step 9 tags `v1.0.0`.

## 7. Anti-Loop Guardrails (carried from P6-P9)

1. One task per dispatch; verify the commit before the next dispatch.
2. Bounded waits only.
3. Never poll to wait — `runSubagent` is blocking.
4. On empty return: verify state once, record, re-dispatch the remainder.
5. On two consecutive failures of the same task: escalate to the user.

## 8. Documented Checklist (task -> file -> done)

- [ ] 10.1 `tests/e2e/test_full_sdlc.py`
- [ ] 10.2 `tests/e2e/test_parallel_sdlc.py`
- [ ] 10.3 `tests/e2e/test_concurrent_soak.py`
- [ ] 10.4 `tests/e2e/test_local_profile.py` + `profiles/local.toml`
- [ ] 10.5 `.github/workflows/ci.yml` + `nightly.yml`
- [ ] 10.6 `tests/e2e/test_schema_conformance.py`
- [ ] 10.7 `scripts/generate_traceability.py` + `docs/traceability.md`
- [ ] 10.8 `README.md` + `docs/runbook.md`
- [ ] 10.9 `scripts/verify_phase_10.sh` + `docs/release_checklist.md`
- [ ] 10.D reviewer + human sign-off + tag `v1.0.0` -> `reports/phase_10_acceptance.json` ACCEPTED
