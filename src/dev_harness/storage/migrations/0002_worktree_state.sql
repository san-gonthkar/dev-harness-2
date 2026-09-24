-- 0002_worktree_state: per-worker worktree HEAD + uncommitted diff (V11 8.21a)
-- V10 stored a single git_commit_hash that could not capture in-flight worktree
-- state; these columns record each worker's worktree HEAD and its uncommitted diff.
ALTER TABLE checkpoints ADD COLUMN worktree_head TEXT;
ALTER TABLE checkpoints ADD COLUMN worktree_diff TEXT;