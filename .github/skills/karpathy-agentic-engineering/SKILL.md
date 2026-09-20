---
name: karpathy-agentic-engineering
description: 'Karpathy-style agentic engineering loop. Use when implementing a feature or task: think before coding, surface assumptions and tradeoffs, keep it simple, make surgical changes, define a checkable success criterion before calling work done. One reviewable increment per round; run tests before continuing.'
user-invocable: true
---

# Karpathy Agentic Engineering

## When to Use

- Implementing any feature or task
- Planning before writing code
- Reviewing whether work is actually done

## The Loop

1. Inject project context: goal, constraints.
2. Rewrite the request as a verifiable success criterion.
3. Propose the approach and tradeoffs before touching code.
4. Deliver one reviewable increment (one diff).
5. Run the tests; fix until green.
6. Human taste-review; then next round.

## Rules

- **Think before coding** — surface assumptions, confusion, alternatives, and tradeoffs before writing code.
- **Define and verify the goal** — turn the request into a checkable outcome before calling it done.
- **Never call work done without a clear success check.**
- **Never edit unrelated code.**
- **One increment per round — no 20-file changes.**

## Related

- `ponytail` — the code-discipline ladder (keep it simple, reuse, stdlib). Load it before writing code.