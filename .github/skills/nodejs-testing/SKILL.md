---
name: nodejs-testing
description: 'Deterministic Node.js testing with vitest/jest. Use when writing or fixing tests: mock at process/IO boundaries, never sleep, use fake timers, assert exact values not truthiness. Covers unit and integration test patterns.'
user-invocable: true
---

# Node.js Testing

## When to Use

- Writing or fixing tests
- Debugging flaky tests

## Rules

1. **Mock at boundaries only** — fake process/IO, never the unit under test's own internals.
2. **No sleeps** — use fake timers (`vi.useFakeTimers()`) or event-driven waits.
3. **Assert exact values** — never `expect(result).toBeTruthy()`; assert the exact value or a named invariant.
4. **Deterministic** — same input, same result; seed any randomness.
5. **One concern per test** — name tests by behavior, not implementation.

## Procedure

1. Write the test for the behavior, not the implementation.
2. Fake only at process/IO boundaries.
3. Run the suite; fix until green and deterministic.