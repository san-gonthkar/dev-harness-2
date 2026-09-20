---
name: sqlite-persistence
description: 'SQLite persistence per the V11 plan. Use when working with the checkpoint store: WAL pragmas, migration runner, SqliteSaver write/read paths, namespace guards, retention with seal preservation, integrity checks, and workspace locks. Covers the storage/ mutation focus set and the data-loss surface.'
user-invocable: true
---

# SQLite Persistence (V11 Phase 1)

## When to Use

- Working with `storage/` — connection, migrations, saver, guards, retention, integrity, locks
- Writing or fixing any SQLite-backed code

## Connection Contract (task 1.1)

Every connection must set:

- `PRAGMA journal_mode=WAL`
- `PRAGMA synchronous=NORMAL`
- `PRAGMA busy_timeout=10000`
- `PRAGMA foreign_keys=ON`

## Migration Runner (task 1.2)

- Forward + `--down`; `schema_migrations` ledger.
- Re-apply is a no-op; rollback returns `sqlite_master` count to baseline exactly.
- New migrations are named `NNNN_name.sql` and registered in the ledger.

## Schema (task 1.3)

`checkpoints` table: composite PK `(project_id, thread_id, checkpoint_id)`, columns `state_json`, `state_sha256`, `git_commit_hash`, `is_paused`, `created_at`; scope index on `(project_id, thread_id, created_at DESC)`.

## Namespace Guard (task 1.6)

Every query enforces `project_id = :project_id AND thread_id = :thread_id`. An unscoped query raises `UnscopedQueryError`. A mutant that drops the `project_id` predicate must be killed.

## Retention (task 1.7)

- Keep last N per thread + **all** `is_paused` seals.
- Prune + `VACUUM` on threshold.
- `VACUUM` under concurrent WAL readers can hit `SQLITE_BUSY` — honor `busy_timeout` and retry, never raise.
- A mutant that prunes a seal must be killed.

## Integrity (task 9.4)

- `state_sha256` must match a recomputed digest on read.
- Byte flip → digest mismatch → row quarantined → `get_tuple()` returns the prior valid checkpoint.

## Workspace Lock (task 1.8)

- `portalocker`, 10s timeout, PID+hostname for stale detection.
- Dead-PID lock reclaimed in one attempt.

## Mutation Focus Set

`guards.py` (project_id predicate), `retention.py` (seal preservation), `restore.py` (dirty check) — a surviving mutant in any of these fails the phase gate.