# Dev Harness

**Hermes TUI Dev Harness** — a terminal application that orchestrates an agentic
software-development pipeline (Groomer → Architect → Developer → Tester → Critic)
against LLM providers, with a four-panel Textual TUI, a host-scoped rate-limit
broker, and SQLite checkpoints.

- **Python** 3.11+ · **Textual** TUI · **LangGraph** pipeline · **Pydantic v2** state · **SQLite (WAL)** checkpoints · **AF_UNIX** IPC
- Version `0.1.0`

---

## ⚠️ Read this first: platform support

The **engine daemon** communicates over **AF_UNIX sockets, which are POSIX-only**.
On native Windows it refuses to start (`UnsupportedPlatformError`).

**The broker now supports a TCP loopback transport** (`tcp:127.0.0.1:8765`), so
the broker — and the live provider path — run natively on Windows. The engine
daemon still requires AF_UNIX (WSL2).

| What you want to run | Native Windows | WSL2 / Linux / macOS |
| :--- | :---: | :---: |
| Offline SDLC pipeline (`engine.cli run` / `plan` / `critic-drill`) | ✅ works | ✅ works |
| Broker daemon (`dev-harness-broker`) | ✅ via `--endpoint tcp:...` | ✅ |
| **Live provider run** (`dev-harness-live`) | ✅ via `--endpoint tcp:...` | ✅ |
| Four-panel TUI shell (`dev-harness`) | ✅ renders | ✅ |
| Engine daemon (`dev-harness-engine`) | ❌ | ✅ |
| TUI **live event feed** (engine → bridge) | ❌ | ✅ |
| Test suite | ✅ (most) | ✅ |

**On Windows, run the live pipeline with the TCP broker endpoint.** The engine
daemon needs WSL2.

> **Note on the TUI:** the four-panel shell renders on Windows, but it is not yet
> wired to a live engine feed — `cli.py` launches `HermesApp` without a `Bridge`,
> so no IPC client is constructed. The bridge is exercised in tests and the
> phase-07 verification script. A live feed needs the engine daemon (WSL2).

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
| `dev-harness-live` | Run the SDLC pipeline against a **real** provider (broker-metered) |

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

## 2b. Run it for real (live provider — OpenRouter)

`dev-harness-live` runs the **same SDLC graph** against a **real provider**,
metered through the broker. Every call reserves capacity before it runs and
commits actual usage after, so rate limits and the cost governor apply.

### 2b.1 Set the API key

The credential resolves in this order: **environment → OS keyring → `0600` file**.

```powershell
$env:DEV_HARNESS_OPENROUTER_API_KEY = "sk-or-v1-..."   # PowerShell
```

```bash
export DEV_HARNESS_OPENROUTER_API_KEY="sk-or-v1-..."   # bash
```

> `DEV_HARNESS_*_API_KEY` variables are treated as **secrets**, not config keys —
> they are never parsed into the config schema.

### 2b.2 Start the broker

```powershell
# Windows (TCP loopback)
dev-harness-broker --config dev-harness.toml --endpoint tcp:127.0.0.1:8765
```

```bash
# WSL2 / Linux / macOS (AF_UNIX default)
dev-harness-broker --config dev-harness.toml
```

Verify: `dev-harness-broker --endpoint tcp:127.0.0.1:8765 --health` → `{"status": "ok"}`.

### 2b.3 Run the pipeline

```powershell
dev-harness-live `
    --workspace . `
    --requirement fixtures/req_simple.md `
    --provider openrouter `
    --endpoint tcp:127.0.0.1:8765
```

On WSL2/Linux/macOS, omit `--endpoint` (AF_UNIX is the default).

Expected output:

```
c1: COMPLETED
gate: RUNNING
```

Check what it cost:

```powershell
dev-harness-broker-cli --config dev-harness.toml --endpoint tcp:127.0.0.1:8765 metrics
# p50=...ms p95=...ms tpm=... usd=0.0013
```

### 2b.4 Configuration

```toml
[providers.openrouter]
rpm = 50
tpm = 200000
max_concurrency = 2
context_window = 128000
usd_per_mtok_in = 0.50
usd_per_mtok_out = 1.50
auth = "api_key"          # "login" is reserved for a future OAuth flow
# base_url = "..."        # optional endpoint override
```

- `budget_usd_per_run` / `budget_usd_per_day` cap spend; exceeding them trips the
  kill-switch and the broker refuses further reservations.
- `auth = "login"` is **accepted by the schema but fails closed at build time** —
  the OAuth device flow is not implemented yet.

### 2b.5 Adding another provider

The factory is a registry — one entry adds a provider:

```python
from dev_harness.providers.factory import ProviderSpec, register
from dev_harness.contracts.enums import ProviderId

register(ProviderSpec(
    provider=ProviderId.ANTHROPIC,
    secret_name="ANTHROPIC_API_KEY",
    default_model="claude-3-5-sonnet-latest",
    build=lambda key, url: MyAdapter(key),
))
```

The pipeline is **provider-agnostic**: it receives an `LLMClient` and never knows
which vendor is behind it.

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
| `OSError: connect(): bad family` | Running the broker/engine on native Windows | Use `--endpoint tcp:127.0.0.1:8765` for the broker; WSL2 for the engine/TUI |
| `UnsupportedPlatformError: ... run inside WSL2` | AF_UNIX unavailable | Run inside WSL2 or on a POSIX host |
| `AuthError: no credential for OPENROUTER_API_KEY` | API key not set | Set `DEV_HARNESS_OPENROUTER_API_KEY` |
| `AuthNotImplementedError` | `auth = "login"` in config | Use `auth = "api_key"` until the OAuth flow lands |
| `BrokerUnavailableError: broker refused reservation` | Rate limit, saturation, or budget exceeded | Check `dev-harness-broker-cli metrics`; raise the budget or wait |
| `ProviderNotConfiguredError` | No `[providers.<name>]` block | Add the block to `dev-harness.toml` |
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
