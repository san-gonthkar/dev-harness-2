# Hermes TUI Dev Harness — Implementation Plan, Coverage Contract & Phase Acceptance Protocol (V10 — Final)

**Supersedes:** V9, V8, V7
**Traces to:** `Hermes TUI Dev Harness - Detailed Technical Design Specification (V7)`
**What V10 adds over V9:** a formal test strategy and per-package coverage contract (replacing V9's arbitrary 85/95% numbers), and an executable **Phase Acceptance Protocol** for every phase so a deliverable can be evaluated *inside its own scope*, with downstream dependencies explicitly stubbed, before the next phase is unblocked.

---

# Part I — Framework

## 1. Reading Protocol

- **Task sizing:** every task carries an `Est` in hours. Nothing exceeds 4h.
- **Lanes:** `A` platform, `B` model-plane, `C` interface, `D` orchestration. Different lanes with satisfied prerequisites run in parallel on different workers.
- **Prerequisites are hard gates.** No task starts until every prerequisite has a green validation row at its declared tier.
- **CI tier:** `PR` blocks merge; `NIGHTLY` blocks the phase gate; `REL` blocks release. Timing-sensitive tests are never `PR`-blocking.
- **Agent handoff:** branch `task/{id}-{slug}`, commit trailer `Task-Id: {id}`, PR body pastes the validation command output. Enforced by task 0.11.
- **Phase completion has three conditions, all required:** (1) every task's validation row green, (2) the phase Coverage Contract met, (3) the phase Acceptance Protocol executed end-to-end with a signed report. Tasks-done is not phase-done.

## 2. Test Strategy

### 2.1 Test Taxonomy

Every test declares exactly one marker. Unmarked tests fail collection (enforced by 0.18).

| Marker | Purpose | Determinism | Tier | Typical Runtime |
| :--- | :--- | :--- | :--- | :--- |
| `unit` | One module, all collaborators faked. | Total | PR | < 50ms |
| `property` | Hypothesis-driven invariants (codecs, reducers, buckets). | Seeded, reproducible | PR | < 2s |
| `contract` | Producer output parsed by the real consumer parser; schema/interface conformance. | Total | PR | < 200ms |
| `integration` | 2+ real subsystems, real SQLite, real sockets, fake providers. | Total | PR | < 5s |
| `negative` | Asserts the correct failure — error type, message, and system state after. | Total | PR | < 1s |
| `timing` | Latency/throughput SLOs. Machine-sensitive. | Statistical | NIGHTLY | varies |
| `slow` | Soaks, memory-growth, large-volume. | Total | NIGHTLY | > 60s |
| `e2e` | Whole system through the daemon. | Total (mock LLM) | NIGHTLY | > 120s |

**Rule:** a defect class must be covered by `negative` before the happy path is considered done. Every `HarnessError` subclass must be reachable by at least one `negative` test through real system behavior, never by direct construction.

### 2.2 Coverage Measurement

- Branch coverage is mandatory (`--cov-branch`). Line-only coverage is not accepted as evidence.
- Targets are **per package**, justified by blast radius, not uniform. A single global number lets high-risk modules hide behind well-covered trivial ones.
- **Ratchet:** a PR may not reduce any package's line or branch coverage by more than 0.5pp versus `main`. Enforced by 0.16.
- **Exclusion policy:** `# pragma: no cover` requires a trailing justification comment on the same line. CI greps for bare pragmas and fails. Total excluded statements are reported per PR.
- **Mutation testing** is required where coverage is known to be a weak proxy — code whose failure is silent (money, data loss, interrupt safety). Coverage says the line ran; mutation says the assertion mattered.

### 2.3 Coverage Ledger

| Package | Line | Branch | Mutation | Rationale |
| :--- | :--- | :--- | :--- | :--- |
| `contracts/` | 100% | 95% | — | Declarative and cheap to cover; every other package depends on it. No excuse for gaps. |
| `storage/` | 95% | 90% | ≥80% | Silent data loss or cross-project leakage is unrecoverable. |
| `vcs/` | 95% | 90% | ≥80% | `git checkout` and worktree removal destroy user work. |
| `broker/` | 95% | 90% | ≥80% | A rate limiter that passes tests but doesn't limit costs money and gets the account throttled. |
| `core/` | 95% | 90% | ≥85% | Interrupt safety. A missed `killpg` leaves runaway agents on the host. |
| `ipc/` | 92% | 85% | — | Framing bugs are loud; contract tests carry more weight than coverage here. |
| `providers/` | 90% | 85% | — | Adapters contain network edges that are meaningful only against real endpoints; those are excluded with justification and covered by 10.4. |
| `engine/` | 88% | 80% | — | Large surface, heavily integration-tested; unit coverage is a weak signal for graph wiring. |
| `recovery/` | 90% | 85% | — | Exercised mainly by `negative` and `e2e`; the tests matter more than the percentage. |
| `observability/` | 90% | 80% | — | Redaction paths must be exhaustively covered; formatting need not be. |
| `tui/` | 75% | 65% | — | Rendering is snapshot- and pilot-tested. Chasing line coverage in widget code produces tests that assert Textual's behavior, not ours. Deliberately the lowest bar in the ledger. |
| **Overall gate** | **88%** | **80%** | — | Derived from the weighted packages above, not chosen first. |

### 2.4 Anti-Patterns Rejected by Review

| Rejected | Why | Required Instead |
| :--- | :--- | :--- |
| `assert result is not None` | Passes for any wrong value. | Assert the exact value or a named invariant. |
| Tests that mock the unit under test's own internals | Asserts the implementation, not the behavior. | Fake only at process/IO boundaries. |
| `time.sleep()` in a non-`timing` test | Flake source and slow suite. | Frozen clock fixture (0.12) or event-driven waits. |
| Live network calls in any tier below `REL` | Non-deterministic CI, cost, rate limits. | `FakeProviderServer` (0.13); `--disable-socket` enforced. |
| Snapshot tests with no reviewed baseline | Locks in whatever was rendered, including bugs. | Baseline reviewed in the PR that introduces it. |
| Coverage achieved by importing modules in a smoke test | Executes lines, asserts nothing. | Mutation gate on the packages where this is tempting. |

## 3. Phase Evaluation Framework

### 3.1 What a Phase Acceptance Protocol Is

Every phase below defines a **Phase Acceptance Protocol**: an ordered, executable sequence that proves the phase's deliverable works *within its own scope* — using only that phase and its prerequisites, with every downstream dependency replaced by a named permitted stub. It answers "can we hand this off?", which the per-task validation matrix does not.

Each protocol is backed by a checked-in script `scripts/verify_phase_{NN}.sh` (one task per phase) so acceptance is reproducible by an agent, not a ritual performed by whoever remembers the steps.

### 3.2 Protocol Anatomy

| Element | Meaning |
| :--- | :--- |
| **Deliverable** | The artifact that exists when the phase ends, stated as a thing, not an activity. |
| **Scope Boundary** | What this phase is explicitly *not* responsible for. Prevents scope creep and premature integration. |
| **Permitted Stubs** | The exact fakes allowed during acceptance. Anything stubbed here must be un-stubbed and re-verified in the phase that owns it. |
| **Acceptance Steps** | Ordered actions with commands and expected evidence. Independently reproducible. |
| **Rejection Criteria** | Conditions that fail acceptance even if all tasks are green. |
| **Exit Artifacts** | Files that must exist and be committed as evidence. |

### 3.3 Acceptance Report

Each protocol emits `reports/phase_{NN}_acceptance.json`:

```json
{
  "phase": "03",
  "commit": "a1b2c3d",
  "executed_at": "2026-09-20T14:02:11Z",
  "tasks_green": ["3.1", "3.2", "..."],
  "coverage": {"providers": {"line": 0.91, "branch": 0.86}},
  "mutation": {"score": null, "required": false},
  "acceptance_steps": [{"step": 1, "status": "PASS", "evidence": "..."}],
  "stubs_used": ["FakeProviderServer", "MockLLM"],
  "rejections": [],
  "verdict": "ACCEPTED",
  "signed_by": "reviewer-agent-02"
}
```

**Signing rule:** the implementing worker may not sign its own phase. Phases 5, 8 and 10 additionally require a human signature — these are the process-model, concurrency, and release gates, where an agent reviewing an agent is not sufficient assurance.

### 3.4 Dependency Graph

```
P0 Scaffolding, Contracts & Test Infrastructure
 |
 +--[A]--> P1 Persistence & Workspace Isolation --+
 +--[A]--> P2 IPC Transport & Event Bus ----------+
 +--[B]--> P3 LLM Provider Abstraction -----------+
                   |                              |
                   v                              |
          P4 Rate-Limit Broker & Cost Governor     |
                   |                              |
                   +------------+-----------------+
                                v
                   P5 Execution Engine Daemon      [human sign-off]
                                |
                +---------------+---------------+
                v                               v
       [D] P6 Critic Gatekeeper          [C] P7 TUI Core
                |                               |
                +---------------+---------------+
                                v
                   [D] P8 SDLC Pipeline & Worker Pool   [human sign-off]
                                |
                                v
                   P9 Error Handling & Recovery
                                |
                                v
                   P10 Verification & Release      [human sign-off]
```

---

# Part II — Phases

## Phase 0: Scaffolding, Shared Contracts & Test Infrastructure

**Objective:** Establish the single source of truth for every enum, schema and path rule, the deterministic test rig all ten downstream phases assert against, and the coverage machinery that enforces §2.
**Prerequisites:** None. **Lane:** A

### 0.A Execution Tasks

| Task ID | Task Description & Deliverable | Targeted Files / Components | Prereq | Est |
| :--- | :--- | :--- | :--- | :--- |
| 0.1 | `src/` layout package; Python >=3.11; deps pinned (textual, rich, langgraph, langchain-core, pydantic>=2, httpx, portalocker, tiktoken, pytest, pytest-asyncio, pytest-cov, pytest-timeout, pytest-socket, hypothesis, psutil, mutmut, ruff, mypy). | `pyproject.toml`, `src/dev_harness/__init__.py` | — | 1.5 |
| 0.2 | Quality gates: ruff; mypy `strict=true` across all of `src/`; `--cov-branch` with per-package thresholds. | `Makefile`, `mypy.ini`, `.coveragerc` | 0.1 | 2.0 |
| 0.3 | Canonical enums: `ExecutionState`, `CriticCommand`, `ChunkStatus`, `PanelId`, `EventType`, `ProviderId`, `FailureClass`. | `contracts/enums.py` | 0.1 | 2.0 |
| 0.4 | Error taxonomy: `HarnessError` root with `remediation`; domain subclasses. | `contracts/errors.py` | 0.1 | 2.0 |
| 0.5 | V7 state as Pydantic v2 models. | `contracts/state.py` | 0.3, 0.4 | 3.0 |
| 0.6 | IPC envelope + discriminated payload union for all 8 event types. | `contracts/events.py` | 0.3 | 3.0 |
| 0.7 | JSON Schema export + golden fixture from TDD §6 + drift check. | `contracts/schema.py`, `schemas/harness_state.v7.json`, `tests/fixtures/state_v7_golden.json` | 0.5 | 2.0 |
| 0.8 | Config loader: TOML + `DEV_HARNESS_*` overrides; provider blocks with `rpm`, `tpm`, `max_concurrency`, `context_window`, `num_ctx`, `keep_alive`, `usd_per_mtok_in/out`. | `config.py`, `dev-harness.example.toml` | 0.5 | 3.0 |
| 0.9 | Secrets provider: env → keyring → `0600` file; never enters `HarnessState`; masked `__repr__`. | `secrets.py` | 0.8 | 2.5 |
| 0.10 | Path derivation: canonical workspace path, socket, lock, run-artifact dir. | `paths.py` | 0.1 | 2.0 |
| 0.11 | Handoff enforcement: commit-msg hook + CI check for `Task-Id:` trailers matching this plan. | `scripts/check_task_trailer.py` | 0.2 | 2.0 |
| 0.12 | Deterministic rig: frozen monotonic clock, `tmp_workspace` git factory, golden-file helper. | `tests/conftest.py`, `tests/support/clock.py`, `tests/support/workspace.py` | 0.1 | 4.0 |
| 0.13 | `MockLLM` + `FakeProviderServer`: scripted completions, streaming, injectable 429/5xx/timeout faults over in-process ASGI. | `tests/support/mock_llm.py`, `tests/support/fake_provider.py` | 0.12, 0.6 | 4.0 |
| 0.14 | Structured JSON logging with correlation + redaction filter. | `observability/logging.py`, `observability/redact.py` | 0.9 | 2.5 |
| 0.15 | Run artifact store: append-only per-run transcript of prompts, completions, diffs, test output; size cap + rotation. | `observability/artifacts.py` | 0.10, 0.14 | 3.0 |
| 0.16 | **Coverage instrumentation & ratchet**: per-package thresholds, 0.5pp regression guard vs `main`, bare-pragma detector. | `scripts/coverage_gate.py`, `.coveragerc` | 0.2 | 3.0 |
| 0.17 | **Mutation harness**: `mutmut` config scoped to `storage/`, `vcs/`, `broker/`, `core/`; baseline capture; score reporter. | `setup.cfg`, `scripts/mutation_gate.py` | 0.16 | 2.5 |
| 0.18 | **Phase verification runner**: shared framework executing a phase's acceptance steps and emitting `reports/phase_NN_acceptance.json`; marker enforcement (unmarked test → collection error). | `scripts/verify_phase.py`, `pytest.ini`, `tests/support/markers.py` | 0.12, 0.16 | 2.5 |
| 0.19 | `scripts/verify_phase_00.sh`. | `scripts/verify_phase_00.sh` | 0.18 | 1.5 |

**Phase 0 total: 46.0h**

### 0.B Validation Matrix

| Task ID | Verification Strategy | Test Commands | Success Criteria | Tier |
| :--- | :--- | :--- | :--- | :--- |
| 0.1 | Import smoke | `pip install -e . && python -c "import dev_harness"` | Exit 0; version resolves. | PR |
| 0.2 | Gate self-test | `make lint typecheck test` | ruff 0 findings; mypy 0 errors across `src/`; pytest exit 0. | PR |
| 0.3 | Enum exhaustiveness + literal ban | `pytest tests/contracts/test_enums.py -q` | All 4 `CriticCommand` members; `grep -rn '"PAUSED"' src/ --include=*.py` matches only `enums.py`. | PR |
| 0.4 | Taxonomy completeness | `pytest tests/contracts/test_errors.py -q` | Every subclass has non-empty `remediation`; AST scan finds 0 bare `raise Exception` in `src/`. | PR |
| 0.5 | Round trip | `pytest tests/contracts/test_state_model.py -q` | `model_validate(golden).model_dump(mode="json") == golden`; missing `project_id` → `ValidationError` at `("project_id",)`. | PR |
| 0.6 | Payload discrimination | `pytest tests/contracts/test_events.py -q` | 1:1 type→payload mapping; unknown type and type/payload mismatch both rejected. | PR |
| 0.7 | Schema + drift | `pytest tests/contracts/test_schema.py -q` | `jsonschema.validate` passes; regenerated schema byte-identical to committed file. | PR |
| 0.8 | Precedence | `pytest tests/test_config.py -q` | `DEV_HARNESS_ANTHROPIC__RPM=10` overrides TOML `50`; missing required key → `ConfigError` naming it. | PR |
| 0.9 | Secret containment | `pytest tests/test_secrets.py -q` | `repr()` → `***`; `HarnessState.model_dump_json()` contains no key substring; `0644` key file → `InsecureKeyFileError`. | PR |
| 0.10 | Path determinism | `pytest tests/test_paths.py -q` | `/tmp/x/` ≡ `/tmp/x`; symlink resolves; distinct paths distinct hashes; socket path ≤104 bytes. | PR |
| 0.11 | Handoff enforcement | `python scripts/check_task_trailer.py --rev HEAD` | Missing trailer → exit 1; `Task-Id: 99.9` → exit 1; valid → exit 0. | PR |
| 0.12 | Rig self-test | `pytest tests/support/test_rig.py -q` | Frozen clock advances only on `tick()`; `tmp_workspace` yields a repo where `git rev-parse HEAD` succeeds. | PR |
| 0.13 | Mock fidelity | `pytest tests/support/test_mock_llm.py -q` | Same seed → byte-identical completion over 10 runs; fault injection yields 429 with `Retry-After`; streaming yields ≥2 chunks + terminal sentinel. | PR |
| 0.14 | Redaction | `pytest tests/observability/test_redaction.py -q` | `sk-ant-api03-XXXX` → `sk-***REDACTED***`; `caplog.text` has no raw key. | PR |
| 0.15 | Artifact store | `pytest tests/observability/test_artifacts.py -q` | 3-turn run writes 3 ordered entries; cap rotates without losing the newest; entries redacted. | PR |
| 0.16 | Ratchet behavior | `pytest tests/tooling/test_coverage_gate.py -q` | Simulated 1.0pp drop → exit 1 naming the package; 0.3pp drop → exit 0; bare `# pragma: no cover` → exit 1. | PR |
| 0.17 | Mutation harness | `python scripts/mutation_gate.py --dry-run` | Produces a baseline for the 4 scoped packages; a deliberately weakened assertion lowers the reported score. | NIGHTLY |
| 0.18 | Runner + marker enforcement | `pytest tests/tooling/test_verify_phase.py -q` | An unmarked test raises a collection error naming the file; runner emits a schema-valid acceptance JSON. | PR |
| 0.19 | Script executes | `bash scripts/verify_phase_00.sh` | Exit 0; `reports/phase_00_acceptance.json` written with `verdict: ACCEPTED`. | PR |

### 0.C Coverage Contract

| Package | Line | Branch | Mutation | Required Test Classes |
| :--- | :--- | :--- | :--- | :--- |
| `contracts/` | 100% | 95% | — | unit, contract, negative |
| `config.py`, `secrets.py`, `paths.py` | 95% | 90% | — | unit, negative |
| `observability/` | 90% | 80% | — | unit, negative |
| `tests/support/` | n/a | n/a | — | Self-tested by 0.12/0.13 rows; the rig is tested, not measured. |

### 0.D Phase Acceptance Protocol

**Deliverable:** an installable package whose contracts are frozen, plus a deterministic test rig and coverage machinery that every later phase uses without modification.
**Scope Boundary:** no runtime behavior. Nothing in Phase 0 talks to a socket, a database, a model, or a terminal.
**Permitted Stubs:** none — Phase 0 has no dependencies to stub.

| Step | Action | Command | Expected Evidence |
| :--- | :--- | :--- | :--- |
| 1 | Clean-clone install | `git clone . /tmp/p0 && cd /tmp/p0 && pip install -e .` | Exit 0 on a machine with no prior harness state. |
| 2 | Full gate | `make ci` | ruff 0, mypy 0 across `src/`, pytest green, coverage gate exit 0. |
| 3 | Contract freeze | `python -m dev_harness.contracts.schema --emit \| diff - schemas/harness_state.v7.json` | Empty diff. Tag `contracts-v1`. |
| 4 | Rig portability proof | Write a throwaway test in a new file using only `tmp_workspace`, `frozen_clock` and `MockLLM`; run it. | Passes with zero additional fixtures or imports beyond `tests.support`. |
| 5 | Determinism proof | `pytest tests/ -q -p no:randomly --count=3` (pytest-repeat) | Identical pass/fail set across 3 runs; no ordering dependence. |
| 6 | Ratchet proof | Delete an assertion in `contracts/`, run `python scripts/coverage_gate.py` | Exits 1 naming `contracts/` and the delta. Revert. |
| 7 | Marker proof | Add an unmarked test, run `pytest` | Collection error naming the file. Revert. |
| 8 | Emit report | `bash scripts/verify_phase_00.sh` | `reports/phase_00_acceptance.json` with `verdict: ACCEPTED`. |

**Rejection Criteria:** any `# pragma: no cover` without justification; any enum value duplicated as a string literal in `src/`; any test in the suite that sleeps; `contracts/` below 100% line.
**Exit Artifacts:** `schemas/harness_state.v7.json`, `tests/fixtures/state_v7_golden.json`, `reports/phase_00_acceptance.json`, git tag `contracts-v1`.

---

## Phase 1: Persistence, Namespacing, Retention & Workspace Isolation

**Objective:** A multi-tenant checkpoint store that cannot leak across projects, binds every checkpoint to a recoverable commit, and does not grow without bound.
**Prerequisites:** 0.5, 0.8, 0.10, 0.12 **Lane:** A

### 1.A Execution Tasks

| Task ID | Task Description & Deliverable | Targeted Files | Prereq | Est |
| :--- | :--- | :--- | :--- | :--- |
| 1.1 | Connection factory: WAL, `synchronous=NORMAL`, `busy_timeout=10000`, `foreign_keys=ON`. | `storage/connection.py` | 0.8 | 2.0 |
| 1.2 | Migration runner, forward + `-- down`, `schema_migrations` ledger. | `storage/migrate.py` | 1.1 | 3.0 |
| 1.3 | `0001_init`: `checkpoints` (composite PK, `state_json`, `state_sha256`, `git_commit_hash`, `is_paused`, `created_at`) + scope index. | `storage/migrations/0001_init.sql` | 1.2 | 2.0 |
| 1.4 | `SqliteSaver` write path (`put`, `put_writes`). | `storage/sqlite_saver.py` | 1.3, 0.5 | 3.5 |
| 1.5 | `SqliteSaver` read path (`get_tuple`, `list`) with newest-first ordering and cursor paging. | `storage/sqlite_saver.py` | 1.4 | 3.0 |
| 1.6 | Namespace guard → `UnscopedQueryError`. | `storage/guards.py` | 1.5 | 2.5 |
| 1.7 | Retention: keep last N per thread + all `is_paused` seals; prune + `VACUUM` on threshold. | `storage/retention.py` | 1.5 | 3.0 |
| 1.8 | Workspace lock (`portalocker`), 10s timeout, PID+hostname for stale detection. | `storage/workspace_lock.py` | 0.10 | 2.5 |
| 1.9 | Git adapter: `head_sha`, `active_branch`, `is_dirty`, `uncommitted_count`. | `vcs/git.py` | 0.4 | 2.5 |
| 1.10 | Worktree manager: create/destroy `.dev-harness/worktrees/{worker_id}` bound to a branch. | `vcs/worktree.py` | 1.9 | 3.5 |
| 1.11 | Checkpoint↔Git binding inside the workspace lock. | `storage/checkpoint_binding.py` | 1.4, 1.8, 1.9 | 2.5 |
| 1.12 | Restore with dirty-tree refusal / autostash, then `git checkout {sha}`. | `vcs/restore.py` | 1.11 | 3.0 |
| 1.13 | Two-workspace concurrency fixture. | `tests/storage/test_multi_project_isolation.py` | 1.5, 1.8, 0.12 | 2.5 |
| 1.14 | `scripts/verify_phase_01.sh`. | `scripts/verify_phase_01.sh` | 0.18 | 1.5 |

**Phase 1 total: 37.0h**

### 1.B Validation Matrix

| Task ID | Verification Strategy | Test Commands | Success Criteria | Tier |
| :--- | :--- | :--- | :--- | :--- |
| 1.1 | PRAGMA assertion | `pytest tests/storage/test_connection.py -q` | `journal_mode='wal'`; `synchronous=1`; `busy_timeout=10000`. | PR |
| 1.2 | Up/down idempotence | `pytest tests/storage/test_migrations.py -q` | Re-apply is a no-op; rollback returns `sqlite_master` count to baseline exactly. | PR |
| 1.3 | Constraints | `pytest tests/storage/test_schema_ddl.py -q` | Duplicate composite PK → `IntegrityError`; `EXPLAIN QUERY PLAN` reports `USING INDEX idx_checkpoints_scope`. | PR |
| 1.4 | Write conformance | `pytest tests/storage/test_saver_write.py -q` | `state_sha256` matches recomputed digest; injected mid-write failure leaves 0 partial rows. | PR |
| 1.5 | Read conformance | `pytest tests/storage/test_saver_read.py -q` | `get_tuple()` equals golden field-for-field; `list(limit=3)` newest-first; paging returns disjoint sets. | PR |
| 1.6 | Unscoped access | `pytest tests/storage/test_guards.py -q` | `list(config={})` → `UnscopedQueryError`; `proj_A` rows count 0 under `proj_B`. | PR |
| 1.7 | Retention | `pytest tests/storage/test_retention.py -q` | `keep=5` over 20 writes leaves 5 non-seal rows + every seal; file shrinks post-`VACUUM`; retained seal still restorable. | PR |
| 1.8 | Lock contention | `pytest tests/storage/test_workspace_lock.py -q -m timing` | `LockTimeout` at 10s ±0.5s; dead-PID lock reclaimed in one attempt. | NIGHTLY |
| 1.9 | Git adapter | `pytest tests/vcs/test_git.py -q` | `head_sha()` == `git rev-parse HEAD`; dirty transitions correct. | PR |
| 1.10 | Worktree lifecycle | `pytest tests/vcs/test_worktree.py -q` | 3 concurrent worktrees have distinct paths/branches; destroy leaves only the primary; file in A absent in B. | PR |
| 1.11 | Atomicity | `pytest tests/storage/test_checkpoint_binding.py -q -m slow` | 50 interleaved writes: every hash passes `git cat-file -e` and equals HEAD at write time. | NIGHTLY |
| 1.12 | Destructive guard | `pytest tests/vcs/test_restore.py -q` | Dirty → `DirtyWorktreeError`, `git status --porcelain` byte-identical; autostash reapplies, `git stash list` empty. | PR |
| 1.13 | Isolation | `pytest tests/storage/test_multi_project_isolation.py -q -m slow` | 200 concurrent writes/workspace: 0 `database is locked`; 0 cross-project rows; < 2× single-workspace baseline. | NIGHTLY |
| 1.14 | Script executes | `bash scripts/verify_phase_01.sh` | Exit 0; acceptance JSON `ACCEPTED`. | PR |

### 1.C Coverage Contract

| Package | Line | Branch | Mutation | Required Test Classes |
| :--- | :--- | :--- | :--- | :--- |
| `storage/` | 95% | 90% | ≥80% | unit, property (retention selection), integration, negative |
| `vcs/` | 95% | 90% | ≥80% | unit, integration, negative |

Mutation focus set: `guards.py` (a mutant that drops the `project_id` predicate must be killed), `retention.py` (a mutant that prunes a seal must be killed), `restore.py` (a mutant that skips the dirty check must be killed). These three are the data-loss surface.

### 1.D Phase Acceptance Protocol

**Deliverable:** a checkpoint store usable by an unrelated consumer with no engine, no IPC, and no LLM — proving persistence stands alone.
**Scope Boundary:** no LangGraph integration, no daemon, no concurrency beyond raw processes. Worktrees are created and destroyed but nothing executes inside them.
**Permitted Stubs:** `tmp_workspace` fixture; a dummy state dict in place of real pipeline output.

| Step | Action | Command | Expected Evidence |
| :--- | :--- | :--- | :--- |
| 1 | Fresh DB bootstrap | `python -m dev_harness.storage.migrate --workspace /tmp/w1 --up` | `.dev-harness/state.db` created; `schema_migrations` has 1 row. |
| 2 | Write/read round trip | `python -m dev_harness.storage.cli put --workspace /tmp/w1 --file tests/fixtures/state_v7_golden.json` then `... get --latest` | Retrieved JSON is byte-identical to the golden fixture. |
| 3 | Isolation demo | Repeat steps 1–2 in `/tmp/w2` with a different `project_id`; then `... list --workspace /tmp/w1` | Only w1's checkpoint listed; w2 absent. |
| 4 | Git binding demo | `git -C /tmp/w1 commit --allow-empty -m x`; write a checkpoint; compare stored hash to `git rev-parse HEAD` | Exact match. |
| 5 | Restore demo | Commit a second change, write a checkpoint, then `python -m dev_harness.vcs.restore --to {first_checkpoint}` | Worktree content reverts; `git rev-parse HEAD` equals the stored hash. |
| 6 | Destructive guard demo | Dirty the tree, repeat step 5 | `DirtyWorktreeError` raised; `git status --porcelain` unchanged before/after. |
| 7 | Worktree demo | Create 3 worktrees, write a distinct file in each, `git worktree list` | 3 worktrees + primary; primary `git status --porcelain` empty. |
| 8 | Retention demo | Write 20 checkpoints with `keep=5`; inspect row count and file size before/after `VACUUM` | 5 non-seal rows + seals retained; file size decreases. |
| 9 | Mutation gate | `python scripts/mutation_gate.py --packages storage,vcs` | Score ≥80%; surviving mutants listed and individually justified in the report. |
| 10 | Emit report | `bash scripts/verify_phase_01.sh` | `reports/phase_01_acceptance.json` `ACCEPTED`. |

**Rejection Criteria:** any surviving mutant in `guards.py`, `retention.py` or `restore.py`; any cross-project row visible in step 3; restore leaving the worktree in a state not matching the stored hash.
**Exit Artifacts:** `reports/phase_01_acceptance.json`, `reports/mutation_storage.json`, isolation soak log.

---

## Phase 2: IPC Transport & Event Bus

**Objective:** A framed, typed, backpressure-safe channel with a producer/consumer contract test so engine and TUI cannot drift.
**Prerequisites:** 0.6, 0.10, 0.14 **Lane:** A

### 2.A Execution Tasks

| Task ID | Task Description & Deliverable | Targeted Files | Prereq | Est |
| :--- | :--- | :--- | :--- | :--- |
| 2.1 | ADR selecting length-prefixed JSON over `AF_UNIX`; gRPC rejected (codegen toolchain for a single-host, low-fanout channel). | `docs/adr/0001-ipc-transport.md` | — | 1.0 |
| 2.2 | Framing codec: 4-byte BE prefix + UTF-8 JSON, 1 MiB max. | `ipc/framing.py` | 2.1, 0.6 | 2.5 |
| 2.3 | `AF_UNIX` server; socket `0600`; stale socket unlinked on bind. | `ipc/server.py` | 2.2, 0.10 | 3.0 |
| 2.4 | Client with connect-retry (cap 5s) and half-open detection. | `ipc/client.py` | 2.2 | 3.0 |
| 2.5 | Pub/sub router: subscribe by `EventType`, fan-out, handler exception isolation. | `ipc/router.py` | 2.3 | 3.0 |
| 2.6 | Backpressure queue (2048): token stream drop-oldest with counter; control never drops. | `ipc/queue.py` | 2.5 | 3.0 |
| 2.7 | POSIX gate; Windows → `UnsupportedPlatformError` naming WSL2. | `ipc/transport.py` | 2.3 | 1.0 |
| 2.8 | Producer/consumer contract harness over all event types. | `tests/ipc/test_event_contract.py`, `tests/fixtures/events/*.json` | 2.2, 0.6 | 3.0 |
| 2.9 | `scripts/verify_phase_02.sh`. | `scripts/verify_phase_02.sh` | 0.18 | 1.5 |

**Phase 2 total: 21.0h**

### 2.B Validation Matrix

| Task ID | Verification Strategy | Test Commands | Success Criteria | Tier |
| :--- | :--- | :--- | :--- | :--- |
| 2.1 | Review gate | `test -f docs/adr/0001-ipc-transport.md` | Decision, Rejected Alternatives, Consequences present. | PR |
| 2.2 | Property round trip | `pytest tests/ipc/test_framing.py -q` | 500 Hypothesis envelopes lossless; 2 MiB → `FrameTooLargeError`; truncated → `IncompleteFrameError` under `--timeout=10`, no hang. | PR |
| 2.3 | Permissions + cleanup | `pytest tests/ipc/test_server.py -q` | mode `600`; bind over leftover socket succeeds. | PR |
| 2.4 | Reconnect | `pytest tests/ipc/test_client.py -q` | Survives server restart; retry intervals non-decreasing, capped 5s. | PR |
| 2.5 | Fan-out isolation | `pytest tests/ipc/test_router.py -q` | 3 subscribers 1 copy each; raising handler doesn't block others; error logged once. | PR |
| 2.6 | Flood | `pytest tests/ipc/test_queue.py -q -m slow` | 10k events into 2048: RSS growth < 50 MB; `dropped_frames > 0`; 0 control events dropped. | NIGHTLY |
| 2.7 | Platform guard | `pytest tests/ipc/test_transport.py -q` | Simulated `win32` → error message contains "WSL2". | PR |
| 2.8 | Contract drift | `pytest tests/ipc/test_event_contract.py -q` | All 8 fixtures parse; adding a required field without fixture update fails (mutation asserted). | PR |
| 2.9 | Script executes | `bash scripts/verify_phase_02.sh` | Exit 0; acceptance JSON `ACCEPTED`. | PR |

### 2.C Coverage Contract

| Package | Line | Branch | Mutation | Required Test Classes |
| :--- | :--- | :--- | :--- | :--- |
| `ipc/` | 92% | 85% | — | unit, property (framing), contract, integration, negative |

Contract coverage is the binding metric here: `test_event_contract.py` must programmatically assert it covers 100% of `EventType` members. Line coverage alone would pass with half the event types untested.

### 2.D Phase Acceptance Protocol

**Deliverable:** a transport two unrelated processes can use to exchange every defined event type, with no engine and no UI present.
**Scope Boundary:** no session management, no business logic, no knowledge of what events mean.
**Permitted Stubs:** a scripted echo server and a recording client, both in `tests/support`.

| Step | Action | Command | Expected Evidence |
| :--- | :--- | :--- | :--- |
| 1 | Start echo server | `python -m dev_harness.ipc.server --workspace /tmp/w1 --echo &` | Socket exists at the derived path with mode `600`. |
| 2 | Send every event type | `python -m dev_harness.ipc.cli send-all --fixtures tests/fixtures/events/` | All 8 echoed back and re-parsed by the real consumer parser; 0 mismatches. |
| 3 | Event-type coverage assertion | `python -m dev_harness.ipc.cli coverage-check` | Reports 8/8 `EventType` members exercised; exit 1 if any unexercised. |
| 4 | Kill and reconnect | `kill %1`, restart server, send one event | Client reconnects without restart and delivers the event. |
| 5 | Flood behavior | `python -m dev_harness.ipc.cli flood --count 10000` | Control events 0 dropped; token drops counted and reported; RSS growth < 50 MB. |
| 6 | Permission negative | `chmod 666 {socket}`, reconnect | Client refuses with `InsecureSocketError`. |
| 7 | Emit report | `bash scripts/verify_phase_02.sh` | Acceptance JSON `ACCEPTED`. |

**Rejection Criteria:** any `EventType` not exercised in step 3; any dropped control event; socket created with mode other than `600`.
**Exit Artifacts:** `reports/phase_02_acceptance.json`, `docs/adr/0001-ipc-transport.md`, `tests/fixtures/events/*.json`.

---

## Phase 3: LLM Provider Abstraction & Streaming Adapters

**Objective:** Give the pipeline something to actually call — a client, a streaming contract, a tokenizer, and an error mapping.
**Prerequisites:** 0.8, 0.9, 0.13, 2.2 **Lane:** B

### 3.A Execution Tasks

| Task ID | Task Description & Deliverable | Targeted Files | Prereq | Est |
| :--- | :--- | :--- | :--- | :--- |
| 3.1 | `LLMClient` Protocol: `complete()`, `stream()` → `TokenChunk`, `count_tokens()`; neutral `Message`/`ToolCall`/`Usage`. | `providers/base.py`, `contracts/llm.py` | 0.6 | 3.0 |
| 3.2 | Model registry: id → provider, context window, max output, pricing, tokenizer. Config-sourced. | `providers/registry.py` | 3.1, 0.8 | 2.5 |
| 3.3 | Error mapping → `RateLimitedError`, `ProviderOverloadedError`, `ContextOverflowError`, `AuthError`, `TransientError` with `retryable`/`retry_after`. | `providers/errors.py` | 3.1, 0.4 | 2.5 |
| 3.4 | Anthropic adapter: non-streaming + SSE streaming, usage extraction. | `providers/anthropic.py` | 3.3, 0.9 | 4.0 |
| 3.5 | OpenRouter adapter with model-name namespacing. | `providers/openrouter.py` | 3.3 | 3.0 |
| 3.6 | Ollama adapter: `/api/chat` streaming, `num_ctx`/`keep_alive`, cold-start detection. | `providers/ollama.py` | 3.3 | 3.5 |
| 3.7 | Model-swap thrash guard: serialize load-forcing requests; expose `model_loaded`. | `providers/ollama_loader.py` | 3.6 | 3.0 |
| 3.8 | Tokenizer service: exact where available, 4-chars/token + 15% margin fallback. | `providers/tokenizer.py` | 3.2 | 2.5 |
| 3.9 | Streaming → IPC bridge emitting sequenced `AGENT_TOKEN_STREAM`. | `providers/stream_bridge.py` | 3.1, 2.2 | 2.5 |
| 3.10 | Shared adapter conformance suite driven by `FakeProviderServer`. | `tests/providers/test_adapters.py` | 3.4–3.6, 0.13 | 3.0 |
| 3.11 | `scripts/verify_phase_03.sh`. | `scripts/verify_phase_03.sh` | 0.18 | 1.5 |

**Phase 3 total: 31.0h**

### 3.B Validation Matrix

| Task ID | Verification Strategy | Test Commands | Success Criteria | Tier |
| :--- | :--- | :--- | :--- | :--- |
| 3.1 | Protocol conformance | `pytest tests/providers/test_base.py -q` | All 3 adapters satisfy `runtime_checkable` `LLMClient`; mypy 0 protocol violations. | PR |
| 3.2 | Registry integrity | `pytest tests/providers/test_registry.py -q` | Every configured model resolves context window + pricing; unknown → `UnknownModelError`; `max_output < context_window` for all. | PR |
| 3.3 | Fault mapping | `pytest tests/providers/test_errors.py -q` | 429→`RateLimitedError(retryable=True, retry_after=30)`; 529→`ProviderOverloadedError`; 401→`AuthError(retryable=False)`; reset→`TransientError`. | PR |
| 3.4 | Anthropic vs fake | `pytest tests/providers/test_anthropic.py -q` | Chunks reassemble byte-identically; `Usage.input_tokens` equals fake-server accounting. | PR |
| 3.5 | OpenRouter | `pytest tests/providers/test_openrouter.py -q` | Namespace preserved in request; reassembly byte-identical. | PR |
| 3.6 | Ollama | `pytest tests/providers/test_ollama.py -q` | `num_ctx`/`keep_alive` in captured body; >5s TTFB → `model_loading=True`, not a timeout error. | PR |
| 3.7 | Thrash guard | `pytest tests/providers/test_ollama_loader.py -q` | 6 interleaved requests across 2 models → ≤2 load events; never concurrent loads. | PR |
| 3.8 | Estimator bounds | `pytest tests/providers/test_tokenizer.py -q` | Estimate ≥ actual for all 20 corpus samples (0 underestimates); overhead ≤30%. | PR |
| 3.9 | Stream ordering | `pytest tests/providers/test_stream_bridge.py -q` | 1,000 chunks strictly increasing `seq`, 0 gaps; terminal envelope carries final `Usage`. | PR |
| 3.10 | Offline guarantee | `pytest tests/providers -q --disable-socket --allow-unix-socket` | Whole suite passes with outbound TCP disabled. | PR |
| 3.11 | Script executes | `bash scripts/verify_phase_03.sh` | Exit 0; acceptance JSON `ACCEPTED`. | PR |

### 3.C Coverage Contract

| Package | Line | Branch | Mutation | Required Test Classes |
| :--- | :--- | :--- | :--- | :--- |
| `providers/` | 90% | 85% | — | unit, contract (shared conformance suite), integration, negative |
| `providers/errors.py` | 100% | 95% | — | Every mapped status code has a `negative` test; this is the file downstream retry logic trusts. |

Excluded with justification: transport-level branches reachable only against a live endpoint (TLS renegotiation, provider-specific 5xx bodies not in the fake server's catalogue). Each exclusion carries an inline justification and is covered by task 10.4.

### 3.D Phase Acceptance Protocol

**Deliverable:** three interchangeable provider clients behind one Protocol, exercisable from a REPL with no engine, broker, pipeline or UI.
**Scope Boundary:** no rate limiting, no cost accounting, no retry orchestration — Phase 3 raises typed errors and lets Phase 4 decide policy.
**Permitted Stubs:** `FakeProviderServer` for all three providers; a real local Ollama is optional and used only in step 6.

| Step | Action | Command | Expected Evidence |
| :--- | :--- | :--- | :--- |
| 1 | Substitutability proof | `python -m dev_harness.providers.cli complete --provider {anthropic,openrouter,ollama} --prompt "ping" --fake` | Identical call signature for all three; all return a populated `Usage`. |
| 2 | Streaming proof | `... stream --provider anthropic --fake --emit-ipc` | Reassembled output byte-identical to the scripted completion; `seq` strictly increasing with 0 gaps. |
| 3 | Fault catalogue | `... fault-drill --codes 429,500,529,401,timeout --fake` | Each code maps to the documented exception with correct `retryable`; printed table matches `providers/errors.py`. |
| 4 | Token accounting | `... count --model {each} --file tests/fixtures/corpus/*.txt` | Estimate ≥ actual for every sample; mean overhead ≤30%; report written. |
| 5 | Thrash guard demo | `... swap-drill --models qwen2.5-coder:7b,llama3:8b --requests 6 --fake` | Load counter ≤2; no overlapping load windows in the emitted timeline. |
| 6 | Local reality check (optional but recorded) | `... complete --provider ollama --model qwen2.5-coder:7b --prompt "ping"` against a real local server | Completion returned; `num_ctx` honored; latency recorded in the report. If skipped, report records `skipped: no_local_ollama`. |
| 7 | No-network proof | `pytest tests/providers -q --disable-socket --allow-unix-socket` | Green with outbound TCP disabled. |
| 8 | Emit report | `bash scripts/verify_phase_03.sh` | Acceptance JSON `ACCEPTED` listing stubs used. |

**Rejection Criteria:** any adapter requiring provider-specific call-site code (breaks substitutability); any token underestimate in step 4; any unjustified `pragma: no cover` in `providers/`.
**Exit Artifacts:** `reports/phase_03_acceptance.json`, `reports/tokenizer_calibration.json`, fault-catalogue table.

---

## Phase 4: Rate-Limit Broker & Cost Governor

**Objective:** Prevent 429s, prevent local memory saturation, and prevent runaway spend.
**Prerequisites:** 3.2, 3.3, 3.8, 2.3, 2.4 **Lane:** B

### 4.A Execution Tasks

| Task ID | Task Description & Deliverable | Targeted Files | Prereq | Est |
| :--- | :--- | :--- | :--- | :--- |
| 4.1 | Token bucket on `time.monotonic()`, fractional refill, thread-safe acquire with timeout. | `broker/bucket.py` | 0.12 | 3.0 |
| 4.2 | Provider policy registry (RPM/TPM/max_concurrency). | `broker/policies.py` | 4.1, 0.8 | 2.0 |
| 4.3 | Reservation protocol `reserve → commit(actual) \| release`, 120s TTL. | `broker/reservation.py` | 4.2, 3.8 | 3.5 |
| 4.4 | Local limiter: `ollama` by concurrency semaphore + queue depth. | `broker/local_limiter.py` | 4.2 | 2.5 |
| 4.5 | Backoff `min(max, base*2^n) + U(0,jitter)` honoring `Retry-After`. | `broker/backoff.py` | 4.1, 3.3 | 2.0 |
| 4.6 | Cost governor: USD from `Usage` × registry pricing; per-run and per-day ceilings. | `broker/cost.py` | 4.3, 3.2 | 3.5 |
| 4.7 | Budget kill-switch: emit `INTERRUPT_REQUEST{STOP, reason=BUDGET}`, refuse further reservations. | `broker/kill_switch.py` | 4.6, 0.6 | 2.5 |
| 4.8 | `dev-harness-broker` daemon: single-instance lock, IPC endpoint, `HEALTH`, graceful drain. | `broker/daemon.py` | 4.3, 4.4, 2.3 | 4.0 |
| 4.9 | Client SDK, fail-closed; `allow_unbrokered=true` the sole escape hatch. | `broker/client.py` | 4.8, 2.4 | 2.5 |
| 4.10 | Metrics feed: p50/p95 latency, TPM burn, cumulative USD via `METRICS_UPDATE`. | `broker/metrics_feed.py` | 4.8, 4.6 | 2.5 |
| 4.11 | `scripts/verify_phase_04.sh`. | `scripts/verify_phase_04.sh` | 0.18 | 1.5 |

**Phase 4 total: 29.5h**

### 4.B Validation Matrix

| Task ID | Verification Strategy | Test Commands | Success Criteria | Tier |
| :--- | :--- | :--- | :--- | :--- |
| 4.1 | Frozen-clock determinism | `pytest tests/broker/test_bucket.py -q` | 50-capacity bucket grants exactly 50 in window 1, 0 until refill; no drift over 10 simulated minutes. | PR |
| 4.2 | Policy loading | `pytest tests/broker/test_policies.py -q` | Unknown provider → `UnknownProviderError`; `ollama` exposes `max_concurrency`, no `rpm`. | PR |
| 4.3 | Leak prevention | `pytest tests/broker/test_reservation.py -q` | Client killed post-`reserve` releases at TTL+1s; `commit(actual<reserved)` returns the delta (asserted on bucket level). | PR |
| 4.4 | Local saturation | `pytest tests/broker/test_local_limiter.py -q` | `max_concurrency=2`: max in-flight == 2 at every 10ms sample. | PR |
| 4.5 | Backoff shape | `pytest tests/broker/test_backoff.py -q` | Attempts 0–5 non-decreasing pre-jitter, capped; `Retry-After: 30` overrides. | PR |
| 4.6 | Cost arithmetic | `pytest tests/broker/test_cost.py -q` | 1M in + 1M out equals expected USD to 4 dp; 100 concurrent commits lose nothing. | PR |
| 4.7 | Kill-switch | `pytest tests/broker/test_kill_switch.py -q` | $0.50 ceiling breach → exactly one STOP with `reason=BUDGET`; next `reserve()` → `BudgetExceededError`; fake server observes 0 further requests. | PR |
| 4.8 | Daemon lifecycle | `pytest tests/broker/test_daemon.py -q` | Second instance exits 3 `AlreadyRunning`; `HEALTH` < 50ms; SIGTERM drains ≤5s, 0 orphaned reservations. | PR |
| 4.9 | Ceiling under load | `pytest tests/broker/test_rate_limiter_load.py -q -m timing` | 100 concurrent vs 50 RPM: grants in any rolling 60s window ≤50; 0 unhandled exceptions; broker down → `BrokerUnavailableError`. | NIGHTLY |
| 4.10 | Metrics emission | `pytest tests/broker/test_metrics_feed.py -q` | `METRICS_UPDATE` carries numeric p95 and cumulative USD within 1s of a batch. | PR |
| 4.11 | Script executes | `bash scripts/verify_phase_04.sh` | Exit 0; acceptance JSON `ACCEPTED`. | PR |

### 4.C Coverage Contract

| Package | Line | Branch | Mutation | Required Test Classes |
| :--- | :--- | :--- | :--- | :--- |
| `broker/` | 95% | 90% | ≥80% | unit, property (bucket refill), integration, negative, timing |
| `broker/cost.py`, `broker/kill_switch.py` | 100% | 95% | ≥90% | These two are the only thing between a retry loop and an unbounded bill. |

Mutation focus set: a mutant that inverts the ceiling comparison, one that drops the `Retry-After` override, and one that skips the kill-switch emit must all be killed.

### 4.D Phase Acceptance Protocol

**Deliverable:** a running broker daemon that a standalone client can reserve capacity from, which throttles correctly and stops spending at a configured ceiling — with no engine or pipeline present.
**Scope Boundary:** the broker does not make provider calls itself; it grants or refuses. It does not know about sessions, chunks, or graphs.
**Permitted Stubs:** `FakeProviderServer`; a synthetic load generator standing in for the engine.

| Step | Action | Command | Expected Evidence |
| :--- | :--- | :--- | :--- |
| 1 | Start daemon | `dev-harness-broker --config profiles/test.toml &` then `... health` | `{status:"ok"}` in < 50ms. |
| 2 | Single-instance proof | Start a second daemon | Exits 3 with `AlreadyRunning`; first remains healthy. |
| 3 | Ceiling demo | `python -m dev_harness.broker.cli loadgen --rpm-target 200 --policy-rpm 50 --duration 120` | Rolling 60s grant count never exceeds 50; plot/CSV written to `reports/`. |
| 4 | Local limiter demo | `... loadgen --provider ollama --concurrency 10 --max 2` | Sampled in-flight never exceeds 2; queue depth reported. |
| 5 | Leak demo | `... reserve --then-kill` | Capacity returns at TTL+1s; bucket level observable before and after. |
| 6 | Budget kill demo | `... loadgen --run-budget 0.50 --unit-cost 0.10` | Stops after 5 units; exactly one STOP emitted with `reason=BUDGET`; subsequent reserves refused. |
| 7 | Fail-closed demo | `kill` the daemon, then `... reserve` | `BrokerUnavailableError`; **no** provider request observed by the fake server. |
| 8 | Metrics demo | `... metrics --follow` during step 3 | p50/p95 and cumulative USD update at least once per second. |
| 9 | Mutation gate | `python scripts/mutation_gate.py --packages broker` | ≥80% overall, ≥90% on `cost.py` and `kill_switch.py`. |
| 10 | Emit report | `bash scripts/verify_phase_04.sh` | Acceptance JSON `ACCEPTED`. |

**Rejection Criteria:** any grant above the ceiling in step 3; any provider request in step 7; a surviving mutant in `cost.py` or `kill_switch.py`.
**Exit Artifacts:** `reports/phase_04_acceptance.json`, `reports/rate_ceiling_load.csv`, `reports/mutation_broker.json`.

---

## Phase 5: Execution Engine Daemon & Session Lifecycle

**Objective:** Build the process that hosts the graph, owns the socket, and serves attached clients.
**Prerequisites:** 1.11, 2.5, 2.6, 4.9 **Lane:** D — **human sign-off required**

### 5.A Execution Tasks

| Task ID | Task Description & Deliverable | Targeted Files | Prereq | Est |
| :--- | :--- | :--- | :--- | :--- |
| 5.1 | `EngineDaemon` skeleton: binds workspace socket, owns the loop, installs signal handlers. | `engine/daemon.py` | 2.3 | 3.5 |
| 5.2 | Session manager: id generation and registry; one active session per workspace. | `engine/session.py` | 5.1, 1.11 | 3.0 |
| 5.3 | Command surface: `START_SESSION`, `ATTACH`, `DETACH`, `STATUS`, `SHUTDOWN`. | `engine/commands.py` | 5.2, 2.5 | 3.0 |
| 5.4 | Multi-client attach with independent detach. | `engine/fanout.py` | 5.3, 2.6 | 3.0 |
| 5.5 | Engine↔broker wiring; all provider calls routed through the broker client. | `engine/provider_gateway.py` | 5.1, 4.9, 3.1 | 2.5 |
| 5.6 | Daemon autostart from CLI with handshake and version check. | `engine/bootstrap.py` | 5.3 | 3.0 |
| 5.7 | Graceful shutdown: drain, seal checkpoint, unlink socket. | `engine/shutdown.py` | 5.2, 1.11 | 2.5 |
| 5.8 | State broadcast so late-attaching clients render correct state. | `engine/state_broadcast.py` | 5.4 | 2.0 |
| 5.9 | `scripts/verify_phase_05.sh`. | `scripts/verify_phase_05.sh` | 0.18 | 1.5 |

**Phase 5 total: 24.0h**

### 5.B Validation Matrix

| Task ID | Verification Strategy | Test Commands | Success Criteria | Tier |
| :--- | :--- | :--- | :--- | :--- |
| 5.1 | Boot + bind | `pytest tests/engine/test_daemon.py -q` | Binds derived socket < 2s; SIGTERM exits 0; socket removed. | PR |
| 5.2 | Session uniqueness | `pytest tests/engine/test_session.py -q` | Second `START_SESSION` → `SessionExistsError` with existing `thread_id`; 1,000 ids unique and sortable. | PR |
| 5.3 | Command surface | `pytest tests/engine/test_commands.py -q` | All 5 return typed responses; unknown → `UnknownCommandError`, connection stays open. | PR |
| 5.4 | Fan-out | `pytest tests/engine/test_fanout.py -q` | 3 clients receive all 100 events in order; killing client 2 leaves 1 and 3 gapless. | PR |
| 5.5 | Gateway enforcement | `pytest tests/engine/test_provider_gateway.py -q` | AST + monkeypatch: 0 adapter calls bypass the broker; broker down → node raises, no provider call. | PR |
| 5.6 | Autostart | `pytest tests/engine/test_bootstrap.py -q` | Cold start spawns daemon, handshake < 3s; version mismatch → `EngineVersionMismatch`, no silent attach. | PR |
| 5.7 | Shutdown integrity | `pytest tests/engine/test_shutdown.py -q` | In-flight drained; final checkpoint `is_paused=True`; `pgrep -g` empty; socket unlinked. | PR |
| 5.8 | Late attach | `pytest tests/engine/test_state_broadcast.py -q` | Client attaching to a paused session receives `PAUSED` in its first snapshot frame before any delta. | PR |
| 5.9 | Script executes | `bash scripts/verify_phase_05.sh` | Exit 0; acceptance JSON `ACCEPTED`. | PR |

### 5.C Coverage Contract

| Package | Line | Branch | Mutation | Required Test Classes |
| :--- | :--- | :--- | :--- | :--- |
| `engine/daemon.py`, `session.py`, `commands.py`, `fanout.py`, `shutdown.py`, `bootstrap.py` | 92% | 85% | — | unit, integration, negative |
| `engine/provider_gateway.py` | 100% | 95% | — | Bypass is the failure mode; every path must be covered including the AST guard itself. |

### 5.D Phase Acceptance Protocol

**Deliverable:** a daemon that can be started, attached to by multiple clients, driven through a trivial scripted "workload", and shut down cleanly — with no pipeline, no personas, and no UI.
**Scope Boundary:** no LangGraph nodes, no SDLC semantics, no TUI. The workload is a stub emitter.
**Permitted Stubs:** a `StubWorkload` emitting scripted `AGENT_TOKEN_STREAM` events; `FakeProviderServer` behind the broker; a headless recording client in place of the TUI.

| Step | Action | Command | Expected Evidence |
| :--- | :--- | :--- | :--- |
| 1 | Cold start | `dev-harness-engine --workspace /tmp/w1 &` | Socket bound; `STATUS` returns `READY` with a `thread_id`. |
| 2 | Session uniqueness | `... start-session` twice | Second returns `SessionExistsError` naming the live `thread_id`. |
| 3 | Multi-client attach | Attach 3 recording clients, run `StubWorkload` emitting 100 events | All 3 transcripts identical and gapless (diff returns empty). |
| 4 | Independent detach | Kill client 2 mid-stream | Clients 1 and 3 continue with 0 gaps; daemon logs one detach, no error. |
| 5 | Late-attach correctness | Pause the stub workload, attach client 4 | Client 4's first frame reports `PAUSED`, before any delta event. |
| 6 | Broker enforcement | Stop the broker, trigger a stub provider call | Call fails with `BrokerUnavailableError`; fake server records 0 requests. |
| 7 | Autostart | Remove the socket, run `dev-harness --workspace /tmp/w1 --self-check` | Daemon respawned; handshake < 3s; health reports engine + broker. |
| 8 | Graceful shutdown | `... shutdown` during an active stub workload | In-flight drained; a sealed checkpoint exists with `is_paused=True`; socket unlinked; `pgrep -g` empty. |
| 9 | Crash residue check | `kill -9` the daemon, then restart | Stale socket reclaimed automatically; no manual cleanup required. |
| 10 | Emit report + human sign-off | `bash scripts/verify_phase_05.sh` | Acceptance JSON `ACCEPTED` with a human `signed_by`. |

**Rejection Criteria:** any client transcript divergence in step 3; any provider call reaching the fake server in step 6; manual cleanup required in step 9.
**Exit Artifacts:** `reports/phase_05_acceptance.json`, three client transcripts + diff output, shutdown log.

---

## Phase 6: Critic Gatekeeper & Asynchronous Interrupt Engine

**Objective:** Sub-second, leak-free interruption of agent tasks and their subprocess trees, with a sealed checkpoint on every pause.
**Prerequisites:** 5.3, 5.7, 1.11, 0.3 **Lane:** D

### 6.A Execution Tasks

| Task ID | Task Description & Deliverable | Targeted Files | Prereq | Est |
| :--- | :--- | :--- | :--- | :--- |
| 6.1 | `CriticGatekeeper` with explicit legal-transition table. | `core/critic.py` | 0.3 | 3.0 |
| 6.2 | Idempotent command handler (PAUSE while PAUSED → `already:true`). | `core/critic_commands.py` | 6.1, 5.3 | 2.5 |
| 6.3 | Task registry per `thread_id`; `cancel_all()` with 1s join. | `core/task_registry.py` | 6.1 | 3.0 |
| 6.4 | Subprocess group manager: `start_new_session=True`, PGID registry. | `core/process_group.py` | 6.3 | 3.0 |
| 6.5 | Escalation `killpg(SIGINT)` → 3.0s → `killpg(SIGKILL)` with `waitpid` reaping. | `core/signals.py` | 6.4 | 3.0 |
| 6.6 | Pause seal with `is_paused`, timestamp, bound hash. | `core/pause_seal.py` | 6.2, 1.11 | 2.5 |
| 6.7 | Interrupt latency histogram on `METRICS_UPDATE`. | `core/metrics.py` | 6.5, 4.10 | 2.0 |
| 6.8 | `scripts/verify_phase_06.sh`. | `scripts/verify_phase_06.sh` | 0.18 | 1.5 |

**Phase 6 total: 20.5h**

### 6.B Validation Matrix

| Task ID | Verification Strategy | Test Commands | Success Criteria | Tier |
| :--- | :--- | :--- | :--- | :--- |
| 6.1 | Exhaustive transitions | `pytest tests/core/test_critic_state.py -q` | All 16 (state, command) pairs asserted; `RESUME` from `STOPPED` → `IllegalTransitionError`; no undefined result state. | PR |
| 6.2 | Idempotency | `pytest tests/core/test_critic_commands.py -q` | Two PAUSEs → one transition, two ACKs, second `already=True`. | PR |
| 6.3 | Cancellation | `pytest tests/core/test_task_registry.py -q` | 20 tasks all `cancelled()`; `asyncio.all_tasks()` afterwards holds only the test task. | PR |
| 6.4 | Group creation | `pytest tests/core/test_process_group.py -q` | `getpgid(child) != getpgid(0)`; exactly one PGID per runner. | PR |
| 6.5 | Orphan elimination | `pytest tests/core/test_signals.py -q` | Runner with 3 grandchildren ignoring SIGINT: `pgrep -g {pgid}` empty within 4.0s; 0 zombies. | PR |
| 6.6 | Seal correctness | `pytest tests/core/test_pause_seal.py -q` | `is_paused=True`; timestamp within 1s; hash == `git rev-parse HEAD`. | PR |
| 6.7 | SLO | `pytest tests/core/test_interrupt_latency.py -q -m timing` | 50 trials: p95 < 500ms, max < 1000ms; `reports/interrupt_latency.json` written. | NIGHTLY |
| 6.8 | Script executes | `bash scripts/verify_phase_06.sh` | Exit 0; acceptance JSON `ACCEPTED`. | PR |

### 6.C Coverage Contract

| Package | Line | Branch | Mutation | Required Test Classes |
| :--- | :--- | :--- | :--- | :--- |
| `core/` | 95% | 90% | ≥85% | unit, integration, negative, timing |
| `core/signals.py` | 100% | 95% | ≥90% | Every escalation branch — grace expiry, early exit, already-dead PGID, reap failure. |

Mutation focus set: a mutant that skips the `SIGKILL` escalation, one that shortens/lengthens the grace window, and one that omits `waitpid` must all be killed.

### 6.D Phase Acceptance Protocol

**Deliverable:** an interrupt subsystem that reliably kills a hostile process tree and seals state, demonstrable against a purpose-built stubborn workload.
**Scope Boundary:** no pipeline nodes, no UI buttons. Commands arrive over IPC from a CLI.
**Permitted Stubs:** `StubbornRunner` — a test binary that spawns 3 grandchildren, traps `SIGINT`, and writes to a file continuously.

| Step | Action | Command | Expected Evidence |
| :--- | :--- | :--- | :--- |
| 1 | Transition table print | `python -m dev_harness.core.cli transitions --table` | Printed table matches the 16 asserted pairs in the test; no `UNDEFINED` cells. |
| 2 | Cooperative interrupt | Start a sleeping asyncio workload, send PAUSE | State `PAUSED` < 500ms; all tasks report `cancelled()`. |
| 3 | Hostile interrupt | Start `StubbornRunner`, send PAUSE | `pgrep -g {pgid}` empty within 4.0s; the runner's output file stops growing; 0 zombies via `ps -o stat`. |
| 4 | Idempotency | Send PAUSE 3× in a row | One transition, three ACKs, ACKs 2–3 carry `already=True`. |
| 5 | Illegal transition | Send RESUME after STOP | `IllegalTransitionError` returned; engine state unchanged. |
| 6 | Seal verification | Inspect the checkpoint written in step 2 | `is_paused=True`; `git_commit_hash` == `git rev-parse HEAD`; timestamp within 1s of the command. |
| 7 | Resume correctness | Send RESUME, observe the workload | Execution continues from the sealed state; no duplicate work re-executed. |
| 8 | SLO measurement | `python -m dev_harness.core.cli latency-drill --trials 50` | p95 < 500ms, max < 1000ms; JSON report written. |
| 9 | Mutation gate | `python scripts/mutation_gate.py --packages core` | ≥85% overall; ≥90% on `signals.py`; 0 survivors in the escalation path. |
| 10 | Emit report | `bash scripts/verify_phase_06.sh` | Acceptance JSON `ACCEPTED`. |

**Rejection Criteria:** any surviving process in step 3; any zombie; p95 above SLO on 2 of 3 nightly runs; a surviving escalation mutant.
**Exit Artifacts:** `reports/phase_06_acceptance.json`, `reports/interrupt_latency.json`, `ps` output from step 3.

---

## Phase 7: Hermes TUI Core Subsystem

**Objective:** Render the four-panel dashboard against a live engine with a 20 Hz throttle, bounded memory, and no event-loop blocking.
**Prerequisites:** 5.4, 5.8, 2.6, 0.3 **Lane:** C

### 7.A Execution Tasks

| Task ID | Task Description & Deliverable | Targeted Files | Prereq | Est |
| :--- | :--- | :--- | :--- | :--- |
| 7.1 | `HermesApp` shell + CSS grid, four regions. | `tui/app.py`, `tui/app.tcss` | 5.4 | 3.0 |
| 7.2 | `#repo-manager`: `DirectoryTree` + `DataTable` on `FILE_CHANGE`/`GIT_STATUS_UPDATE`. | `tui/panels/repo_manager.py` | 7.1, 1.9 | 3.5 |
| 7.3 | `#execution-canvas`: `RichLog` + `Sparkline` on `AGENT_TOKEN_STREAM`/`TEST_PROGRESS`. | `tui/panels/execution_canvas.py` | 7.1 | 3.5 |
| 7.4 | Scrollback cap + spill of older lines to the run artifact file. | `tui/scrollback.py` | 7.3, 0.15 | 2.5 |
| 7.5 | `#model-registry`: provider, p50/p95, TPM burn, cumulative USD from `METRICS_UPDATE`. | `tui/panels/model_registry.py` | 7.1, 4.10 | 3.0 |
| 7.6 | `#critic-bar`: `Input`, PAUSE/RESUME/STOP, HITL Approve/Reject. | `tui/panels/critic_bar.py` | 7.1, 0.3 | 3.0 |
| 7.7 | IPC→UI bridge via `call_from_thread`/`post_message`. | `tui/bridge.py` | 7.1, 2.6 | 3.0 |
| 7.8 | 20 Hz coalescing throttle. | `tui/throttle.py` | 7.7 | 2.5 |
| 7.9 | Keybindings: `Ctrl+C` priority-bound to PAUSE; quit on `Ctrl+Q` with confirm modal. | `tui/bindings.py` | 7.6 | 2.0 |
| 7.10 | Safe renderer: Markdown + diff colorizer escaping Rich markup. | `tui/render.py` | 7.3 | 3.0 |
| 7.11 | CLI entrypoint `dev-harness [--workspace] [--self-check]`. | `cli.py` | 7.1, 5.6 | 3.0 |
| 7.12 | `scripts/verify_phase_07.sh`. | `scripts/verify_phase_07.sh` | 0.18 | 1.5 |

**Phase 7 total: 33.5h**

### 7.B Validation Matrix

| Task ID | Verification Strategy | Test Commands | Success Criteria | Tier |
| :--- | :--- | :--- | :--- | :--- |
| 7.1 | Headless mount + snapshot | `pytest tests/tui/test_layout.py -q` | 4 panel IDs resolve under `run_test()`; 100×30 render matches reviewed snapshot. | PR |
| 7.2 | Event-driven update | `pytest tests/tui/test_repo_manager.py -q` | `GIT_STATUS_UPDATE{branch:"feat/x",dirty:3}` updates cells within 100ms. | PR |
| 7.3 | Stream fidelity | `pytest tests/tui/test_execution_canvas.py -q` | 500 events reassemble exactly; Sparkline length == `TEST_PROGRESS` count. | PR |
| 7.4 | Memory bound | `pytest tests/tui/test_scrollback.py -q -m slow` | 200k lines: RichLog ≤ `max_lines`; RSS growth < 100 MB; spilled lines retrievable in order. | NIGHTLY |
| 7.5 | Metrics render | `pytest tests/tui/test_model_registry.py -q` | No feed → `latency: —`; with feed → p95 to 1 dp, USD to 4 dp. | PR |
| 7.6 | Command publication | `pytest tests/tui/test_critic_bar.py -q` | `#btn-pause` emits exactly one `INTERRUPT_REQUEST{PAUSE}`. | PR |
| 7.7 | Thread safety | `pytest tests/tui/test_bridge.py -q` | 5,000 cross-thread events: 0 `NoActiveAppError`, 0 dropped control events. | PR |
| 7.8 | Throttle rate | `pytest tests/tui/test_throttle.py -q -m timing` | 2s / 10k tokens: ≤44 `RichLog.write` calls; max loop iteration < 50ms. | NIGHTLY |
| 7.9 | Keybinding override | `pytest tests/tui/test_bindings.py -q` | `ctrl+c` leaves app running and emits PAUSE; `ctrl+q` opens modal. | PR |
| 7.10 | Markup injection | `pytest tests/tui/test_render.py -q` | `[bold red]` renders literally; no `MarkupError`. | PR |
| 7.11 | CLI smoke | `dev-harness --workspace ./tmp/ws --self-check` | Exit 0; prints socket, DB, broker and engine health; non-git dir → exit 2 `NotAGitRepository`. | PR |
| 7.12 | Script executes | `bash scripts/verify_phase_07.sh` | Exit 0; acceptance JSON `ACCEPTED`. | PR |

### 7.C Coverage Contract

| Package | Line | Branch | Mutation | Required Test Classes |
| :--- | :--- | :--- | :--- | :--- |
| `tui/` | 75% | 65% | — | unit (pilot-driven), integration, negative, timing |
| `tui/bridge.py`, `tui/throttle.py`, `tui/render.py` | 95% | 90% | — | These three carry logic, not rendering: thread marshalling, coalescing math, and markup escaping. The 75% ledger figure covers widget wiring, not these. |

The split exists because a uniform TUI target either forces meaningless widget tests or lets the genuinely risky marshalling code hide at 60%.

### 7.D Phase Acceptance Protocol

**Deliverable:** an operator-usable dashboard attached to a live engine, driven by a scripted workload.
**Scope Boundary:** no real SDLC content — the canvas renders stub token streams. HITL buttons publish events that nothing yet consumes.
**Permitted Stubs:** `StubWorkload` from Phase 5; recorded `METRICS_UPDATE` feed.

| Step | Action | Command | Expected Evidence |
| :--- | :--- | :--- | :--- |
| 1 | Cold launch | `dev-harness --workspace /tmp/w1` | Daemon autostarts; four panels render; `--self-check` reports engine + broker healthy. |
| 2 | Live stream | Run `StubWorkload` emitting 10k tokens over 30s | Canvas streams smoothly; no visible freeze; `reports/throttle_trace.json` shows ≤20 writes/s. |
| 3 | Repo panel truth | `touch` a tracked file in the workspace | Diff count increments within 1s; branch name matches `git rev-parse --abbrev-ref HEAD`. |
| 4 | Metrics panel truth | Replay the recorded metrics feed | p95 and cumulative USD displayed and updating; values match the feed to the displayed precision. |
| 5 | Pause from UI | Click PAUSE | Engine transitions to `PAUSED`; critic bar reflects it; a sealed checkpoint exists. |
| 6 | `Ctrl+C` behavior | Press `Ctrl+C` | App stays running and pauses. Press `Ctrl+Q` → confirm modal, then clean exit. |
| 7 | Reattach | Kill the TUI (not the daemon), relaunch | New instance shows current state including `PAUSED` on first frame; stream resumes. |
| 8 | Long-run memory | Stream 200k lines | RSS growth < 100 MB; older lines retrievable from the run artifact file. |
| 9 | Degradation | Resize terminal to 60×20 | Single-panel fallback; no exception in logs. |
| 10 | Emit report | `bash scripts/verify_phase_07.sh` | Acceptance JSON `ACCEPTED` with snapshot diffs attached. |

**Rejection Criteria:** any UI freeze > 200ms during step 2; `Ctrl+C` quitting the app; RSS growth beyond bound in step 8; any snapshot baseline committed without review.
**Exit Artifacts:** `reports/phase_07_acceptance.json`, `reports/throttle_trace.json`, reviewed snapshot baselines.

---

## Phase 8: SDLC Pipeline Engine & Parallel Worker Pool

**Objective:** Assemble the Groomer → Architect → Developer → Tester → Critic graph with bounded retries, HITL gates, context budgeting, and parallel chunk execution over isolated worktrees.
**Prerequisites:** 1.5, 1.10, 5.5, 6.2, 3.1, 0.5 **Lane:** D — **human sign-off required**

### 8.A Execution Tasks

| Task ID | Task Description & Deliverable | Targeted Files | Prereq | Est |
| :--- | :--- | :--- | :--- | :--- |
| 8.1 | Persona templates for five roles with output contracts and refusal behavior. | `engine/personas/*.md` | 0.5 | 3.0 |
| 8.2 | Persona output validators with one repair retry. | `engine/personas/validators.py` | 8.1, 0.5 | 3.0 |
| 8.3 | LangGraph channels and reducers. | `engine/state.py` | 0.5, 1.5 | 3.0 |
| 8.4 | Groomer node → `groomed_requirements{LOCKED}`. | `engine/nodes/groomer.py` | 8.2, 8.3 | 3.0 |
| 8.5 | Architect node → `technical_design` with `openapi_spec`, `db_schema`. | `engine/nodes/architect.py` | 8.4 | 3.5 |
| 8.6 | Chunk DAG builder + topological validator (cycles, orphans). | `engine/dag.py` | 8.5 | 3.0 |
| 8.7 | Worker pool honoring DAG readiness and `max_parallel_workers`; sets `assigned_worker_id`. | `engine/worker_pool.py` | 8.6, 1.10 | 4.0 |
| 8.8 | Per-worker worktree binding on `chunk/{chunk_id}`. | `engine/worker_workspace.py` | 8.7, 1.10 | 3.5 |
| 8.9 | Integration merge: sequential fast-forward/rebase with conflict surfacing. | `engine/integrator.py` | 8.8 | 4.0 |
| 8.10 | Developer node with worktree-root write guard. | `engine/nodes/developer.py` | 8.8, 6.4 | 3.5 |
| 8.11 | Tester node: invocation, structured pass/fail, timeout. | `engine/nodes/tester.py` | 8.10 | 3.0 |
| 8.12 | Differential test selector with documented full-suite fallback. | `engine/testing/differential.py` | 8.11 | 3.5 |
| 8.13 | Retry router: inner-loop ceiling 3 → Architect; e2e ceiling 2 → HITL. | `engine/routing.py` | 8.12 | 3.0 |
| 8.14 | Critic node as strict binary gate → `CriticScopeViolation`. | `engine/nodes/critic.py` | 8.3, 6.2 | 3.0 |
| 8.15 | HITL gate via `interrupt_before` + `Command(resume=...)`. | `engine/hitl.py` | 8.14, 7.6 | 3.5 |
| 8.16 | Context budgeting: 50-line trace cap (head 30 / tail 20) + total prompt budget. | `engine/context.py` | 3.8, 3.2 | 3.0 |
| 8.17 | E2E classifier routing with HITL fallback on unparseable reports. | `engine/classifier.py` | 8.13 | 2.5 |
| 8.18 | Graph assembly `build_graph(config)` compiled with `SqliteSaver`. | `engine/pipeline.py` | 8.4–8.17 | 3.0 |
| 8.19 | `scripts/verify_phase_08.sh`. | `scripts/verify_phase_08.sh` | 0.18 | 1.5 |

**Phase 8 total: 59.5h**

### 8.B Validation Matrix

| Task ID | Verification Strategy | Test Commands | Success Criteria | Tier |
| :--- | :--- | :--- | :--- | :--- |
| 8.1 | Contract lint | `pytest tests/engine/test_personas.py -q` | Each file declares `## Output Contract`; keyword scan finds none permitting Critic code edits. | PR |
| 8.2 | Malformed output | `pytest tests/engine/test_persona_validators.py -q` | Truncated JSON → exactly one repair retry, then `PersonaOutputError`; valid output → 0 retries. | PR |
| 8.3 | Reducer semantics | `pytest tests/engine/test_state_reducers.py -q` | Parallel `chunk_dag` appends → 2 entries; concurrent retry increments → exactly +2. | PR |
| 8.4 | Groomer contract | `pytest tests/engine/test_groomer.py -q` | Validates as `GroomedRequirements`; `LOCKED`; re-invocation is a no-op. | PR |
| 8.5 | Architect contract | `pytest tests/engine/test_architect.py -q` | `openapi_spec` parses; empty design leaves `status != APPROVED`. | PR |
| 8.6 | DAG validation | `pytest tests/engine/test_dag.py -q` | Cycle → `CyclicDependencyError` naming both ids; unknown dep → `OrphanDependencyError`; order stable over 10 runs. | PR |
| 8.7 | Scheduling | `pytest tests/engine/test_worker_pool.py -q` | `max_parallel=3` over a 7-chunk diamond: no chunk starts before deps; peak concurrency ≤3; all 7 `COMPLETED`. | PR |
| 8.8 | Worktree isolation | `pytest tests/engine/test_worker_workspace.py -q` | 3 workers writing the same relative path → 3 distinct contents; primary `git status --porcelain` empty throughout. | PR |
| 8.9 | Integration merge | `pytest tests/engine/test_integrator.py -q` | Non-overlapping branches merge clean; overlapping edit → `IntegrationConflict` naming file + both chunk ids; primary unmodified. | PR |
| 8.10 | Escape guard | `pytest tests/engine/test_developer.py -q` | `../../etc/passwd` → `WorkspaceEscapeError`; symlink escape blocked; valid writes stay in the worker's worktree. | PR |
| 8.11 | Tester behavior | `pytest tests/engine/test_tester.py -q` | Passing suite → counts; hanging test killed at timeout and reported `TIMEOUT`, not `FAILED`. | PR |
| 8.12 | Differential selection | `pytest tests/engine/test_differential.py -q` | Exact dependent test set (set equality); unavailable graph → full-suite fallback with logged reason. | PR |
| 8.13 | Retry ceilings | `pytest tests/engine/test_routing.py -q` | Routes to Architect on attempt 4; terminates `FAILED` after e2e ceiling; no infinite loop under `--timeout=120`. | PR |
| 8.14 | Zero-drift | `pytest tests/engine/test_critic_scope.py -q` | Critic writing `groomed_requirements` → `CriticScopeViolation`; PAUSE/RESUME state diff confined to `tui_state`. | PR |
| 8.15 | HITL round trip | `pytest tests/engine/test_hitl.py -q` | Halts with persisted checkpoint; button resume advances exactly one node. | PR |
| 8.16 | Budgeting | `pytest tests/engine/test_context.py -q` | 500-line trace → 50 lines with first and last frames; prompt ≤ context_window − max_output for every registry model. | PR |
| 8.17 | Classifier | `pytest tests/engine/test_classifier.py -q` | 10 labelled fixtures 100% correct; unparseable → HITL, never a guess. | PR |
| 8.18 | Graph E2E (mock LLM) | `pytest tests/engine/test_sdlc_pipeline.py -q` | Requirement → green unit test; final checkpoint schema-valid; `chunk_dag[0].status == COMPLETED`; 0 live network calls. | PR |
| 8.19 | Script executes | `bash scripts/verify_phase_08.sh` | Exit 0; acceptance JSON `ACCEPTED`. | PR |

### 8.C Coverage Contract

| Package | Line | Branch | Mutation | Required Test Classes |
| :--- | :--- | :--- | :--- | :--- |
| `engine/` (nodes, pipeline) | 88% | 80% | — | unit, integration, negative, e2e |
| `engine/dag.py`, `worker_pool.py`, `worker_workspace.py`, `integrator.py` | 95% | 90% | ≥80% | The concurrency surface. A scheduling or isolation bug corrupts work silently and is nearly impossible to reproduce after the fact. |
| `engine/routing.py`, `classifier.py` | 95% | 90% | — | Every branch of every ceiling and every classification path. |

### 8.D Phase Acceptance Protocol

**Deliverable:** an end-to-end SDLC graph that decomposes a requirement, executes chunks in parallel across isolated worktrees, merges them, and checkpoints correctly — driven by a mock LLM.
**Scope Boundary:** no real model quality claims. Correctness of orchestration, not of generated code. Real-model behavior is Phase 10's job.
**Permitted Stubs:** `MockLLM` with scripted persona responses; a fixture target repo with a known-good test suite.

| Step | Action | Command | Expected Evidence |
| :--- | :--- | :--- | :--- |
| 1 | Serial baseline | `python -m dev_harness.engine.cli run --requirement fixtures/req_simple.md --max-parallel 1 --mock` | Completes; all chunks `COMPLETED`; final checkpoint schema-valid. |
| 2 | DAG correctness | `... plan --requirement fixtures/req_diamond.md --mock --print-dag` | Topological order valid; cycle fixture rejected with both ids named. |
| 3 | Parallel execution | `... run --requirement fixtures/req_diamond.md --max-parallel 3 --mock --trace` | Trace shows 3 concurrent workers; no chunk starts before its deps; peak concurrency exactly 3. |
| 4 | Isolation proof | During step 3, snapshot `git worktree list` and each worktree's `git status` | 3 worktrees + primary; primary clean throughout; no file written by two workers. |
| 5 | Merge proof | Inspect the primary branch after step 3 | Contains all chunk changes; `git log --oneline` shows one commit per chunk; no lost writes (diff vs union of worktrees empty). |
| 6 | Conflict surfacing | `... run --requirement fixtures/req_conflict.md --max-parallel 2 --mock` | `IntegrationConflict` names the file and both chunk ids; primary branch unmodified; run terminates cleanly, not mid-merge. |
| 7 | Retry + escalation | `... run --requirement fixtures/req_failing.md --mock` | Inner loop retries 3× then routes to Architect; e2e ceiling reached → HITL; terminal state `FAILED`, never a loop. |
| 8 | HITL gate | `... run --requirement fixtures/req_hitl.md --mock` with a TUI attached | Graph halts at approval; checkpoint persisted; Approve advances exactly one node. |
| 9 | Critic containment | `... critic-drill --mock` (Critic attempts a requirements write) | `CriticScopeViolation`; full state diff confined to `tui_state`. |
| 10 | Budget compliance | Inspect prompts emitted in step 1 | Every prompt ≤ context_window − max_output for its model; traces capped at 50 lines with first and last frames present. |
| 11 | Mutation gate | `python scripts/mutation_gate.py --packages engine.dag,engine.worker_pool,engine.worker_workspace,engine.integrator` | ≥80%; 0 survivors in the readiness check and the worktree binding. |
| 12 | Emit report + human sign-off | `bash scripts/verify_phase_08.sh` | Acceptance JSON `ACCEPTED` with a human `signed_by`. |

**Rejection Criteria:** any lost write in step 5; primary worktree dirty at any point in step 4; any infinite retry; a surviving mutant in the readiness or worktree-binding logic; any live network call.
**Exit Artifacts:** `reports/phase_08_acceptance.json`, `reports/parallel_trace.json`, `git worktree list` snapshots, merge diff proof.

---

## Phase 9: Error Handling, Recovery & Edge Cases

**Objective:** Every failure mode named in the audit becomes observable, recoverable, and covered by a negative test.
**Prerequisites:** Phases 1–8 **Lane:** A/B/C/D

### 9.A Execution Tasks

| Task ID | Task Description & Deliverable | Targeted Files | Prereq | Est |
| :--- | :--- | :--- | :--- | :--- |
| 9.1 | Crash recovery: detect un-finalized session, offer resume-from-checkpoint. | `recovery/session_recovery.py` | 8.18, 1.12 | 3.5 |
| 9.2 | Stale artifact reclamation (sockets, locks, worktrees). | `recovery/reclaim.py` | 2.3, 1.8, 1.10 | 3.0 |
| 9.3 | Provider fallback chain with `DEGRADED` notice. | `broker/fallback.py` | 4.9, 3.3 | 3.0 |
| 9.4 | Corrupt checkpoint detection via `state_sha256`; quarantine + rollback. | `storage/integrity.py` | 1.4 | 3.0 |
| 9.5 | Terminal degradation below 80×24. | `tui/responsive.py` | 7.1 | 2.5 |
| 9.6 | Git edge cases: detached HEAD, unborn branch, submodules, pre-existing worktrees. | `vcs/edge_cases.py` | 1.12, 1.10 | 3.0 |
| 9.7 | Canvas-level secret redaction. | `tui/render.py`, `observability/redact.py` | 0.14, 7.10 | 2.0 |
| 9.8 | Resource exhaustion: `ENOSPC`, `SQLITE_BUSY` → actionable banner. | `storage/errors.py` | 1.1, 0.4 | 2.5 |
| 9.9 | Context-overflow recovery: summarize-and-retry once, then escalate. | `engine/overflow.py` | 8.16, 3.3 | 3.0 |
| 9.10 | Phase rollback procedures per phase. | `docs/rollback.md` | — | 2.0 |
| 9.11 | `scripts/verify_phase_09.sh` + chaos drill runner. | `scripts/verify_phase_09.sh`, `scripts/chaos_drill.py` | 0.18 | 1.5 |

**Phase 9 total: 29.0h**

### 9.B Validation Matrix

| Task ID | Verification Strategy | Test Commands | Success Criteria | Tier |
| :--- | :--- | :--- | :--- | :--- |
| 9.1 | Kill-and-resume | `pytest tests/recovery/test_session_recovery.py -q -m e2e` | SIGKILL mid-run then restart: state equals last seal field-for-field; worktree SHA matches. | NIGHTLY |
| 9.2 | Reclamation | `pytest tests/recovery/test_reclaim.py -q` | Dead-PID socket, lock and worktree removed; fresh session < 2s; live-PID artifacts untouched. | PR |
| 9.3 | Fallback | `pytest tests/broker/test_fallback.py -q` | 529 → secondary within 1 retry; registry shows `DEGRADED`; exhausted → `AllProvidersUnavailable`. | PR |
| 9.4 | Corruption | `pytest tests/storage/test_integrity.py -q` | Byte flip fails digest, row quarantined, `get_tuple()` returns prior valid checkpoint. | PR |
| 9.5 | Small terminal | `pytest tests/tui/test_responsive.py -q` | `run_test(size=(60,20))` mounts; only `#execution-canvas` visible. | PR |
| 9.6 | Git edges | `pytest tests/vcs/test_edge_cases.py -q` | Unborn → `NoCommitsError`; detached restore succeeds and reports detached; duplicate worktree → `WorktreeExistsError`. | PR |
| 9.7 | Canvas redaction | `pytest tests/tui/test_canvas_redaction.py -q` | Key absent from widget buffer, logs and run artifacts. | PR |
| 9.8 | Exhaustion | `pytest tests/storage/test_errors.py -q` | `ENOSPC` and `SQLITE_BUSY` → `HarnessError` with remediation; 0 unhandled tracebacks reach the TUI. | PR |
| 9.9 | Overflow recovery | `pytest tests/engine/test_overflow.py -q` | One summarize-retry, then HITL escalation; 0 infinite loops. | PR |
| 9.10 | Rollback rehearsal | Manual per `docs/rollback.md` on a scratch clone | Migration `0001` rolls back clean; app starts on prior tag; documented time < 15 min. | REL |
| 9.11 | Chaos drill | `python scripts/chaos_drill.py --all` | All 8 injected faults produce the documented error and a recoverable system. | NIGHTLY |

### 9.C Coverage Contract

| Package | Line | Branch | Mutation | Required Test Classes |
| :--- | :--- | :--- | :--- | :--- |
| `recovery/` | 90% | 85% | — | negative, integration, e2e |
| `storage/integrity.py` | 100% | 95% | ≥85% | The last line of defence against serving corrupt state. |
| Cross-cutting | — | — | — | **Error reachability gate:** every `HarnessError` subclass reachable by ≥1 `negative` test through real behavior. `scripts/coverage_gate.py --errors` fails on any unreachable subclass. |

That error-reachability gate is the phase's most important metric — more than any percentage, since the whole phase exists to make failures behave.

### 9.D Phase Acceptance Protocol

**Deliverable:** a system that survives a scripted chaos drill and recovers without manual filesystem surgery.
**Scope Boundary:** no new features. If a step requires new functionality rather than new handling, it belongs to the owning phase.
**Permitted Stubs:** `MockLLM`; fault injection via `FakeProviderServer` and `chaos_drill.py`.

| Step | Action | Command | Expected Evidence |
| :--- | :--- | :--- | :--- |
| 1 | Error reachability | `python scripts/coverage_gate.py --errors` | Every `HarnessError` subclass has ≥1 producing `negative` test; exit 0. |
| 2 | Kill-9 during a run | `python scripts/chaos_drill.py --fault kill9-engine` | Restart detects the un-finalized session and resumes from the last seal; state matches field-for-field. |
| 3 | Kill-9 with worktrees live | `... --fault kill9-parallel` | Stale worktrees reclaimed on restart; `git worktree list` clean; no manual `git worktree prune` needed. |
| 4 | Corrupt a checkpoint | `... --fault corrupt-checkpoint` | Digest mismatch detected; row quarantined; prior checkpoint served; run continues. |
| 5 | Disk full | `... --fault enospc` | Actionable banner with remediation; 0 tracebacks in the TUI; DB not corrupted (integrity check passes after recovery). |
| 6 | Provider outage | `... --fault provider-529` | Falls back to secondary within 1 retry; registry shows `DEGRADED`; run completes. |
| 7 | Total provider outage | `... --fault all-providers-down` | `AllProvidersUnavailable`; run halts with a resumable checkpoint, not a crash. |
| 8 | Context overflow | `... --fault oversized-context` | One summarize-retry, then clean HITL escalation. |
| 9 | Secret leak attempt | `... --fault echo-api-key` | Key redacted in canvas, logs and run artifacts simultaneously (all three greps empty). |
| 10 | Rollback rehearsal | Follow `docs/rollback.md` for Phase 1 on a scratch clone | Clean rollback in < 15 min, timed and recorded. |
| 11 | Emit report | `bash scripts/verify_phase_09.sh` | Acceptance JSON `ACCEPTED` with the fault matrix attached. |

**Rejection Criteria:** any fault requiring manual filesystem cleanup; any unhandled traceback surfacing to the TUI; any unreachable `HarnessError` subclass; a raw key found in any of the three sinks in step 9.
**Exit Artifacts:** `reports/phase_09_acceptance.json`, `reports/chaos_matrix.json`, rollback rehearsal timing log.

---

## Phase 10: Final Verification, Traceability & Release

**Objective:** Prove the assembled system against the TDD and gate release on measurable thresholds.
**Prerequisites:** Phases 0–9 green **Lane:** All — **human sign-off required**

### 10.A Execution Tasks

| Task ID | Task Description & Deliverable | Targeted Files | Prereq | Est |
| :--- | :--- | :--- | :--- | :--- |
| 10.1 | E2E: requirement → tested chunk, with scripted mid-run PAUSE/RESUME. | `tests/e2e/test_full_sdlc.py` | 9.* | 4.0 |
| 10.2 | Parallel E2E: 3-wide pool through integration merge. | `tests/e2e/test_parallel_sdlc.py` | 10.1 | 3.5 |
| 10.3 | Two-project 30-minute concurrent soak. | `tests/e2e/test_concurrent_soak.py` | 10.1 | 3.0 |
| 10.4 | Local-only profile run (Ollama + Qwen 2.5 Coder, no hosted providers). | `tests/e2e/test_local_profile.py`, `profiles/local.toml` | 10.1, 3.7 | 3.5 |
| 10.5 | CI tiering: PR suite < 10 min; nightly runs `timing`/`slow`/`e2e`/mutation. | `.github/workflows/ci.yml`, `nightly.yml` | 0.2, 0.16 | 3.0 |
| 10.6 | Schema conformance regression over every E2E checkpoint. | `tests/e2e/test_schema_conformance.py` | 10.1 | 2.0 |
| 10.7 | Traceability generator: TDD § → task → test, failing on gaps. | `scripts/generate_traceability.py`, `docs/traceability.md` | 10.1 | 3.0 |
| 10.8 | Packaging, README, operator runbook. | `README.md`, `docs/runbook.md` | 10.1, 9.10 | 3.5 |
| 10.9 | `scripts/verify_phase_10.sh` + release checklist. | `scripts/verify_phase_10.sh`, `docs/release_checklist.md` | 0.18 | 1.5 |

**Phase 10 total: 27.0h**

### 10.B Validation Matrix

| Task ID | Verification Strategy | Test Commands | Success Criteria | Tier |
| :--- | :--- | :--- | :--- | :--- |
| 10.1 | Full E2E | `pytest tests/e2e/test_full_sdlc.py -q --timeout=900` | Completes; PAUSE seals `is_paused=True`; RESUME continues from that checkpoint; final chunk `COMPLETED`; `pgrep -g` empty. | NIGHTLY |
| 10.2 | Parallel E2E | `pytest tests/e2e/test_parallel_sdlc.py -q --timeout=1200` | 3 concurrent worktrees; merge clean; primary contains all three chunks; 0 lost writes. | NIGHTLY |
| 10.3 | Soak | `pytest tests/e2e/test_concurrent_soak.py -q --timeout=2400` | 0 `database is locked`; 0 cross-project rows; RSS growth < 15% over 30 min; grants within ceilings. | NIGHTLY |
| 10.4 | Local profile | `pytest tests/e2e/test_local_profile.py -q -m slow` | SDLC completes against Ollama; 0 hosted calls; `num_ctx` respected; ≤1 model load; cumulative USD 0.00. | NIGHTLY |
| 10.5 | CI budget | `time make ci` | PR suite < 10 min on the standard runner; nightly reports separately. | PR |
| 10.6 | Schema regression | `pytest tests/e2e/test_schema_conformance.py -q` | 100% of emitted checkpoints validate; added field fails until schema + fixture updated together. | PR |
| 10.7 | Traceability | `python scripts/generate_traceability.py --check` | Every TDD section 1–6 maps to ≥1 task and ≥1 passing test; exit 1 on any gap. | REL |
| 10.8 | Runbook dry run | Manual on a clean machine | New operator reaches a running four-panel TUI with live broker + engine in < 10 min without reading source. | REL |
| 10.9 | Release gate | `bash scripts/verify_phase_10.sh` | All phase acceptance reports present and `ACCEPTED`; checklist complete. | REL |

### 10.C Coverage Contract

| Scope | Line | Branch | Mutation | Notes |
| :--- | :--- | :--- | :--- | :--- |
| Whole repo | 88% | 80% | — | The weighted aggregate of §2.3, verified at release rather than assumed. |
| `storage/`, `vcs/`, `broker/`, `core/` | per ledger | per ledger | per ledger | Mutation gates re-run at release on the merged trunk, not only per-PR. |
| E2E path coverage | — | — | — | Every node in the compiled graph must be executed at least once across 10.1 + 10.2, asserted programmatically from the run trace. |

The graph-node coverage assertion exists because a pipeline can hit 88% line coverage while an entire node never runs in any end-to-end test.

### 10.D Phase Acceptance Protocol

**Deliverable:** a tagged, installable release with a validated runbook and a complete traceability report.
**Scope Boundary:** none — this is the only phase with no stubs permitted on the critical path, other than `MockLLM` in 10.1–10.3 for determinism. 10.4 is the un-stubbed reality check.
**Permitted Stubs:** `MockLLM` for deterministic E2E only; **none** in step 4 below.

| Step | Action | Command | Expected Evidence |
| :--- | :--- | :--- | :--- |
| 1 | Phase report audit | `python scripts/verify_phase.py --audit-all` | All 11 acceptance reports present, `ACCEPTED`, commit-pinned; 5/8/10 carry human signatures. |
| 2 | Full nightly | Trigger `nightly.yml` | 10.1–10.4 green; mutation gates green on trunk. |
| 3 | Graph-node coverage | `python scripts/generate_traceability.py --graph-coverage` | Every compiled node executed ≥1× across the E2E runs; exit 1 on any unexecuted node. |
| 4 | Real-model run | `pytest tests/e2e/test_local_profile.py -q` against a live local Ollama | SDLC completes on Qwen 2.5 Coder 7B; failure rate per persona recorded in the report (this is the R5 measurement). |
| 5 | Cold-machine install | On a fresh VM: clone, `pip install .`, follow `docs/runbook.md` | Four-panel TUI + healthy broker + engine in < 10 min, by someone who did not write the runbook. |
| 6 | Traceability | `python scripts/generate_traceability.py --check` | Exit 0; `docs/traceability.md` regenerated and committed. |
| 7 | Rollback rehearsal | Follow `docs/rollback.md` from the release tag to the prior tag | Completes < 15 min with no data loss. |
| 8 | Release checklist | `docs/release_checklist.md` | Every item checked and initialed. |
| 9 | Tag and sign | `bash scripts/verify_phase_10.sh && git tag v1.0.0` | Acceptance JSON `ACCEPTED` with human `signed_by`. |

**Rejection Criteria:** any phase report missing, unsigned, or not commit-pinned; any unexecuted graph node; runbook requiring source code to complete; traceability gaps.
**Exit Artifacts:** `reports/phase_10_acceptance.json`, `docs/traceability.md`, `reports/local_profile_run.json`, signed release checklist, tag `v1.0.0`.

---

# Part III — Program Data

## 11. Effort Model & Critical Path

| Phase | Hours | Lane | Earliest Start | Human Sign-off |
| :--- | :--- | :--- | :--- | :--- |
| P0 Scaffolding, Contracts & Test Infra | 46.0 | A | Day 0 | — |
| P1 Persistence | 37.0 | A | after P0 | — |
| P2 IPC | 21.0 | A | after P0 (∥ P1) | — |
| P3 Providers | 31.0 | B | after P0 (∥ P1/P2) | — |
| P4 Broker & Cost | 29.5 | B | after P3 | — |
| P5 Engine Daemon | 24.0 | D | after P1, P2, P4 | Yes |
| P6 Critic | 20.5 | D | after P5 | — |
| P7 TUI | 33.5 | C | after P5 (∥ P6) | — |
| P8 Pipeline & Workers | 59.5 | D | after P6, P7 | Yes |
| P9 Error Handling | 29.0 | A/B/C/D | after P8 | — |
| P10 Verification & Release | 27.0 | All | after P9 | Yes |
| **Total** | **358.0** | | | |

**Critical path:** P0 → P3 → P4 → P5 → P6 → P8 → P9 → P10 = **266.5h** (74% of the serial total). Three concurrent workers on lanes A/B/C buys roughly 26%. The chain through providers → broker → engine → pipeline cannot be shortened by adding workers.

**Scheduling consequence:** staffing beyond 3 workers before P8 is waste. The fan-out point is P8, where the worker pool itself becomes the parallel mechanism.

## 12. Changelog

### V10 over V9

| # | Addition | Why |
| :--- | :--- | :--- |
| A1 | §2 Test Strategy: taxonomy with 8 markers, measurement policy, anti-pattern table. | V9 named test commands but never defined what a test class *is* or what constitutes acceptable evidence. |
| A2 | §2.3 Coverage Ledger: per-package line/branch/mutation targets with written rationale. | V9's 85%/95% figures were asserted, not derived — the same sin V7 committed with "100% Validated". |
| A3 | Mutation testing on `storage/`, `vcs/`, `broker/`, `core/` with named focus mutants. | Coverage proves execution, not assertion strength. These four packages fail silently. |
| A4 | Per-phase Coverage Contract sections (`X.C`). | Makes the ledger enforceable at the phase gate rather than only at release. |
| A5 | Per-phase Acceptance Protocol (`X.D`) with Deliverable, Scope Boundary, Permitted Stubs, ordered steps, Rejection Criteria, Exit Artifacts. | V9 had per-task validation but no way to evaluate a *phase deliverable in its own scope*. This was the explicit gap. |
| A6 | `scripts/verify_phase_NN.sh` per phase + shared runner (0.18). | Acceptance must be reproducible by an agent, not a remembered ritual. |
| A7 | Acceptance report JSON + signing rule; implementer may not self-sign; human gates on P5/P8/P10. | An agent reviewing its own work is not assurance at the process-model, concurrency and release gates. |
| A8 | Error-reachability gate (9.C). | Percentage coverage cannot tell you whether a declared error is actually producible. |
| A9 | Graph-node coverage assertion (10.C). | A pipeline can pass 88% coverage with an entire node never executed end-to-end. |
| A10 | Coverage ratchet + bare-pragma detector (0.16). | Prevents slow erosion and pragma-based cheating. |
| A11 | Marker enforcement — unmarked tests fail collection (0.18). | A taxonomy nobody is forced to use is documentation, not policy. |
| A12 | Chaos drill runner (9.11) replacing ad-hoc fault tests. | Makes Phase 9 evaluable as a deliverable rather than a pile of negative tests. |
| A13 | `tui/` split contract: 75% for widgets, 95% for `bridge`/`throttle`/`render`. | A uniform TUI number either forces junk widget tests or lets thread-marshalling hide at 60%. |

### V9 over V8 (retained for provenance)

Critical: no LLM provider layer (→ P3); no execution engine process (→ P5); test rig assumed but never built (→ 0.12/0.13); parallel workers sharing one git worktree (→ 1.10, 8.7–8.9). High: no cost governance (→ 4.6/4.7); no effort model or critical path (→ §11); timing tests PR-blocking (→ marker + NIGHTLY tier). Medium: oversized tasks split; phase gates and rollback added; unbounded checkpoint and scrollback growth capped; secrets loading specified; run transcripts added; mypy strict widened; persona-output and context-overflow recovery added; Ollama swap thrash addressed; local-only profile made a first-class configuration; IPC contract drift test added.

## 13. Residual Risks (Accepted, Not Fixed)

| # | Risk | Why Accepted | Trigger To Revisit | Measured By |
| :--- | :--- | :--- | :--- | :--- |
| R1 | LLM-authored code executes on the host with path and process-group guards but no container sandbox. | Containerization adds ~40h and a Docker dependency to a local dev tool. | First untrusted-requirement use case or any multi-user deployment. | — |
| R2 | POSIX-only (`AF_UNIX`, `flock`, `killpg`). | WSL2 is supported; native Windows would fork the transport layer. | A Windows-native requirement. | 2.7 |
| R3 | Differential test selection can miss transitively affected tests. | Full suite runs at the P10 gate. | Any escaped defect traced to selection. | 8.12, 10.1 |
| R4 | Single-writer assumption on the primary branch during integration. | Merge is sequential by design. | Multi-repo or multi-user harness use. | 8.9 |
| R5 | Qwen 2.5 Coder 7B may be too weak for the Architect persona. | Provider abstraction permits per-node model assignment. | Architect failure rate > 30%. | 10.4 step 4 |
| R6 | Mutation testing is scoped to 4 packages, not the whole repo. | Full-repo mutation runs would exceed the nightly budget. | Nightly budget doubles, or a silent-failure defect escapes outside the scoped set. | 0.17 |

## 14. Traceability: TDD Section → Tasks → Tests

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

## 15. Self-Assessment

Scored against the rubric this document has applied to its predecessors.

| Criterion | Weight | Score | Justification |
| :--- | :--- | :--- | :--- |
| Buildability — could a team follow this and get a running system? | 25% | 10 | Every layer present and ordered: contracts, storage, transport, providers, broker, engine process, UI, pipeline. No phase depends on something nothing builds. |
| Task granularity | 10% | 10 | 100 tasks, none over 4h, each with a named file deliverable. |
| Testability of exit criteria | 20% | 10 | Every task has an exact command and an exact assertion. No "verify it works". |
| Test strategy rigor | 15% | 9 | Taxonomy, branch coverage, ratchet, mutation gates, anti-pattern bans, error-reachability. Loses a point: mutation scoped to 4 packages (R6), and `engine/` — the largest surface — has no mutation gate. |
| Phase evaluability | 15% | 10 | Every phase has a scoped, scripted acceptance protocol with permitted stubs, rejection criteria, and a signed report. |
| Schedulability | 10% | 9 | Hours, lanes, critical path, staffing conclusion. Loses a point: estimates are unvalidated first-pass figures with no confidence interval; expect ±30% until P0 actuals land. |
| Honesty about limits | 5% | 10 | Six residual risks, each with a revisit trigger and a measurement task. Nothing is claimed as validated before it runs. |

**Weighted score: 9.7 / 10.**

The two deductions are real and deliberate rather than oversights: mutation coverage stops at the four highest-risk packages because full-repo mutation would blow the nightly budget, and the effort figures are estimates that only P0 actuals can calibrate. Both are recorded as R6 and as the ±30% note here rather than being smoothed over. Every remaining weakness in this program is now an execution risk, not a planning gap — which is the correct place for it to sit before Day 0.
