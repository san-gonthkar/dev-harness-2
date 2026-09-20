---
name: git-worktrees
description: 'Git and worktree operations per the V11 plan. Use when working with the git adapter, worktree manager, checkpoint-to-git binding, restore with dirty-tree handling, or git edge cases. Covers the vcs/ mutation focus set and destructive-operation safety.'
user-invocable: true
---

# Git & Worktrees (V11 Phase 1, 8)

## When to Use

- Working with `vcs/` — git adapter, worktree manager, restore, edge cases
- Any operation that checks out, resets, or removes work

## Git Adapter (task 1.9)

`head_sha()`, `active_branch()`, `is_dirty()`, `uncommitted_count()`. Must match `git rev-parse HEAD` exactly.

## Worktree Manager (task 1.10)

- Create/destroy `.dev-harness/worktrees/{worker_id}` bound to a branch.
- 3 concurrent worktrees have distinct paths/branches; destroy leaves only the primary.
- A file written in worktree A is absent in worktree B.

## Checkpoint↔Git Binding (task 1.11)

- Inside the workspace lock: every checkpoint's `git_commit_hash` equals HEAD at write time and passes `git cat-file -e`.

## Restore (task 1.12)

- Dirty tree → `DirtyWorktreeError`; `git status --porcelain` byte-identical before/after.
- Autostash reapplies; `git stash list` empty after.
- A mutant that skips the dirty check must be killed.

## Edge Cases (task 9.6)

- Unborn branch → `NoCommitsError`.
- Detached HEAD restore succeeds and reports detached.
- Duplicate worktree → `WorktreeExistsError`.

## Safety Rules

- **Never** run `git checkout`/`git reset`/`git worktree remove` without the dirty-tree guard.
- Worktree removal destroys user work — always verify the target is a harness-created worktree under `.dev-harness/worktrees/`.

## Mutation Focus Set

`restore.py` (dirty check) — a surviving mutant fails the phase gate.