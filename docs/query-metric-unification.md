# Query/Metric Unification

Status: implementation contract. Collapse the two user-facing concepts (Query +
Metric) into ONE: a **Query** that can optionally **expose itself as a governed
metric**. The metric *engine* (MetricDefinition + compile.py + registry API)
stays; only the SOURCE and the user-facing surface change.

Goal: a plain query is just SQL. Declare a `metric` block on it and the SAME
object becomes consumable parametrically by AI, watches, dashboards, and the
pre-agg optimizer — no separate Metric object/page.

## 0. Grounding (what exists — do not break)

- `MetricDefinition` (`app/metrics/models.py`): `id, name, measure(Measure:
  name/agg/expr/type/format), base_table|base_sql, datastore_id, dimensions[],
  time_dimension{column,grains,default_grain}, default_filters[], rls_keys[],
  description, owner, required_scope`. `to_dict/from_dict`. **Unchanged.**
- `compile.py::compile_metric` — turns a MetricDefinition + requested
  dims/grain/filters into SQL. **Unchanged.**
- `MetricRegistry` (`app/metrics/registry.py`): in-memory, keyed by metric **id
  (= slug** for persisted, e.g. `"revenue"`). `get(id)`, `all()`,
  `ensure_persisted_metric(id)` (on-demand load), `load_persisted_metrics()`
  (startup load from the `metrics` table), `_seed_demo_metrics` (in-code
  `demo_revenue`). The registry API is the seam ALL consumers use.
- Consumers (all go THROUGH the registry — keep them working, ideally untouched):
  watches (`routes/watches.py` stores `metric_id` = slug; resolves via
  `ensure_persisted_metric`/`get_metric_registry`), AI (`chat/tools.py`,
  `chat/llm.py`), pre-agg (`connectors/planner.route_to_rollup_shape`),
  dashboard widget binding (frontend `MetricPicker` → `/metrics` GET).
- `queries.config` is `jsonb` and already holds `{sql, params, datastore_id,
  output_schema, ...}`. `metrics` table: `{id, org_id, project_id, created_by,
  slug, name, definition=MetricDefinition.to_dict()}`, UNIQUE(org_id, slug).

## 1. The `config.metric` block (the unification)

A query's `config` gains an OPTIONAL `metric` key. Present ⇒ the query is a
governed metric. Exact schema:

```jsonc
queries.config = {
  "sql": "SELECT order_date, region, product, amount FROM orders",  // base grain
  "params": [...],
  "datastore_id": "…",
  "output_schema": …,
  "metric": {                       // OPTIONAL — present ⇒ exposed as a metric
    "slug": "revenue",              // STABLE metric id (preserved across migration)
    "measure": { "name": "revenue", "agg": "sum", "expr": "amount",
                 "type": "additive", "format": "currency" },
    "dimensions": [ { "name": "region", "expr": null, "type": "text" },
                    { "name": "product", "expr": null, "type": "text" } ],
    "time_dimension": { "column": "order_date", "grains": ["day","week","month"],
                        "default_grain": "day" },     // or null
    "default_filters": [],
    "rls_keys": ["tenant_id"],
    "owner": null,
    "description": ""
  }
}
```

### Adapter: query row → MetricDefinition

`app/metrics/registry.py::_definition_from_query_row(row)`:
- `id`         = `config.metric.slug`  (the stable metric id consumers reference)
- `name`       = `query.name`
- `base_sql`   = `config.sql`          (base_table stays None — queries are SQL)
- `datastore_id` = `config.datastore_id`
- `measure / dimensions / time_dimension / default_filters / rls_keys / owner /
  description` = from `config.metric.*`
- Reuse `MetricDefinition.from_dict` / the existing validation in
  `routes/metrics.py::_build_definition` so validation rules are identical.

**The base-grain rule:** `config.sql` MUST be authored at base/low grain (select
the dimension + raw measure columns, NO `GROUP BY`); the metric layer owns the
aggregation. This is the same discipline metrics have today, moved onto the query.

## 2. Registry: load metrics from queries

- Add `load_metrics_from_queries()` — `SELECT id, org_id, project_id, name,
  config FROM queries WHERE config ? 'metric'` → `_definition_from_query_row` →
  register by `config.metric.slug`. Tenant/registry semantics mirror the current
  `load_persisted_metrics`.
- `ensure_persisted_metric(metric_id)` now resolves a query-with-metric by slug
  (org-scoped), instead of the `metrics` table.
- Keep `_seed_demo_metrics` (in-code `demo_revenue`) AS-IS.
- **Registry public API unchanged** (`get`, `all`, `ensure_persisted_metric`) so
  every consumer keeps working untouched.

## 3. `/metrics` routes (compat, query-backed)

- `GET /metrics`, `GET /metrics/{id}` — keep; now resolve via the registry
  (query-backed). MetricPicker + WatchesPage keep working unchanged.
- `POST/PUT /metrics` — repoint `_persist_metric` to UPSERT a **query** with a
  `config.metric` block (by org + slug), instead of writing the `metrics` table.
  So any existing write path still works but lands in `queries`. (Primary
  authoring moves to the query editor — Section 5.)
- `DELETE /metrics/{id}` — clears the `metric` block from the backing query (or
  deletes the query if it was metric-only — pick the safe option; document it).

## 4. Migration `0012_metrics_to_queries.sql`

Idempotent data migration: for each `metrics` row, INSERT a `queries` row
(unless a query already exposes that slug in this org) with:
- `name` = metrics.name, `org_id/project_id/created_by` copied,
- `config` = `jsonb_build_object('sql', COALESCE(definition->>'base_sql',
  'SELECT * FROM ' || (definition->>'base_table')), 'datastore_id',
  definition->'datastore_id', 'metric', jsonb_build_object('slug', slug,
  'measure', definition->'measure', 'dimensions',
  COALESCE(definition->'dimensions','[]'), 'time_dimension',
  definition->'time_dimension', 'default_filters',
  COALESCE(definition->'default_filters','[]'), 'rls_keys',
  COALESCE(definition->'rls_keys','[]'), 'owner', definition->'owner',
  'description', definition->'description'))`.
- Preserve the `slug` so metric ids stay stable (watches/dashboards/AI keep
  resolving). Do NOT drop the `metrics` table in this migration (deprecate it;
  a later migration drops it once verified). Idempotent: `WHERE NOT EXISTS
  (SELECT 1 FROM queries q WHERE q.org_id = m.org_id AND q.config->'metric'->>'slug' = m.slug)`.

## 5. Frontend

- **Query editor** (`src/pages/app/QueryWorkspace.jsx` / `QueryCodeView.jsx`):
  add an "Expose as metric" section — pick the measure column + agg + additivity
  + format, the dimensions, the time column/grain, rls keys. Writes
  `config.metric`. A plain query leaves it empty.
- **Remove the standalone Metrics page** (`src/pages/app/MetricsPage.jsx`) from
  nav + routes. Metric authoring now lives on the query.
- **MetricPicker** (`src/components/app/MetricPicker.jsx`) + `src/lib/metrics.js`
  + the dashboard widget binding + WatchesPage metric selection: keep —
  `/metrics` GET still lists metrics (now query-backed). Adjust copy to "Queries
  exposed as metrics" if helpful.
- Migrate any "create metric" UI into the query editor's section.

## 6. Verification bar

- Backend: existing metric/watch/ai/preagg/dashboard tests stay green; add tests
  for: the query→MetricDefinition adapter; the registry loading a query-with-
  metric by slug; `/metrics` GET returns query-backed metrics; `POST /metrics`
  lands in `queries`; the migration moves a metric → query preserving slug
  (idempotent); a watch referencing a migrated metric_id still resolves + breaches.
  Run in isolation (note the known pre-existing `login_events` cross-module
  isolation issue). 
- Frontend: `npm run build` green; `npx eslint` clean; the query editor writes a
  valid `config.metric`; MetricPicker still lists metrics.
- Critical invariant: **metric ids (slugs) are stable across the migration** so
  no watch/dashboard/AI reference breaks.
