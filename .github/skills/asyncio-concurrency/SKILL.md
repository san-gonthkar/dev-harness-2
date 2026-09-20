---
name: asyncio-concurrency
description: 'asyncio concurrency and process control per the V11 plan. Use when working with IPC backpressure, task cancellation, subprocess groups, killpg escalation, or the token bucket. Covers the core/ mutation focus set and interrupt safety.'
user-invocable: true
---

# asyncio & Concurrency (V11 Phases 2, 4, 6)

## When to Use

- Working with `ipc/` (backpressure queue), `broker/` (token bucket), or `core/` (task registry, process groups, signals)
- Any code that cancels tasks, kills processes, or manages concurrency

## Backpressure Queue (task 2.6)

- Bounded `asyncio.Queue(maxsize=2048)`.
- `AGENT_TOKEN_STREAM` uses drop-oldest with a dropped-frame counter.
- Control events (`INTERRUPT_REQUEST`, etc.) never drop.

## Token Bucket (task 4.1)

- On `time.monotonic()`, fractional refill, thread-safe acquire with timeout.
- 50-capacity bucket grants exactly 50 in window 1, 0 until refill; no drift over 10 simulated minutes.

## Task Registry (task 6.3)

- Per `thread_id`; `cancel_all()` with 1s join.
- After cancellation, `asyncio.all_tasks()` holds only the test task.

## Process Groups (task 6.4)

- Subprocesses start with `start_new_session=True`; PGID registry.
- `getpgid(child) != getpgid(0)`; exactly one PGID per runner.

## Signal Escalation (task 6.5)

- `killpg(SIGINT)` → 3.0s grace → `killpg(SIGKILL)` with `waitpid` reaping.
- Runner with 3 grandchildren ignoring SIGINT: `pgrep -g {pgid}` empty within 4.0s; 0 zombies.
- A mutant that skips the `SIGKILL` escalation, shortens/lengthens the grace window, or omits `waitpid` must be killed.

## Interrupt SLO (task 6.7)

- 50 trials: p95 < 500ms, max < 1000ms.

## Mutation Focus Set

`core/signals.py` — every escalation branch (grace expiry, early exit, already-dead PGID, reap failure) must be covered.