---
name: nodejs-typescript
description: 'TypeScript strict coding standards. Use when writing or fixing TypeScript/Node.js code: strict mode, no any, typed errors, async/await patterns, explicit return types. Covers tsconfig, ESLint, and error handling conventions.'
user-invocable: true
---

# Node.js TypeScript Standards

## When to Use

- Writing or fixing TypeScript/Node.js code
- Setting up tsconfig or ESLint
- Reviewing typed error handling

## Standards

1. **Strict mode**: `strict: true` in tsconfig; no `any` leaks; no untyped function signatures.
2. **Typed errors**: every error is a typed class or a discriminated union; never throw bare strings or `Error` without context.
3. **Async/await**: prefer async/await over raw promises; never fire-and-forget without error handling.
4. **Explicit returns**: annotate function return types; no implicit `any` from inference gaps.
5. **ESLint**: zero findings on the configured ruleset.

## Procedure

1. Read the code the change touches; trace the real flow.
2. Write typed code; annotate boundaries.
3. Run the typecheck and lint; fix all findings.