-- 0015_metric_targets.sql
-- KPI Targets: store target configuration in the metrics definition JSONB.
--
-- The MetricTarget is serialised inside the ``definition`` JSONB column that
-- already exists on the ``metrics`` table (added in 0008_metrics.sql).  No new
-- columns are required — the target is a sub-key ``target`` inside the
-- ``definition`` JSON blob, exactly like ``measure`` / ``dimensions`` etc.
--
-- This migration adds:
--   1. A generated (immutable) boolean column ``has_target`` so the app can
--      cheaply filter/index metrics that carry a target without a JSON scan.
--   2. An index on ``has_target`` for dashboards that list only KPIs with goals.
--
-- DOWN path: drop the index and the generated column.

-- ── UP ──────────────────────────────────────────────────────────────────────

-- Generated column: true when the definition JSONB contains a non-null "target"
-- key.  IMMUTABLE because it depends only on the stored JSONB value.
ALTER TABLE metrics
    ADD COLUMN IF NOT EXISTS has_target boolean
        GENERATED ALWAYS AS (
            (definition -> 'target') IS NOT NULL
            AND (definition -> 'target') != 'null'::jsonb
        ) STORED;

-- Partial index: cheap lookup for metrics that have a KPI target.
CREATE INDEX IF NOT EXISTS metrics_has_target_idx
    ON metrics (org_id, project_id)
    WHERE has_target;

COMMENT ON COLUMN metrics.has_target IS
    'True when definition.target is present.  Generated column — do not set directly.';

-- ── DOWN ─────────────────────────────────────────────────────────────────────
-- To reverse this migration:
--
--   DROP INDEX IF EXISTS metrics_has_target_idx;
--   ALTER TABLE metrics DROP COLUMN IF EXISTS has_target;
