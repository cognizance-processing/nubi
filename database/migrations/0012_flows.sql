-- M12: Flows workflow orchestrator — flows, flow_runs, task_runs tables
-- Forward-only; never edit after applying.

CREATE TABLE IF NOT EXISTS flows (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      uuid        NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    created_by  uuid        NOT NULL REFERENCES users(id) ON DELETE SET NULL,
    name        text        NOT NULL,
    spec        jsonb       NOT NULL,
    version     integer     NOT NULL DEFAULT 1,
    enabled     boolean     NOT NULL DEFAULT true,
    schedule    text,
    next_run_at timestamptz,
    last_run_at timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS flows_org_id_idx ON flows (org_id);

CREATE TABLE IF NOT EXISTS flow_runs (
    id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    flow_id      uuid        NOT NULL REFERENCES flows(id) ON DELETE CASCADE,
    org_id       uuid        NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    state        text        NOT NULL DEFAULT 'pending'
                             CHECK (state IN ('pending','running','success','failed','cancelled')),
    params       jsonb       NOT NULL DEFAULT '{}'::jsonb,
    trigger      text        NOT NULL DEFAULT 'manual'
                             CHECK (trigger IN ('manual','schedule','event','agent')),
    scheduled_at timestamptz,
    started_at   timestamptz,
    finished_at  timestamptz,
    error        text,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS flow_runs_flow_id_idx ON flow_runs (flow_id);
CREATE INDEX IF NOT EXISTS flow_runs_state_idx   ON flow_runs (state);

CREATE TABLE IF NOT EXISTS task_runs (
    id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    flow_run_id  uuid        NOT NULL REFERENCES flow_runs(id) ON DELETE CASCADE,
    org_id       uuid        NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    task_key     text        NOT NULL,
    state        text        NOT NULL DEFAULT 'pending'
                             CHECK (state IN ('pending','ready','running','success','failed','retrying','skipped')),
    attempt      integer     NOT NULL DEFAULT 0,
    depends_on   text[]      NOT NULL DEFAULT '{}',
    cache_key    text,
    result       jsonb,
    error        text,
    scheduled_at timestamptz,
    started_at   timestamptz,
    finished_at  timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS task_runs_flow_run_id_idx ON task_runs (flow_run_id);
CREATE INDEX IF NOT EXISTS task_runs_claim_idx       ON task_runs (state, scheduled_at);
