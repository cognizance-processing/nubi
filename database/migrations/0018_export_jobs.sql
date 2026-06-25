-- Migration 0018: export_jobs — async/job-backed bulk export for the data-custody tier.
--
-- Deployers can enqueue a bulk-export job (POST /lake/{id}/export/jobs) instead
-- of blocking on the synchronous COPY TO path. A worker claims queued jobs via
-- CAS (compare-and-swap UPDATE … WHERE state='queued') and executes the same
-- DuckDB COPY TO logic used by the synchronous endpoint.
--
-- SECURITY:
-- * ``dest_creds_ref`` stores a SECRET-STORE KEY (e.g. a reference into the org's
--   secret store), NEVER plaintext credentials.  The worker resolves the secret at
--   execution time and discards it after the COPY completes.
-- * Every row is org-scoped (org_id FK + all queries filter on org_id).
-- * The ``result`` JSONB column stores exported-file metadata (paths, counts) —
--   it never stores credentials or sensitive data.
--
-- State machine: queued → running → succeeded | failed
-- The worker claims via:
--   UPDATE export_jobs SET state='running', worker_id=… WHERE id=$1 AND state='queued'
-- so concurrent workers never double-execute a job.

CREATE TABLE IF NOT EXISTS export_jobs (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Org scoping (every query must filter on org_id).
    org_id          uuid        NOT NULL,
    -- The managed-lake datastore being exported.
    datastore_id    uuid        NOT NULL,
    -- User who enqueued the job.
    user_id         uuid        NOT NULL,
    -- Destination URI (e.g. s3://bucket/prefix/ or file:///dir/).
    -- Validated at enqueue time (must be outside the source lake).
    dest_uri        text        NOT NULL,
    -- Reference/key into the org's secret store for destination credentials.
    -- NEVER stores plaintext credentials.  NULL when the lake's own credentials
    -- are reused (source and destination share the same S3 environment).
    dest_creds_ref  text        NULL,
    -- Single table to export.  NULL means "all tables".
    "table"         text        NULL,
    -- Explicit SELECT to export.  Validated as SELECT-only at enqueue time.
    sql             text        NULL,
    -- Output format: 'parquet' or 'csv'.
    format          text        NOT NULL DEFAULT 'parquet',
    -- State machine: queued → running → succeeded | failed
    state           text        NOT NULL DEFAULT 'queued',
    -- Worker that claimed this job (hostname:pid).  NULL until claimed.
    worker_id       text        NULL,
    -- Populated when state='succeeded': [{table, dest_uri, format}, …]
    result          jsonb       NULL,
    -- Populated when state='failed': human-readable error message.
    error           text        NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- Worker claim hot-path: fast scan of queued jobs.
CREATE INDEX IF NOT EXISTS idx_export_jobs_state
    ON export_jobs (state);

-- Org-scoped listing (GET jobs for an org/datastore).
CREATE INDEX IF NOT EXISTS idx_export_jobs_org_datastore
    ON export_jobs (org_id, datastore_id);
