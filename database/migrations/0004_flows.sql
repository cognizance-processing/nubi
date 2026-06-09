-- 0004_flows.sql
-- Flows workflow orchestrator: flows, flow_runs, task_runs, and the
-- incremental-materialization watermarks table.
--
-- flows.spec is the canonical FlowSpec jsonb.  NOTE: the spec deliberately
-- carries NO ``env`` key — the execution environment is resolved at trigger
-- time (explicit override → the project's default environment), never from
-- the spec.  flow_runs.env records the env each run resolved to.

CREATE TABLE IF NOT EXISTS flows (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      uuid        NOT NULL REFERENCES orgs     (id) ON DELETE CASCADE,
    project_id  uuid        NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    created_by  uuid        NOT NULL REFERENCES users    (id) ON DELETE SET NULL,
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

CREATE INDEX IF NOT EXISTS flows_org_id_idx     ON flows (org_id);
CREATE INDEX IF NOT EXISTS flows_project_id_idx ON flows (project_id);

-- ── flow_runs: one row per execution of a flow ───────────────────────────────
-- ``env`` is the resolved execution environment for the run (override → the
-- project's default env).  Materialize tasks namespace their object-storage
-- targets under this env.

CREATE TABLE IF NOT EXISTS flow_runs (
    id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    flow_id      uuid        NOT NULL REFERENCES flows(id) ON DELETE CASCADE,
    org_id       uuid        NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    state        text        NOT NULL DEFAULT 'pending'
                             CHECK (state IN ('pending','running','success','failed','cancelled')),
    params       jsonb       NOT NULL DEFAULT '{}'::jsonb,
    trigger      text        NOT NULL DEFAULT 'manual'
                             CHECK (trigger IN ('manual','schedule','event','agent')),
    env          text,
    scheduled_at timestamptz,
    started_at   timestamptz,
    finished_at  timestamptz,
    error        text,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS flow_runs_flow_id_idx ON flow_runs (flow_id);
CREATE INDEX IF NOT EXISTS flow_runs_state_idx   ON flow_runs (state);

-- ── task_runs: one row per task execution within a flow_run ──────────────────
--
-- logs               — per-task captured stdout/log lines (jsonb array).
-- lease_expires_at   — when the worker lease expires; NULL means not leased.
-- worker_id          — opaque identifier of the worker holding the lease.
-- parent_task_run_id — for map child task_runs, references the parent map
--                      task_run in the same flow_run.  NULL otherwise.
-- branch_taken       — for branch task_runs, the label of the condition that
--                      matched at runtime (e.g. 'condition_0', 'default').

CREATE TABLE IF NOT EXISTS task_runs (
    id                 uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    flow_run_id        uuid        NOT NULL REFERENCES flow_runs(id) ON DELETE CASCADE,
    org_id             uuid        NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    task_key           text        NOT NULL,
    state              text        NOT NULL DEFAULT 'pending'
                       CONSTRAINT task_runs_state_check
                       CHECK (state IN (
                           'pending',
                           'ready',
                           'running',
                           'success',
                           'failed',
                           'retrying',
                           'skipped',
                           'waiting_children',
                           'upstream_failed',
                           'timed_out'
                       )),
    attempt            integer     NOT NULL DEFAULT 0,
    depends_on         text[]      NOT NULL DEFAULT '{}',
    cache_key          text,
    result             jsonb,
    error              text,
    logs               jsonb       NOT NULL DEFAULT '[]'::jsonb,
    lease_expires_at   timestamptz NULL,
    worker_id          text        NULL,
    parent_task_run_id uuid        REFERENCES task_runs(id) ON DELETE CASCADE,
    branch_taken       text,
    scheduled_at       timestamptz,
    started_at         timestamptz,
    finished_at        timestamptz,
    created_at         timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS task_runs_flow_run_id_idx ON task_runs (flow_run_id);

-- Worker claim query: ready/retrying tasks ordered by scheduled_at.
-- NOTE: the migration runner wraps each migration in a transaction, and
-- CREATE INDEX CONCURRENTLY cannot run in a transaction block — a plain
-- CREATE INDEX is used instead (negligible lock on a fresh table).
CREATE INDEX IF NOT EXISTS task_runs_claim_idx
    ON task_runs (state, scheduled_at)
    WHERE state IN ('ready', 'retrying');

CREATE INDEX IF NOT EXISTS idx_task_runs_state_scheduled_at
    ON task_runs (state, scheduled_at ASC NULLS FIRST);

-- Map fan-in: look up all child task_runs of a map node efficiently.
CREATE INDEX IF NOT EXISTS task_runs_parent_idx
    ON task_runs (parent_task_run_id)
    WHERE parent_task_run_id IS NOT NULL;

-- ── flow_watermarks: incremental materialization watermarks ──────────────────
-- Per-(flow, model, env) watermark.  ``model_key`` is the materialize task
-- key; ``env`` namespaces dev/prod so the same model in different
-- environments tracks independent watermarks.  ``watermark`` is stored as
-- text (an ISO timestamp string) so it survives any time_column type without
-- coercion.

CREATE TABLE IF NOT EXISTS flow_watermarks (
    flow_id     uuid NOT NULL REFERENCES flows(id) ON DELETE CASCADE,
    model_key   text NOT NULL,
    env         text NOT NULL DEFAULT 'prod',
    watermark   text,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (flow_id, model_key, env)
);
