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
                             CHECK (trigger IN ('manual','schedule','event','agent','sweep','backfill')),
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

-- ── B2: Run-level seed + code-version snapshot columns on flow_runs ──────────
-- seed:         integer seed for this run's stochastic cells (derived from the
--               run_id for reproducibility across retries).  NULL = no seed.
-- code_version: a snapshot of the flow spec version/hash at trigger time.
--               Stored as jsonb for extensibility (e.g. {version: 3, hash: "..."}).
-- params_snapshot: full copy of the resolved params at trigger time (independent
--               of any later spec edits).

ALTER TABLE flow_runs
    ADD COLUMN IF NOT EXISTS seed              bigint,
    ADD COLUMN IF NOT EXISTS code_version      jsonb,
    ADD COLUMN IF NOT EXISTS params_snapshot   jsonb;

-- ── B2: flow_run_outputs — data-lineage: output → flow_run link ───────────────
-- Records which flow run PRODUCED a named output table / dataset so
-- dashboards / queries can answer "which run produced these numbers?".
--
-- output_key:   logical name for the output (e.g. task_key or table name).
-- output_uri:   physical URI / table path (e.g. s3://... or duckdb path).
-- output_type:  'table' | 'file' | 'dataset' | 'metric'.
-- task_key:     which task_run inside the flow run produced this output.
-- meta:         optional free-form metadata (row counts, schema hash, etc.).

CREATE TABLE IF NOT EXISTS flow_run_outputs (
    id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    flow_run_id  uuid        NOT NULL REFERENCES flow_runs(id) ON DELETE CASCADE,
    org_id       uuid        NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    task_key     text        NOT NULL,
    output_key   text        NOT NULL,
    output_uri   text,
    output_type  text        NOT NULL DEFAULT 'table'
                             CHECK (output_type IN ('table', 'file', 'dataset', 'metric', 'artifact')),
    meta         jsonb,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS flow_run_outputs_run_id_idx
    ON flow_run_outputs (flow_run_id);
CREATE INDEX IF NOT EXISTS flow_run_outputs_org_id_idx
    ON flow_run_outputs (org_id);
CREATE INDEX IF NOT EXISTS flow_run_outputs_output_key_idx
    ON flow_run_outputs (output_key);

-- ── B6: flow_triggers — event/webhook/downstream trigger registry ─────────────
-- Registers which flows fire in response to events, webhooks, or the
-- completion of another flow (downstream triggers).
--
-- kind:   'event' | 'webhook' | 'downstream'
-- source: for event/webhook: the event_key string
--         for downstream: the upstream flow_id (text, not FK, for flexibility)
-- secret: optional HMAC secret for webhook signature verification
-- extra:  free-form jsonb (filter predicates, on_states list, static params, etc.)

CREATE TABLE IF NOT EXISTS flow_triggers (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      uuid        NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    flow_id     uuid        NOT NULL REFERENCES flows(id) ON DELETE CASCADE,
    kind        text        NOT NULL
                            CHECK (kind IN ('event', 'webhook', 'downstream')),
    source      text        NOT NULL,
    secret      text,
    extra       jsonb       NOT NULL DEFAULT '{}'::jsonb,
    enabled     boolean     NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS flow_triggers_org_id_idx    ON flow_triggers (org_id);
CREATE INDEX IF NOT EXISTS flow_triggers_flow_id_idx   ON flow_triggers (flow_id);
CREATE INDEX IF NOT EXISTS flow_triggers_source_idx    ON flow_triggers (source);
-- Efficient lookup of triggers by event_key + org.
CREATE INDEX IF NOT EXISTS flow_triggers_event_idx
    ON flow_triggers (org_id, source)
    WHERE kind IN ('event', 'webhook') AND enabled = TRUE;
-- Efficient lookup of downstream triggers by upstream flow_id.
CREATE INDEX IF NOT EXISTS flow_triggers_downstream_idx
    ON flow_triggers (org_id, source)
    WHERE kind = 'downstream' AND enabled = TRUE;

-- ── B6: flow_sla_alerts — SLA breach log ──────────────────────────────────────
-- Optional log of runs that exceeded their expected duration.  Populated by the
-- SLA hook (flag_sla_breach) when the flow spec carries a freshness_sla_s hint.
-- Consumers can query this table for alerting / dashboarding without touching
-- the hot flow_runs table.

CREATE TABLE IF NOT EXISTS flow_sla_alerts (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          uuid        NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    flow_id         uuid        NOT NULL REFERENCES flows(id) ON DELETE CASCADE,
    flow_run_id     uuid        NOT NULL REFERENCES flow_runs(id) ON DELETE CASCADE,
    expected_s      float       NOT NULL,
    actual_s        float       NOT NULL,
    state           text        NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS flow_sla_alerts_org_id_idx  ON flow_sla_alerts (org_id);
CREATE INDEX IF NOT EXISTS flow_sla_alerts_flow_id_idx ON flow_sla_alerts (flow_id);

-- ── B3: writeback_requests — governed write-back state machine ────────────────
-- One row per write-back request submitted via the Canvas action widget.
-- State machine: pending_approval → committed | rejected; or auto-commit.
--
-- idempotency_key:  caller-supplied deduplication key (per-org unique).
-- rows:             JSONB snapshot of the rows submitted for writing.
-- target:           {connector_id, object} target descriptor.
-- mode:             'append' | 'overwrite' | 'merge'.
-- approval_required: when TRUE the write is gated behind an approver action.
-- state:            current state of the state machine.
-- result:           JSONB result from the loader layer (populated on commit).
-- approved_by:      user_id of the member who approved/rejected the request.

CREATE TABLE IF NOT EXISTS writeback_requests (
    id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id           uuid        NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    idempotency_key  text        NOT NULL,
    rows             jsonb       NOT NULL DEFAULT '[]'::jsonb,
    target           jsonb       NOT NULL DEFAULT '{}'::jsonb,
    mode             text        NOT NULL DEFAULT 'append'
                                 CHECK (mode IN ('append', 'overwrite', 'merge')),
    created_by       uuid        NOT NULL,
    approval_required boolean    NOT NULL DEFAULT false,
    state            text        NOT NULL DEFAULT 'pending_approval'
                                 CHECK (state IN (
                                     'pending_approval',
                                     'committed',
                                     'rejected',
                                     'failed'
                                 )),
    result           jsonb,
    error            text,
    approved_by      uuid,
    meta             jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (org_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS writeback_requests_org_id_idx
    ON writeback_requests (org_id);
CREATE INDEX IF NOT EXISTS writeback_requests_state_idx
    ON writeback_requests (org_id, state)
    WHERE state = 'pending_approval';
