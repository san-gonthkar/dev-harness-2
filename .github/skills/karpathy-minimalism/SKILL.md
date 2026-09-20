---
name: karpathy-minimalism
description: 'Dependency minimalism and supply-chain hygiene. Use when adding a dependency, choosing between a library and hand-written code, or reviewing pinned requirements. Decision tree: implement in 50 lines if possible; check transitive dependency count; audit postinstall hooks; pin versions.'
user-invocable: true
---

# Karpathy Minimalism

## When to Use

- Adding a dependency
- Choosing between a library and hand-written code
- Reviewing `pyproject.toml` or pinned requirements

## Decision Tree

Need this functionality?

- Can it be implemented in 50 lines or fewer? → write it yourself.
- Otherwise, how many transitive dependencies does the library pull?
  - Fewer than 5 → consider it.
  - More than 20 → red flag. Find an alternative or vendor the core code.

## Supply-Chain Checks (before any install)

1. Audit the transitive dependency tree.
2. Check for postinstall hooks — each is a permission grant.
3. Prefer pinned versions and a lockfile.
4. Record the justification for every dependency added.

## Rules

- Every layer of abstraction is a wall to understanding — do not add one casually.
- A dependency is an attack surface; treat each install as an act of trust.