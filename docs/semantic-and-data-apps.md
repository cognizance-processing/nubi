# Semantic Layer, Data Apps, and the Close-the-Loop Architecture

This document consolidates four shipped capabilities that together form Nubi's
"operational BI" stack: a governed semantic layer (Bet 1), a smart query engine
that serves that layer from pre-agg rollups (Bet 2), Flows as a first-class
data-app engine (Axis B), and Canvas — an HTML-native sibling to Dashboards
(Axis C). It concludes with a worked diagram of the close-the-loop cycle that
unifies all four.

Related docs:
- [Architecture & Economics](architecture-and-economics.md) — compute model, billing COGS mapping, wedge economics
- [Metrics reference](metrics-reference.md) — agent/MCP reference for querying metrics
- [Flows](flows.md) — full Flows task/DAG reference
- [Dashboards](dashboards.md) — structured dashboard grid reference

---

## Bet 1 — Semantic layer enrichment

### What a metric is

A registered query is reusable SQL. Useful, but the business logic inside it
("delivered percentage", "rolling revenue") is re-encoded per query; two
dashboards can silently disagree on the definition of the same KPI.

A `MetricDefinition` encodes that logic once — with a declared owner, a time
dimension, the dimensions it may be grouped by, and the RLS keys it must carry —
and compiles to SQL on demand. Governance is enforced before any SQL runs: a
caller cannot group by an undeclared dimension or filter on an arbitrary column.

### Declaration schema

A metric is declared as a `MetricDefinition` (Python dataclass, round-trips
through JSONB) and registered via `POST /metrics` or the MCP `create_metric`
tool. The key fields are:

| Field | Purpose |
|---|---|
| `id` | Stable, URL-safe identifier |
| `measure` | The primary aggregated quantity — `{name, agg, expr, type, format}` |
| `extra_measures` | Additional aggregated quantities queryable at the same grain |
| `derived_measures` | Post-aggregation arithmetic over base measures — **never aggregated themselves** |
| `dimensions` | The **allowed** grouping columns — callers may not group by anything outside this set |
| `time_dimension` | `{column, grains, default_grain}` — the time column and permitted `DATE_TRUNC` grains |
| `default_filters` | Author-governed WHERE fragments inlined verbatim (trusted; never user input) |
| `rls_keys` | Columns that must survive into every grain so the planner's RLS predicate lands correctly |

### Worked example: PvD (delivered/ordered ratio KPI)

```python
from app.metrics.models import (
    MetricDefinition, Measure, DerivedMeasure, Dimension, TimeDimension
)

pvd = MetricDefinition(
    id="pvd",
    name="Promise vs Delivered",
    base_table="order_lines",
    measure=Measure(name="ordered", agg="sum", expr="ordered_qty"),
    extra_measures=(
        Measure(name="delivered", agg="sum", expr="delivered_qty"),
    ),
    derived_measures=(
        DerivedMeasure(
            name="fill_rate",
            formula="delivered / ordered",   # compiler auto-wraps: delivered / NULLIF(ordered, 0)
            format="percent",
        ),
    ),
    dimensions=(
        Dimension(name="region"),
        Dimension(name="product_category"),
    ),
    time_dimension=TimeDimension(
        column="shipped_at",
        grains=("day", "week", "month"),
        default_grain="month",
    ),
    rls_keys=("org_id",),
)
```

Query it (no raw SQL required):

```http
POST /metrics/pvd/query
{
  "dimensions": ["region"],
  "time_grain": "month",
  "filters": [{ "field": "region", "op": "in", "value": ["EMEA", "APAC"] }],
  "order_by": [["shipped_at_month", "asc"]]
}
```

The compiler emits the layered CTE automatically because `derived_measures` is
non-empty:

```sql
WITH __base AS (
    SELECT region,
           DATE_TRUNC('month', shipped_at) AS shipped_at_month,
           SUM(ordered_qty)   AS ordered,
           SUM(delivered_qty) AS delivered
    FROM   order_lines
    WHERE  region IN ($1, $2)
    GROUP BY region, DATE_TRUNC('month', shipped_at)
)
SELECT region,
       shipped_at_month,
       ordered,
       delivered,
       delivered / NULLIF(ordered, 0) AS fill_rate
FROM __base
ORDER BY shipped_at_month ASC
```

RLS: if the planner's token carries `{"org_id": "acme"}`, it injects
`WHERE org_id = 'acme'` on the outermost SELECT. Because the compiler projected
`org_id` through `__base` (as an extra GROUP BY column), the predicate lands on
a real output column.

### Worked example: rolling 4-week revenue

```http
POST /metrics/weekly_revenue/query
{
  "dimensions": ["channel"],
  "time_grain": "week",
  "time_comparisons": [
    { "measure": "revenue", "kind": "rolling_avg", "periods": 4, "name": "revenue_4w_avg" }
  ]
}
```

Compiled outer layer (over `__base`):

```sql
SELECT channel, ordered_at_week, revenue,
       AVG(revenue) OVER (
           PARTITION BY channel
           ORDER BY ordered_at_week
           ROWS BETWEEN 3 PRECEDING AND CURRENT ROW
       ) AS revenue_4w_avg
FROM __base
```

### Full time-intelligence vocabulary

| `kind` | What it produces |
|---|---|
| `prior_period` | `LAG(measure, N)` — the value N buckets ago |
| `pop_abs` | Current minus N-period-ago value |
| `pop_pct` | `(current - prior) / NULLIF(prior, 0)` |
| `prior_year` | `LAG(measure, YEAR_LAG_BY_GRAIN[grain])` — same bucket one year prior |
| `yoy_abs` / `yoy_pct` | Year-over-year absolute / percentage change |
| `ytd` | Cumulative sum since Jan 1 of the current year |
| `qtd` | Cumulative sum since start of the current quarter |
| `mtd` | Cumulative sum since start of the current month |
| `rolling_sum` | Trailing N-period sum |
| `rolling_avg` | Trailing N-period average |
| `latest_snapshot` | QUALIFY ROW_NUMBER() OVER (PARTITION BY entity ORDER BY time DESC) = 1 — deduplicates to one row per entity before aggregation |

### Dynamic top-N with Other bucket

```http
POST /metrics/revenue_by_product/query
{
  "dimensions": ["product"],
  "time_grain": "month",
  "top_n": { "dimension": "product", "n": 5, "measure": "revenue",
             "other": true, "other_label": "Other products" }
}
```

Produces a UNION of the top-5 rows (QUALIFY RANK() <= 5) and an "Other" row
that re-aggregates the base measures for all remaining members, then recomputes
any derived measures from those sums.

---

## Bet 2 — Smart engine: pre-agg rollups for derived and windowed metrics

### Problem

A `fill_rate` metric has a layered CTE. The outer SELECT applies a formula and
possibly window functions. Naive pre-agg routing would look at the outer SELECT,
see expressions the rollup cannot serve, and fall back to the raw table.

### Solution: `__base`-aware router

`build_rollup_for_metric` produces a rollup whose shape exactly mirrors the
`__base` CTE: additive base measures (SUM/COUNT/MIN/MAX only — non-re-aggregable
aggregates like AVG and percentile are skipped), all declared dimensions, the raw
time column. The router's soundness check matches against `__base`, not the outer
SELECT. When it proves the rollup covers `__base`, the full layered query is
rewritten to read from the rollup instead of the raw fact table. The outer
derived/window layer runs unchanged on top.

```
Incoming metric query
        │
        ▼
compile_metric → (WITH __base AS (...) SELECT ...)
        │
        ▼
router: can __base be served by a registered rollup?
  YES → rewrite FROM <fact_table> to FROM <rollup_table>
  NO  → execute against raw fact table (always-safe fallback)
        │
        ▼
execute → Arrow IPC → browser / cache
```

### Building a metric-driven rollup

```python
from app.connectors.preagg import build_rollup_for_metric

built = build_rollup_for_metric(
    metric=pvd,                     # MetricDefinition from above
    grains=["month"],               # include the raw shipped_at column
    source_database="/data/orders.duckdb",
    rollup_id="pvd_monthly",
)
# built.rollup_id, built.rollup_table, built.datastore_id
```

This materializes `SELECT region, org_id, shipped_at, SUM(ordered_qty), SUM(delivered_qty) FROM order_lines GROUP BY region, org_id, shipped_at` into a DuckDB rollup table and registers it so the router finds it on the next query.

### Per-board query fusion and shared cache key

A `DataProvider` in a board spec declares multiple result queries sharing a
`base_cte`. The resolver (`backend/app/dashboards/board_data.py`) runs them in
one round-trip and returns `{result_name: Arrow table}`. The cache key is
`(provider_id, frozen_params, rls_hash)` where `rls_hash = sha256(json(policies))[:16]`.
Different tenants with identical queries never share a cache entry.

### Economics tie-in

The smart engine is what makes "viewers are free" scale under load:

- Rollup build: a one-time server compute event billed at `compute_zar_per_1000_cu`.
- Subsequent dashboard views: the browser fetches the Arrow result from the cache or rollup (effectively a static read), then runs DuckDB-WASM locally. **Zero server scan per view.**
- Adding 10,000 viewers to a rollup-backed dashboard costs zero incremental server compute. The marginal cost is one DB auth check per session (~R0.001/user/month).

---

## Axis B — Flows as a data-app engine

Flows is Nubi's orchestration layer: a DAG of typed tasks (SQL query, Python
cell, materialize, map fan-out, etc.) that runs on a schedule, on a trigger, or
on demand. The data-app extensions (Waves 2–4) make it a compute / decision /
write-back engine.

### Per-cell compute resources

Each `TaskSpec` carries resource fields that travel to the execution layer:

```json
{
  "key": "train_model",
  "kind": "python",
  "cpu_cores": 2.0,
  "mem_mb": 4096,
  "timeout_s": 300,
  "stochastic": false
}
```

| Field | Detail |
|---|---|
| `cpu_cores` | Fractional CPU cores requested. Forwarded to the remote kernel (E2B / Modal); clamped for the local subprocess runner. `0` = provider default. |
| `mem_mb` | Memory in MiB. Same behaviour. |
| `timeout_s` | Per-attempt wall-clock timeout. `0` = no timeout. The local runner enforces this via process-group SIGKILL; the remote runner enforces it at the microVM level. |
| `stochastic` | When `true`: (a) per-run `seed` is injected into the cell's namespace so retries of the same run are deterministic; (b) the cache is bypassed so stale results from a prior run do not persist across runs. |

`map` tasks additionally carry `map_concurrency` to cap parallel child tasks.

The remote kernel tier (E2B / Modal Firecracker microVM) is the production
execution target for Python cells; the interface and primitives are in place, and
provisioning is handled by the provider's platform. The local subprocess runner
is the development-grade path.

### Run lineage and reproducibility

Every `materialize_flow_run` call creates a `flow_runs` row carrying:

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "seed": 1426263040,
  "code_version": { "spec_hash": "...", "committed_at": "..." },
  "params_snapshot": { "start_date": "2026-06-01", "region": "EMEA" },
  "trigger": "sweep"
}
```

- `run_id` — stable across retries; used as the lineage key.
- `seed` — derived from `run_id` by `int(run_id_hex[:8], 16) & 0x7FFFFFFF`. Stochastic cells receive this as `__seed__` in their namespace.
- `code_version` — snapshot of the flow spec at invocation time (useful for auditing when the spec changes between runs).
- `flow_run_outputs` — lineage table linking `run_id` → task outputs; used by sweep/backfill grouping and by the audit trail.

### Typed artifact channel

For heavyweight objects (trained models, serialised closures, large binary
blobs) that cannot cross cell boundaries via the Arrow rows channel:

```python
# Cell A — produces a model
model = train(df)
handle = ctx.put_artifact(model, kind="joblib", name="churn_model_v1")
return {"model_handle": handle}   # JSON-serialisable; goes through JSONB

# Cell B — consumes the model
handle = upstream["model_handle"]
model = ctx.get_artifact(handle)  # downloads + deserialises; org_id check
preds = model.predict(new_df)
```

Artifacts are stored under `orgs/<org_id>/` in the configured object store
(`ARTIFACTS_BASE_URI`). `get_artifact` refuses to deserialise across org
boundaries — the `org_id` on the handle must match the executing org.

Supported kinds: `pickle`, `joblib`, `bytes`, `json`. `InMemoryArtifactStore`
is used in tests; `ObjectStoreArtifactStore` writes to file/S3/GCS/Azure.

### Scenario sweep and backfill

```python
# Expand a grid into the full Cartesian product and run each cell
result = await run_sweep(
    store, flow,
    grid={"region": ["EMEA", "APAC", "AMER"], "model": ["v1", "v2"]},
    trigger="sweep", now=now, claims=claims,
)
# result.diff_surface() → [{index, params, outputs}, ...]
# Failed cells are recorded in result.cells; they do not abort the matrix.

# Re-run a flow over a historical date range
backfill = await run_backfill(
    store, flow,
    start=date(2026, 1, 1), end=date(2026, 6, 1),
    window=timedelta(weeks=1),
    trigger="backfill", now=now, claims=claims,
)
```

Each cell / window is a full flow run with its own `run_id`, `seed`, and
`params_snapshot`. Sweep cells are linked back to the sweep via
`params.__sweep_id__`; backfill windows via `params.__backfill_id__`.

### Event / webhook / downstream triggers

Three trigger kinds, managed via `POST /flows/triggers`:

| Kind | Fires when |
|---|---|
| `event` | `POST /flows/triggers/fire` is called with a matching `event_key` |
| `webhook` | An external HTTP POST hits `POST /flows/triggers/webhook/{id}` (optional HMAC-signed) |
| `downstream` | A named upstream flow reaches a terminal state |

The `on_flow_run_complete` hook fires downstream triggers when any flow_run
completes. It is best-effort, idempotent (guarded by `__upstream_run_id__` in
the spawned run's params), and error-isolated — it never raises.

SLA: `flag_sla_breach(flow_run, expected_s, now)` returns `True` if the run
exceeded `expected_duration_s`. Breaches are surfaced in the run-history API
response and queryable in the ops UI.

### Governed write-back

Write-back is the "close-the-loop" primitive: a Flow cell computes a decision
(e.g. a recommended price change) and writes it back to the source connector.

```http
# Preview — never touches the connector
POST /flows/writeback/preview
{ "rows": [...], "target": "prices", "mode": "upsert", "key_columns": ["sku_id"] }
→ { "rows": [...], "row_count": 12, "dry_run": true }

# Commit (dry_run=false and approval_required=false → immediate)
POST /flows/writeback
{ "rows": [...], "target": "prices", "mode": "upsert",
  "key_columns": ["sku_id"], "idempotency_key": "<flow_run_id>:<task_key>" }
→ { "id": "wb_...", "state": "committed", ... }

# With approval gate
POST /flows/writeback
{ ..., "approval_required": true }
→ { "id": "wb_...", "state": "pending_approval" }
# An approver (role: owner / admin) calls:
POST /flows/writeback/{id}/approve
→ { "id": "wb_...", "state": "committed" }
```

State machine:

```
submitted
    │  approval_required=false
    ▼
committed ── (error) ──► failed

submitted
    │  approval_required=true
    ▼
pending_approval ─── approve ──► committed
                 └── reject  ──► rejected
```

RBAC: `writer` roles (owner / admin / member) may submit. `approver` roles
(owner / admin) may approve / reject / edit. `viewer` is always denied.

Idempotency: `idempotency_key` (caller-supplied, e.g. `flow_run_id + ":" + task_key`) — a
network retry returns the existing record without re-applying the write.

---

## Axis C — Canvas: HTML-native sibling to Dashboards

### What Canvas is

A Dashboard is a JSON/Pydantic spec (`DashboardSpec`) rendered onto a CSS grid
of typed widgets. Canvas is the opposite end of the flexibility spectrum: the
source of truth is an HTML document that the author (human or LLM) writes
directly. Data comes alive through `<nubi-*>` custom elements and `{{token}}`
interpolation.

Canvas reuses the dashboards data layer (`collect.py`, `run_query_rows`,
`_resolve_connector`, RLS, variables, cross-filter runtime) and the
`report_send` flow handler. It adds: a code+visual HTML editor, a right-hand
binding inspector, the `/c/:id` public viewer, and an LLM generate/edit/repair
loop.

### CanvasDoc model

```json
{
  "version": 1,
  "title": "Q3 Exec Brief",
  "html": "<section>\n  <h1>Q3 Summary</h1>\n  <nubi-kpi data-el-id=\"el_1\"></nubi-kpi>\n  <p>Fill rate: <nubi-value data-el-id=\"el_2\"></nubi-value></p>\n</section>",
  "bindings": {
    "el_1": { "kind": "query",  "query_id": "rev_total", "field": "total", "format": "currency" },
    "el_2": { "kind": "metric", "metric_id": "pvd", "dimensions": [], "time_grain": "month" },
    "el_3": { "kind": "api",    "connector_id": "shopify_http", "path": "/orders/count.json",
              "select": "$.count", "format": "number" }
  },
  "variables": [{ "name": "region", "type": "select", "default": "EMEA" }]
}
```

The side `bindings` map (keyed by `data-el-id`) is the editor's unit of work.
HTML stays readable and LLM-writable; the editor mutates bindings without
rewriting the HTML string. Validation (server-side, `validate_canvas_doc`)
cross-checks that every `data-el-id` in `bindings` exists in the HTML and vice
versa, and resolves `query_id` / `metric_id` / `connector_id` against their
registries.

### Binding kinds

| Kind | What it does |
|---|---|
| `query` | Runs a registered query; optionally extracts a single field (`field`) for `{{token}}`-style scalar injection |
| `metric` | Runs a semantic-layer metric via `compile_metric` — dimensions, time_grain, and filters are configurable per element |
| `api` | Calls an HTTP_JSON connector; `path` is appended to the connector's base URL; `select` is a JSONPath expression applied to the response JSON |

All three binding kinds honour RLS: data flows through the same
`collect.py` / `run_query_rows` / `_resolve_connector` pipeline as dashboard
widgets, so per-org predicates and `source_unsupported_rls` guards apply
unchanged.

### Custom element vocabulary

Canvas uses the `nubi-*` client convention. All elements require `data-el-id`
when data-bound:

| Element | Purpose |
|---|---|
| `<nubi-kpi>` | Single KPI metric card |
| `<nubi-table>` | Tabular data grid |
| `<nubi-chart>` | Chart (type attribute: scatter / bar / line / pie / area) |
| `<nubi-metric>` | Semantic-layer metric value |
| `<nubi-filter>` | Variable filter control |
| `<nubi-text>` | Rich-text / Markdown panel |
| `<nubi-value>` | Inline single scalar (`{{token}}`-style injection) |

Plain HTML elements can also carry `data-el-id` to receive `{{token}}` text or
attribute injection from a `query` or `api` binding.

### Security model

Canvas HTML passes through two independent safety layers:

1. **Client**: `sanitizeDashboardHtml` (DOMPurify allowlist) before `innerHTML`
   is set. No `<script>`, `on*=`, or `javascript:/data:` URI survives.
2. **Server**: `validate_canvas_doc` (extends `validate_dashboard_html`) on save.
   Same block list plus binding consistency checks.

The extended allowlist adds `nubi-metric`, `nubi-filter`, `nubi-text`,
`nubi-value` to the existing `nubi-kpi`, `nubi-table`, `nubi-chart` set.
New tags require explicit allowlisting with justification; the sanitizer trust
boundary is never relaxed.

### LLM integration

`backend/app/ai/canvas.py` mirrors the dashboard AI module:

- `generate_canvas_doc(question, catalog, …)` — teaches the LLM the `nubi-*`
  element vocabulary, the `bindings` map shape, and the safety rules; runs a
  generate → validate → repair loop (up to `MAX_DASHBOARD_REPAIR_ROUNDS`
  rounds); raises a loud HTTP 422 on final failure.
- `edit_canvas_doc(doc, instruction, catalog, …)` — diff-style edit ("make the
  header red", "bind el_3 to the revenue metric") with the same validate/repair loop.
  Canvas shines here because the model edits HTML directly instead of a constrained spec.

Routes: `POST /ai/canvas`, `POST /ai/canvas/edit`, `GET /ai/canvas/schema`,
`POST /canvas/validate` (stateless validation oracle).

### Scheduled sending

The `report_send` flow handler is generalised to dispatch on `canvas_id` OR
`board_id`. A Canvas can be sent on a schedule to recipients (html/pdf) with
per-recipient RLS (`locked_params` + captured `policies`), email, and optional
Slack/Teams notify channels — identical to the board `report_send` flow, no
forked code.

The editor's "Schedule" dialog writes a `report_send`-style flow task config
with `{canvas_id, format, recipients, params, locked_params, …}`.

### Routes and navigation

| Route | Surface |
|---|---|
| `GET /canvas/:id` | Canvas editor (code + visual split, RHS inspector) |
| `GET /c/:id` | Public viewer (read-only, URL ↔ variable sync) |
| `GET /canvases` | Canvas list page (mirrors `/dashboards`) |
| `POST /canvases` | Create a Canvas (CRUD via repo, mirrors boards) |
| `PUT /canvases/:id` | Save / update |
| `POST /canvas/validate` | Stateless validation oracle |
| `POST /ai/canvas` | LLM generate |
| `POST /ai/canvas/edit` | LLM edit |

---

## The close-the-loop architecture

### The thesis

Most BI tools do one thing: display data. Nubi closes the loop: compute a
decision (Flow), show it (Dashboard or Canvas), act on it (action / approval
widget → write-back), write the result back to the source, re-trigger the next
compute cycle.

```
┌─────────────────────────────────────────────────────────────────────┐
│                     CLOSE-THE-LOOP CYCLE                            │
│                                                                     │
│  ┌─────────────────┐   materialized to   ┌─────────────────────┐  │
│  │                 │────────────────────►│   Source table       │  │
│  │   FLOW          │                     │   (warehouse / DuckDB│  │
│  │  (compute +     │◄───downstream────── │    or any connector) │  │
│  │   decide)       │    trigger          └──────────┬───────────┘  │
│  │                 │                                │               │
│  │  - per-cell     │              base_table / base_sql             │
│  │    cpu/mem/     │                                │               │
│  │    timeout      │                                ▼               │
│  │  - stochastic   │                   ┌────────────────────────┐  │
│  │    cells +      │                   │   Semantic model        │  │
│  │    run seed     │                   │   MetricDefinition:     │  │
│  │  - artifact     │                   │   - base measures       │  │
│  │    channel      │                   │   - derived / ratio     │  │
│  │  - sweep /      │                   │   - time intelligence   │  │
│  │    backfill     │                   │   - top-N               │  │
│  └─────────────────┘                   │   - rls_keys            │  │
│         │                              └───────────┬─────────────┘  │
│         │ write-back                               │ compile_metric  │
│         │ (idempotent,                             ▼                │
│         │  dry-run,             ┌──────────────────────────────┐   │
│         │  RBAC,                │  Pre-agg rollup (optional)   │   │
│         │  approval gate)       │  build_rollup_for_metric      │   │
│         │                       │  __base-aware router          │   │
│         │                       └──────────────┬───────────────┘   │
│         │                                       │ Arrow IPC         │
│         │                                       ▼                   │
│         │                       ┌──────────────────────────────┐   │
│         │                       │  Dashboard / Canvas           │   │
│         │                       │  (display + filter)           │   │
│         │                       │  - DuckDB-WASM in browser     │   │
│         │                       │  - $0 / view marginal cost    │   │
│         │                       │  - variables, cross-filter    │   │
│         │                       │  - scheduled send (PDF/HTML)  │   │
│         │                       └──────────────┬───────────────┘   │
│         │                                       │ action / approval  │
│         └───────────────────────────────────────┘  widget           │
└─────────────────────────────────────────────────────────────────────┘
```

### Walking the cycle

1. **Flow** computes a decision (forecast, anomaly, recommendation) using SQL
   or Python cells with governed compute resources. Stochastic cells get a
   run-level seed for reproducibility. Artifacts (model blobs) cross cell
   boundaries via the typed artifact channel.

2. **Materialized to a table**: a `materialize` or `connector_write` task writes
   the result to a warehouse table (or DuckDB file). This is the source of truth
   for the next display cycle.

3. **Semantic model**: a `MetricDefinition` declares how that table is governed —
   what dimensions may be grouped by, which RLS keys protect it, what derived
   KPIs can be computed. An agent or dashboard can query it without writing SQL.

4. **Pre-agg rollup** (optional): `build_rollup_for_metric` materializes a
   rollup that covers the metric's `__base` layer so repeated dashboard views hit
   the rollup instead of the raw table. The browser fetches the Arrow result and
   runs DuckDB-WASM locally — zero server compute per view.

5. **Dashboard or Canvas**: a structured grid (`DashboardSpec`) or a freeform
   HTML document (`CanvasDoc`) binds to the metric, query, or API connector.
   Both surfaces use the same data/RLS layer and the same browser-compute wedge.

6. **Action / approval widget**: a Canvas element (or a dashboard action widget)
   lets a user review the Flow's recommendation and approve / reject / edit it.

7. **Write-back**: the approved value is written back to the source connector via
   the governed write-back engine (idempotent, RBAC, dry-run preview).

8. **Downstream trigger**: the write-back completion fires a registered
   downstream trigger, which spawns the next Flow run. The loop closes.

### What each step costs

| Step | Server compute? | Billing line |
|---|---|---|
| Flow run (Python / SQL cell) | Yes | `compute_zar_per_1000_cu` |
| Rollup build | Yes (one-time) | `compute_zar_per_1000_cu` |
| Snapshot refresh | Yes (periodic) | `scan_zar_per_tib` + `storage_zar_per_gb_month` |
| Dashboard / Canvas view | **No** (browser computes) | zero |
| Write-back preview (dry-run) | Minimal (diff only) | zero |
| Write-back commit | Yes (connector write) | `compute_zar_per_1000_cu` |
| Scheduled Canvas/board send | Yes (render + deliver) | `compute_zar_per_1000_cu` |
| Viewer seats | **No** | zero at every tier |

The wedge invariant is preserved at every step. The billing model meters what
the server does, not who views the result.

---

## Open-core boundary

All four capabilities above are part of the OSS core (`backend/app/`, `src/`).
Billing meters (`app/ee/billing/tiers.py`), Paystack integration, and
cloud-specific provisioning stay in `ee/`. The `agent_run_zar_per_run` and
`compute_zar_per_1000_cu` meters that Flow runs and write-backs consume are
defined in `ee/` and injected into the OSS runtime via the billing hooks —
the OSS core never imports from `ee/` directly.

---

## Claim-native embedded-host tenancy

### What it is

Standard embed JWTs require the user to already be a member of an org in the
`org_members` table. **Host-mode** tenancy lets a third-party application manage
tenancy itself: the JWT carries an `org_id` (or a configurable claim name) and
Nubi trusts it without querying `org_members`.

### Enabling host mode

Set `host_mode: true` and `org_claim` on a JWT issuer via the admin API
(`POST /security/jwt-issuers`):

```json
{
  "name": "My App",
  "issuer": "https://my-app.example.com",
  "audience": "nubi:my-project",
  "jwks_url": "https://my-app.example.com/.well-known/jwks.json",
  "host_mode": true,
  "org_claim": "tenant_id"
}
```

When a token from this issuer arrives the auth layer reads
`claims["tenant_id"]` (or `"org"` when `org_claim` is absent) and pins the
request's org to that value — no `org_members` lookup occurs.

### Security model

- Only issuers explicitly flagged `host_mode: true` trigger this path. Standard
  issuers still require `org_members` membership.
- The org claim value MUST be a plain `string`. Arrays, objects, and booleans are
  rejected with `403 forbidden`.
- RLS policies still come from the `policies` claim in the JWT, verified by
  the same token-signature check as all embed tokens. The org claim selects the
  tenant; RLS policies govern what data that tenant sees.
- Host-mode tokens cannot carry write or admin scopes — only read scopes are
  honoured for embed tokens.
- An X-Org-Id header that tries to redirect to a different org than the claim is
  rejected with `403`.

### Implementation path

`backend/app/auth/deps.py` → `_maybe_pin_host_mode_org()` reads the verified
`VerifiedIdentity`; `backend/app/routes/_org.py` exposes `host_mode_org_pin`
(a `ContextVar`) consumed by `get_user_org` / `resolve_org_id` on every request.

---

## Templated datastores and the secret resolver

### Claim templating

A single datastore definition can serve many tenants by resolving connection
fields (database, schema, host) from JWT claims at request time using
`{{ claims.<name> }}` placeholders in the datastore's `template_config`:

```yaml
# In the datastore config
template_config:
  database: "ks_{{ claims.org }}"
  schema:   "{{ claims.tenant }}_data"
```

The placeholder is resolved by `TemplateResolver` in
`backend/app/connectors/claim_template.py`.

### Security constraints

1. **Allowlist**: placeholder names must be in the `CLAIM_ALLOWLIST`:
   `org`, `sub`, `email`, `tenant`, `workspace`, `environment`, `region`.
   Any other name raises `TemplateSecurityError` before substitution begins.

2. **Value validation**: resolved values must match `^[a-zA-Z0-9_-]{1,128}$`.
   SQL/shell injection characters (quotes, semicolons, slashes, spaces) cause
   an immediate rejection.

3. **Substitution is safe**: implemented with `re.sub` / string manipulation —
   no `eval`, no `exec`, no Jinja2.

### Pluggable secret resolver

Credentials are likewise resolved per-tenant through the `SecretResolver`
abstraction (`backend/app/connectors/secret_resolver.py`):

| Kind | Behaviour |
|------|-----------|
| `"encrypted_store"` (default) | Uses the existing per-datastore encrypted secret store, scoped to `org_id` extracted from claims. Existing connectors are unaffected. |
| `"external"` | Calls a registered async callable `(datastore_id, claims) -> dict`. Wire a vault client at startup via `set_external_resolver_factory()`. |

Resolver failure always propagates — there is no fallback to a default credential
set. A wrong-tenant credential silencing would be a security failure.

---

## Outbound webhooks

### Event catalog

Nubi fires outbound HTTPS webhooks for five platform events:

| Event type | Fired when |
|------------|-----------|
| `watch_breach` | A monitored metric breaches its threshold (`backend/app/ai/watch.py`) |
| `freshness_stale` | A flow run or dataset exceeds its freshness SLA |
| `query_failed` | A query errors on an org's behalf |
| `flow_completed` | A flow run finalises (success or failure) |
| `query_executed` | A query completes successfully — for host audit / POPIA logging (metadata-only, no row data) |

### Envelope

Every event is delivered as a JSON POST with this top-level shape:

```json
{
  "type": "watch_breach",
  "id": "a3f2c1d0-…",
  "org_id": "9e1b…",
  "occurred_at": "2024-02-15T10:23:45.123456+00:00",
  "data": { "...event-specific fields..." }
}
```

The `id` is a UUID4 unique per emission (idempotency key).

#### `watch_breach` data

```json
{
  "watch_id":   "…",
  "name":       "Revenue Watch",
  "metric_id":  "revenue",
  "value":      48500.0,
  "explanation":"…",
  "labels":     { "category_id": "cat-abc" }
}
```

`labels` is an arbitrary host-supplied metadata map set per watch definition
(empty object `{}` when not set). Subscribers can key on it to correlate
breach events with their own domain objects. See
[files-as-code.md — Watches as code](files-as-code.md#d2-watches-as-code) for
declaring watches via `nubi apply` and
[observability.md](observability.md#watch_breach--labels-passthrough) for the
full payload reference.

#### `freshness_stale` data

```json
{ "flow_run_id": "…", "flow_id": "…", "name": "…", "age_s": 7200.0, "sla_s": 3600.0 }
```

#### `query_failed` data

```json
{ "error_code": "connector_error", "message": "…", "datastore_id": "…", "query_id": "…" }
```

#### `flow_completed` data

```json
{ "flow_run_id": "…", "flow_id": "…", "name": "…", "state": "failed", "duration_s": 12.4, "failed_task": "step_3", "error": "…" }
```

#### `query_executed` data

**POPIA-safe by construction** — this payload contains metadata only. No row data, no SQL literals with bound values, and no filter values that could carry personal information are ever included.

```json
{ "query_id": "my_query_slug", "subject": "user-uuid-or-embed", "datasource_id": "ds-uuid", "row_count": 42 }
```

| Field | Type | Description |
|-------|------|-------------|
| `query_id` | string \| null | The registered query id or metric slug; `null` for ad-hoc SQL |
| `subject` | string | The caller's user-id, or `"embed"` for embed-token callers |
| `datasource_id` | string \| null | The org-scoped datastore id; `null` for the built-in demo connector |
| `row_count` | integer \| null | Number of rows in the result set (never the rows themselves) |

Use this event to maintain an immutable access log for regulatory compliance (POPIA, GDPR data-access logs). Subscribe only the endpoints that require this volume of events — it fires on every successful cache-MISS query execution.

### HMAC signing

Each delivery is signed with HMAC-SHA256. The signed payload is
`"{timestamp}.{body}"` (Stripe-style) where `body` is the canonical JSON
(`sort_keys=True`, compact separators). Headers sent with every request:

| Header | Value |
|--------|-------|
| `X-Nubi-Signature` | Hex HMAC-SHA256 of `"{ts}.{body}"` keyed by the endpoint secret |
| `X-Nubi-Timestamp` | Unix timestamp (seconds) used in the signed payload |
| `X-Nubi-Event` | Event type string (e.g. `watch_breach`) |
| `Content-Type` | `application/json` |

Verification example (Python):

```python
import hashlib, hmac, time

def verify(secret: str, body: bytes, timestamp: int, signature: str) -> bool:
    signed_payload = f"{timestamp}.".encode() + body
    expected = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
```

Reject events where `abs(time.time() - X-Nubi-Timestamp) > 300` to guard
against replay attacks.

### Per-org endpoint management

Webhooks are configured per org via the REST API:

```http
POST   /api/v1/webhooks
GET    /api/v1/webhooks
GET    /api/v1/webhooks/{endpoint_id}
PUT    /api/v1/webhooks/{endpoint_id}
DELETE /api/v1/webhooks/{endpoint_id}
```

Create request body:

```json
{
  "name": "My Webhook",
  "url": "https://hooks.example.com/nubi",
  "secret": "your-signing-secret",
  "event_types": ["watch_breach", "flow_completed"],
  "active": true
}
```

The `secret` field is write-only — it is never returned by read endpoints.

### SSRF protection

URLs are validated at registration time AND at delivery time (defence-in-depth
against DNS rebinding). Private/loopback IPs, cloud metadata endpoints, and
non-HTTPS schemes are blocked. Registration of a blocked URL returns
`400 ssrf_blocked`.

### Delivery behaviour

- Up to 4 attempts with exponential backoff (base 0.5 s, 10 s timeout per attempt).
- `2xx` = success; other `4xx` (non-429) = permanent failure (no retry).
- `5xx` and `429` are retried.
- Fire-and-forget: delivery failure never propagates to the request or flow that
  triggered the event.

---

## Declarative provisioning (`nubi apply` / `nubi plan`)

### Bundle layout

A bundle is a directory with a `bundle.yaml` manifest plus optional
resource sub-directories:

```
mybundle/
  bundle.yaml          # required
  metrics/             # optional; *.yaml metric definitions
    revenue.yaml
  datastores.yaml      # optional; list of connector envelopes
  dashboards/          # optional; *.json dashboard envelopes
    main.json
  queries/             # optional; *.yaml or *.json query envelopes
    top_customers.yaml
```

`bundle.yaml` schema:

```yaml
apiVersion: nubi/v1
kind: bundle
metadata:
  org: <org-id-or-slug>     # required
  version: "1"              # required
  project: <project-id>     # optional
```

### CLI commands

```bash
# Preview what would change (no writes):
nubi plan ./mybundle

# Apply the bundle idempotently:
nubi apply ./mybundle
```

Both commands POST to `POST /api/v1/apply` with `dry_run: true` (plan) or
`dry_run: false` (apply). Auth is a first-party Bearer token with write scope.

### Idempotency

Applying the same bundle twice is a true no-op. Every resource kind has a
stable key:

- **Metrics**: `config.metric.slug` — upserted by slug within the org.
- **Dashboards**: `metadata.id` — create or update by UUID.
- **Connectors**: `metadata.id` when present — otherwise create.
- **Queries**: `metadata.id` when present — otherwise create.

The server reports `"action": "unchanged"` for resources that are already
up-to-date; `"action": "created"` or `"action": "updated"` when a write occurs.

### Partial failure

One failing envelope never aborts the rest. The response always returns
HTTP 200; inspect `summary.failed` and per-resource `error` fields to detect
problems:

```json
{
  "results": [
    { "kind": "query", "id": "…", "name": "Revenue", "action": "created" },
    { "kind": "query", "id": null, "name": "Bad metric", "action": "failed", "error": "…" }
  ],
  "summary": { "created": 1, "updated": 0, "unchanged": 0, "failed": 1 },
  "dry_run": false
}
```

### HTTP API

The same logic is available directly:

```http
POST /api/v1/apply
Authorization: Bearer <first-party-token>

{
  "version": "1",
  "resources": [<portability-envelope>, …],
  "dry_run": false
}
```
