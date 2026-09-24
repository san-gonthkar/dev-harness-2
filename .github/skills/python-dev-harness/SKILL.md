---
name: python-dev-harness
description: 'Implement and validate Dev Harness Python tasks from the V11 plan. Use when implementing a task, writing typed Pydantic v2 models, adding marked pytest tests, fixing mypy strict or ruff failures, or running the phase validation commands. Covers the hexagonal layout, error taxonomy, and coverage contract.'
argument-hint: 'Task ID or module to implement, e.g. "task 1.4 SqliteSaver write path"'
user-invocable: true
---

# Python Dev Harness Implementation

## When to Use

- Implementing a task from the V11 plan; writing or fixing code under `src/dev_harness/`
- Adding tests that must satisfy the validation matrix and marker rules
- Resolving `mypy --strict` / `ruff` failures, running an acceptance protocol or mutation gate

## Procedure

### 1. Locate the task contract
In `requirements/Dev_Harness_Implementation_Plan_V11_Final.md`: the phase's `X.A` row (ID, deliverable, files, prereqs), the matching `X.B` row (exact command, success criteria, tier), and `X.C` (coverage contract, mutation focus set).

### 2. Implement
1. Hexagonal: `contracts/` depends on nothing; `tui/` and `engine/` never import each other.
2. Pydantic v2 for state models (`model_validate` / `model_dump(mode="json")`).
3. Canonical enums from `contracts/enums.py` — never state string literals.
4. `HarnessError` subclasses with non-empty `remediation` — never bare exceptions.
5. All provider calls route through the broker client.

### 3. Test
1. Write tests in the file the validation row names.
2. Exactly one marker per test (`unit`, `property`, `contract`, `integration`, `negative`, `timing`, `slow`, `e2e`).
3. Cover the defect class with a `negative` test before the happy path.
4. Frozen clock (`tests/support/clock.py`) and `tmp_workspace` — never `time.sleep()`.
5. `MockLLM` / `FakeProviderServer` — never live network calls.
6. **Cover every branch in the task's own test file — never a trailing `*gaps*` file.** Check the touched package's branch % as you go (`pytest tests/<pkg> -q --cov=dev_harness.<pkg> --cov-branch`) and add the missing case to the module's test file. `*gaps*`/coverage-padding files are a closed-workaround: they are excluded from the smoke lane and defer the cost to a phase gate where it is more expensive to fix.
7. **Use `parametrize` for tables** — the 16-cell transition table, error mappings, and status-code maps are data, not 16 near-identical functions. One parametrized test replaces a whole family of copy-paste tests and is what the reviewer expects for a tabular contract.

### 4. Validate
**Smoke lane only** — `make test` (or `scripts/test_lane.ps1 smoke`) plus targeted `pytest tests/<package> -q`. Never `make test-full` / `make coverage` / `make ci` / `make test-nightly` unless the user explicitly asks in their current message; a `NIGHTLY`-tier row is not run (record it `pending - requires user-authorized full-suite run`). Then `make lint typecheck`.

Mutation-scoped packages (`storage/`, `vcs/`, `broker/`, `core/`, `engine/{dag,worker_pool,worker_workspace,integrator}.py`): `python scripts/mutation_gate.py --packages <package>`; kill any surviving focus-set mutant.

### 5. Hand off
Branch `task/{id}-{slug}`; commit trailer `Task-Id: {id}`; PR body pastes the validation output.

**Commit discipline (anti-churn):** commit **code** per task. Append to `memory.md` freely, but do **not** create a standalone `memory`/`progress`-only commit per task — the orchestrator batches state-file commits once per phase close. A commit whose only content is `memory.md` or `progress.md` is churn and is forbidden.

## References

Plan `requirements/Dev_Harness_Implementation_Plan_V11_Final.md` · test rig `tests/support/` · gates `Makefile`, `mypy.ini`, `.coveragerc`.
