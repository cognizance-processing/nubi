# Metrics / Semantic Layer — design (Wave C)

**Thesis.** Today the unit of reuse is a registered SQL query, so business logic
("revenue", "active customer", "churn") is re-encoded per dashboard and two
dashboards can silently disagree. A **metric** defines that logic ONCE — with an
owner, a grain, allowed dimensions, and RLS keys — and is compiled to SQL on
demand. This is the layer that makes AI authoring **consistent**, not merely
valid: an agent answers "what was churn last month" from a *governed definition*,
not freshly hallucinated SQL that passed syntax validation. (LookML, dbt metrics,
Cube all exist for this.)

**Grow on the existing substrate — do NOT build a new engine.** A metric reuses,
verbatim:
- the **query registry** pattern (`app/queries/registry.py`): in-memory singleton
  + DB persistence (`metrics` table) + startup load. `MetricRegistry` mirrors
  `QueryRegistry`.
- the **planner** (`app/connectors/planner.py:plan`): compiled metric SQL goes
  through `plan(sql, claims=…)` so RLS predicates from `claims["policies"]` are
  injected as AST `col = value` filters, params bind positionally (never
  concatenated), and the cache key is computed the same way.
- the **pre-agg router** (`route_to_rollup_shape` + `get_registry()`): a hot
  metric query routes to a rollup and collapses to ~$0, exactly like a raw query.
- the **execution path** in `routes/query.py` (`_build_connector_for_plan`,
  cache, metering): the metric query endpoint reuses these helpers, not a fork.

## The contract (authoritative — `app/metrics/models.py`, already written)
- `Measure{name, agg(sum|count|count_distinct|min|max|avg), expr, type(additive|
  semi_additive|non_additive), format}` — the thing measured (`revenue = SUM(amount)`).
- `Dimension{name, expr?, type}` — an **allowed** grouping column. An agent may
  only group by declared dimensions; unknown dims are REJECTED (the governance point).
- `TimeDimension{column, grains[], default_grain}` — the time column + supported
  `day|week|month|quarter|year` buckets.
- `MetricDefinition{id, name, measure, base_table|base_sql, datastore_id,
  dimensions[], time_dimension?, default_filters[], rls_keys[], owner,
  required_scope, extra_measures[]}` — `default_filters` are author-governed WHERE
  fragments (trusted, no user input); `rls_keys` MUST survive into the grain.
- `MetricQuery{metric_id, dimensions[], time_grain?, filters[], order_by[], limit}`
  — the request; `filters` are `MetricFilter{field, op, value}` bound as params.

## Compilation (`app/metrics/compile.py` — `compile_metric(metric, mq, *, dialect)`)
1. **Govern:** every requested dimension ∈ `metric.dimensions`; every filter
   `field` ∈ allowed dims or the time column; `time_grain` ∈ `time_dimension.grains`.
   Anything else → `MetricError` (the agent can't ask for arbitrary columns).
2. **Build via sqlglot AST:** SELECT = requested dims + `date_trunc(grain, time_col)`
   bucket + `agg(expr) AS measure.name` (+ extra_measures). FROM = `base_table` or
   `(base_sql)` subquery. WHERE = `default_filters` (verbatim, trusted) AND the
   user `filters` as **bound `{{param}}` placeholders** (never concatenated).
   GROUP BY = the dims + time bucket. ORDER BY/LIMIT as requested.
3. Return `(sql_with_named_params, params_dict)`. The caller hands this to
   `planner.plan(sql, params, claims)` → RLS + positional binding + cache key →
   `route_to_rollup_shape` → execute. **`rls_keys` are guaranteed present in the
   grain** so the planner's RLS predicate lands on a real column (same invariant
   the rollup path already enforces in `materialize.py`).

## Surface (`app/metrics/registry.py`, `app/routes/metrics.py`)
- `GET/POST/PUT/DELETE /metrics` (+ `/metrics/{id}`) — CRUD, first-party write
  only, persisted to the `metrics` table, registered in the singleton (mirrors
  `register_query`). Reuses the agent-sandbox scope/idempotency conventions.
- `POST /metrics/{id}/query` — body = `MetricQuery`; compiles → plans → executes
  through the **existing** query execution helpers (cache + metering + rollup
  routing + RLS), returns Arrow exactly like `POST /query`. Embed-safe: raw SQL
  is never accepted here, only governed metric+dims+filters.
- `POST /metrics/{id}/sql` — dry compile (returns the SQL + params) for debugging
  / agent introspection, no execution.

## Exposure to agents (Wave C3)
- `/ai/context` gains a `metrics[]` block (id, measure, dimensions, time grains,
  description) so an agent discovers governed metrics alongside queries.
- MCP: `list_metrics`, `query_metric(metric_id, dimensions, time_grain, filters)`.
- A short `docs/metrics-reference.md` for agents (how to query a metric vs a query).
- (Later) a Widget may bind to a `metric_id` + dims/grain instead of a `query_id`;
  out of scope for this wave — the query endpoint is the v1 integration point.

## Storage (`database/migrations/0008_metrics.sql`, already written)
`metrics(id, org_id, project_id, name, definition jsonb, created_by, created_at,
updated_at)` — definition holds the serialized `MetricDefinition`. Mirrors the
`queries` resource table; loaded into the registry at startup like queries.

## Invariants
DuckDB only. RLS keys in the grain + injected by `plan()`. Bound params never
concatenated (only `default_filters`, which are author-trusted, are inlined).
Governance: requested dims/filters/grains validated against the definition.
Open-core: definition + compile + registry in core; no billing logic here.

## Wave C task split (disjoint files)
- **Contract (orchestrator):** `app/metrics/models.py` + `0008_metrics.sql`.
- **C1 registry+routes:** `app/metrics/registry.py`, `app/routes/metrics.py`
  (CRUD + `/query` + `/sql`), `main.py` registration, startup load, tests.
- **C2 compiler:** `app/metrics/compile.py` (govern + AST build + grain + filters
  + rls_keys), unit tests.
- **C3 exposure:** `/ai/context` metrics block (`routes/ai.py`), MCP tools
  (`mcp/nubi_mcp/server.py`), `docs/metrics-reference.md`.
