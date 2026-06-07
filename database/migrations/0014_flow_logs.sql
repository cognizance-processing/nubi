-- M14: Flow robustness — add logs column + new terminal states to task_runs.
-- Forward-only; never edit after applying.

-- 1. Add ``logs`` jsonb column to task_runs for per-task captured stdout/log lines.
ALTER TABLE task_runs
    ADD COLUMN IF NOT EXISTS logs jsonb NOT NULL DEFAULT '[]'::jsonb;

-- 2. Expand the state CHECK constraint to include the new terminal states:
--    ``timed_out``       — task exceeded timeout_s; treated as failed.
--    ``upstream_failed`` — an upstream dependency failed; task will not run.
--
-- PostgreSQL does not support ALTER CONSTRAINT in-place on a named constraint,
-- so we drop and re-add.
ALTER TABLE task_runs
    DROP CONSTRAINT IF EXISTS task_runs_state_check;

ALTER TABLE task_runs
    ADD CONSTRAINT task_runs_state_check CHECK (
        state IN (
            'pending',
            'ready',
            'running',
            'retrying',
            'success',
            'failed',
            'timed_out',
            'upstream_failed',
            'cancelled'
        )
    );

-- 3. Update the claim index to include 'retrying' so the worker picks up due
--    retrying tasks efficiently (retrying tasks have scheduled_at set to
--    retry_at so the state+scheduled_at index keeps them efficient).
DROP INDEX IF EXISTS task_runs_claim_idx;
CREATE INDEX IF NOT EXISTS task_runs_claim_idx
    ON task_runs (state, scheduled_at)
    WHERE state IN ('ready', 'retrying');
