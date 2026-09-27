# Dev Harness

**Hermes TUI Dev Harness** — a terminal application that orchestrates an agentic
software-development pipeline (Groomer → Architect → Developer → Tester → Critic)
against LLM providers, with a four-panel Textual TUI, a host-scoped rate-limit
broker, and SQLite checkpoints.

- **Python** 3.11+ · **Textual** TUI · **LangGraph** pipeline · **Pydantic v2** state · **SQLite (WAL)** checkpoints · **AF_UNIX** IPC
- Version `0.1.0`

---

## ⚠️ Read this first: platform support

The full stack (broker daemon + engine daemon + TUI) communicates over **AF_UNIX
sockets, which are POSIX-only**. On native Windows the transport refuses to start
(`UnsupportedPlatformError`), and the broker/engine fail with
`OSError: connect(): bad family`.

| What you want to run | Native Windows | WSL2 / Linux / macOS |
| :--- | :---: | :---: |
| Offline SDLC pipeline (`engine.cli run` / `plan` / `critic-drill`) | ✅ works | ✅ works |
| Broker daemon (`dev-harness-broker`) | ❌ | ✅ |
| Engine daemon (`dev-harness-engine`) | ❌ | ✅ |
| Four-panel TUI (`dev-harness`) | ❌ | ✅ |
| Test suite | ✅ (most) | ✅ |

**On Windows, run the full stack inside WSL2.** The offline pipeline runs natively.

---

## 1. Build (install)

From the repository root:

```bash
pip install .                 # runtime only
pip install -e ".[dev]"       # editable + dev tools (pytest, ruff, mypy, mutmut, hypothesis)
```

`make install` is equivalent to the second command.

This installs four console scripts:

| Command | Purpose |
| :--- | :--- |
| `dev-harness` | Launch the four-panel TUI (or `--self-check`) |
| `dev-harness-broker` | Run the host-scoped rate-limit broker daemon |
| `dev-harness-broker-cli` | Broker load generator / manual reserve / metrics |
| `dev-harness-engine` | Ensure the workspace engine daemon is running |

### Windows (PowerShell) — use the venv interpreter

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Then either activate the venv (`.venv\Scripts\Activate.ps1`) or prefix commands
with `.venv\Scripts\python.exe`.

---

## 2. Run it right now (offline pipeline — works on Windows)

The engine CLI executes the **real compiled SDLC graph** against a deterministic
mock client. No network, no broker, no credentials. It **fails closed without
`--mock`** (no live provider adapters are wired in this build).

> **Run these from the repository root** — the `run` subcommand imports
> `tests.support.mock_llm`, which is only importable from the repo root.

```powershell
# Plan a requirement into a chunk DAG and print the topological order
.venv\Scripts\python.exe -m dev_harness.engine.cli plan `
    --workspace . --requirement fixtures/req_simple.md --mock --print-dag

# Execute the full pipeline end to end and persist a trace
.venv\Scripts\python.exe -m dev_harness.engine.cli run `
    --workspace . --requirement fixtures/req_simple.md --mock --trace

# Prove the Critic's read-only containment
.venv\Scripts\python.exe -m dev_harness.engine.cli critic-drill --workspace . --mock
```

Expected output of `run` (exit code `0`):

```
c1: COMPLETED
gate: RUNNING
trace: <workspace>/reports/parallel_trace.json
```

`--workspace` must be a **git repository**. Other fixtures:
`fixtures/req_diamond.md`, `fixtures/req_conflict.md`, `fixtures/req_cycle.md`,
`fixtures/req_failing.md`, `fixtures/req_hitl.md`.

---

## 3. Run the full stack (WSL2 / Linux / macOS)

### 3.1 Install a WSL2 distribution (Windows only, one time)

WSL2 is present on this machine but **has no distribution installed**. In an
**elevated** PowerShell:

```powershell
wsl --install -d Ubuntu
```

Reboot if prompted, then open Ubuntu and continue below **inside WSL**.

### 3.2 Build inside WSL

```bash
cd /mnt/c/Workspace/dev-harness-2      # or clone the repo into the Linux filesystem
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 3.3 Configure

```bash
cp dev-harness.example.toml dev-harness.toml
```

The config is loaded from `./dev-harness.toml` (current working directory). Key
settings:

- `workspace_path` — default workspace.
- `budget_usd_per_run` / `budget_usd_per_day` — cost-governor ceilings.
- `[broker] allow_unbrokered` — keep `false` (fail-closed) in normal operation.
- `[providers.*]` — per-provider `rpm`, `tpm`, `max_concurrency`, `context_window`, cost rates.

`DEV_HARNESS_*` environment variables override the file (e.g.
`DEV_HARNESS_ANTHROPIC__RPM=10`).

> **Ollama is disabled** (2026-09-27). The `[providers.ollama]` block is
> commented out in `dev-harness.example.toml` and `profiles/local.toml`.
> Uncomment it to re-enable local inference.

### 3.4 Start the broker (host-scoped, one per machine)

```bash
dev-harness-broker &            # background
dev-harness-broker --health     # -> {"status": "ok"}
```

Optional — inspect metrics or drive synthetic load:

```bash
dev-harness-broker-cli metrics --follow
dev-harness-broker-cli loadgen --provider anthropic --duration 30
```

### 3.5 Start the engine daemon (workspace-scoped)

The daemon autostarts on first connect. To start it explicitly:

```bash
dev-harness-engine --workspace /path/to/your/repo --self-check
```

### 3.6 Launch the TUI

```bash
dev-harness --workspace /path/to/your/repo
```

The four-panel Hermes TUI opens. If the engine daemon is not running it is
spawned automatically and the handshake completes in under 3 seconds.

### 3.7 Verify

```bash
dev-harness --workspace /path/to/your/repo --self-check
```

Expected output:

```
socket: /path/to/your/repo/.dev-harness/harness-<ns>.sock
db: /path/to/your/repo/.dev-harness/state.db
broker: ok
engine: thread_id=<id> state=<STATE> version=0.1.0
```

Exit `0` = healthy; `1` = broker or engine unreachable; `2` = workspace is not a
git repository.

---

## 4. Troubleshooting

| Symptom | Cause | Fix |
| :--- | :--- | :--- |
| `OSError: connect(): bad family` | Running the broker/engine on native Windows | Run inside WSL2 (see §3.1) |
| `UnsupportedPlatformError: ... run inside WSL2` | AF_UNIX unavailable | Run inside WSL2 or on a POSIX host |
| `dev-harness` exits `2` | `--workspace` is not a git repo | `git init` the workspace or point elsewhere |
| `broker: unavailable` | Broker daemon not running | Start `dev-harness-broker` |
| `engine unavailable` | Engine daemon could not handshake | Run `dev-harness-engine --workspace <ws>` and retry |
| `AlreadyRunning` on broker start | A broker is already up (host-scoped) | Use the existing instance; check `--health` |
| `ModuleNotFoundError: No module named 'tests'` | Ran `engine.cli run` outside the repo root | Run it from the repository root |
| `only '--mock' is supported in this build` | Missing `--mock` | Add `--mock` (no live adapters are wired) |
| Config error on start | Missing/invalid `dev-harness.toml` key | Fix the key; re-copy from `dev-harness.example.toml` |

---

## 5. Development

| Task | Command |
| :--- | :--- |
| Install (editable + dev) | `make install` |
| Lint + format check | `make lint` |
| Typecheck (`mypy --strict`) | `make typecheck` |
| Smoke lane (PR tier, fast) | `make test` |
| Full suite | `make test-full` |
| Full suite + branch coverage | `make coverage` |
| PR gate (lint + types + coverage + ratchet) | `make ci` |

The smoke lane (`make test`) is the default during development; the full suite
and coverage gate run on demand and in CI.

---

## 6. Architecture summary

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
- Durable state lives in `<workspace>/.dev-harness/state.db`; the broker socket is host-scoped under `~/.local/share/dev-harness/`.

---

## 7. Documentation

- [`docs/runbook.md`](docs/runbook.md) — operator runbook: install → configure → run → verify.
- [`docs/rollback.md`](docs/rollback.md) — per-phase rollback procedures.
- [`docs/traceability.md`](docs/traceability.md) — task → code → test traceability.
- [`docs/event_ownership.md`](docs/event_ownership.md) — IPC event ownership.
- [`docs/adr/`](docs/adr/) — architecture decision records.
- [`requirements/Dev_Harness_Implementation_Plan_V11_Final.md`](requirements/Dev_Harness_Implementation_Plan_V11_Final.md) — the authoritative implementation plan.
