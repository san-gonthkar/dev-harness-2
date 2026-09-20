# Hermes TUI Dev Harness — Detailed Implementation Plan & Validation Matrix (V9)

**Supersedes:** V8 (`Dev_Harness_Implementation_Plan_and_Validation_Matrix_V8.md`), V7
**Traces to:** `Hermes TUI Dev Harness - Detailed Technical Design Specification (V7)`
**V9 mandate:** close the five structural defects found in the V8 audit — no LLM provider layer, no execution-engine process, no test infrastructure, no parallel-worker isolation, no cost governance — and attach an effort model so the plan can actually be scheduled across parallel agent workers.

---

## 0. Reading Protocol

- **Task sizing:** every task carries an explicit `Est` in hours. Nothing exceeds 4h. Anything that grew past 4h during V8 review has been split (see §12 changelog).
- **Lanes:** tasks carry a lane (`A` platform, `B` model-plane, `C` interface, `D` orchestration). Tasks in different lanes with satisfied prerequisites may run in parallel on different workers.
- **Prerequisites are hard gates.** No task starts until every prerequisite has a green validation row at its declared CI tier.
- **CI tier:** `PR` = blocks merge. `NIGHTLY` = scheduled, blocks the phase gate. `REL` = release gate only. Timing-sensitive tests are never `PR`-blocking (see 0.12).
- **Phase Gates:** each phase ends with a Definition of Done. A phase is not complete when its tasks are done; it is complete when its gate passes.
- **Agent handoff:** every task is executed on branch `task/{id}-{slug}`, with commit trailer `Task-Id: {id}`, and the PR body must paste the output of its validation command. Enforced by 0.11.

### Corrected Dependency Graph (V9)

V8 ordered Scaffolding → Storage → IPC → TUI → Critic → Broker → Pipeline. That order cannot build: the pipeline nodes call an LLM through a provider layer that no phase created, and no phase created the engine process that hosts the graph and serves the IPC socket the TUI connects to. V9 inserts both.

```
P0 Scaffolding, Contracts & Test Infrastructure
 |
 +--[A]--> P1 Persistence & Workspace Isolation --+
 |                                                |
 +--[A]--> P2 IPC Transport & Event Bus ----------+
 |                                                |
 +--[B]--> P3 LLM Provider Abstraction -----------+
                   |                              |
                   v                              |
          P4 Rate-Limit Broker & Cost Governor     |
                   |                              |
                   +------------+-----------------+
                                v
                   P5 Execution Engine Daemon & Session Lifecycle
                                |
                +---------------+---------------+
                v                               v
       [D] P6 Critic Gatekeeper          [C] P7 TUI Core
                |                               |
                +---------------+---------------+
                                v
                   [D] P8 SDLC Pipeline & Parallel Worker Pool
                                |
                                v
                   P9 Error Handling, Recovery & Edge Cases
                                |
                                v
                   P10 Final Verification, Traceability & Release
```

---

## Phase 0: Scaffolding, Shared Contracts & Test Infrastructure

**Objective:** Establish the single source of truth for every enum, schema and path rule, *and* the deterministic test rig that all ten downstream phases assert against.
**Prerequisites:** None.
**Lane:** A

### Phase 0 Execution Tasks

| Task ID | Task Description & Deliverable | Targeted Files / Components | Prereq | Est | Lane |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 0.1 | `src/` layout package; pin Python >=3.11; declare deps (textual, rich, langgraph, langchain-core, pydantic>=2, httpx, portalocker, tiktoken, pytest, pytest-asyncio, pytest-cov, pytest-timeout, hypothesis, psutil, ruff, mypy). | `pyproject.toml`, `src/dev_harness/__init__.py` | — | 1.5 | A |
| 0.2 | Quality gates: ruff, mypy `strict=true` across **all** of `src/` (V8 scoped strict to `contracts/` only), coverage config with per-package thresholds. | `Makefile`, `mypy.ini`, `.coveragerc` | 0.1 | 1.5 | A |
| 0.3 | Canonical enums: `ExecutionState{READY,RUNNING,PAUSED,STOPPED}`, `CriticCommand{START,PAUSE,RESUME,STOP}`, `ChunkStatus`, `PanelId`, `EventType`, `ProviderId`, `FailureClass`. | `src/dev_harness/contracts/enums.py` | 0.1 | 2.0 | A |
| 0.4 | Error taxonomy: `HarnessError` root with `remediation` field; subclasses for storage, ipc, provider, broker, engine, vcs domains. | `src/dev_harness/contracts/errors.py` | 0.1 | 2.0 | A |
| 0.5 | V7 state as Pydantic v2 models (`HarnessState` and all nested blocks). | `src/dev_harness/contracts/state.py` | 0.3, 0.4 | 3.0 | A |
| 0.6 | IPC envelope + discriminated payload union for all 8 event types incl. `INTERRUPT_ACK` and `METRICS_UPDATE`. | `src/dev_harness/contracts/events.py` | 0.3 | 3.0 | A |
| 0.7 | JSON Schema export + golden fixture derived from TDD §6; drift-detection check. | `src/dev_harness/contracts/schema.py`, `schemas/harness_state.v7.json`, `tests/fixtures/state_v7_golden.json` | 0.5 | 2.0 | A |
| 0.8 | Config loader: TOML + `DEV_HARNESS_*` env overrides; provider blocks carry `rpm`, `tpm`, `max_concurrency`, `context_window`, `num_ctx`, `keep_alive`, `usd_per_mtok_in/out`. | `src/dev_harness/config.py`, `dev-harness.example.toml` | 0.5 | 3.0 | A |
| 0.9 | **Secrets provider** (absent in V8): resolve keys from env → OS keyring → file with `0600` check; keys never enter `HarnessState`, never serialize, `__repr__` masked. | `src/dev_harness/secrets.py` | 0.8 | 2.5 | A |
| 0.10 | Path derivation: canonical workspace path; socket `/tmp/dev-harness-{sha256[:16]}-{uid}.sock`; lock `.git/dev-harness.lock`; run artifacts `.dev-harness/runs/{thread_id}/`. | `src/dev_harness/paths.py` | 0.1 | 2.0 | A |
| 0.11 | Agent handoff enforcement: commit-msg hook + CI check requiring `Task-Id:` trailer and a matching task in this plan. | `scripts/check_task_trailer.py`, `.github/workflows/ci.yml` | 0.2 | 2.0 | A |
| 0.12 | **Test infrastructure — deterministic rig** (absent in V8, silently assumed by ~20 V8 tests): frozen monotonic clock fixture, `tmp_workspace` git-repo factory, golden-file helper, pytest markers `unit/integration/timing/slow`. | `tests/conftest.py`, `tests/support/clock.py`, `tests/support/workspace.py`, `pytest.ini` | 0.1 | 4.0 | A |
| 0.13 | **`MockLLM` + `FakeProviderServer`**: scripted deterministic completions, streaming chunk emission, injectable 429/5xx/timeout faults, served over an in-process ASGI app for httpx. | `tests/support/mock_llm.py`, `tests/support/fake_provider.py` | 0.12, 0.6 | 4.0 | A |
| 0.14 | Structured JSON logging with `project_id`/`thread_id` correlation + secret-redaction filter. | `src/dev_harness/observability/logging.py`, `src/dev_harness/observability/redact.py` | 0.9 | 2.5 | A |
| 0.15 | **Run artifact store** (absent in V8/V7): append-only per-run transcript of prompts, completions, diffs and test output under `.dev-harness/runs/{thread_id}/`, with size cap and rotation. | `src/dev_harness/observability/artifacts.py` | 0.10, 0.14 | 3.0 | A |

**Phase 0 total: 38h**

### Phase 0 Validation Matrix

| Task ID | Verification Strategy | Test Commands / Validation Script | Success Criteria (Pass/Fail) | Tier |
| :--- | :--- | :--- | :--- | :--- |
| 0.1 | Import smoke | `pip install -e . && python -c "import dev_harness"` | Exit 0; version resolves. | PR |
| 0.2 | Gate self-test | `make lint typecheck test` | ruff 0 findings; mypy 0 errors across `src/`; pytest exit 0. | PR |
| 0.3 | Enum exhaustiveness + literal ban | `pytest tests/contracts/test_enums.py -q` | All four `CriticCommand` members present; `grep -rn '"PAUSED"' src/ --include=*.py` matches only `enums.py`. | PR |
| 0.4 | Taxonomy completeness | `pytest tests/contracts/test_errors.py -q` | Every subclass sets non-empty `remediation`; all inherit `HarnessError`; no bare `Exception` raised anywhere in `src/` (AST scan). | PR |
| 0.5 | Round-trip | `pytest tests/contracts/test_state_model.py -q` | `model_validate(golden).model_dump(mode="json") == golden`; missing `project_id` → `ValidationError` at `("project_id",)`. | PR |
| 0.6 | Payload discrimination | `pytest tests/contracts/test_events.py -q` | 1:1 `EventType`→payload mapping; unknown type → `ValidationError`; type/payload mismatch rejected. | PR |
| 0.7 | Schema conformance + drift | `pytest tests/contracts/test_schema.py -q` | `jsonschema.validate` passes; regenerated schema byte-identical to committed file. | PR |
| 0.8 | Precedence | `pytest tests/test_config.py -q` | `DEV_HARNESS_ANTHROPIC__RPM=10` overrides TOML `50`; missing required key → `ConfigError` naming it. | PR |
| 0.9 | Secret containment | `pytest tests/test_secrets.py -q` | `repr(secret)` returns `***`; `HarnessState.model_dump_json()` over a state built with a live key contains no key substring; a `0644` key file raises `InsecureKeyFileError`. | PR |
| 0.10 | Path determinism | `pytest tests/test_paths.py -q` | `/tmp/x/` ≡ `/tmp/x`; symlink resolves to real path; distinct paths → distinct hashes; socket path ≤104 bytes. | PR |
| 0.11 | Handoff enforcement | `python scripts/check_task_trailer.py --rev HEAD` | Commit lacking `Task-Id:` exits 1; `Task-Id: 99.9` (not in plan) exits 1; valid trailer exits 0. | PR |
| 0.12 | Rig self-test | `pytest tests/support/test_rig.py -q` | Frozen clock advances only on explicit `tick()`; `tmp_workspace` yields a repo where `git rev-parse HEAD` succeeds; markers registered (`pytest --markers` lists all 4). | PR |
| 0.13 | Mock fidelity | `pytest tests/support/test_mock_llm.py -q` | Same seed → byte-identical completion across 10 runs; fault injection yields HTTP 429 with `Retry-After`; streaming yields ≥2 chunks with correct terminal sentinel. | PR |
| 0.14 | Redaction | `pytest tests/observability/test_redaction.py -q` | `sk-ant-api03-XXXX` → `sk-***REDACTED***`; `caplog.text` contains no raw key substring. | PR |
| 0.15 | Artifact store | `pytest tests/observability/test_artifacts.py -q` | A 3-turn run writes 3 transcript entries in order; exceeding the size cap rotates without data loss of the newest entry; entries are redacted. | PR |

**Phase 0 Gate (DoD):** `make ci` green; schema + enums frozen and tagged `contracts-v1`; `MockLLM` usable by an unrelated test module with zero additional setup.

---

## Phase 1: Persistence, Namespacing, Retention & Workspace Isolation

**Objective:** Multi-tenant checkpoint store that cannot leak across projects, binds every checkpoint to a recoverable commit, and does not grow without bound.
**Prerequisites:** 0.5, 0.8, 0.10, 0.12
**Lane:** A

### Phase 1 Execution Tasks

| Task ID | Task Description & Deliverable | Targeted Files / Components | Prereq | Est | Lane |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 1.1 | Connection factory: `journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=10000`, `foreign_keys=ON`. | `storage/connection.py` | 0.8 | 2.0 | A |
| 1.2 | Migration runner with forward + `-- down` rollback and `schema_migrations` ledger. | `storage/migrate.py` | 1.1 | 3.0 | A |
| 1.3 | Migration `0001_init`: `checkpoints` (composite PK `project_id,thread_id,checkpoint_id`; `state_json`, `state_sha256`, `git_commit_hash`, `is_paused`, `created_at`) + scope index. | `storage/migrations/0001_init.sql` | 1.2 | 2.0 | A |
| 1.4 | `SqliteSaver` **write path** (`put`, `put_writes`) against the LangGraph `BaseCheckpointSaver` interface. *(V8 bundled the whole interface into one 8h task.)* | `storage/sqlite_saver.py` | 1.3, 0.5 | 3.5 | A |
| 1.5 | `SqliteSaver` **read path** (`get_tuple`, `list`) with newest-first ordering and cursor paging. | `storage/sqlite_saver.py` | 1.4 | 3.0 | A |
| 1.6 | Namespace guard raising `UnscopedQueryError` when `project_id`+`thread_id` predicates are absent. | `storage/guards.py` | 1.5 | 2.5 | A |
| 1.7 | **Retention policy** (absent in V8): keep last N checkpoints per thread + all `is_paused` seals; prune + `VACUUM` on a size threshold. | `storage/retention.py` | 1.5 | 3.0 | A |
| 1.8 | Workspace lock via `portalocker`, 10s timeout, PID+hostname written for stale detection. | `storage/workspace_lock.py` | 0.10 | 2.5 | A |
| 1.9 | Git adapter: `head_sha`, `active_branch`, `is_dirty`, `uncommitted_count` — `subprocess` with explicit `cwd`, no shell. | `vcs/git.py` | 0.4 | 2.5 | A |
| 1.10 | **Git worktree manager** (absent in V8; required by parallel workers in P8): create/destroy `.dev-harness/worktrees/{worker_id}` bound to a branch. | `vcs/worktree.py` | 1.9 | 3.5 | A |
| 1.11 | Checkpoint↔Git binding: capture `head_sha()` inside the workspace lock so state and hash are atomic. | `storage/checkpoint_binding.py` | 1.4, 1.8, 1.9 | 2.5 | A |
| 1.12 | Restore: refuse on dirty worktree unless `stash_policy="autostash"`, then `git checkout {sha}` and re-hydrate state. | `vcs/restore.py` | 1.11 | 3.0 | A |
| 1.13 | Concurrency fixture: two isolated temp workspaces, each with its own repo and DB. | `tests/storage/test_multi_project_isolation.py` | 1.5, 1.8, 0.12 | 2.5 | A |

**Phase 1 total: 35.5h**

### Phase 1 Validation Matrix

| Task ID | Verification Strategy | Test Commands / Validation Script | Success Criteria (Pass/Fail) | Tier |
| :--- | :--- | :--- | :--- | :--- |
| 1.1 | PRAGMA assertion | `pytest tests/storage/test_connection.py -q` | `pragma_journal_mode='wal'`; `pragma_synchronous=1`; `pragma_busy_timeout=10000`. | PR |
| 1.2 | Up/down idempotence | `pytest tests/storage/test_migrations.py -q` | Re-apply is a no-op; rollback returns `sqlite_master` row count to baseline exactly. | PR |
| 1.3 | Constraints | `pytest tests/storage/test_schema_ddl.py -q` | Duplicate composite PK → `IntegrityError`; `EXPLAIN QUERY PLAN` reports `USING INDEX idx_checkpoints_scope`. | PR |
| 1.4 | Write-path conformance | `pytest tests/storage/test_saver_write.py -q` | `put()` persists a row whose `state_sha256` matches a recomputed digest; `put_writes` is transactional (injected failure leaves 0 partial rows). | PR |
| 1.5 | Read-path conformance | `pytest tests/storage/test_saver_read.py -q` | `get_tuple()` equals the written golden state field-for-field; `list(limit=3)` is newest-first; paging returns disjoint sets. | PR |
| 1.6 | Unscoped access negative test | `pytest tests/storage/test_guards.py -q` | `list(config={})` → `UnscopedQueryError`; `proj_A` checkpoints return count 0 under `proj_B`. | PR |
| 1.7 | Retention | `pytest tests/storage/test_retention.py -q` | With `keep=5`, 20 writes leave exactly 5 non-seal rows + every `is_paused` seal; DB file size shrinks after `VACUUM`; restore of a retained seal still succeeds. | PR |
| 1.8 | Lock contention + stale reclaim | `pytest tests/storage/test_workspace_lock.py -q` | Second acquirer raises `LockTimeout` at 10s ±0.5s; lock file with a dead PID reclaimed in one attempt. | NIGHTLY |
| 1.9 | Git adapter | `pytest tests/vcs/test_git.py -q` | `head_sha()` == `git rev-parse HEAD`; `is_dirty()` True after touching a tracked file, False after `git checkout .`. | PR |
| 1.10 | Worktree lifecycle | `pytest tests/vcs/test_worktree.py -q` | 3 worktrees created concurrently each report a distinct path and branch; destroy leaves `git worktree list` with only the primary; a file written in worktree A is absent in B. | PR |
| 1.11 | Atomicity under concurrency | `pytest tests/storage/test_checkpoint_binding.py -q` | 50 interleaved writes: every stored hash satisfies `git cat-file -e` exit 0 and equals HEAD at write time. | NIGHTLY |
| 1.12 | Destructive-restore guard | `pytest tests/vcs/test_restore.py -q` | Dirty tree → `DirtyWorktreeError` with `git status --porcelain` byte-identical before/after; autostash reapplies and leaves `git stash list` empty. | PR |
| 1.13 | Cross-project isolation | `pytest tests/storage/test_multi_project_isolation.py -q` | 200 concurrent writes per workspace: 0 `database is locked`; 0 cross-project rows; wall clock < 2× single-workspace baseline. | NIGHTLY |

**Phase 1 Gate:** isolation + binding tests green on two consecutive nightly runs; restore of an arbitrary retained checkpoint reproduces both state and worktree SHA.

---

## Phase 2: IPC Transport & Event Bus

**Objective:** A framed, typed, backpressure-safe channel with a producer/consumer contract test so the engine and TUI cannot drift.
**Prerequisites:** 0.6, 0.10, 0.14
**Lane:** A

### Phase 2 Execution Tasks

| Task ID | Task Description & Deliverable | Targeted Files / Components | Prereq | Est | Lane |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 2.1 | ADR selecting length-prefixed JSON over `AF_UNIX`; gRPC rejected (codegen toolchain for a single-host, low-fanout channel). | `docs/adr/0001-ipc-transport.md` | — | 1.0 | A |
| 2.2 | Framing codec: 4-byte BE length prefix + UTF-8 JSON, 1 MiB max frame. | `ipc/framing.py` | 2.1, 0.6 | 2.5 | A |
| 2.3 | Async `AF_UNIX` server; socket created `0600`; stale socket unlinked on bind. | `ipc/server.py` | 2.2, 0.10 | 3.0 | A |
| 2.4 | Client with connect-retry (exponential, cap 5s) and half-open detection. | `ipc/client.py` | 2.2 | 3.0 | A |
| 2.5 | Pub/sub router: subscribe by `EventType`, N-way fan-out, per-handler exception isolation. | `ipc/router.py` | 2.3 | 3.0 | A |
| 2.6 | Backpressure queue: bounded 2048; `AGENT_TOKEN_STREAM` drop-oldest with counter; control events never drop. | `ipc/queue.py` | 2.5 | 3.0 | A |
| 2.7 | Platform gate: POSIX check at import; Windows → `UnsupportedPlatformError` naming WSL2. | `ipc/transport.py` | 2.3 | 1.0 | A |
| 2.8 | **Producer/consumer contract test harness** (absent in V8): every event type is emitted by a recorded producer fixture and parsed by the real consumer parser; drift fails the build. | `tests/ipc/test_event_contract.py`, `tests/fixtures/events/*.json` | 2.2, 0.6 | 3.0 | A |

**Phase 2 total: 19.5h**

### Phase 2 Validation Matrix

| Task ID | Verification Strategy | Test Commands / Validation Script | Success Criteria (Pass/Fail) | Tier |
| :--- | :--- | :--- | :--- | :--- |
| 2.1 | Review gate | `test -f docs/adr/0001-ipc-transport.md` | Contains Decision, Rejected Alternatives, Consequences. | PR |
| 2.2 | Property-based round trip | `pytest tests/ipc/test_framing.py -q` | Hypothesis 500 envelopes lossless; 2 MiB → `FrameTooLargeError`; truncated stream → `IncompleteFrameError`, no hang (`--timeout=10`). | PR |
| 2.3 | Permissions + stale cleanup | `pytest tests/ipc/test_server.py -q` | `oct(stat.st_mode)[-3:] == "600"`; bind over leftover socket succeeds without `EADDRINUSE`. | PR |
| 2.4 | Reconnect | `pytest tests/ipc/test_client.py -q` | Survives server restart and delivers the next message; retry intervals non-decreasing, capped 5s. | PR |
| 2.5 | Fan-out isolation | `pytest tests/ipc/test_router.py -q` | 3 subscribers each get 1 copy; a raising handler does not block the other 2; error logged once. | PR |
| 2.6 | Flood behavior | `pytest tests/ipc/test_queue.py -q` | 10k token events into a 2048 queue: RSS growth < 50 MB; `dropped_frames > 0`; 0 `INTERRUPT_REQUEST` dropped. | NIGHTLY |
| 2.7 | Platform guard | `pytest tests/ipc/test_transport.py -q` | Simulated `win32` → `UnsupportedPlatformError` whose message contains "WSL2". | PR |
| 2.8 | Contract drift | `pytest tests/ipc/test_event_contract.py -q` | All 8 event fixtures parse; adding a required field without updating fixtures fails the test (mutation check asserted). | PR |

**Phase 2 Gate:** contract test covers 100% of `EventType` members (asserted programmatically, not by eyeball).

---

## Phase 3: LLM Provider Abstraction & Streaming Adapters — **NEW IN V9**

**Objective:** Give the pipeline something to actually call. V7 and V8 both reference provider names in config and panels but never define a client, a streaming contract, a tokenizer, or an error mapping.
**Prerequisites:** 0.8, 0.9, 0.13, 2.2
**Lane:** B

### Phase 3 Execution Tasks

| Task ID | Task Description & Deliverable | Targeted Files / Components | Prereq | Est | Lane |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 3.1 | `LLMClient` Protocol: `complete()`, `stream()` yielding `TokenChunk`, `count_tokens()`; provider-neutral `Message`/`ToolCall`/`Usage` models. | `providers/base.py`, `contracts/llm.py` | 0.6 | 3.0 | B |
| 3.2 | Model registry: id → provider, context window, max output, pricing, tokenizer name. Sourced from config, not hardcoded. | `providers/registry.py` | 3.1, 0.8 | 2.5 | B |
| 3.3 | Provider error mapping: HTTP/transport faults → `RateLimitedError`, `ProviderOverloadedError`, `ContextOverflowError`, `AuthError`, `TransientError`, each with `retryable` and `retry_after`. | `providers/errors.py` | 3.1, 0.4 | 2.5 | B |
| 3.4 | Anthropic adapter: non-streaming + SSE streaming, usage extraction. | `providers/anthropic.py` | 3.3, 0.9 | 4.0 | B |
| 3.5 | OpenRouter adapter (OpenAI-compatible surface) with model-name namespacing. | `providers/openrouter.py` | 3.3 | 3.0 | B |
| 3.6 | Ollama adapter: `/api/chat` streaming, `num_ctx` and `keep_alive` passthrough, cold-start detection. | `providers/ollama.py` | 3.3 | 3.5 | B |
| 3.7 | **Model-swap thrash guard** (Qwen-7B-on-CPU specific): serialize requests that would force an Ollama model load; expose `model_loaded` state. | `providers/ollama_loader.py` | 3.6 | 3.0 | B |
| 3.8 | Tokenizer service: exact counts where a tokenizer exists, 4-chars/token + 15% margin fallback. | `providers/tokenizer.py` | 3.2 | 2.5 | B |
| 3.9 | Streaming → IPC bridge emitting `AGENT_TOKEN_STREAM` envelopes with sequence numbers. | `providers/stream_bridge.py` | 3.1, 2.2 | 2.5 | B |
| 3.10 | Provider integration suite driven by `FakeProviderServer` — no network in CI. | `tests/providers/test_adapters.py` | 3.4, 3.5, 3.6, 0.13 | 3.0 | B |

**Phase 3 total: 29.5h**

### Phase 3 Validation Matrix

| Task ID | Verification Strategy | Test Commands / Validation Script | Success Criteria (Pass/Fail) | Tier |
| :--- | :--- | :--- | :--- | :--- |
| 3.1 | Protocol conformance | `pytest tests/providers/test_base.py -q` | All three adapters satisfy `isinstance(x, LLMClient)` via `runtime_checkable`; mypy reports 0 protocol violations. | PR |
| 3.2 | Registry integrity | `pytest tests/providers/test_registry.py -q` | Every configured model resolves a context window and pricing; unknown model → `UnknownModelError`; `max_output < context_window` asserted for all entries. | PR |
| 3.3 | Fault mapping table | `pytest tests/providers/test_errors.py -q` | 429→`RateLimitedError(retryable=True, retry_after=30)`; 529→`ProviderOverloadedError`; 401→`AuthError(retryable=False)`; connection reset→`TransientError`. | PR |
| 3.4 | Anthropic adapter vs fake | `pytest tests/providers/test_anthropic.py -q` | Streamed chunks reassemble byte-identically to the scripted completion; `Usage.input_tokens` equals fake-server accounting. | PR |
| 3.5 | OpenRouter adapter | `pytest tests/providers/test_openrouter.py -q` | Model name `anthropic/claude-x` routes with namespace preserved; reassembly byte-identical. | PR |
| 3.6 | Ollama adapter | `pytest tests/providers/test_ollama.py -q` | `num_ctx` and `keep_alive` present in the captured request body; cold-start (>5s TTFB) surfaces `model_loading=True` rather than a timeout error. | PR |
| 3.7 | Thrash guard | `pytest tests/providers/test_ollama_loader.py -q` | 6 interleaved requests across 2 models produce ≤2 load events (asserted on fake-server load counter), never concurrent loads. | PR |
| 3.8 | Estimator bounds | `pytest tests/providers/test_tokenizer.py -q` | Estimate ≥ actual for all 20 corpus samples (0 underestimates); overhead ≤30%. | PR |
| 3.9 | Stream bridge ordering | `pytest tests/providers/test_stream_bridge.py -q` | 1,000 chunks arrive with strictly increasing `seq`, 0 gaps; terminal envelope carries final `Usage`. | PR |
| 3.10 | Offline guarantee | `pytest tests/providers -q --disable-socket --allow-unix-socket` | Entire suite passes with outbound TCP disabled — proving zero live-network dependence. | PR |

**Phase 3 Gate:** all three adapters pass the same shared conformance suite; suite runs with sockets disabled.

---

## Phase 4: Rate-Limit Broker & Cost Governor

**Objective:** Prevent 429s, prevent local memory saturation, and prevent runaway spend — the last of which neither V7 nor V8 addressed at all despite displaying "token burn rate".
**Prerequisites:** 3.2, 3.3, 3.8, 2.3, 2.4
**Lane:** B

### Phase 4 Execution Tasks

| Task ID | Task Description & Deliverable | Targeted Files / Components | Prereq | Est | Lane |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 4.1 | Token bucket on `time.monotonic()`, fractional refill, thread-safe acquire with timeout. | `broker/bucket.py` | 0.12 | 3.0 | B |
| 4.2 | Provider policy registry (RPM/TPM/max_concurrency per provider). | `broker/policies.py` | 4.1, 0.8 | 2.0 | B |
| 4.3 | Reservation protocol `reserve → commit(actual) \| release`, 120s TTL so a crashed client cannot leak capacity. | `broker/reservation.py` | 4.2, 3.8 | 3.5 | B |
| 4.4 | Local-inference limiter: `ollama` governed by concurrency semaphore + queue depth, not RPM/TPM. | `broker/local_limiter.py` | 4.2 | 2.5 | B |
| 4.5 | Backoff `min(max, base*2^n) + U(0,jitter)`, honoring `Retry-After`. | `broker/backoff.py` | 4.1, 3.3 | 2.0 | B |
| 4.6 | **Cost governor**: accumulate USD from `Usage` × registry pricing; per-run and per-day ceilings. | `broker/cost.py` | 4.3, 3.2 | 3.5 | B |
| 4.7 | **Budget kill-switch**: on ceiling breach, emit `INTERRUPT_REQUEST{command=STOP, reason=BUDGET}` and refuse further reservations. | `broker/kill_switch.py` | 4.6, 0.6 | 2.5 | B |
| 4.8 | `dev-harness-broker` daemon: single-instance lock, IPC endpoint, `HEALTH`, graceful drain. | `broker/daemon.py` | 4.3, 4.4, 2.3 | 4.0 | B |
| 4.9 | Broker client SDK, fail-closed by default; `allow_unbrokered=true` is the only escape hatch. | `broker/client.py` | 4.8, 2.4 | 2.5 | B |
| 4.10 | Metrics feed publishing p50/p95 latency, TPM burn, and cumulative USD via `METRICS_UPDATE`. | `broker/metrics_feed.py` | 4.8, 4.6 | 2.5 | B |

**Phase 4 total: 28h**

### Phase 4 Validation Matrix

| Task ID | Verification Strategy | Test Commands / Validation Script | Success Criteria (Pass/Fail) | Tier |
| :--- | :--- | :--- | :--- | :--- |
| 4.1 | Frozen-clock determinism | `pytest tests/broker/test_bucket.py -q` | 50-capacity bucket grants exactly 50 in window 1, 0 until refill; no drift over 10 simulated minutes. | PR |
| 4.2 | Policy loading | `pytest tests/broker/test_policies.py -q` | Unknown provider → `UnknownProviderError`; `ollama` policy exposes `max_concurrency` and no `rpm`. | PR |
| 4.3 | Leak prevention | `pytest tests/broker/test_reservation.py -q` | Client killed post-`reserve` releases at TTL+1s; `commit(actual<reserved)` returns the delta to the bucket (asserted on bucket level). | PR |
| 4.4 | Local saturation | `pytest tests/broker/test_local_limiter.py -q` | `max_concurrency=2`: max in-flight == 2 at every 10ms sample across 10 calls. | PR |
| 4.5 | Backoff shape | `pytest tests/broker/test_backoff.py -q` | Attempts 0–5 non-decreasing pre-jitter, capped at `max_backoff`; `Retry-After: 30` overrides computed value. | PR |
| 4.6 | Cost arithmetic | `pytest tests/broker/test_cost.py -q` | 1M in + 1M out at configured rates equals the expected USD to 4 decimals; cost accumulates across threads without loss (100 concurrent commits). | PR |
| 4.7 | Kill-switch | `pytest tests/broker/test_kill_switch.py -q` | Breaching a $0.50 run ceiling emits exactly one STOP with `reason=BUDGET`; the next `reserve()` raises `BudgetExceededError`; no further provider requests observed by the fake server. | PR |
| 4.8 | Daemon lifecycle | `pytest tests/broker/test_daemon.py -q` | Second instance exits 3 `AlreadyRunning`; `HEALTH` < 50ms; SIGTERM drains ≤5s with 0 orphaned reservations. | PR |
| 4.9 | Ceiling adherence under load | `pytest tests/broker/test_rate_limiter_load.py -q` | 100 concurrent reservations vs 50 RPM: grants in any rolling 60s window ≤50; 0 unhandled exceptions; broker down → `BrokerUnavailableError`, never silent passthrough. | NIGHTLY |
| 4.10 | Metrics emission | `pytest tests/broker/test_metrics_feed.py -q` | `METRICS_UPDATE` carries numeric p95 and cumulative USD within 1s of a simulated batch. | PR |

**Phase 4 Gate:** a scripted runaway-loop scenario terminates on budget rather than on human intervention.

---

## Phase 5: Execution Engine Daemon & Session Lifecycle — **NEW IN V9**

**Objective:** Build the process that V7 assumed and V8 forgot — the thing that hosts the graph, owns the socket, and that the TUI attaches to. Without this, no task in V8 produced a runnable system.
**Prerequisites:** 1.11, 2.5, 2.6, 4.9
**Lane:** D

### Phase 5 Execution Tasks

| Task ID | Task Description & Deliverable | Targeted Files / Components | Prereq | Est | Lane |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 5.1 | `EngineDaemon` process skeleton: binds the workspace socket, owns the asyncio loop, installs signal handlers. | `engine/daemon.py` | 2.3 | 3.5 | D |
| 5.2 | Session manager: `project_id`/`thread_id` generation and registry; one active session per workspace. | `engine/session.py` | 5.1, 1.11 | 3.0 | D |
| 5.3 | Command surface: `START_SESSION`, `ATTACH`, `DETACH`, `STATUS`, `SHUTDOWN` over IPC. | `engine/commands.py` | 5.2, 2.5 | 3.0 | D |
| 5.4 | Multi-client attach: N TUI clients receive the same event stream; detach of one does not disturb others. | `engine/fanout.py` | 5.3, 2.6 | 3.0 | D |
| 5.5 | Engine↔broker wiring: all provider calls routed through the broker client. | `engine/provider_gateway.py` | 5.1, 4.9, 3.1 | 2.5 | D |
| 5.6 | Daemon autostart from CLI (spawn detached if no live socket) with startup handshake and version check. | `engine/bootstrap.py` | 5.3 | 3.0 | D |
| 5.7 | Graceful shutdown: drain in-flight tasks, seal a checkpoint, unlink socket. | `engine/shutdown.py` | 5.2, 1.11 | 2.5 | D |
| 5.8 | Engine-side `is_paused` state broadcast so late-attaching clients render correct state. | `engine/state_broadcast.py` | 5.4 | 2.0 | D |

**Phase 5 total: 22.5h**

### Phase 5 Validation Matrix

| Task ID | Verification Strategy | Test Commands / Validation Script | Success Criteria (Pass/Fail) | Tier |
| :--- | :--- | :--- | :--- | :--- |
| 5.1 | Boot + bind | `pytest tests/engine/test_daemon.py -q` | Daemon binds the derived socket within 2s; SIGTERM exits 0; socket file removed on exit. | PR |
| 5.2 | Session uniqueness | `pytest tests/engine/test_session.py -q` | Second `START_SESSION` in the same workspace returns `SessionExistsError` with the existing `thread_id`; ids are ULID-sortable and unique across 1,000 generations. | PR |
| 5.3 | Command surface | `pytest tests/engine/test_commands.py -q` | Each of the 5 commands returns a typed response; unknown command → `UnknownCommandError`, connection stays open. | PR |
| 5.4 | Multi-client fan-out | `pytest tests/engine/test_fanout.py -q` | 3 attached clients each receive all 100 events in order; killing client 2 mid-stream leaves 1 and 3 with 0 gaps. | PR |
| 5.5 | Gateway enforcement | `pytest tests/engine/test_provider_gateway.py -q` | AST/monkeypatch assertion: 0 direct adapter calls bypass the broker; with broker down, the node raises rather than calling the provider. | PR |
| 5.6 | Autostart handshake | `pytest tests/engine/test_bootstrap.py -q` | Cold CLI start spawns a daemon and completes handshake < 3s; version mismatch → `EngineVersionMismatch`, no silent attach. | PR |
| 5.7 | Shutdown integrity | `pytest tests/engine/test_shutdown.py -q` | In-flight task drained; final checkpoint `is_paused=True`; `pgrep -g {pgid}` empty; socket unlinked. | PR |
| 5.8 | Late attach | `pytest tests/engine/test_state_broadcast.py -q` | A client attaching to a paused session receives `ExecutionState.PAUSED` in its first snapshot frame, before any delta events. | PR |

**Phase 5 Gate:** `dev-harness --workspace X --self-check` from a cold machine starts broker + engine and reports both healthy.

---

## Phase 6: Critic Gatekeeper & Asynchronous Interrupt Engine

**Objective:** Sub-second, leak-free interruption of agent tasks and their subprocess trees, with a sealed checkpoint on every pause.
**Prerequisites:** 5.3, 5.7, 1.11, 0.3
**Lane:** D

### Phase 6 Execution Tasks

| Task ID | Task Description & Deliverable | Targeted Files / Components | Prereq | Est | Lane |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 6.1 | `CriticGatekeeper` with an explicit legal-transition table; illegal transitions raise `IllegalTransitionError`. | `core/critic.py` | 0.3 | 3.0 | D |
| 6.2 | Idempotent command handler (PAUSE while PAUSED → `INTERRUPT_ACK{already:true}`). | `core/critic_commands.py` | 6.1, 5.3 | 2.5 | D |
| 6.3 | Task registry per `thread_id`; `cancel_all()` with 1s join. | `core/task_registry.py` | 6.1 | 3.0 | D |
| 6.4 | Subprocess group manager: `start_new_session=True`, PGID registry per thread. | `core/process_group.py` | 6.3 | 3.0 | D |
| 6.5 | Escalation `killpg(SIGINT)` → 3.0s → `killpg(SIGKILL)`, with `waitpid` reaping. | `core/signals.py` | 6.4 | 3.0 | D |
| 6.6 | Pause seal: checkpoint with `is_paused`, `last_interrupt_timestamp`, bound `git_commit_hash`. | `core/pause_seal.py` | 6.2, 1.11 | 2.5 | D |
| 6.7 | Interrupt latency histogram exported on `METRICS_UPDATE`. | `core/metrics.py` | 6.5, 4.10 | 2.0 | D |

**Phase 6 total: 19h**

### Phase 6 Validation Matrix

| Task ID | Verification Strategy | Test Commands / Validation Script | Success Criteria (Pass/Fail) | Tier |
| :--- | :--- | :--- | :--- | :--- |
| 6.1 | Exhaustive transition table | `pytest tests/core/test_critic_state.py -q` | All 16 (state, command) pairs asserted; `RESUME` from `STOPPED` → `IllegalTransitionError`; no undefined resulting state. | PR |
| 6.2 | Idempotency | `pytest tests/core/test_critic_commands.py -q` | Two PAUSEs → one transition, two ACKs, second `already=True`. | PR |
| 6.3 | Cancellation | `pytest tests/core/test_task_registry.py -q` | 20 sleeping tasks all `cancelled()`; `asyncio.all_tasks()` afterwards contains only the test task. | PR |
| 6.4 | Group creation | `pytest tests/core/test_process_group.py -q` | `os.getpgid(child) != os.getpgid(0)`; exactly one PGID per runner. | PR |
| 6.5 | Orphan elimination | `pytest tests/core/test_signals.py -q` | Runner with 3 grandchildren ignoring SIGINT: `pgrep -g {pgid}` empty within 4.0s; `psutil` reports 0 zombies. | PR |
| 6.6 | Seal correctness | `pytest tests/core/test_pause_seal.py -q` | Latest checkpoint `is_paused=True`; timestamp within 1s of request; hash == `git rev-parse HEAD`. | PR |
| 6.7 | Sub-second SLO | `pytest tests/core/test_interrupt_latency.py -q -m timing` | 50 trials: p95 < 500ms, max < 1000ms; results at `reports/interrupt_latency.json`. Marked `timing` — non-blocking on PR, blocking on the phase gate. | NIGHTLY |

**Phase 6 Gate:** latency SLO met on 3 consecutive nightly runs (guards against a single lucky run); orphan test green on both Linux and macOS runners.

---

## Phase 7: Hermes TUI Core Subsystem

**Objective:** Render the four-panel dashboard against a live engine with a 20 Hz throttle, bounded memory, and no event-loop blocking.
**Prerequisites:** 5.4, 5.8, 2.6, 0.3
**Lane:** C

### Phase 7 Execution Tasks

| Task ID | Task Description & Deliverable | Targeted Files / Components | Prereq | Est | Lane |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 7.1 | `HermesApp` shell + CSS grid declaring four regions. | `tui/app.py`, `tui/app.tcss` | 5.4 | 3.0 | C |
| 7.2 | `#repo-manager`: `DirectoryTree` + `DataTable` bound to `FILE_CHANGE`/`GIT_STATUS_UPDATE`; branch, diff count, path, session list. | `tui/panels/repo_manager.py` | 7.1, 1.9 | 3.5 | C |
| 7.3 | `#execution-canvas`: `RichLog` + `Sparkline` consuming `AGENT_TOKEN_STREAM`/`TEST_PROGRESS`. | `tui/panels/execution_canvas.py` | 7.1 | 3.5 | C |
| 7.4 | **Scrollback cap** (absent in V8): `RichLog(max_lines=N)` + spill of older lines to the run artifact file, so a long run cannot exhaust memory. | `tui/scrollback.py` | 7.3, 0.15 | 2.5 | C |
| 7.5 | `#model-registry`: `OptionList` + `Static` rendering provider, p50/p95, TPM burn, cumulative USD from `METRICS_UPDATE`. | `tui/panels/model_registry.py` | 7.1, 4.10 | 3.0 | C |
| 7.6 | `#critic-bar`: `Input`, PAUSE/RESUME/STOP buttons, HITL Approve/Reject; publishes `INTERRUPT_REQUEST`. | `tui/panels/critic_bar.py` | 7.1, 0.3 | 3.0 | C |
| 7.7 | IPC→UI bridge marshalling via `App.call_from_thread`/`post_message`. | `tui/bridge.py` | 7.1, 2.6 | 3.0 | C |
| 7.8 | 20 Hz coalescing throttle flushing an accumulated buffer as one write. | `tui/throttle.py` | 7.7 | 2.5 | C |
| 7.9 | Keybindings: `Ctrl+C` priority-bound to PAUSE (Textual defaults it to quit), quit moved to `Ctrl+Q` with confirm modal. | `tui/bindings.py` | 7.6 | 2.0 | C |
| 7.10 | Safe renderer: Markdown + unified-diff colorizer escaping Rich markup in model output. | `tui/render.py` | 7.3 | 3.0 | C |
| 7.11 | CLI entrypoint `dev-harness [--workspace] [--self-check]` wiring config, bootstrap, attach, app. | `cli.py`, `[project.scripts]` | 7.1, 5.6 | 3.0 | C |

**Phase 7 total: 32h**

### Phase 7 Validation Matrix

| Task ID | Verification Strategy | Test Commands / Validation Script | Success Criteria (Pass/Fail) | Tier |
| :--- | :--- | :--- | :--- | :--- |
| 7.1 | Headless mount + snapshot | `pytest tests/tui/test_layout.py -q` | All four panel IDs resolve under `app.run_test()`; 100×30 render matches committed snapshot. | PR |
| 7.2 | Event-driven update | `pytest tests/tui/test_repo_manager.py -q` | `GIT_STATUS_UPDATE{branch:"feat/x",dirty:3}` updates cells to `feat/x` and `3` within 100ms. | PR |
| 7.3 | Stream fidelity | `pytest tests/tui/test_execution_canvas.py -q` | 500 token events reassemble exactly; Sparkline length == `TEST_PROGRESS` count. | PR |
| 7.4 | Memory bound | `pytest tests/tui/test_scrollback.py -q -m slow` | 200k lines streamed: RichLog holds ≤ `max_lines`; process RSS growth < 100 MB; spilled lines retrievable from the artifact file in original order. | NIGHTLY |
| 7.5 | Metrics render | `pytest tests/tui/test_model_registry.py -q` | No feed → `latency: —`; with feed → p95 to 1 decimal and USD to 4 decimals. | PR |
| 7.6 | Command publication | `pytest tests/tui/test_critic_bar.py -q` | `#btn-pause` emits exactly one `INTERRUPT_REQUEST` with `command == PAUSE`. | PR |
| 7.7 | Thread safety | `pytest tests/tui/test_bridge.py -q` | 5,000 cross-thread events: 0 `NoActiveAppError`, 0 dropped control events. | PR |
| 7.8 | Throttle rate | `pytest tests/tui/test_throttle.py -q -m timing` | 2s flood of 10k tokens: `RichLog.write` calls ≤44; max event-loop iteration latency < 50ms. | NIGHTLY |
| 7.9 | Keybinding override | `pytest tests/tui/test_bindings.py -q` | `pilot.press("ctrl+c")` leaves `app.is_running` True and emits PAUSE; `ctrl+q` opens confirm modal. | PR |
| 7.10 | Markup injection | `pytest tests/tui/test_render.py -q` | Output containing `[bold red]` renders literally; no `MarkupError`. | PR |
| 7.11 | CLI smoke | `dev-harness --workspace ./tmp/ws --self-check` | Exit 0; prints socket, DB, broker and engine health; non-git dir exits 2 `NotAGitRepository`. | PR |

**Phase 7 Gate:** a human can attach to a live engine session, observe streaming tokens, and pause it from the UI.

---

## Phase 8: SDLC Pipeline Engine & Parallel Worker Pool

**Objective:** Assemble the Groomer → Architect → Developer → Tester → Critic graph with bounded retries, HITL gates, context budgeting, and genuine parallel chunk execution over isolated worktrees.
**Prerequisites:** 1.5, 1.10, 5.5, 6.2, 3.1, 0.5
**Lane:** D

### Phase 8 Execution Tasks

| Task ID | Task Description & Deliverable | Targeted Files / Components | Prereq | Est | Lane |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 8.1 | Persona templates for the five roles, each with an explicit output contract and refusal behavior. | `engine/personas/*.md` | 0.5 | 3.0 | D |
| 8.2 | Persona output validators (parse + schema-check each role's response; retry on malformed output). | `engine/personas/validators.py` | 8.1, 0.5 | 3.0 | D |
| 8.3 | LangGraph channels and reducers (append-only `chunk_dag`, LWW `tui_state`, counter reducers for retries). | `engine/state.py` | 0.5, 1.5 | 3.0 | D |
| 8.4 | Groomer node → `groomed_requirements{status=LOCKED}`. | `engine/nodes/groomer.py` | 8.2, 8.3 | 3.0 | D |
| 8.5 | Architect node → `technical_design` with `openapi_spec` and `db_schema`. | `engine/nodes/architect.py` | 8.4 | 3.5 | D |
| 8.6 | Chunk DAG builder + topological validator (cycle + orphan-dependency detection). | `engine/dag.py` | 8.5 | 3.0 | D |
| 8.7 | **Worker pool** honoring DAG readiness and `max_parallel_workers`; assigns `assigned_worker_id`. | `engine/worker_pool.py` | 8.6, 1.10 | 4.0 | D |
| 8.8 | **Per-worker worktree binding**: each worker executes in `.dev-harness/worktrees/{worker_id}` on `chunk/{chunk_id}` (V8 had parallel workers writing one tree — a correctness bug, not a style issue). | `engine/worker_workspace.py` | 8.7, 1.10 | 3.5 | D |
| 8.9 | **Merge/integration step**: sequential fast-forward or rebase of completed chunk branches with conflict surfacing. | `engine/integrator.py` | 8.8 | 4.0 | D |
| 8.10 | Developer node with a workspace-root write guard rejecting paths outside its worktree. | `engine/nodes/developer.py` | 8.8, 6.4 | 3.5 | D |
| 8.11 | Tester node (invocation, structured pass/fail, timeout). | `engine/nodes/tester.py` | 8.10 | 3.0 | D |
| 8.12 | Differential test selector with documented full-suite fallback. | `engine/testing/differential.py` | 8.11 | 3.5 | D |
| 8.13 | Retry router: inner-loop ceiling 3 → Architect; e2e ceiling 2 → HITL. | `engine/routing.py` | 8.12 | 3.0 | D |
| 8.14 | Critic node as strict binary gate; writes only `critic_gatekeeper_status`/`is_paused`, else `CriticScopeViolation`. | `engine/nodes/critic.py` | 8.3, 6.2 | 3.0 | D |
| 8.15 | HITL gate via `interrupt_before` + `Command(resume=...)` from `#critic-bar`. | `engine/hitl.py` | 8.14, 7.6 | 3.5 | D |
| 8.16 | Context budgeting: traces capped at 50 lines (head 30 / tail 20) + total prompt budget from the model's context window. | `engine/context.py` | 3.8, 3.2 | 3.0 | D |
| 8.17 | E2E failure classifier: `CHUNK_IMPLEMENTATION_BUG` → Developer, `INTEGRATION_SPEC_MISMATCH` → Architect, unparseable → HITL. | `engine/classifier.py` | 8.13 | 2.5 | D |
| 8.18 | Graph assembly `build_graph(config)` compiled with `SqliteSaver`. | `engine/pipeline.py` | 8.4–8.17 | 3.0 | D |

**Phase 8 total: 58h**

### Phase 8 Validation Matrix

| Task ID | Verification Strategy | Test Commands / Validation Script | Success Criteria (Pass/Fail) | Tier |
| :--- | :--- | :--- | :--- | :--- |
| 8.1 | Contract lint | `pytest tests/engine/test_personas.py -q` | Each file declares `## Output Contract`; none permits the Critic to modify code (keyword scan asserted). | PR |
| 8.2 | Malformed-output handling | `pytest tests/engine/test_persona_validators.py -q` | Truncated JSON triggers exactly one repair retry, then `PersonaOutputError`; valid output passes first time with 0 retries. | PR |
| 8.3 | Reducer semantics | `pytest tests/engine/test_state_reducers.py -q` | Two parallel `chunk_dag` appends → 2 entries (no clobber); concurrent retry increments → exactly +2. | PR |
| 8.4 | Groomer contract | `pytest tests/engine/test_groomer.py -q` | Output validates as `GroomedRequirements`; `status=="LOCKED"`; re-invocation on locked input is a no-op. | PR |
| 8.5 | Architect contract | `pytest tests/engine/test_architect.py -q` | `openapi_spec` parses as valid YAML/JSON; empty design leaves `status != "APPROVED"`. | PR |
| 8.6 | DAG validation | `pytest tests/engine/test_dag.py -q` | Cyclic fixture → `CyclicDependencyError` naming both ids; unknown dep → `OrphanDependencyError`; topological order stable across 10 runs. | PR |
| 8.7 | Scheduling correctness | `pytest tests/engine/test_worker_pool.py -q` | With `max_parallel=3` over a 7-chunk diamond DAG: no chunk starts before its deps complete; peak concurrency ≤3; all 7 reach `COMPLETED`. | PR |
| 8.8 | Worktree isolation | `pytest tests/engine/test_worker_workspace.py -q` | 3 workers writing the same relative path produce 3 distinct file contents in 3 worktrees; primary worktree `git status --porcelain` is empty throughout. | PR |
| 8.9 | Integration merge | `pytest tests/engine/test_integrator.py -q` | Non-overlapping chunk branches merge clean; a deliberate overlapping edit raises `IntegrationConflict` naming the file and both chunk ids, leaving the primary branch unmodified. | PR |
| 8.10 | Escape guard | `pytest tests/engine/test_developer.py -q` | Write to `../../etc/passwd` → `WorkspaceEscapeError`; symlink escape also blocked; legitimate writes land inside the worker's worktree only. | PR |
| 8.11 | Tester behavior | `pytest tests/engine/test_tester.py -q` | Passing suite → `passed=True` with counts; a hanging test is killed at the timeout and reported `TIMEOUT`, not `FAILED`. | PR |
| 8.12 | Differential selection | `pytest tests/engine/test_differential.py -q` | Changing one module selects exactly its dependent test set (set equality vs fixture); unavailable graph triggers full-suite fallback and logs the reason. | PR |
| 8.13 | Retry ceilings | `pytest tests/engine/test_routing.py -q` | Forced failures route to Architect on attempt 4; terminate `FAILED` after the e2e ceiling; no infinite loop under `--timeout=120`. | PR |
| 8.14 | Zero-drift | `pytest tests/engine/test_critic_scope.py -q` | Critic writing `groomed_requirements` → `CriticScopeViolation`; full state diff over PAUSE/RESUME confined to `tui_state`. | PR |
| 8.15 | HITL round trip | `pytest tests/engine/test_hitl.py -q` | Graph halts at approval with a persisted checkpoint; simulated button resume advances exactly one node. | PR |
| 8.16 | Budgeting | `pytest tests/engine/test_context.py -q` | 500-line trace → 50 lines containing first and last frames; total prompt tokens ≤ context_window − max_output for every model in the registry. | PR |
| 8.17 | Classifier routing | `pytest tests/engine/test_classifier.py -q` | 10 labelled fixtures route with 100% accuracy; unparseable report routes to HITL, never guesses. | PR |
| 8.18 | Graph E2E (mocked LLM) | `pytest tests/engine/test_sdlc_pipeline.py -q` | Requirement → green unit test; final checkpoint validates against `harness_state.v7.json`; `chunk_dag[0].status=="COMPLETED"`; 0 live network calls. | PR |

**Phase 8 Gate:** a 7-chunk DAG executes 3-wide in parallel, merges clean, and its final state validates against the V7 schema.

---

## Phase 9: Error Handling, Recovery & Edge Cases

**Objective:** Every failure mode named in the audit becomes observable, recoverable, and covered by a negative test.
**Prerequisites:** Phases 1–8
**Lane:** A/D

### Phase 9 Execution Tasks

| Task ID | Task Description & Deliverable | Targeted Files / Components | Prereq | Est | Lane |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 9.1 | Crash recovery: detect an un-finalized session on startup and offer resume-from-checkpoint. | `recovery/session_recovery.py` | 8.18, 1.12 | 3.5 | D |
| 9.2 | Stale artifact reclamation for orphaned sockets, locks and worktrees after an unclean exit. | `recovery/reclaim.py` | 2.3, 1.8, 1.10 | 3.0 | A |
| 9.3 | Provider fallback chain (`anthropic → openrouter → ollama`) with a `DEGRADED` notice on `#model-registry`. | `broker/fallback.py` | 4.9, 3.3 | 3.0 | B |
| 9.4 | Corrupt checkpoint detection via `state_sha256`; quarantine and roll back to the previous valid checkpoint. | `storage/integrity.py` | 1.4 | 3.0 | A |
| 9.5 | Terminal degradation: below 80×24 collapse to a single panel rather than crash. | `tui/responsive.py` | 7.1 | 2.5 | C |
| 9.6 | Git edge cases: detached HEAD, unborn branch, submodules, pre-existing worktrees. | `vcs/edge_cases.py` | 1.12, 1.10 | 3.0 | A |
| 9.7 | Canvas-level secret redaction (streaming output, not just logs). | `tui/render.py`, `observability/redact.py` | 0.14, 7.10 | 2.0 | C |
| 9.8 | Resource exhaustion: `ENOSPC` and `SQLITE_BUSY` surface an actionable banner, not a traceback. | `storage/errors.py` | 1.1, 0.4 | 2.5 | A |
| 9.9 | **Context-overflow recovery**: `ContextOverflowError` triggers summarize-and-retry once before escalation. | `engine/overflow.py` | 8.16, 3.3 | 3.0 | D |
| 9.10 | **Phase rollback procedures**: documented revert path per phase (migration down, feature-flag off, tag rollback). | `docs/rollback.md` | — | 2.0 | A |

**Phase 9 total: 27.5h**

### Phase 9 Validation Matrix

| Task ID | Verification Strategy | Test Commands / Validation Script | Success Criteria (Pass/Fail) | Tier |
| :--- | :--- | :--- | :--- | :--- |
| 9.1 | Kill-and-resume | `pytest tests/recovery/test_session_recovery.py -q` | SIGKILL mid-run then restart: state equals last sealed checkpoint field-for-field; worktree SHA matches `git_commit_hash`. | NIGHTLY |
| 9.2 | Reclamation | `pytest tests/recovery/test_reclaim.py -q` | Stale socket, lock and worktree from a dead PID removed; fresh session starts within 2s; a live PID's artifacts untouched. | PR |
| 9.3 | Fallback chain | `pytest tests/broker/test_fallback.py -q` | 529 on primary routes to secondary within 1 retry; registry shows `DEGRADED`; exhausted chain → `AllProvidersUnavailable`. | PR |
| 9.4 | Corruption | `pytest tests/storage/test_integrity.py -q` | Byte-flipped `state_json` fails the digest check, moves to `checkpoints_quarantine`, and `get_tuple()` returns the prior valid checkpoint. | PR |
| 9.5 | Small terminal | `pytest tests/tui/test_responsive.py -q` | `run_test(size=(60,20))` mounts without exception; only `#execution-canvas` visible. | PR |
| 9.6 | Git edges | `pytest tests/vcs/test_edge_cases.py -q` | Unborn branch → `NoCommitsError` (not `CalledProcessError`); detached HEAD restore succeeds and reports detached; pre-existing worktree name → `WorktreeExistsError`. | PR |
| 9.7 | Canvas redaction | `pytest tests/tui/test_canvas_redaction.py -q` | Streamed key renders redacted; raw key absent from widget buffer, log files and run artifacts. | PR |
| 9.8 | Exhaustion | `pytest tests/storage/test_errors.py -q` | Simulated `ENOSPC` and `SQLITE_BUSY` each yield a `HarnessError` with remediation; 0 unhandled tracebacks reach the TUI. | PR |
| 9.9 | Overflow recovery | `pytest tests/engine/test_overflow.py -q` | Oversized context triggers exactly one summarize-retry; second overflow escalates to HITL; 0 infinite retry loops. | PR |
| 9.10 | Rollback rehearsal | Manual: execute `docs/rollback.md` for Phase 1 on a scratch clone | Migration `0001` rolls back cleanly; app starts on the prior tag; documented time-to-rollback < 15 min. | REL |

**Phase 9 Gate:** every `HarnessError` subclass has at least one test that produces it through real system behavior, not by direct construction.

---

## Phase 10: Final Verification, Traceability & Release

**Objective:** Prove the assembled system against the TDD and gate release on measurable thresholds.
**Prerequisites:** Phases 0–9 green
**Lane:** All

### Phase 10 Execution Tasks

| Task ID | Task Description & Deliverable | Targeted Files / Components | Prereq | Est | Lane |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 10.1 | E2E: requirement → groomed → designed → chunked → implemented → tested, with a scripted mid-run PAUSE/RESUME. | `tests/e2e/test_full_sdlc.py` | 9.* | 4.0 | D |
| 10.2 | Parallel E2E: 3-wide worker pool through integration merge. | `tests/e2e/test_parallel_sdlc.py` | 10.1 | 3.5 | D |
| 10.3 | Two-project 30-minute concurrent soak on a shared broker. | `tests/e2e/test_concurrent_soak.py` | 10.1 | 3.0 | A |
| 10.4 | **Local-only profile run** (Ollama + Qwen 2.5 Coder, no hosted providers) as a first-class supported configuration. | `tests/e2e/test_local_profile.py`, `profiles/local.toml` | 10.1, 3.7 | 3.5 | B |
| 10.5 | CI tiering: PR suite < 10 min; nightly runs `timing`/`slow`/soak. | `.github/workflows/ci.yml`, `.github/workflows/nightly.yml` | 0.2 | 3.0 | A |
| 10.6 | Schema conformance regression over every checkpoint emitted during E2E. | `tests/e2e/test_schema_conformance.py` | 10.1 | 2.0 | A |
| 10.7 | Traceability generator mapping TDD sections → task IDs → test files, failing on gaps. | `scripts/generate_traceability.py`, `docs/traceability.md` | 10.1 | 3.0 | A |
| 10.8 | Packaging, README, operator runbook (start/stop broker and engine, recover a wedged session, roll back). | `README.md`, `docs/runbook.md` | 10.1, 9.10 | 3.5 | A |

**Phase 10 total: 25.5h**

### Phase 10 Validation Matrix

| Task ID | Verification Strategy | Test Commands / Validation Script | Success Criteria (Pass/Fail) | Tier |
| :--- | :--- | :--- | :--- | :--- |
| 10.1 | Full-system E2E | `pytest tests/e2e/test_full_sdlc.py -q --timeout=900` | Completes; PAUSE seals `is_paused=True`; RESUME continues from that checkpoint; final chunk `COMPLETED`; `pgrep -g` empty. | NIGHTLY |
| 10.2 | Parallel E2E | `pytest tests/e2e/test_parallel_sdlc.py -q --timeout=1200` | 3 chunks execute concurrently in 3 worktrees; integration merge clean; primary branch contains all three chunks' changes; 0 lost writes. | NIGHTLY |
| 10.3 | Soak | `pytest tests/e2e/test_concurrent_soak.py -q --timeout=2400` | 0 `database is locked`; 0 cross-project rows; RSS growth < 15% over 30 min; broker grants within every provider ceiling. | NIGHTLY |
| 10.4 | Local profile | `pytest tests/e2e/test_local_profile.py -q -m slow` | Full SDLC completes against Ollama with 0 hosted-provider calls; `num_ctx` respected; ≤1 model load event; cumulative USD == 0.00. | NIGHTLY |
| 10.5 | CI budget | `time make ci` | PR suite wall clock < 10 min on the standard runner; nightly workflow schedules and reports separately. | PR |
| 10.6 | Schema regression | `pytest tests/e2e/test_schema_conformance.py -q` | 100% of emitted checkpoints validate; an added field fails until schema and golden fixture are updated together. | PR |
| 10.7 | Traceability completeness | `python scripts/generate_traceability.py --check` | Every TDD section 1–6 maps to ≥1 task and ≥1 passing test; exit 1 on any unmapped section. | REL |
| 10.8 | Runbook dry run | Manual on a clean machine following `docs/runbook.md` | New operator reaches a running four-panel TUI with live broker + engine in < 10 min without reading source. | REL |

**Phase 10 Gate (Release):** 10.1–10.4 green on 3 consecutive nightlies; traceability check exit 0; runbook validated by someone who did not write it.

---

## 11. Effort Model & Critical Path

| Phase | Hours | Lane | Earliest Start |
| :--- | :--- | :--- | :--- |
| P0 Scaffolding & Test Infra | 38.0 | A | Day 0 |
| P1 Persistence | 35.5 | A | after P0 |
| P2 IPC | 19.5 | A | after P0 (parallel with P1) |
| P3 Providers | 29.5 | B | after P0 (parallel with P1/P2) |
| P4 Broker & Cost | 28.0 | B | after P3 |
| P5 Engine Daemon | 22.5 | D | after P1, P2, P4 |
| P6 Critic | 19.0 | D | after P5 |
| P7 TUI | 32.0 | C | after P5 (parallel with P6) |
| P8 Pipeline & Workers | 58.0 | D | after P6, P7 |
| P9 Error Handling | 27.5 | A/B/C/D | after P8 |
| P10 Verification | 25.5 | All | after P9 |
| **Total** | **335.0** | | |

**Critical path:** P0 → P3 → P4 → P5 → P6 → P8 → P9 → P10 = **258h**. With three concurrent workers on lanes A/B/C, elapsed ≈ **258h**, i.e. parallelism buys roughly 23% against the 335h serial total — the chain through providers → broker → engine → pipeline dominates and cannot be shortened by adding workers.

**Scheduling consequence:** staffing more than 3 workers before P8 is waste. The fan-out point is P8, where the worker pool itself becomes parallel.

## 12. V9 Changelog — Defects Fixed From V8

| # | V8 Defect | Severity | V9 Fix |
| :--- | :--- | :--- | :--- |
| D1 | No LLM provider layer; pipeline nodes called nothing. System could not run. | Critical | New Phase 3 (10 tasks). |
| D2 | No execution-engine process; the TUI's IPC client had no server to attach to. | Critical | New Phase 5 (8 tasks). |
| D3 | ~20 tests presumed a `MockLLM`, frozen clock and workspace factory that no task built. | Critical | Tasks 0.12, 0.13. |
| D4 | Parallel chunk workers shared one git worktree — guaranteed lost writes. | Critical | Tasks 1.10, 8.7–8.9. |
| D5 | Token burn displayed but never costed; no spend ceiling on autonomous loops. | High | Tasks 4.6, 4.7. |
| D6 | No effort estimates, no lanes, no critical path — unschedulable. | High | §11 plus `Est`/`Lane` columns. |
| D7 | Timing tests (`p95`, throttle rate) were PR-blocking — guaranteed flaky CI. | High | `timing` marker + NIGHTLY tier (10.5). |
| D8 | Several tasks still >4h (saver interface, tester+differential, graph assembly). | Medium | Split into 1.4/1.5, 8.11/8.12, 8.4–8.18. |
| D9 | No phase-level Definition of Done or rollback path. | Medium | Phase Gates + task 9.10. |
| D10 | Checkpoints grew unbounded; `RichLog` scrollback unbounded. | Medium | Tasks 1.7, 7.4. |
| D11 | Secrets loading unspecified (only redaction covered). | Medium | Task 0.9. |
| D12 | No run transcript, so agent failures were undiagnosable post-hoc. | Medium | Task 0.15. |
| D13 | mypy strict scoped to `contracts/` only. | Medium | Task 0.2 widened to all of `src/`. |
| D14 | Malformed persona output had no handling path. | Medium | Task 8.2. |
| D15 | `ContextOverflowError` had no recovery, only truncation-at-write. | Medium | Task 9.9. |
| D16 | Ollama model-swap thrash on a 7B CPU host unaddressed. | Medium | Task 3.7. |
| D17 | Local-only operation was never an asserted configuration. | Medium | Task 10.4 + `profiles/local.toml`. |
| D18 | IPC producer/consumer could drift silently. | Low | Task 2.8. |

## 13. Residual Risks (Accepted, Not Fixed)

| # | Risk | Why Accepted | Trigger To Revisit |
| :--- | :--- | :--- | :--- |
| R1 | LLM-authored code executes on the host with only path and process-group guards; no container sandbox. | Containerization adds ~40h and a Docker dependency to a local dev tool. | First untrusted-requirement use case, or any multi-user deployment. |
| R2 | POSIX-only (`AF_UNIX`, `flock`, `killpg`). | WSL2 is a supported path; native Windows would fork the transport layer. | A Windows-native user requirement. |
| R3 | Differential test selection can miss transitively affected tests. | Full suite runs at the Phase 10 gate. | Any escaped defect traced to selection. |
| R4 | Single-writer assumption on the primary branch during integration. | Merge is sequential by design (8.9). | Multi-repo or multi-user harness use. |
| R5 | Qwen 2.5 Coder 7B may not produce architecture-grade output for the Architect persona. | Provider abstraction (P3) permits per-node model assignment. | Measured Architect failure rate > 30% in 10.4. |

## 14. Traceability: TDD Section → Implementing Tasks

| TDD § | Requirement | Tasks | Verifying Tests |
| :--- | :--- | :--- | :--- |
| §1 | System foundation, process boundaries | 0.1, 5.1, 5.6 | `test_daemon.py`, `test_bootstrap.py` |
| §2.1 | Async loop / worker separation | 7.1, 7.7, 5.4 | `test_layout.py`, `test_bridge.py`, `test_fanout.py` |
| §2.1 | 20 Hz throttle | 7.8 | `test_throttle.py` |
| §2.2 | Four-panel protocol (incl. `DataTable`, `Sparkline`) | 7.2–7.6 | panel test modules |
| §2.2 | Panel event subscriptions | 0.6, 2.5, 2.8 | `test_events.py`, `test_event_contract.py` |
| §2.2 | Latency + burn-rate display | 4.10, 7.5 | `test_metrics_feed.py`, `test_model_registry.py` |
| §3.1 | Interrupt dispatch, Ctrl+C | 7.9, 6.2 | `test_bindings.py`, `test_critic_commands.py` |
| §3.1 | Cancellation, SIGINT→SIGKILL | 6.3, 6.5 | `test_task_registry.py`, `test_signals.py` |
| §3.1 | Checkpoint seal on pause | 6.6 | `test_pause_seal.py` |
| §3.2 | Gatekeeper state machine | 6.1 | `test_critic_state.py` |
| §4.1 | WAL + synchronous=NORMAL | 1.1 | `test_connection.py` |
| §4.1 | `(project_id, thread_id)` namespacing | 1.3, 1.6 | `test_schema_ddl.py`, `test_guards.py` |
| §4.2 | Workspace-scoped sockets | 0.10, 2.3 | `test_paths.py`, `test_server.py` |
| §4.2 | Directory-scoped locks | 1.8 | `test_workspace_lock.py` |
| §4.2 | Git checkpoint + restore | 1.11, 1.12 | `test_checkpoint_binding.py`, `test_restore.py` |
| §5.1 | Token bucket, RPM/TPM | 4.1–4.3 | `test_bucket.py`, `test_rate_limiter_load.py` |
| §5.1 | Local inference saturation | 4.4, 3.7 | `test_local_limiter.py`, `test_ollama_loader.py` |
| §5.1 | Backoff + jitter | 4.5 | `test_backoff.py` |
| §6 | Unified V7 state schema | 0.5, 0.7, 8.3 | `test_state_model.py`, `test_schema.py` |
| §6 | `chunk_dag` + `assigned_worker_id` | 8.6, 8.7 | `test_dag.py`, `test_worker_pool.py` |
| §6 | Retry counters, e2e classification | 8.13, 8.17 | `test_routing.py`, `test_classifier.py` |
| §6 | `rate_limiting` state fields | 4.3, 4.10 | `test_reservation.py`, `test_metrics_feed.py` |
