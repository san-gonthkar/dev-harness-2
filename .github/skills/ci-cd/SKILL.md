---
name: ci-cd
description: 'CI/CD tiering and release gates per the V11 plan. Use when working with GitHub Actions workflows, the PR/NIGHTLY/REL tiering, the coverage ratchet, mutation gates, or the release checklist. Covers task 10.5 and the phase acceptance protocol runner.'
user-invocable: true
---

# CI/CD (V11 Phase 10)

## When to Use

- Working with `.github/workflows/` (ci.yml, nightly.yml) or the PR/NIGHTLY/REL tiering
- Running the release gate

## Tiering (task 10.5)

| Tier | Blocks | Runs |
| :--- | :--- | :--- |
| PR | merge | unit, property, contract, integration, negative |
| NIGHTLY | phase gate | timing, slow, e2e, mutation |
| REL | release | traceability, runbook, release checklist |

PR suite < 10 min on the standard runner; timing-sensitive tests are never PR-blocking.

## Local Lane Rule (absolute)

CI tiers describe what *CI* runs. Locally, agents run the **smoke lane only** (`make test` / `scripts/test_lane.ps1 smoke`) — the PR tier. Full suite, coverage, nightly, and mutation runs happen only on the user's explicit instruction in their current message. A phase gate or coverage contract is a *requirement to track*, not permission: record `pending - requires user-authorized full-suite run` and ask.

## Gates

- **Coverage ratchet**: `scripts/coverage_gate.py` — no package may drop >0.5pp; bare `# pragma: no cover` fails.
- **Mutation gates**: `scripts/mutation_gate.py` — re-run at release on the merged trunk.
- **Marker enforcement**: unmarked tests fail collection.
- **Error reachability**: `scripts/coverage_gate.py --errors` — every `HarnessError` subclass reachable by >=1 `negative` test.

## Phase Acceptance

`scripts/verify_phase_{NN}.sh` emits `reports/phase_{NN}_acceptance.json` (`verdict: ACCEPTED`). The implementer may not sign its own phase; phases 5, 8, 10 need a human signature.

## Release Gate (task 10.9)

`bash scripts/verify_phase_10.sh` — all phase acceptance reports present and `ACCEPTED`; checklist complete.
