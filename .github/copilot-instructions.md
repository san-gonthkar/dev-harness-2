# Project Guidelines — Dev Harness

This workspace builds the **Hermes TUI Dev Harness**: a Python 3.11+ terminal application that orchestrates an agentic SDLC pipeline (Groomer → Architect → Developer → Tester → Critic) against LLM providers, with a four-panel Textual TUI, a rate-limit broker, and SQLite-backed checkpoints.

The authoritative plan is `requirements/Dev_Harness_Implementation_Plan_V11_Final.md`. It defines the task numbering (`0.1`, `1.3`, …), the phase gates, the coverage contract, and the acceptance protocols. **Every code change must trace to a task in that document.** The plan's §2 (test strategy), §3 (phase evaluation), and per-phase `X.C`/`X.D` sections are binding; do not restate them here.

## Code Style

- **Python 3.11+**, typed with `mypy --strict` across all of `src/`. No `Any` leaks; no untyped function signatures.
- **Ruff** for linting and formatting. Zero findings on `make lint`.
- **Pydantic v2** for all state models. Use `model_validate` / `model_dump(mode="json")`; never hand-roll serialization.
- **Enums are canonical.** `ExecutionState`, `CriticCommand`, `ChunkStatus`, `PanelId`, `EventType`, `ProviderId`, `FailureClass` live in `contracts/enums.py`. Never write a state string literal (`"PAUSED"`) outside `enums.py` — the literal-ban test greps for it.
- **Errors are typed.** Every exception subclasses `HarnessError` and carries a non-empty `remediation`. Never `raise Exception` or `raise RuntimeError` bare.
- **No `time.sleep()` in tests.** Use the frozen clock fixture (`tests/support/clock.py`) or event-driven waits.

## Architecture

- **Hexagonal layout** under `src/dev_harness/`: `contracts/` (schemas, enums, errors), `storage/` (SQLite), `vcs/` (git), `ipc/` (AF_UNIX transport), `providers/` (LLM adapters), `broker/` (rate limiting), `engine/` (daemon + pipeline), `core/` (critic/interrupt), `tui/` (Textual), `observability/`, `recovery/`.
- **Dependency direction is one-way**: `contracts/` is depended on by everything and depends on nothing. `tui/` and `engine/` never import each other — they communicate only over IPC events.
- **All provider calls go through the broker client.** No adapter is ever called directly from engine code (the AST guard test enforces this).
- **All state flows through `HarnessState`.** Secrets never enter state; they live in env → keyring → `0600` file and are masked in `__repr__`.

## Build and Test

- Install: `pip install -e .` · Full gate: `make ci` (ruff → mypy → pytest → coverage gate) · Lint: `make lint` · Typecheck: `make typecheck` · Tests: `make test`
- **Every test declares exactly one marker** (`unit`, `property`, `contract`, `integration`, `negative`, `timing`, `slow`, `e2e`). Unmarked tests fail collection.
- **Branch coverage is mandatory** (`--cov-branch`). Per-package thresholds are in `.coveragerc`; the ratchet blocks any PR that drops a package by >0.5pp.
- **`# pragma: no cover` requires a trailing justification comment** on the same line. Bare pragmas fail CI.
- **No live network calls in tests.** Use `MockLLM` / `FakeProviderServer` from `tests/support/`; the `--disable-socket` suite must pass.

## Conventions

- **Task handoff**: branch `task/{id}-{slug}`, commit trailer `Task-Id: {id}`, PR body pastes the validation command output. Enforced by `scripts/check_task_trailer.py`.
- **Phase completion** requires three things, all green: every task's validation row, the phase coverage contract, and the phase acceptance protocol with a signed report. Tasks-done is not phase-done.
- **Mutation gates** apply to `storage/`, `vcs/`, `broker/`, `core/`, and the four engine concurrency modules (`dag.py`, `worker_pool.py`, `worker_workspace.py`, `integrator.py`). A surviving mutant in a named focus set fails the gate.
- **Acceptance protocols are scripted**, not remembered: `scripts/verify_phase_{NN}.sh`. The implementing worker may not sign its own phase report.
- **Documentation lives in `requirements/` and `docs/`.** Do not create new top-level doc folders.

## Progress Monitoring & Git Workflow

- **`memory.md`** is the single shared memory file for agents (append-only history). **`progress.md`** is the human-readable monitoring dashboard — the orchestrator mirrors every state change into it (phase table, current task progress, quality gates, recent activity, git/push log) so the user can track progress remotely.
- **Commit at regular meaningful intervals**: after each task's validation passes (or a small batch of 2–3 tasks), after each quality gate, and after each phase closes. Use the `Task-Id: {id}` trailer. Never commit broken state or stray artifacts (`*.log`, `coverage.json`, temp files).
- **Push to `origin`/`main` after every commit** (or batch). The GitHub repository is set up; if a push fails, record it in `progress.md` and continue — never block the loop on push.
- **The orchestrator** (`phase-orchestrator` agent + skill) is the keeper of phase state, the progress dashboard, and the commit/push cadence. Implementing agents commit their own task work; the orchestrator pushes.