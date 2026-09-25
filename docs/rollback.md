# Rollback Procedures (V11 9.10)

Per-phase rollback procedures for the Dev Harness. The 9.D protocol rehearses the
Phase 1 procedure on a scratch clone and records the timing; the target is a
clean rollback in **< 15 minutes**.

## Principles

- **Never roll back in place on a live workspace.** Clone to a scratch directory,
  rehearse there, then apply to the real workspace only after the rehearsal is
  green.
- **State is the database, not the code.** The harness keeps all durable state in
  `<workspace>/.dev-harness/state.db` (SQLite, WAL). Rolling back code without
  rolling back the schema leaves the app unable to read its own state.
- **Worktrees are disposable.** `.dev-harness/worktrees/*` are harness-owned and
  can be recreated; never hand-edit them during a rollback.
- **Secrets never enter the repo.** Rollback never touches the keyring or the
  `0600` key file.

## Preconditions

| Check | Command | Expected |
| :--- | :--- | :--- |
| Clean tree | `git status --porcelain` | empty |
| On the release branch | `git rev-parse --abbrev-ref HEAD` | `main` |
| No live daemon | `python -m dev_harness.cli --workspace <ws> --self-check` | engine not running |
| Backup taken | `cp <ws>/.dev-harness/state.db <ws>/.dev-harness/state.db.bak` | file exists |

## Phase 1 — Persistence & Workspace Isolation

The reference procedure (rehearsed by 9.D step 10).

1. **Clone to scratch.**
   ```bash
   git clone <repo> /tmp/rollback-rehearsal && cd /tmp/rollback-rehearsal
   ```
2. **Check out the prior tag.**
   ```bash
   git checkout contracts-v1
   ```
3. **Roll the schema back.**
   ```bash
   python -m dev_harness.storage.migrate --workspace /tmp/rollback-ws --down
   ```
   Expected: `rolled back: ['0002', '0001']`; `sqlite_master` retains only
   `schema_migrations` (verified 2026-09-24).
4. **Verify the app starts on the prior tag.**
   ```bash
   python -m dev_harness.cli --workspace /tmp/rollback-ws --self-check
   ```
   Expected: exit 0; socket, DB, broker and engine health printed.
5. **Record the timing.** Note wall-clock minutes in the rehearsal log; the
   acceptance threshold is < 15 min.

## Phase 2 — IPC Transport & Event Bus

- No schema. Roll back the code only (`git checkout <prior-tag>`).
- Remove stale sockets before restarting: `rm -f <ws>/.dev-harness/harness-*.sock`.
- The event vocabulary is closed (`contracts/enums.py`); a rollback that changes
  it requires a coordinated TUI + engine restart.

## Phase 3 — LLM Provider Abstraction

- No schema. Roll back the code only.
- Provider credentials live outside the repo (env → keyring → `0600` file); a
  rollback does not touch them.
- If a model registry entry was removed, `UnknownModelError` is expected on
  resume — re-add the entry or switch models.

## Phase 4 — Rate-Limit Broker & Cost Governor

- No schema. Roll back the code only.
- Stop the broker daemon before rolling back so no in-flight reservation is
  orphaned: `python -m dev_harness.broker.cli stop --workspace <ws>`.
- Cost counters live in the broker's own state; a rollback does not reset spend.

## Phase 5 — Execution Engine Daemon

- No schema. Roll back the code only.
- Shut the daemon down gracefully first (`SIGTERM`), then roll back.
- Stale sockets and locks are reclaimed on the next start (9.2).

## Phase 6 — Critic Gatekeeper & Interrupt Engine

- No schema. Roll back the code only.
- A paused session seals a checkpoint; rolling back code does not unseal it.
  Resume with the prior tag, or start a fresh session.

## Phase 7 — Hermes TUI Core Subsystem

- No schema. Roll back the code only.
- The TUI is a client; it holds no durable state. A rollback requires only a
  restart.

## Phase 8 — SDLC Pipeline & Worker Pool

- **Schema:** `0002_worktree_state` adds `checkpoints.worktree_head` /
  `worktree_diff`. Rolling back past 8.21a requires `--down` (see Phase 1).
- **Worktrees:** destroy harness-owned worktrees before rolling back:
  ```bash
  git worktree list --porcelain   # inspect
  git worktree prune              # remove stale registrations
  ```
- **In-flight work:** capture each worktree's HEAD + diff (8.21b) before rolling
  back, or the uncommitted work is lost.

## Phase 9 — Error Handling & Recovery

- No schema. Roll back the code only.
- The quarantine side table (`checkpoint_quarantine`, 9.4) is additive; a
  rollback leaves it in place. It is safe to drop manually if desired:
  `DROP TABLE IF EXISTS checkpoint_quarantine;`

## Phase 10 — Verification & Release

- No schema. Roll back the code only.
- Re-run `scripts/verify_phase_10.sh` after any rollback to confirm the release
  gates still hold.

## Rehearsal log

| Date | Phase | Operator | Wall-clock | Result |
| :--- | :--- | :--- | :--- | :--- |
| 2026-09-24 | 1 | orchestrator (automated) | < 1 min (scratch) | `--down` rolled back `['0002','0001']`; `sqlite_master` reduced to `schema_migrations` |

> The 9.D protocol (step 10) requires a **timed, recorded** rehearsal on a
> scratch clone. The row above records the automated verification performed
> during 9.10; a human operator should re-run and time it before release.
