-- Migration 0026: Incremental materialization watermarks + flow_run env.
--
-- flow_watermarks — per-(flow, model, env) incremental materialization
--   watermark.  ``model_key`` is the materialize task key; ``env`` namespaces
--   dev/prod so the same model in different environments tracks independent
--   watermarks.  ``watermark`` is stored as text (an ISO timestamp string) so
--   it survives any time_column type without coercion.
--
-- flow_runs.env — the resolved execution environment for a flow run
--   (override → spec.env → 'prod').  Materialize tasks namespace their
--   object-storage targets under this env.  Old rows read as 'prod'.

CREATE TABLE IF NOT EXISTS flow_watermarks (
    flow_id     UUID NOT NULL REFERENCES flows(id) ON DELETE CASCADE,
    model_key   TEXT NOT NULL,
    env         TEXT NOT NULL DEFAULT 'prod',
    watermark   TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (flow_id, model_key, env)
);

ALTER TABLE flow_runs
  ADD COLUMN IF NOT EXISTS env TEXT;
