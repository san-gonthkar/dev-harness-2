# Project Guidelines — Dev Harness

**Hermes TUI Dev Harness**: Python 3.11+ terminal app orchestrating an agentic SDLC pipeline (Groomer -> Architect -> Developer -> Tester -> Critic) against LLM providers, with a four-panel Textual TUI, a rate-limit broker, and SQLite checkpoints.

Plan: `requirements/Dev_Harness_Implementation_Plan_V11_Final.md` — the authoritative contract (task numbering, phase gates, coverage contract, acceptance protocols). **Every change traces to a task in it.** Its §2 (test strategy), §3 (phase evaluation), and per-phase `X.C`/`X.D` are binding; do not restate them.

## Code Style

- Python 3.11+, `mypy --strict` across `src/` — no `Any` leaks, no untyped signatures.
- Ruff for lint + format; zero findings on `make lint`.
- Pydantic v2 for all state models (`model_validate` / `model_dump(mode="json")`); never hand-roll serialization.
- Enums in `contracts/enums.py` are canonical. Never write a state string literal (`"PAUSED"`) outside it — a literal-ban test greps for it.
- Every exception subclasses `HarnessError` with a non-empty `remediation`; never bare `Exception`/`RuntimeError`.
- No `time.sleep()` in tests — use the frozen clock (`tests/support/clock.py`).

## Architecture

- Hexagonal: `contracts/` (schemas, enums, errors) is depended on by everything and depends on nothing; then `storage/`, `vcs/`, `ipc/`, `providers/`, `broker/`, `engine/`, `core/`, `tui/`, `observability/`, `recovery/`.
- `tui/` and `engine/` never import each other — they communicate only over IPC events.
- All provider calls go through the broker client; the AST guard test forbids direct adapter calls from engine code.
- All state flows through `HarnessState`. Secrets never enter state: env -> keyring -> `0600` file, masked in `__repr__`.

## Build and Test

- Install `pip install -e .` · Lint `make lint` · Typecheck `make typecheck`.

### Test Lane Policy (MANDATORY)

- **Always run the smoke lane. Never run the full suite unless the user explicitly asks in their current message.** Full suite = `make test-full`, `make coverage`, `make ci`, `make test-nightly`, or any `pytest tests ...` without the smoke exclusions.
- Smoke lane: `make test` / `scripts/test_lane.ps1 smoke` / `scripts/test_lane.sh smoke`. PR tier — excludes NIGHTLY markers (`timing`/`slow`/`e2e`), coverage-padding `*gaps*` files, spikes, meta-tests; `--timeout=120`; ~432 tests in <20s.
- A phase gate or coverage contract is a *requirement to track*, **not** permission — ask the user. Never write "run the full suite" / "make ci" in a plan or brief without permission.
- If smoke passes, that is sufficient; record what is unverified and move on. If smoke fails, fix it and re-run smoke.
- A lane with no output is a defect: `pytest.ini` sets `timeout = 600` (smoke tightens to 120). Stop and report; do not wait.
- One marker per test (`unit`, `property`, `contract`, `integration`, `negative`, `timing`, `slow`, `e2e`); unmarked tests fail collection.
- Branch coverage is mandatory (`--cov-branch`); per-package thresholds in `.coveragerc`; the ratchet blocks a >0.5pp drop.
- `# pragma: no cover` needs a trailing justification comment on the same line.
- No live network calls — use `MockLLM` / `FakeProviderServer` from `tests/support/`.
- A `PreToolUse` hook (`.github/hooks/test-lane-guard.json`) prompts the user on full-suite commands.

## Conventions

- Task handoff: branch `task/{id}-{slug}`, `Task-Id: {id}` trailer, PR body pastes validation output (`scripts/check_task_trailer.py`).
- Phase completion needs all three green: every validation row, the coverage contract, the signed acceptance protocol. Tasks-done is not phase-done.
- Mutation gates cover `storage/`, `vcs/`, `broker/`, `core/`, and engine `dag.py`, `worker_pool.py`, `worker_workspace.py`, `integrator.py`; a surviving focus-set mutant fails the gate.
- Acceptance protocols are scripted, not remembered: `scripts/verify_phase_{NN}.sh`. The implementer may not sign its own phase.
- Docs live in `requirements/` and `docs/`; do not add new top-level doc folders.

## State Files & Git

- `memory.md` — append-only shared agent memory (phase table, task log, `Current Status`, session registry). `progress.md` — human dashboard mirroring every state change.
- **Resume, never restart**: locate the last known step (`memory.md` + `progress.md` + git), verify, record, continue. Never re-dispatch done tasks or re-open closed phases.
- Commit after each task (or 2-3), each gate, each phase close, with the `Task-Id` trailer. Push to `origin`/`main` after every commit; if push fails, log it in `progress.md` and continue.
- Never commit broken state or stray artifacts (`*.log`, `coverage.json`, temp files).
