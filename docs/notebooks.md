# Notebooks — Cell-Based Flows

A notebook in Nubi is a sequence of SQL and Python cells that run in order and share data between them. Under the hood a notebook is a **FlowSpec** — the same format that powers the visual DAG builder, scheduled flows, and the SDK. There is no second system: one spec, two runtime modes, two UI views.

---

## Thesis: notebooks, queries, and flows are the same thing

> **"Fabric notebooks-in-a-DAG + SQLMesh SQL-first transforms — on your own warehouse."**

- Like **Microsoft Fabric notebooks**: cells are DAG nodes; the same notebook artifact runs interactively (preview) or at scale (scheduled/durable). Unlike Fabric: no OneLake, no ADLS Gen2, no Spark coupling. Your warehouse is the data layer.
- Like **SQLMesh**: SQL-first authoring, plan-before-apply gate, column-level lineage, cross-engine transpilation via sqlglot. Unlike SQLMesh: no project layout required, no dbt-style `ref()`, works against any BYO connector.
- Like **Hex**: DAG inferred from cell references; notebook and DAG are the same artifact. Unlike Hex: open-core, self-hostable, BYO warehouse only — no compute cost on preview.

The cost wedge: **preview against 500-row samples costs nothing on the BYO warehouse.** Only durable runs hit the warehouse at full scale.

---

## Data model

### CellSpec IS TaskSpec

A cell is a `TaskSpec` (`backend/app/flows/spec.py`) with three additive top-level fields (`cell_type`, `execution_mode`, `freshness_sla_s`) and four additive config keys:

```
TaskSpec fields (unchanged, backward-compatible):
  key          — stable human-readable slug, e.g. "revenue"
                 (generated as "cell_sql_4f2a" — type + 4-char random suffix)
  kind         — "query" | "python" | "noop" | "materialize" | …
  needs        — DAG edges (upstream cell keys)
  config       — kind-specific dict
  retries, retry_backoff_s, timeout_s, cache_ttl_s, ui

Top-level additions (CellSpec / TaskSpec, additive):
  cell_type:        "sql" | "python" | "markdown"   (maps to kind)
  execution_mode:   "preview" | "durable" | null     (per-cell override)
  freshness_sla_s:  int (0 = no staleness alert)

Config additions for SQL cells (kind='query'):
  config.source_dialect  — dialect the SQL was authored in
  config.datastore_id    — BYO warehouse connector; absent = demo DuckDB
  config.preview_limit   — row cap for interactive runs (default 500)

Config additions for Python cells (kind='python'):
  config.use_remote_kernel — route to E2B/Modal in durable mode
```

These are JSONB spec fields — no new DB table needed.

### NotebookSpec — the notebook envelope

`NotebookSpec` (`backend/app/flows/notebook.py`) is a thin wrapper over `FlowSpec`. The executor never sees it: `notebook_to_flowspec()` compiles it to a plain `FlowSpec` before execution.

Key fields on `NotebookSpec`:

| Field | Type | Description |
|-------|------|-------------|
| `notebook_id` | `str` | Stable UUID — maps to `flows.id` in the store. |
| `view` | `"notebook"` \| `"dag"` | Active UI view. |
| `tasks` | `list[CellSpec]` | Cells in top-to-bottom order (also accessible as `.cells`). |
| `execution_mode` | `"preview"` \| `"durable"` | Notebook-level default. Per-cell `execution_mode` overrides when set. |
| `runtime_config` | `NotebookRuntimeConfig` | Row limit, memory limit, compute target — lives at the root, not inside cells. |
| `env` | `str` | Execution environment tag (`"dev"`, `"staging"`, `"prod"`). |
| `params` | `list[FlowParam]` | Parameter declarations (the "parameter cell" equivalent). |

`runtime_config` is a root-level object, not a cell magic (`%%configure`) — avoiding the Fabric trap where the config cell must come first.

### The three representations of one spec

```
NotebookSpec  ←→  FlowSpec  ←→  nubi.flows SDK Python
      ↑               ↑               ↑
notebook_to_flowspec()       flow_spec_to_sdk()
flowspec_to_notebook()       SDK .compile()
```

All three are round-trippable. Switching views never changes the underlying spec.

---

## Authoring a notebook

### Cell types

| `cell_type` | `kind` compiled to | What it does |
|-------------|-------------------|--------------|
| `sql` | `query` | Runs a SQL SELECT against your warehouse (or demo DuckDB). |
| `python` | `python` | Runs Python code with `inputs` dict populated by upstream cells. |
| `markdown` | `noop` | Decorative text cell — passthrough, no execution. |

### Cell keys

Every cell gets a **stable slug** — generated as `cell_sql_<4-char suffix>` or `cell_python_<4-char suffix>`. Keys are stable across reorders so that cross-cell SQL references (`SELECT * FROM cell_sql_4f2a`) keep working after you move cells.

Display labels (`[1]`, `[2]`, …) reflect current order. The stable key is what downstream SQL references; the ordinal label is visual-only.

### Cross-cell data flow

Each executed cell's result is registered in two namespaces under the same cell key:

| Runtime | How written | How read |
|---------|-------------|----------|
| Preview (in-process DuckDB) | Backend registers rows as a DuckDB in-memory table | `SELECT * FROM cell_revenue` in a downstream SQL cell |
| Durable (work-pool) | `TaskContext.inputs["cell_revenue"]` dict | `{{ inputs.cell_revenue.rows }}` template in downstream cell config; durable SQL cells auto-register upstream Python output as in-memory DuckDB tables (blueprint OQ-3 resolution) |

### Automatic edge inference

In notebook view (`view="notebook"`), `infer_notebook_edges()` fills missing `needs` edges automatically:

1. **SQL cells**: sqlglot parses `FROM` clauses; any `cell_<key>` table name matching an earlier cell is added to `needs`.
2. **Python cells**: `ast.parse` finds `inputs["<key>"]` subscript patterns.
3. **Sequential fallback**: if no reference is found, the previous cell is added to `needs` (Jupyter-compatible linear order).
4. **Explicit `needs` always wins** — inference only fills empty lists.

In DAG view (`view="dag"`), explicit `needs` edges from the canvas are used directly; inference is not called.

### Parameters

`NotebookSpec.params` declares flow-level parameters (same `FlowParam` model used by all flows). These appear as a pinned parameter cell at the top of the notebook UI and are referenced in cell config strings as `{{ params.region }}`.

---

## Two execution runtimes

`execution_mode` is a notebook-level default with per-cell override. The same spec can run in either mode.

### Preview (interactive)

Triggered by clicking the run button on a cell.

- Runs in-process using DuckDB — no work-pool, no task store writes.
- Rows capped at `preview_limit` (default 500, configurable up to 10 000).
- **Row cap is enforced by the backend** (`POST /flows/preview`) — not by the warehouse. In v1, the planner does not inject `LIMIT` before the warehouse query for BYO connectors; the cap is applied after fetch. Full push-down LIMIT into the warehouse query is a Phase 2 item.
- RLS claims are passed to every cell's handler so row-level policies are enforced.
- Result shape: `{ cell_key, columns, rows, row_count, total_row_count }`.

The preview endpoint runs all upstream cells in topological order before the target cell so cross-cell references resolve. Upstream cell results are accumulated in `inputs` and passed to each subsequent cell.

### Durable (work-pool)

Triggered by "Run All" in the notebook toolbar, `POST /flows/{id}/run`, or on a schedule.

- Runs through the normal `drain_flow_run` work-pool path.
- No row cap — full data from the warehouse.
- Results are persisted as `task_run.result` rows in the flow store.
- Use `POST /flows/run-cell` to run a single cell durably (includes all upstream dependencies automatically).
- Log output is captured per task and accessible via `GET /flows/runs/{run_id}/tasks/{task_key}/logs`.

### Mode selection table

| Trigger | Mode |
|---------|------|
| Cell run button | Preview |
| "Run All" toolbar button | Durable (full data) |
| `POST /flows/{id}/run` | Durable |
| Scheduler / `flow_tick` | Durable |
| `POST /flows/preview` | Preview |
| `POST /flows/run-cell` | Durable (single cell) |

---

## Cross-engine SQL (source_dialect)

SQL cells support a `source_dialect` config key. When present, sqlglot transpiles the authored SQL from the source dialect to the target before planning:

```json
{
  "kind": "query",
  "cell_type": "sql",
  "config": {
    "sql": "SELECT DATE_TRUNC('month', ts) AS mo, SUM(revenue) FROM orders GROUP BY 1",
    "source_dialect": "bigquery",
    "datastore_id": "ds-snowflake-uuid"
  }
}
```

This allows authoring BigQuery SQL that runs against a Snowflake datastore without manual rewriting. Transpile runs before RLS injection (never after) so that predicate stripping cannot occur.

**v1 honest limits**: `datastore_id` connector resolution in durable `_handle_query` requires `org_id` on `TaskContext` (added by the Keystone agent). The `_resolve_flow_connector` helper resolves the connector from the datastore registry and returns the target dialect. Preview runs use demo DuckDB when `datastore_id` is absent.

---

## Column lineage and plan-before-apply

### Column lineage

`backend/app/flows/lineage.py` provides `build_cell_lineage_graph(spec)` which walks every `query` task in topological order and calls `extract_column_lineage()` (sqlglot-backed) to produce a `CellLineageGraph`:

- `nodes` — per-cell summary (output columns, input edges).
- `edges` — flat list of `CellColumnEdge` (from_cell, from_col, to_cell, to_col).
- `column_flow` — inverted index: `"cell_key:output_col"` → list of downstream `"cell_key:input_col"` strings.

Cross-cell tracing works because each SQL cell's source is registered as a virtual table under its key (`cell_revenue → SELECT ...`) in the sqlglot `sources` map, so downstream cells that reference `cell_revenue` get full column-level tracing through the upstream SQL.

### Plan-before-apply

`lineage_plan(spec, changed_cell_key)` returns a plan showing downstream impact before a durable materialize run:

```json
{
  "valid": true,
  "issues": [],
  "lineage": { "nodes": {…}, "edges": […], "column_flow": {…} },
  "downstream_impact": [
    {
      "cell_key": "final_blend",
      "change_type": "breaking",
      "affected_columns": ["revenue"]
    }
  ]
}
```

`change_type` is `"breaking"` when the affected column is referenced in a WHERE, GROUP BY, JOIN, or HAVING clause of the downstream cell (the column change would silently alter filter behavior). `"non_breaking"` means it only appears in the SELECT list.

### Lineage API endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /lineage/flow/{id}` | Column-level lineage graph for a stored flow (notebook). |
| `POST /lineage/plan` | Ephemeral plan — accepts `{spec, changed_cell_key}`, returns impact report. No data written. |
| `POST /lineage/cell` | Ad-hoc single-cell column lineage. Accepts `{sql, dialect, cell_key, upstream_cells: {key: sql}}`. Used by the notebook UI after each preview run. |
| `GET /lineage/query/{id}` | Lineage for a registered query (extended with `column_edges` from the `query` task lineage). |

---

## REST API — notebook endpoints

All endpoints require a valid first-party Bearer token. Auth: `current_user`.

### Save or create a notebook

```
POST /api/v1/flows/notebooks
Content-Type: application/json

{
  "notebook": { ...NotebookSpec dict... },
  "name": "optional override name"
}
```

Compiles the `NotebookSpec` to a `FlowSpec` via `notebook_to_flowspec()` and persists it as a flow. Returns the created flow row (`201`).

### Load a notebook

```
GET /api/v1/flows/notebooks/{id}
```

Loads the persisted flow and wraps it back into a `NotebookSpec` via `flowspec_to_notebook()`. Returns `{ ...flow_row, notebook: NotebookSpec }`.

### Preview a cell

```
POST /api/v1/flows/preview

{
  "spec": { ...FlowSpec dict... },   // OR
  "flow_id": "uuid",
  "cell_key": "revenue",             // optional; defaults to last cell
  "params": {},
  "preview_limit": 500               // 1–10000
}
```

Returns `{ cell_key, columns, rows, row_count, total_row_count }`.

### Run a cell durably

```
POST /api/v1/flows/run-cell

{
  "spec": { ...FlowSpec dict... },   // OR
  "flow_id": "uuid",
  "cell_key": "revenue",
  "params": {}
}
```

Returns `{ cell_key, columns, rows, row_count, flow_run_id }`.

---

## UI — Canvas and Notebook views

The Flows page (`/flows`) supports two views of the same `FlowSpec`, toggled by the **ViewToggle** button in the toolbar:

| View | What you see | Authoring surface |
|------|-------------|-------------------|
| **Notebook** (`view="notebook"`) | Cells stacked top-to-bottom (linear, Jupyter-style) | SQL or Python cell with Monaco editor; inline results grid below each cell |
| **Canvas / DAG** (`view="dag"`) | React Flow canvas with nodes and arrows | Node inspector; drag-to-connect edges; code panel |

Switching views never changes the underlying spec. `NotebookView.jsx` manages the notebook layout; `FlowBuilder.jsx` owns the canvas and delegates to `NotebookView` when `viewMode === 'notebook'`. The active view is persisted in `spec.view` so it round-trips on save/reload.

### Notebook toolbar actions

| Action | What it does |
|--------|-------------|
| `+ SQL` | Inserts a blank SQL cell (`cell_type: 'sql'`, `kind: 'query'`). |
| `+ Python` | Inserts a blank Python cell (`cell_type: 'python'`, `kind: 'python'`). |
| Save | Creates or updates the flow via `POST /flows` or `PUT /flows/{id}`. |
| Run all | Triggers a durable run via `POST /flows/{id}/run`. Requires saving first. |

### Cell toolbar

Each cell shows a `[N]` ordinal badge, a Run button (preview), move-up/down arrows, and a delete button. Running a cell calls `POST /flows/preview` and renders rows in a collapsible `DataTable` below the editor.

### Cross-cell references in the notebook

Reference an upstream cell's result as a table name in SQL:

```sql
-- In cell "summary" — reads from the "revenue" cell's output
SELECT region, SUM(amount) AS total
FROM revenue
GROUP BY region
```

The cell key (`revenue`) is the DuckDB table name. The toolbar badge shows the ordinal label (`[2]`); the actual key used in SQL is the stable slug.

In Python:

```python
# Access upstream cell output via inputs dict
rows = inputs["revenue"]["rows"]
result = {"total": sum(r["amount"] for r in rows)}
```

---

## Python SDK — notebook authoring

Notebooks authored as Python via the code-first SDK compile to the same `FlowSpec`:

```python
from nubi.flows import flow, task

@task(kind="query", sql="SELECT region, SUM(revenue) FROM sales GROUP BY region")
def revenue(): pass

@task(kind="python", code="result = {'total': sum(r['revenue'] for r in inputs['revenue']['rows'])}")
def summarise(revenue): pass

@flow
def my_notebook():
    r = revenue()
    summarise(r)

spec = my_notebook.compile()   # → FlowSpec dict; POST to /api/v1/flows
```

`flow_spec_to_sdk(spec)` generates scaffold-grade Python from any saved notebook. The in-builder code panel (`src/flows/CodePanel.jsx`) surfaces this as an editable view — "Apply code" round-trips through `POST /flows/compile`.

---

## Environment tags and virtual dev/prod

`FlowSpec.env` (default `"prod"`) is a run-time tag. Materialize tasks stamp the env into the DuckDB blend file path:

```
blends/<flow_id>__dev.duckdb
blends/<flow_id>__prod.duckdb
```

Running with `env="dev"` writes to the dev blend without touching prod. Promotion is an atomic file rename. For BYO warehouse materializations, virtual env support is deferred to v2 (blueprint OQ-4 decision: v1 limits virtual envs to DuckDB blend files).

---

## Caching

`cache_ttl_s > 0` on a `CellSpec` enables result memoisation. Preview and durable cache keys never collide: preview runs include `LIMIT` in the plan before the SHA-256 hash; durable runs do not.

**Honest limits**: the cache is an in-memory LRU (256 entries, 5-min TTL, single-process). Multi-process deployments will miss across workers. A Redis backend maps directly to the `get_cache()` interface upgrade path.

---

## Freshness alerts

`freshness_sla_s > 0` on a cell emits a staleness event via `flow_tick` when the cell has not succeeded within the configured window. Set to `0` (default) to disable.
