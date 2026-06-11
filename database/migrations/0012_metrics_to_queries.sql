-- 0012_metrics_to_queries.sql
-- Query/Metric unification — move governed metrics into queries.
--
-- A metric is no longer a separate object: a `queries` row whose `config` carries
-- a `metric` block IS the governed metric (keyed by config.metric.slug). The
-- metric ENGINE (MetricDefinition + compile.py + the registry API) is unchanged;
-- only the SOURCE changes. The registry now sources metrics from queries-with-
-- `config.metric` (app/metrics/registry.py::load_metrics_from_queries), so this
-- migration backfills the unified store from the deprecated `metrics` table.
--
-- For each `metrics` row we INSERT a `queries` row carrying:
--   config.sql          ← definition.base_sql, or `SELECT * FROM <base_table>`
--                         when the legacy metric was authored against a physical
--                         table (queries are SQL — the metric layer owns the
--                         aggregation, so the base SQL is the raw base grain).
--   config.datastore_id ← definition.datastore_id
--   config.metric       ← { slug, measure, dimensions, time_dimension,
--                           default_filters, rls_keys, owner, description }
-- The `slug` is PRESERVED verbatim so every consumer (watches.metric_id,
-- dashboard widget bindings, AI) keeps resolving the SAME metric id.
--
-- DEPRECATION: the `metrics` table is NOT dropped here — it is retained (now
-- unused by the runtime) so a rollback / late verification can still read it. A
-- later migration drops it once the unified path is proven in production.
--
-- IDEMPOTENT: re-running is a clean no-op. The WHERE NOT EXISTS guard skips any
-- metric whose slug is already exposed by a query in the same org, so this never
-- double-inserts (and never clobbers a query already authored with that slug).

INSERT INTO queries (id, org_id, project_id, created_by, name, config)
SELECT
    gen_random_uuid(),
    m.org_id,
    m.project_id,
    m.created_by,
    m.name,
    jsonb_build_object(
        'sql',
            COALESCE(
                m.definition->>'base_sql',
                'SELECT * FROM ' || (m.definition->>'base_table')
            ),
        'datastore_id', m.definition->'datastore_id',
        'metric', jsonb_build_object(
            'slug',            m.slug,
            'measure',         m.definition->'measure',
            'dimensions',      COALESCE(m.definition->'dimensions',      '[]'::jsonb),
            'time_dimension',  m.definition->'time_dimension',
            'default_filters', COALESCE(m.definition->'default_filters', '[]'::jsonb),
            'rls_keys',        COALESCE(m.definition->'rls_keys',        '[]'::jsonb),
            'owner',           m.definition->'owner',
            'description',     m.definition->'description'
        )
    )
FROM metrics m
WHERE NOT EXISTS (
    SELECT 1 FROM queries q
    WHERE q.org_id = m.org_id
      AND q.config->'metric'->>'slug' = m.slug
);

COMMENT ON TABLE metrics IS
    'DEPRECATED (migration 0012): governed metrics are now sourced from '
    'queries-with-config.metric (keyed by config.metric.slug). This table is '
    'retained for rollback/verification and dropped by a later migration.';
