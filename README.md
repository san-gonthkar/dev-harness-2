# Dev Harness

**Hermes TUI Dev Harness** — a terminal application that orchestrates an agentic
software-development pipeline (Groomer → Architect → Developer → Tester → Critic)
against LLM providers, with a four-panel Textual TUI, a host-scoped rate-limit
broker, and SQLite checkpoints.

- **Python** 3.11+ · **Textual** TUI · **LangGraph** pipeline · **Pydantic v2** state · **SQLite (WAL)** checkpoints · **AF_UNIX** IPC
- Version `0.1.0`

## What it is

The harness runs a multi-persona SDLC pipeline over a git workspace. Each persona
is a graph node; the engine daemon executes the graph, the broker meters every
provider call (rate limits, token buckets, cost governor, kill-switch), and the
TUI renders live state across four panels. All durable state lives in
`<workspace>/.dev-harness/state.db`; all provider traffic routes through the
broker — never a direct adapter call.

## Install

```bash
pip install .
```

For development (adds pytest, ruff, mypy, mutmut, hypothesis):

```bash
pip install -e ".[dev]"     # or: make install
```

This installs four console scripts:

| Command | Purpose |
| :--- | :--- |
| `dev-harness` | Launch the four-panel TUI (or `--self-check`) |
| `dev-harness-broker` | Run the host-scoped rate-limit broker daemon |
| `dev-harness-broker-cli` | Broker load generator / manual reserve / metrics |
| `dev-harness-engine` | Ensure the workspace engine daemon is running |

## Quickstart

```bash
# 1. Configure (from the repo root)
cp dev-harness.example.toml dev-harness.toml

# 2. Start the broker (host-scoped; run in the background)
dev-harness-broker &

# 3. Point at a git workspace and launch the TUI
dev-harness --workspace /path/to/your/repo
```

The engine daemon autostarts on first connect. To verify health without opening
the TUI:

```bash
dev-harness --workspace /path/to/your/repo --self-check
```

This prints the derived socket and DB paths plus broker and engine health.

> **Full step-by-step operator walkthrough:** [`docs/runbook.md`](docs/runbook.md).

## Architecture summary

Hexagonal layout under `src/dev_harness/`. `contracts/` (schemas, enums, errors)
is depended on by everything and depends on nothing; the remaining packages layer
on top:

```
contracts/  →  storage/  vcs/  ipc/  providers/  broker/  engine/  core/  tui/  observability/  recovery/
```

- **`tui/` and `engine/` never import each other** — they communicate only over IPC events.
- **All provider calls go through the broker client**; an AST guard test forbids direct adapter calls from engine code.
- **All state flows through `HarnessState`** (Pydantic v2). Secrets never enter state: env → keyring → `0600` file, masked in `__repr__`.
- **Enums in `contracts/enums.py` are canonical** — no state string literals elsewhere.

## Development

| Task | Command |
| :--- | :--- |
| Install (editable + dev) | `make install` |
| Lint + format check | `make lint` |
| Typecheck (`mypy --strict`) | `make typecheck` |
| Smoke lane (PR tier, fast) | `make test` |
| Full suite | `make test-full` |
| Full suite + branch coverage | `make coverage` |
| PR gate (lint + types + coverage + ratchet) | `make ci` |

The smoke lane (`make test`) is the default during development; the full suite and
coverage gate run on demand and in CI.

## Documentation

- [`docs/runbook.md`](docs/runbook.md) — operator runbook: install → configure → run → verify.
- [`docs/rollback.md`](docs/rollback.md) — per-phase rollback procedures.
- [`docs/traceability.md`](docs/traceability.md) — task → code → test traceability.
- [`docs/event_ownership.md`](docs/event_ownership.md) — IPC event ownership.
- [`docs/adr/`](docs/adr/) — architecture decision records.
- [`requirements/Dev_Harness_Implementation_Plan_V11_Final.md`](requirements/Dev_Harness_Implementation_Plan_V11_Final.md) — the authoritative implementation plan.
