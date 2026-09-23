---
name: python-dev-harness
description: 'Implement and validate Dev Harness Python tasks from the V11 plan. Use when implementing a task, writing typed Pydantic v2 models, adding marked pytest tests, fixing mypy strict or ruff failures, or running the phase validation commands. Covers the hexagonal layout, error taxonomy, and coverage contract.'
argument-hint: 'Task ID or module to implement, e.g. "task 1.4 SqliteSaver write path"'
user-invocable: true
---

# Python Dev Harness Implementation

## When to Use

- Implementing a task from `requirements/Dev_Harness_Implementation_Plan_V11_Final.md`
- Writing or fixing Python under `src/dev_harness/`
- Adding tests that must satisfy the plan's validation matrix and marker rules
- Resolving `mypy --strict` or `ruff` failures
- Running a phase acceptance protocol or mutation gate

## Procedure

### 1. Locate the Task Contract

1. Open `requirements/Dev_Harness_Implementation_Plan_V11_Final.md`.
2. Find the task row in the phase's `X.A` table: task ID, deliverable, targeted files, prereqs, Est.
3. Find the matching row in the phase's `X.B` validation matrix: exact test command, success criteria, tier.
4. Read the phase's `X.C` coverage contract for required test classes and mutation focus set.

### 2. Implement

1. Follow the hexagonal layout: `contracts/` depends on nothing; `tui/` and `engine/` never import each other.
2. Use Pydantic v2 (`model_validate` / `model_dump(mode="json")`) for all state models.
3. Use canonical enums from `contracts/enums.py` — never state string literals.
4. Raise `HarnessError` subclasses with non-empty `remediation` — never bare exceptions.
5. Route all provider calls through the broker client.

### 3. Test

1. Write tests in the file the validation row names.
2. Declare exactly one marker per test: `unit`, `property`, `contract`, `integration`, `negative`, `timing`, `slow`, `e2e`.
3. Cover the defect class with a `negative` test before the happy path is done.
4. Use `tests/support/clock.py` (frozen clock) and `tests/support/workspace.py` (`tmp_workspace`) — never `time.sleep()`.
5. Use `MockLLM` / `FakeProviderServer` from `tests/support/` — never live network calls.

### 4. Validate

**Test lane rule (absolute).** Run the **smoke lane** for validation — `make test` (or `scripts/test_lane.ps1 smoke`), plus a targeted `pytest tests/<your-package> -q` for the package you touched. **NEVER** run the full suite (`make test-full`, `make coverage`, `make ci`, `make test-nightly`) unless the user explicitly asks for it in their current message. If the validation row's tier is `NIGHTLY`, do not run it — record it as `pending — requires user-authorized full-suite run` and report it.

Then:

```bash
make lint typecheck
```

For mutation-scoped packages (`storage/`, `vcs/`, `broker/`, `core/`, `engine/dag.py`, `engine/worker_pool.py`, `engine/worker_workspace.py`, `engine/integrator.py`):

```bash
python scripts/mutation_gate.py --packages <package>
```

Kill any surviving mutant in the phase's named focus set.

### 5. Hand Off

- Branch: `task/{id}-{slug}`
- Commit trailer: `Task-Id: {id}`
- PR body: paste the validation command output

## References

- Plan: `requirements/Dev_Harness_Implementation_Plan_V11_Final.md`
- Test rig: `tests/support/` (clock, workspace, mock_llm, fake_provider)
- Quality gates: `Makefile`, `mypy.ini`, `.coveragerc`