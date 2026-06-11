-- 0008_metrics.sql
-- Metrics / semantic layer (Wave C).
--
-- A metric defines business logic (e.g. revenue = SUM(amount)) ONCE — with an
-- owner, a grain, allowed dimensions, and RLS keys — and is compiled to SQL on
-- demand (app/metrics/compile.py). This table persists the governed definition;
-- the application loads it into the in-process MetricRegistry at startup, exactly
-- like the queries registry. The serialized MetricDefinition (see
-- app/metrics/models.py:MetricDefinition.to_dict) lives in ``definition`` jsonb.
--
-- Project-scoped like the other resources (id, org_id, project_id, created_by).

CREATE TABLE IF NOT EXISTS metrics (
    id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       uuid        NOT NULL REFERENCES orgs     (id) ON DELETE CASCADE,
    project_id   uuid        NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    created_by   uuid        NOT NULL REFERENCES users    (id) ON DELETE RESTRICT,
    -- Stable, URL-safe slug callers reference (e.g. "revenue"). Unique per org
    -- so /metrics/{slug}/query resolves deterministically within a tenant.
    slug         text        NOT NULL CHECK (char_length(slug) > 0),
    name         text        NOT NULL,
    -- Serialized MetricDefinition: measure, dimensions, time_dimension,
    -- base_table/base_sql, datastore_id, default_filters, rls_keys, owner, etc.
    definition   jsonb       NOT NULL DEFAULT '{}',
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (org_id, slug)
);

COMMENT ON TABLE metrics IS
    'Governed metric definitions for the semantic layer. The application '
    'compiles each definition to SQL on demand (RLS injected by the planner). '
    'Loaded into the in-process MetricRegistry at startup.';

COMMENT ON COLUMN metrics.definition IS
    'Serialized app.metrics.models.MetricDefinition (JSONB): measure, allowed '
    'dimensions, time_dimension + grains, base_table/base_sql, datastore_id, '
    'default_filters (trusted), rls_keys (must stay in grain), owner.';

CREATE INDEX IF NOT EXISTS metrics_project_id_idx ON metrics (project_id);
CREATE INDEX IF NOT EXISTS metrics_org_slug_idx   ON metrics (org_id, slug);
