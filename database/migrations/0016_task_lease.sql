-- Migration 0016: Add lease fields to task_runs for work-pool concurrency.
--
-- Adds:
--   lease_expires_at  timestamptz  NULL  — when the worker lease expires; NULL means not leased.
--   worker_id         text         NULL  — opaque identifier of the worker holding the lease.
--
-- Adds an index on (state, scheduled_at) to speed up the claim query used by
-- claim_ready_task_run.  NOTE: the migration runner (database/migrate.py) wraps
-- each migration in a transaction, and CREATE INDEX CONCURRENTLY cannot run in a
-- transaction block.  A plain CREATE INDEX is used instead — on a fresh/small
-- task_runs table the brief lock is negligible.

ALTER TABLE task_runs
    ADD COLUMN IF NOT EXISTS lease_expires_at  timestamptz  NULL,
    ADD COLUMN IF NOT EXISTS worker_id         text         NULL;

CREATE INDEX IF NOT EXISTS idx_task_runs_state_scheduled_at
    ON task_runs (state, scheduled_at ASC NULLS FIRST);
