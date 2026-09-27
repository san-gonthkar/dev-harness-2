# Operator Runbook

Get from a clean machine to a running four-panel TUI with a live broker + engine
in **under 10 minutes**, without reading source.

Follow the steps in order. Every command below is a real console script or
Makefile target shipped with the harness.

---

## 0. Prerequisites

| Requirement | Check |
| :--- | :--- |
| Python **3.11+** | `python --version` |
| git | `git --version` |
| A git workspace to operate on | `git -C /path/to/your/repo rev-parse --is-inside-work-tree` |

The workspace **must be a git repository**. Pointing `--workspace` at a non-git
directory exits with code `2` (`NotAGitRepository`).

---

## 1. Install

From the repository root:

```bash
pip install .
```

This installs the four console scripts: `dev-harness`, `dev-harness-broker`,
`dev-harness-broker-cli`, `dev-harness-engine`.

For a development checkout (adds pytest, ruff, mypy, mutmut, hypothesis):

```bash
pip install -e ".[dev]"     # or: make install
```

---

## 2. Configure

Copy the template to the working directory and adjust as needed:

```bash
cp dev-harness.example.toml dev-harness.toml
```

The config is loaded from `./dev-harness.toml` (the current working directory).
Key settings:

- `workspace_path` — default workspace.
- `budget_usd_per_run` / `budget_usd_per_day` — cost governor ceilings.
- `[broker] allow_unbrokered` — keep `false` (fail-closed) in normal operation.
- `[providers.*]` — per-provider `rpm`, `tpm`, `max_concurrency`, `context_window`, and cost rates.

`DEV_HARNESS_*` environment variables override the file.

---

## 3. Start the broker

The broker is **host-scoped** (one instance per machine, socket under
`~/.local/share/dev-harness/`), not workspace-scoped. Start it in the background:

```bash
dev-harness-broker &
```

Check it is up:

```bash
dev-harness-broker --health
```

Optional — inspect live metrics or drive synthetic load:

```bash
dev-harness-broker-cli metrics --follow
dev-harness-broker-cli loadgen --provider anthropic --duration 30
```

---

## 4. Start the engine

The engine daemon is **workspace-scoped** and autostarts on first connect. To
start it explicitly and confirm the handshake:

```bash
dev-harness-engine --workspace /path/to/your/repo
```

Add `--self-check` to print the daemon status:

```bash
dev-harness-engine --workspace /path/to/your/repo --self-check
```

---

## 5. Launch the TUI

```bash
dev-harness --workspace /path/to/your/repo
```

The four-panel Hermes TUI opens. If the engine daemon is not yet running it is
spawned automatically and the handshake completes in under 3 seconds.

---

## 6. Verify

Run the self-check — it prints the derived socket and DB paths plus broker and
engine health, then exits without opening the TUI:

```bash
dev-harness --workspace /path/to/your/repo --self-check
```

Expected output shape:

```
socket: /path/to/your/repo/.dev-harness/harness-<ns>.sock
db: /path/to/your/repo/.dev-harness/state.db
broker: ok
engine: thread_id=<id> state=<STATE> version=0.1.0
```

- `broker: ok` — the broker daemon answered a HEALTH request.
- `engine: ...` — the engine daemon answered the STATUS handshake.

Exit code `0` means healthy; `1` means the broker or engine could not be reached.

---

## 7. Test & CI commands

| Task | Command |
| :--- | :--- |
| Lint + format check | `make lint` |
| Typecheck (`mypy --strict`) | `make typecheck` |
| Smoke lane (PR tier, fast) | `make test` |
| Full suite | `make test-full` |
| Full suite + branch coverage | `make coverage` |
| PR gate (lint + types + coverage + ratchet) | `make ci` |

---

## 8. Rollback

If a run leaves the workspace in a bad state, follow the per-phase procedures in
[`docs/rollback.md`](rollback.md). Principles: never roll back in place on a live
workspace (clone to scratch first), state is the database
(`<workspace>/.dev-harness/state.db`), worktrees are disposable, and secrets are
never touched.

---

## Troubleshooting

| Symptom | Cause | Fix |
| :--- | :--- | :--- |
| `dev-harness` exits `2` | `--workspace` is not a git repo | `git init` the workspace or point elsewhere |
| `broker: unavailable` | Broker daemon not running | Start `dev-harness-broker` |
| `engine unavailable` | Engine daemon could not handshake | Run `dev-harness-engine --workspace <ws>` and retry |
| `AlreadyRunning` on broker start | A broker is already up (host-scoped) | Use the existing instance; check `dev-harness-broker --health` |
| Config error on start | Missing/invalid `dev-harness.toml` key | Fix the offending key; re-copy from `dev-harness.example.toml` |
