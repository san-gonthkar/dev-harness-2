---
name: nodejs-tooling
description: 'Node.js tooling and package management. Use when working with package.json, npm/pnpm, build scripts, or Node.js lifecycle hooks. Covers dependency pinning, script conventions, and hook safety.'
user-invocable: true
---

# Node.js Tooling

## When to Use

- Working with package.json, npm/pnpm
- Writing build or lifecycle scripts
- Adding or reviewing dependencies

## Rules

1. **Pin dependencies** — exact versions or lockfile; never floating ranges for runtime deps.
2. **Scripts are documented** — every package.json script has a one-line purpose.
3. **Hooks are safe** — lifecycle hooks must be idempotent, fail loudly, and never block on interactive input.
4. **Minimal deps** — prefer stdlib and existing deps; audit transitive trees before adding.
5. **Node version** — declare `engines` and match the workspace's Node version.

## Procedure

1. Check the existing package.json and lockfile before changing anything.
2. Add dependencies only with justification.
3. Run the scripts; verify they work from a clean checkout.