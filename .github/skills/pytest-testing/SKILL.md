---
name: pytest-testing
description: 'Pytest testing per the V11 plan test strategy. Use when writing or fixing tests: declare exactly one marker, use the frozen clock and MockLLM/FakeProviderServer, assert exact values, cover defects with negative tests, satisfy branch coverage and mutation focus sets. Covers pytest-asyncio, pytest-socket, and the coverage ratchet.'
user-invocable: true
---

# Pytest Testing (V11 §2)

## When to Use

- Writing or fixing any test in this workspace
- Running the validation matrix command for a task
- Debugging a flaky or failing test

## The Marker Rule

Every test declares **exactly one marker**. Unmarked tests fail collection (enforced by 0.18).

| Marker | Purpose | Tier |
| :--- | :--- | :--- |
| `unit` | One module, all collaborators faked | PR |
| `property` | Hypothesis-driven invariants | PR |
| `contract` | Producer output parsed by real consumer parser | PR |
| `integration` | 2+ real subsystems, real SQLite, real sockets, fake providers | PR |
| `negative` | Asserts the correct failure — type, message, state after | PR |
| `timing` | Latency/throughput SLOs, machine-sensitive | NIGHTLY |
| `slow` | Soaks, memory-growth, large-volume | NIGHTLY |
| `e2e` | Whole system through the daemon | NIGHTLY |

```python
@pytest.mark.negative
def test_unscoped_query_raises() -> None:
    ...
```

## Rules

1. **Cover the defect class with a `negative` test before the happy path is done.** Every `HarnessError` subclass must be reachable by ≥1 `negative` test through real system behavior — never by direct construction.
2. **No `time.sleep()`.** Use the frozen clock fixture (`tests/support/clock.py`) or event-driven waits.
3. **No live network calls.** Use `MockLLM` / `FakeProviderServer` from `tests/support/`; the `--disable-socket` suite must pass.
4. **Assert exact values, never truthiness.** `assert result is not None` is rejected by review — assert the exact value or a named invariant.
5. **Mock only at process/IO boundaries.** Never mock the unit under test's own internals.
6. **Branch coverage is mandatory** (`--cov-branch`). The ratchet blocks any PR that drops a package by >0.5pp.
7. **`# pragma: no cover` requires a trailing justification comment** on the same line. Bare pragmas fail CI.

## Async Tests

`pytest-asyncio` runs with `asyncio_mode=auto` (task 0.18). Async tests need no decorator:

```python
async def test_fanout_ordering() -> None:
    ...
```

## Mutation Gates

For `storage/`, `vcs/`, `broker/`, `core/`, and the engine concurrency modules, run the mutation gate and kill any surviving mutant in the phase's named focus set:

```bash
python scripts/mutation_gate.py --packages <package>
```