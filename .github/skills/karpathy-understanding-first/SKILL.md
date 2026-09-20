---
name: karpathy-understanding-first
description: 'Understanding-first output contract. Use when delivering code, analysis, or research: append key assumptions, verified items, unverified items, and what the user must check themselves. You can outsource thinking, but you cannot outsource understanding.'
user-invocable: true
---

# Karpathy Understanding First

## When to Use

- Delivering any code, analysis, or research output
- Ending a task where the user must trust the result

## The Output Contract

Append this section to every delivery:

## What You Need to Understand

- Key assumptions: [list]
- Verified: [evidence]
- Still speculative: [list, with a verification method]
- Check yourself: [specific files, lines, or configs]

## Rules

- Never present speculation as verified.
- Always name what the user must check personally.
- If you cannot verify it, say so and give the method.