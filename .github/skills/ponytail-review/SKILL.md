---
name: ponytail-review
description: 'Review code for over-engineering. Use when reviewing a diff or PR, or when a change looks larger than its task. Outputs a delete-list: code, abstractions, and dependencies that can be removed or replaced with simpler equivalents, applying the ponytail ladder.'
user-invocable: true
---

# Ponytail Review

## When to Use

- Reviewing a diff before merge
- Reviewing a PR
- Auditing a change that feels over-built

## Procedure

1. Read the diff; identify every file, abstraction, and dependency added.
2. Apply the ponytail ladder to each addition:
   - Does it need to exist? (YAGNI)
   - Could it reuse existing code?
   - Does stdlib or a native feature cover it?
   - Could it be one line?
3. Check safety: validation, error handling, security, accessibility must stay.
4. Produce a delete-list.

## Output Format

A delete-list, one item per line:

- `path/to/file.py` — delete: reason
- `path/to/abstraction.py` — replace with stdlib `x`: reason
- `dependency` — remove: reason

Keep items you cannot justify deleting. Never propose deleting a safety guard.