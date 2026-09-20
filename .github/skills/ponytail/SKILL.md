---
name: ponytail
description: 'Lazy senior dev ruleset. Use when writing or editing code: stop at the first YAGNI rung that holds — skip what need not exist, reuse what exists, prefer stdlib and native features, one line over a component. Never cut validation, error handling, security, or accessibility. Read the code before choosing the rung.'
user-invocable: true
---

# Ponytail — The Lazy Senior Dev

## When to Use

- Writing or editing any code
- Before adding a dependency, abstraction, or component
- When a change feels larger than the task

## The Ladder

Before writing code, stop at the first rung that holds:

1. Does this need to exist? → no: skip it (YAGNI)
2. Already in this codebase? → reuse it, don't rewrite
3. Stdlib does it? → use it
4. Native platform feature? → use it
5. Installed dependency? → use it
6. One line? → one line
7. Only then: the minimum that works

## Rules

- **Lazy about the solution, never about reading**: read the code the change touches and trace the real flow before picking a rung.
- **Never cut**: trust-boundary validation, data-loss handling, security, accessibility.
- **Mark deliberate corner-cuts** with a `ponytail:` comment so they are reviewable: `# ponytail: stdlib covers this`.

## Procedure

1. Read the code the change touches; trace the real flow.
2. Walk the ladder; stop at the first rung that holds.
3. Write only what the task needs — never speculative features or abstractions.
4. Keep every safety guard.

## Related

- `ponytail-review` — review a diff for over-engineering.