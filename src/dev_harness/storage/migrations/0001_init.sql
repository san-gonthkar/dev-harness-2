-- 0001_init: checkpoints table (V11 1.3)
CREATE TABLE IF NOT EXISTS checkpoints (
    project_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    checkpoint_id TEXT NOT NULL,
    state_json TEXT NOT NULL,
    state_sha256 TEXT NOT NULL,
    git_commit_hash TEXT,
    is_paused INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (project_id, thread_id, checkpoint_id)
);

CREATE INDEX IF NOT EXISTS idx_checkpoints_scope
    ON checkpoints (project_id, thread_id, created_at DESC);
