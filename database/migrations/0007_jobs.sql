-- M11: Scheduled jobs + job runs tables
-- Forward-only; never edit after applying.

CREATE TABLE IF NOT EXISTS jobs (
    id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       uuid        NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    created_by   uuid        NOT NULL REFERENCES users(id) ON DELETE SET NULL,
    name         text        NOT NULL,
    kind         text        NOT NULL CHECK (kind IN ('query', 'python')),
    target       text        NOT NULL,
    schedule     text        NOT NULL,
    enabled      boolean     NOT NULL DEFAULT true,
    next_run_at  timestamptz,
    last_run_at  timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS jobs_org_id_idx ON jobs (org_id);

CREATE TABLE IF NOT EXISTS job_runs (
    id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id       uuid        NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    status       text        NOT NULL CHECK (status IN ('success', 'error')),
    started_at   timestamptz,
    finished_at  timestamptz,
    row_count    integer,
    message      text,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS job_runs_job_id_idx ON job_runs (job_id);
