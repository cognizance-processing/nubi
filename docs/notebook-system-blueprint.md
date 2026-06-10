# Nubi Unified Notebook/Cell System — Sign-Off Blueprint

**Status**: Design complete. Awaiting human decisions on open questions before Phase 2 begins.
**Thesis**: A notebook, a SQL query, and a flow are the same DAG-of-cells at different UI granularities. One spec, two runtimes, zero warehouse lock-in.

---

## 1. Product Positioning

**"Fabric notebooks-in-a-DAG + SQLMesh SQL-first transforms — on your own warehouse."**

- Like **Microsoft Fabric**: notebooks are first-class DAG nodes; cells chain together; the same notebook artifact runs interactively (preview) or at scale (scheduled). Unlike Fabric: no OneLake, no ADLS Gen2, no Spark-session coupling. The user's warehouse IS the data layer.
- Like **SQLMesh**: SQL-first, plan-before-apply gate, virtual dev/prod environments, column-level lineage, cross-engine transpilation via sqlglot. Unlike SQLMesh: no opinionated project layout, no dbt-like ref() system; works against any BYO connector, not a single analytical warehouse.
- Like **Hex**: reactive DAG inferred from cell references; notebook and DAG are the same artifact viewed differently. Unlike Hex: open-core, self-hostable, BYO warehouse.
- **Not like Databricks/Spark**: no JVM, no cluster spin-up. Interactive runs use DuckDB-WASM in the browser (zero latency). Durable runs use the existing work-pool + Modal/E2B remote kernels.

The cost wedge: preview against 500-row samples costs nothing on the BYO warehouse. Only `materialize` (durable) tasks hit the warehouse at full scale.

---

## 2. Unified Data Model

### 2.1 Core Principle: CellSpec IS TaskSpec

`TaskSpec` in `backend/app/flows/spec.py:106` already represents a cell. The schema is structurally complete. **No Pydantic model rewrite** is needed — only additive changes.

A `CellSpec` is a `TaskSpec` with four additional config keys (stored in the existing `config: dict[str, Any]` field) and two new top-level fields:

```
TaskSpec fields (unchanged):
  key          — stable cell identifier (e.g. "cell_revenue")
  kind         — "query" | "python" | "materialize" | "noop" | ...
  needs        — DAG edges (upstream cell keys)
  config       — kind-specific dict (extended below)
  retries, retry_backoff_s, timeout_s, cache_ttl_s, ui

New top-level fields added to TaskSpec:
  cell_type: "sql" | "python" | "markdown" = "sql"
    User-facing label (maps to kind: sql→query, python→python, markdown→noop)

  execution_mode: "interactive" | "durable" = "interactive"
    Per-cell mode. Can be hoisted to NotebookSpec level with per-cell override.
    (See Open Question 5.)

New config keys for SQL cells (query kind):
  config.source_dialect   — dialect the SQL was authored in (e.g. "bigquery")
                            when set, transpiled to target before plan()
  config.datastore_id     — BYO warehouse connector; absent = demo DuckDB
  config.preview_limit    — row cap for interactive runs (default 500)

New config keys for Python cells (python kind):
  config.use_remote_kernel — route to E2B/Modal in durable mode (default false)

New config keys for materialize cells:
  config.incremental       — bool; enable last-run-ts injection
  config.incremental_ts_col — timestamp column name for incremental filter
  config.freshness_sla_s   — stale-alert threshold in seconds (0 = no alert)
```

### 2.2 NotebookSpec — The Notebook Envelope

`NotebookSpec` is a thin wrapper over `FlowSpec`. It lives in a new file `backend/app/flows/notebook.py` (~100 lines). The executor never sees it — `notebook_to_flow()` compiles it to a `FlowSpec` before execution.

```python
class NotebookRuntimeConfig(BaseModel):
    interactive_row_limit: int = 500
    duckdb_memory_limit: str = "512MB"
    pyodide_packages: list[str] = []       # future; not in v1
    durable_compute: Literal["local", "e2b", "modal"] = "local"
    durable_timeout_s: int = 3600

class NotebookSpec(BaseModel):
    # inherited from FlowSpec shape:
    version: int = 1
    name: str
    params: list[FlowParam] = []           # parameter cell (= Fabric's param cell)
    tasks: list[CellSpec] = []             # cells in top-to-bottom order

    # notebook-specific additions:
    notebook_id: str                       # stable UUID; maps to flows.id in store
    view: Literal["notebook", "dag"] = "notebook"
    runtime_config: NotebookRuntimeConfig = NotebookRuntimeConfig()
    source: Literal["notebook", "flow", "query"] = "notebook"
```

`runtime_config` lives at the notebook root, **not inside a cell body**. This avoids Fabric's `%%configure`-must-be-first-cell trap. Cells never know about compute sizing; they are pure computation declarations.

### 2.3 The Three Views of One Spec

```
NotebookSpec  ←→  FlowSpec  ←→  nubi.flows SDK Python
     ↑                ↑               ↑
notebook_to_flow()    |         flow_spec_to_sdk()
flow_to_notebook()    |         SDK .compile()
                      |
              validate_flow_spec()  [spec.py:248 — unchanged]
              drain_flow_run()      [runtime.py:980 — unchanged]
```

- **Notebook view** (`view="notebook"`): cells rendered top-to-bottom in `QueryWorkspace.jsx`. `needs` edges auto-inferred by `infer_notebook_edges()`.
- **DAG/flow view** (`view="dag"`): same spec rendered as React Flow graph in `src/flows/FlowBuilder.jsx`. Explicit `needs` edges shown as arrows. Switch `view` to toggle.
- **SQL query view**: a single-cell `NotebookSpec` IS the current query system. Existing registered queries are single-task FlowSpecs; surfacing them in the notebook editor requires no migration.

### 2.4 Ordering: Auto-Inferred Edges

For `view="notebook"`, `needs` edges are filled by `infer_notebook_edges(cells)` in `backend/app/flows/notebook.py`:

1. **SQL cells**: call `extract_lineage(sql).tables` (already in `backend/app/lineage/extract.py`). If any table name matches `cell_<key>` of an earlier cell, add that key to `needs`.
2. **Python cells**: `ast.parse(code)`, find `inputs["<key>"]` Name load nodes. Any matched key added to `needs`.
3. **Sequential fallback**: if a cell has no inferred or explicit needs AND is not a root, add `needs=[previous_cell.key]` for Jupyter-compatible linear execution.
4. **Explicit `needs` always wins** — inference only fills empty `needs` lists.

For `view="dag"`, `needs` edges are explicit (set by the React Flow canvas). `infer_notebook_edges` is not called.

### 2.5 Stable Cell Keys

Cell keys must be stable UUIDs (`cell_<uuid_prefix>`) so re-ordering cells does not break cross-cell `FROM cell_N` references. Display labels (`cell_1`, `cell_2`) reflect current order. The existing `cellRef = cell_${index + 2}` index-based naming in `QueryWorkspace.jsx:1186` is replaced with `cell.key` (the stable UUID slug), while display badges show ordinal labels.

### 2.6 Cross-Cell Data Flow — Dual Namespace

Every executed cell produces a named table in two namespaces that share the same cell key:

| Namespace | How written | How read |
|---|---|---|
| **Interactive (DuckDB-WASM)** | `registerArrowTable("cell_revenue", table)` — `wasmRuntime.js:471` | `SELECT * FROM cell_revenue` via `runLocalSqlForCell` — `wasmRuntime.js:521` |
| **Durable (TaskContext.inputs)** | task result dict returned by `execute_task` | `{{ inputs.cell_revenue.rows }}` template in downstream cell config — `executor.py:131` |

The canonical wire format between cells is **Arrow IPC** in both namespaces. Python cells in durable mode (when `use_remote_kernel=true`) return Arrow IPC via the compute runner (`runner.py` — already uses `pyarrow.ipc`). Python cells in durable mode without remote kernel return a JSON dict; downstream SQL cells that reference them via DuckDB-WASM will need the durable run to write a temp DuckDB table (see Open Question 3 / Phase 2).

### 2.7 Mapping Table: CellSpec → Execution

| cell_type | persist | execution_mode | Compiled kind | Handler |
|---|---|---|---|---|
| sql | false | interactive | query | `routes/query.py` (streaming Arrow IPC, LIMIT injected) |
| sql | false | durable | query | `flows/registry.py:_handle_query` (BYO connector) |
| sql | true | durable | materialize | `flows/registry.py:_handle_materialize` (DuckDB blend) |
| python | — | interactive | python | `routes/compute.py` → E2B/Modal/local |
| python | — | durable | python | `flows/registry.py:_handle_python` (local or E2B/Modal) |
| markdown | — | either | noop | `flows/registry.py:_handle_noop` (passthrough) |

---

## 3. Two-Runtime Execution Model

### 3.1 Mode Selection

Mode is a **call-site parameter**, not a spec field. The same `FlowSpec` can run in either mode:

| Trigger | Mode |
|---|---|
| User clicks Run on a cell | interactive/preview |
| "Run All" in notebook UI | interactive/preview |
| `POST /flows/{id}/run` | durable (full data) |
| Scheduler / `flow_tick` | durable (full data) |
| `POST /flows/{id}/plan` | preview (dry-run, no writes) |

`execution_mode` on `CellSpec` declares the cell's **intent** for cases where cells within the same notebook must differ (e.g. a markdown cell is always noop regardless of mode). The parent notebook/run's mode overrides individual cells unless explicitly pinned.

### 3.2 Interactive (Preview) Runtime

**SQL cells** — the preview path through `routes/query.py`:

```
Cell runs in browser
  └── SQL references cell_N?
        YES → runLocalSqlForCell(sql)  [wasmRuntime.js:521]
              → in-browser DuckDB-WASM, zero latency, no backend call
        NO  → runArrowQuery(sql, onBatch, {datastoreId})  [wasmRuntime.js:112]
              → POST /api/v1/query {sql, datastore_id, preview_limit: 500}
              → planner.plan(sql, claims, params, dialect=TARGET, limit=500)
                  [planner.py:210 — limit already supported at line 300]
              → connector.execute(plan) → Arrow IPC stream
  └── registerArrowTable("cell_key", table)  [wasmRuntime.js:471]
      → downstream cells can SELECT * FROM cell_key locally
```

**Required change**: Add `preview_limit: int | None = None` to `QueryIn` at `routes/query.py:227` and thread it into `planner_plan(limit=body.preview_limit)` at line 446.

**Python cells** — already wired: `runPythonCell(code, inputs)` → `POST /api/v1/compute/run` → `_choose_runner()` (E2B/Modal/local) at `routes/compute.py:192`. No change needed.

### 3.3 Durable (Work-Pool) Runtime

**SQL cells** — the durable path through `flows/registry.py:_handle_query`:

**Current gap**: `_handle_query` at line 224 hardcodes `DuckDBConnector()` and ignores `config.datastore_id`.

**Required change** in `flows/registry.py:_handle_query` (lines 220–230):

```python
# 1. Resolve connector from datastore_id if present
datastore_id = config.get("datastore_id")
if datastore_id:
    connector, target_dialect = _resolve_flow_connector(datastore_id, ctx.org_id)
else:
    connector = DuckDBConnector()
    target_dialect = "duckdb"

# 2. Source dialect transpile (cross-engine pushdown)
source_dialect = config.get("source_dialect")
if source_dialect and source_dialect != target_dialect:
    resolved_sql = sqlglot.transpile(
        resolved_sql, read=source_dialect, write=target_dialect,
        unsupported_level=sqlglot.ErrorLevel.WARN,
    )[0]

# 3. Plan with target dialect
physical_plan = plan(resolved_sql, claims=claims,
                     params=positional_params, dialect=target_dialect)

# 4. Execute
arrow_table = connector.execute(physical_plan)
```

**Required new helper** `_resolve_flow_connector(datastore_id, org_id)` added to `flows/registry.py`. Mirrors `routes/query.py:515–668` connector resolution logic. Returns `(connector_instance, dialect_str)`.

**Required change**: Add `org_id: str | None = None` to `TaskContext` dataclass at `executor.py:65`, populated from the flow run's org context in `run_one_ready_task` at `runtime.py:761`.

**Python cells** — durable Python with remote kernel:

**Current gap**: `_handle_python` at `flows/registry.py:251` always uses `sys.executable` local subprocess. No E2B/Modal.

**Required change** — add `use_remote_kernel` branch to `_handle_python`:

```python
if config.get("use_remote_kernel"):
    from app.routes.compute import _choose_runner
    runner = _choose_runner()
    inputs_arrow = {k: pa.Table.from_pylist(v["rows"])
                    for k, v in ctx.inputs.items() if "rows" in v}
    result = runner.run(code, inputs_arrow, timeout_s=config.get("timeout_s", 60))
    rows = result.table.to_pylist() if result.table else []
    return {"rows": rows, "row_count": len(rows),
            "columns": result.table.schema.names if result.table else []}
# else: existing local subprocess path unchanged
```

### 3.4 SQLGlot Cross-Engine Pushdown

**The missing wire**: `routes/query.py:446` calls `planner_plan()` with no `dialect` argument. The planner defaults to `"postgres"`. BigQuery and Snowflake connectors then do a second-step `$N` → `?`/`%s` placeholder translation. This breaks dialect-specific SQL (QUALIFY, DATE_TRUNC argument order, GENERATE_SERIES, etc.).

**Fix**: Add `CONNECTOR_DIALECT` map to `backend/app/connectors/registry.py`:

```python
CONNECTOR_DIALECT: dict[str, str] = {
    "postgres": "postgres",  "redshift": "postgres",
    "duckdb": "duckdb",
    "snowflake": "snowflake",
    "bigquery": "bigquery",
    "mysql": "mysql",        "mariadb": "mysql",
    "http_json": "postgres", "jdbc": "postgres",
}
```

In `routes/query.py` after `ctype` resolution at line 534:

```python
from app.connectors.registry import CONNECTOR_DIALECT
target_dialect = CONNECTOR_DIALECT.get(ctype or "duckdb", "postgres")

physical_plan = planner_plan(
    sql=effective_sql,
    claims=claims,
    params=effective_params,
    dialect=target_dialect,       # NEW
    limit=body.preview_limit or None,  # NEW
)
```

The planner already threads `dialect` end-to-end through `parse_one(dialect)` → AST transforms → `tree.sql(dialect)` → `PhysicalPlan.dialect`. RLS injection at `planner.py:287–297` operates on the AST before SQL generation; it is unaffected by dialect changes. The `$N` → `?`/`%s` connector translations remain as a safety backstop during transition.

**The source_dialect transpile** (cross-engine authoring): a notebook cell with `config.source_dialect="bigquery"` executing against a Snowflake datastore:

```python
# In _handle_query, before plan():
sql = sqlglot.transpile(sql, read="bigquery", write="snowflake")[0]
plan(sql, ..., dialect="snowflake")
```

Transpile runs **before** RLS injection (never after) to prevent predicate stripping.

### 3.5 Virtual Environments (SQLMesh-Style Dev/Prod)

**Design**: env is a flow-run tag, not a spec field. Every `FlowSpec` gains two new top-level fields (add to `spec.py`):

```python
class FlowSpec(BaseModel):
    ...
    env: str = Field(default="prod", description="Execution env: dev|staging|prod.")
    runtime_config: dict[str, Any] = Field(default_factory=dict,
        description="Top-level runtime hints (not inside cells).")
```

**Dev run semantics**: `materialize` tasks stamp the env into the DuckDB file path via `blend_database_path(flow_id, env="dev")` → `blends/<flow_id>__dev.duckdb`. Prod reads are unaffected — they still open `blends/<flow_id>__prod.duckdb`.

**Promotion (Virtual Update)**: `POST /flows/{id}/promote?from_env=dev&to_env=prod` does an atomic pointer swap — renames `...__dev.duckdb` to `...__prod.duckdb` (or updates the `database` field in the registered datastore row). Zero compute at promotion time. For BYO warehouse materializations (not DuckDB files), promotion is a `RENAME TABLE dev_schema.tbl TO prod_schema.tbl` against the warehouse connector. (See Open Question 4.)

**Plan endpoint** (SQLMesh `plan` equivalent): `POST /flows/{id}/plan` — new route in `routes/flows.py`:

```
Request:  { params?: dict, env?: "dev" }
Response: {
  valid: bool,
  env: str,
  tasks: [
    { key, kind, status: "no_change"|"schema_change"|"new",
      change_type?: "breaking"|"non_breaking",
      added_columns?: [str], removed_columns?: [str],
      downstream_impact?: [str],
      estimated_rows?: int }
  ],
  issues: [str]
}
```

Implementation: reuse `validate_flow_spec()` at `spec.py:248` for structure validation. For `materialize` tasks, parse `combine_sql` with sqlglot, diff output columns against the current blend schema, classify breaking (column removed/renamed) vs non-breaking (column added). Walk the lineage graph for downstream impact. No data is written during a plan call.

### 3.6 Result Caching

**Gap**: `ContentAddressedCache` (`connectors/cache.py`) is used only in `routes/query.py`. The flows `_handle_query` does not use it, so `TaskSpec.cache_ttl_s` has no effect in durable mode.

**Fix**: Wire `get_cache()` into `_handle_query`:

```python
from app.connectors.cache import get_cache
cache = get_cache()
if task_cache_ttl_s > 0:
    cached = cache.get(physical_plan.cache_key)
    if cached:
        # deserialize IPC bytes → return rows
        ...
    else:
        arrow_table = connector.execute(physical_plan)
        cache.put(physical_plan.cache_key, table_to_ipc_bytes(arrow_table))
```

Preview and durable cache keys never collide: preview calls `plan(sql, limit=500)` which embeds the LIMIT in the AST before the SHA-256 hash; durable calls `plan(sql, limit=None)` — different hash.

**Honest limits**: The cache is an in-memory LRU (256 entries, 5-min TTL, single-process). Multi-process deployments (load-balanced uvicorn) will miss across workers. A Redis backend is the upgrade path; the `get_cache()` interface at `cache.py:84` maps directly to Redis GET/SET.

---

## 4. Column-Level Lineage + BYO-Warehouse

### 4.1 Column Lineage — New Module

Create `backend/app/lineage/column.py` with one public function:

```python
def extract_column_lineage(
    sql: str,
    dialect: str = "postgres",
    sources: dict[str, str] | None = None,  # {cell_key: upstream_sql}
    schema: dict | None = None,
) -> list[dict]:
    """Column-level lineage edges.

    Each edge: {"output_col": str, "from_table": str|None, "from_col": str}
    Returns [] on parse failure (never raises).
    """
    # sqlglot.lineage.lineage(column=None, sql=...) returns dict[col, Node]
    # Walk each Node.downstream until leaf (no .downstream) to find physical source.
```

`sqlglot.lineage.lineage` is the public API from `sqlglot/lineage.py`. `sources={cell_key: sql}` enables cross-cell lineage: a downstream cell that writes `SELECT * FROM cell_revenue` with `sources={"cell_revenue": upstream_sql}` gets full column-level tracing through the upstream cell.

### 4.2 Extend extract.py

Add `column_edges` to the return dict of `extract_lineage()` at `backend/app/lineage/extract.py:327`:

```python
from app.lineage.column import extract_column_lineage
column_edges = extract_column_lineage(sql, dialect=dialect)
return {
    "tables": real_tables,
    "columns": columns,
    "outputs": outputs,
    "column_edges": column_edges,   # NEW
}
```

### 4.3 Extend graph.py — Cross-Query Column Flow

Add `column_flow: dict[str, list[str]]` to `LineageGraph` at `backend/app/lineage/graph.py`:

- Key: `"producing_query_id:output_col"`
- Value: `["consuming_query_id:input_col", ...]`

In `build_graph()`, after the existing table/column index build, add a second pass linking each query's `outputs` to downstream queries' `columns`. For notebook cells using `cell_key` as table names, the match is: `col_ref["table"] == cell_key` of an upstream cell. This completes the cross-cell column DAG.

### 4.4 New Lineage Routes in routes/lineage.py

Add two new endpoints to `backend/app/routes/lineage.py`:

**`GET /lineage/impact/{query_id}?column=X`**: return downstream queries/cells affected if the given output column changes. Classify each as `breaking` (column appears in WHERE/GROUP BY/JOIN of downstream) or `non_breaking` (SELECT-only). This is the SQLMesh plan-gate equivalent shown in the UI before durable materialize runs.

**`POST /lineage/cell`**: ephemeral column lineage for ad-hoc notebook cells (not persisted to registry). Accepts `{sql, dialect, cell_key, upstream_cells: {key: sql}}`. Calls `extract_column_lineage(sql, sources=upstream_cells)` and returns edges. Used by the notebook UI after each interactive cell run. Auth: `current_user` required.

**Extend `GET /lineage/query/{id}`** response at line 144 to include `column_edges` from the stored extraction.

### 4.5 BYO-Warehouse — The Full Picture

**No Nubi-owned storage.** The data flow:

```
User's BYO Warehouse (Snowflake/BigQuery/Postgres/DuckDB)
    │
    │  push-down SQL via connector (RLS baked in by planner)
    │  [routes/query.py → planner.py → connector.execute() → Arrow IPC]
    ▼
Nubi server memory (Arrow IPC)
    │
    │  only when materialize=true:
    │  DuckDB blend (materialize.py) — LOCAL CACHE, not source of truth
    │  blend file: blends/<flow_id>__<env>.duckdb
    ▼
Dashboard reads
    │  same query route, same RLS injection
    │  reads from blend DuckDB or directly from BYO warehouse
    └──────────────────────────────────────────────────────
```

**RLS preservation** through every path:

- Interactive: `planner.py:287–297` injects `WHERE col = claim` as AST node before `tree.sql(dialect)`. Never string-concatenated.
- Durable (`_handle_query`): `plan(sql, claims=claims, ...)` — RLS from flow-run auth context. Gap: must add claims through the new `_resolve_flow_connector` path (the existing route at `routes/query.py:678` already gates: if RLS policies present and connector declares `predicate_rls=False` → 501. This gate must be replicated in `_handle_query`).
- Blend serve-time: blend DuckDB is read through normal query route, RLS re-injected. `rls_keys` declared in materialize task config survive the blend merge (verified in `materialize.py:285–293`).

**Medallion on BYO warehouse** (Bronze/Silver/Gold without Nubi-owned lake):

| Tier | FlowSpec kind | Write target | Config |
|---|---|---|---|
| Bronze | `query` | BYO source (read-only) | `datastore_id=source_ds` |
| Silver | `query` or `materialize` | BYO transform target OR blend DuckDB | Author's choice |
| Gold | `materialize` | blend DuckDB (default) OR BYO target warehouse | `database=path` or future `write_to_warehouse` |

Portability: every tier references a `datastore_id`. To move the pipeline to a different warehouse, update the `datastore_id` registry entries. No pipeline code changes.

---

## 5. Open Questions — Human Decisions Required

These are genuine design forks where a wrong default is hard to undo. Decision needed before Phase 2 begins.

**OQ-1: execution_mode — per-cell or notebook-level?**
Current design: each `CellSpec` has `execution_mode`. In practice, notebooks are nearly always all-interactive or all-durable. Options:
- (A) Hoist to `NotebookSpec` with per-cell override as escape hatch. Simpler for users; "run this notebook in durable mode" is one toggle.
- (B) Keep per-cell. More granular; a notebook can mix preview cells (markdown, exploratory SQL) with durable cells (materialize).
*Recommendation: A (notebook-level default + per-cell override). Simpler UX; escape hatch preserves power.*

**OQ-2: Stable cell keys — UUID vs human-readable slug?**
`cell_<uuid_prefix>` is stable across reorders but ugly in SQL (`SELECT * FROM cell_7f3a2b`). Human slug (`cell_revenue`) is readable but must be unique and manually maintained.
*Recommendation: human slug, validated unique within the notebook on save, with a rename-and-update-references operation in the UI. DuckDB table names are case-insensitive so slug collisions across notebooks are not a risk (each notebook's interactive WASM session is isolated).*

**OQ-3: Durable Python cell → downstream SQL cell data bridge?**
When `_handle_python` runs in durable mode (local subprocess), it returns a JSON dict. If a downstream durable SQL cell does `SELECT * FROM cell_transform`, the DuckDB-WASM table named `cell_transform` does not exist (it was registered in the browser session, not the durable worker). Options:
- (A) Require Python cells whose output is consumed by downstream SQL cells to set `persist=true`, which triggers a `materialize`-style DuckDB write in the worker. The downstream SQL cell then reads from the blend DuckDB by `query_id`.
- (B) In `_handle_query` on the durable path, check `ctx.inputs[cell_key]` for a `rows` list and register it as a DuckDB in-memory table before running the SQL.
*Recommendation: B — automatic in-memory DuckDB registration from `ctx.inputs` in the durable `_handle_query`. Transparent to the author. No need for explicit `persist=true` on Python cells unless the result must outlive the flow run.*

**OQ-4: Promotion for BYO warehouse materializations (non-DuckDB)?**
The dev/prod Virtual Update for DuckDB blend files is a file rename. For materializations that write to user's Snowflake/BigQuery tables (future `write_to_warehouse` task), promotion would be `ALTER TABLE RENAME` against the warehouse. This requires connector support for DDL. Options:
- (A) Limit virtual environments to DuckDB blend files in v1; BYO warehouse tables are always written directly to prod.
- (B) Add a `promote_sql` to the `materialize` task config — author declares the DDL to run at promotion time. Flexible but requires author knowledge.
*Recommendation: A for v1. Document the limitation. BYO warehouse virtual envs are Phase 4.*

**OQ-5: The asyncio boundary in `_resolve_flow_connector`?**
`_handle_query` runs in a `ThreadPoolExecutor` thread (executor.py:300). The connector/datastore lookup (`repo.get()`) is async. Options:
- (A) `asyncio.run()` inside the thread — creates a new event loop per call. Works but 1–3ms overhead.
- (B) Add `repo.get_sync()` synchronous variant to the repo layer.
- (C) Make `_handle_query` async and await from `execute_task` (bigger refactor, cleanest long-term).
*Recommendation: B for v1. A synchronous `get_sync()` method wrapping `asyncio.get_event_loop().run_until_complete()` is a 5-line change and keeps the threading model stable.*

---

## 6. Phased Implementation Task Graph

Phases are sequenced so each phase builds on verified foundations. Agents within a phase are independent and can run in parallel. Dependencies are noted.

### Phase 1 — Foundation (no UI, pure backend plumbing)
*Can ship as a backend-only change. Unblocks all other phases.*

| Task | File(s) | Change | Owner |
|---|---|---|---|
| **P1-A**: Add `CONNECTOR_DIALECT` map | `backend/app/connectors/registry.py` | Add `CONNECTOR_DIALECT: dict[str,str]` (8 connectors) | Backend-Connectors |
| **P1-B**: Thread target dialect into query route | `backend/app/routes/query.py:446` | Pass `dialect=CONNECTOR_DIALECT.get(ctype)` + `limit=body.preview_limit` to `planner_plan()`. Add `preview_limit: int | None` to `QueryIn`. | Backend-Query |
| **P1-C**: Fix `_handle_query` — resolve datastore + dialect | `backend/app/flows/registry.py:158–230` | Add `_resolve_flow_connector()` helper; replace hardcoded `DuckDBConnector()` with connector resolution from `config.datastore_id`; add source_dialect transpile step | Backend-Flows |
| **P1-D**: Add `org_id` to `TaskContext` | `backend/app/flows/executor.py:65`, `runtime.py:761` | Add `org_id: str | None` field; populate from flow run at `run_one_ready_task` | Backend-Flows |
| **P1-E**: Wire cache into `_handle_query` | `backend/app/flows/registry.py` | Use `get_cache()` singleton; respect `cache_ttl_s` on `TaskSpec` | Backend-Flows |

P1-A must complete before P1-B and P1-C. P1-D must complete before P1-C (needed by `_resolve_flow_connector`).

### Phase 2 — Data Model (spec + notebook module)
*Depends on: P1 complete. Unlocks Phase 3 and Phase 4.*

| Task | File(s) | Change | Owner |
|---|---|---|---|
| **P2-A**: Add `env` + `runtime_config` to `FlowSpec` | `backend/app/flows/spec.py:231` | Two new Pydantic fields; `validate_flow_spec` accepts them silently | Backend-Spec |
| **P2-B**: Create `NotebookSpec`, `CellSpec`, `NotebookRuntimeConfig` | `backend/app/flows/notebook.py` (new ~100 lines) | `notebook_to_flow()`, `flow_to_notebook()`, `infer_notebook_edges()` | Backend-Spec |
| **P2-C**: Add `use_remote_kernel` branch to `_handle_python` | `backend/app/flows/registry.py:251` | Call `_choose_runner()` when `config.use_remote_kernel=true` | Backend-Flows |
| **P2-D**: Add `freshness_sla_s` to `TaskSpec` + staleness check | `backend/app/flows/spec.py:196`, `runtime.py:1412` | New field + `flow_tick` emits stale event when `now - last_success_at > freshness_sla_s` | Backend-Spec |
| **P2-E**: Stamp `env` into `blend_database_path` | `backend/app/flows/materialize.py:75` | `blend_database_path(flow_id, env="prod")` signature change | Backend-Flows |

P2-A before P2-B. P2-B before Phase 3 routes. P2-E before Phase 3 virtual envs.

### Phase 3 — Routes and Lineage
*Depends on: P1 + P2 complete. Can be parallelized internally.*

| Task | File(s) | Change | Owner |
|---|---|---|---|
| **P3-A**: `POST /flows/{id}/plan` + `POST /flows/{id}/promote` | `backend/app/routes/flows.py` | Two new routes; plan uses `validate_flow_spec` + sqlglot column diff; promote does file rename / datastore row update | Backend-Routes |
| **P3-B**: Column lineage module | `backend/app/lineage/column.py` (new ~60 lines) | `extract_column_lineage(sql, dialect, sources, schema)` using `sqlglot.lineage.lineage` | Backend-Lineage |
| **P3-C**: Extend `extract_lineage` + `build_graph` | `backend/app/lineage/extract.py:327`, `graph.py:81` | Add `column_edges` key; add `column_flow` index to `LineageGraph` | Backend-Lineage |
| **P3-D**: New lineage routes | `backend/app/routes/lineage.py` | `GET /impact/{id}`, `POST /cell`; extend `GET /query/{id}` response | Backend-Routes |
| **P3-E**: `@outputs()` decorator for Python cells | `backend/nubi/flows/_nodes.py`, `flows/codegen.py:488` | Add `def outputs(*cols)` decorator; emit in codegen | Backend-SDK |

P3-B before P3-C. P3-C before P3-A (plan uses lineage for downstream impact). P3-D depends on P3-B and P3-C.

### Phase 4 — Notebook UI (frontend)
*Depends on: P1 + P2 + P3-A routes available. Largest frontend effort.*

| Task | File(s) | Change | Owner |
|---|---|---|---|
| **P4-A**: Stable cell keys in `QueryWorkspace` | `src/pages/app/QueryWorkspace.jsx` | Replace index-based `cell_${index+2}` with `cell.key` (stable UUID slug); display ordinal labels separately | Frontend |
| **P4-B**: Persist scratch cells as `NotebookSpec` | `src/pages/app/QueryWorkspace.jsx` | Serialize `scratchCells` to `NotebookSpec.tasks` on Save; reload on mount; call `POST /flows` to persist | Frontend |
| **P4-C**: Notebook ↔ DAG view toggle | `src/pages/app/QueryWorkspace.jsx` or new `src/pages/app/NotebookPage.jsx` | Toggle button switching `view="notebook"` (linear) ↔ `view="dag"` (React Flow canvas from `src/flows/FlowBuilder.jsx`) | Frontend |
| **P4-D**: Parameter cell UI | `src/pages/app/QueryWorkspace.jsx` | Render `NotebookSpec.params` as a pinned, non-reorderable parameter cell at the top of the notebook; highlighted differently | Frontend |
| **P4-E**: Plan gate UI | `src/pages/app/QueryWorkspace.jsx` or modal | Before "Run All (durable)": call `POST /flows/{id}/plan`, show impact panel (breaking/non-breaking badges, downstream query list). Require confirmation before proceeding. | Frontend |
| **P4-F**: Column lineage panel | `src/pages/app/QueryWorkspace.jsx` | After each cell run: call `POST /lineage/cell`, render a collapsed "Lineage" section showing output → source column edges | Frontend |

P4-A before P4-B. P4-B before P4-C. P4-D can run in parallel with P4-C. P4-E depends on P3-A. P4-F depends on P3-D.

### Phase 5 — Advanced (post-launch, lower urgency)

| Task | Notes |
|---|---|
| Incremental materialize (`config.incremental=true`) | Extend `flows/store.py` to record last-run interval per task; inject `WHERE ts >= @last_run` into `combine_sql` |
| Pyodide in-browser Python | Adds ~10MB WASM bundle; deferred per OQ in prior research |
| Cross-flow notebook chaining (`notebook` task kind) | References another `FlowSpec` by ID; executor resolves recursively |
| BYO warehouse promotion (virtual envs for non-DuckDB materializations) | Requires connector DDL support (`RENAME TABLE`) |
| Redis-backed cache | Upgrade path for multi-process deployments |
| Column masking (`column_masking=true` on connector capability) | Replaces column values with NULL for unauthorized callers |

---

## 7. File-to-Integration-Point Index

Quick-reference for implementers. Each row is a concrete change with the exact file and line number.

| # | File | Line | Change type | Description |
|---|---|---|---|---|
| 1 | `backend/app/connectors/registry.py` | after line 210 | ADD | `CONNECTOR_DIALECT: dict[str, str]` map |
| 2 | `backend/app/routes/query.py` | ~227 | ADD field | `preview_limit: int \| None = None` to `QueryIn` |
| 3 | `backend/app/routes/query.py` | ~446 | WIRE | `dialect=CONNECTOR_DIALECT.get(ctype)` + `limit=body.preview_limit` to `planner_plan()` |
| 4 | `backend/app/flows/executor.py` | 65 | ADD field | `org_id: str \| None = None` to `TaskContext` dataclass |
| 5 | `backend/app/flows/runtime.py` | 761 | WIRE | Populate `ctx.org_id` from flow run at `run_one_ready_task` |
| 6 | `backend/app/flows/registry.py` | 158–230 | MODIFY | `_handle_query`: resolve `datastore_id`, source_dialect transpile, pass `dialect` to `plan()` |
| 7 | `backend/app/flows/registry.py` | new func | ADD | `_resolve_flow_connector(datastore_id, org_id) -> (connector, dialect)` |
| 8 | `backend/app/flows/registry.py` | ~227 | ADD | Wire `get_cache()` — respect `cache_ttl_s` in durable path |
| 9 | `backend/app/flows/registry.py` | 251 | MODIFY | `_handle_python`: add `use_remote_kernel` branch calling `_choose_runner()` |
| 10 | `backend/app/flows/spec.py` | ~231 | ADD fields | `env: str = "prod"` and `runtime_config: dict = {}` to `FlowSpec` |
| 11 | `backend/app/flows/spec.py` | ~196 | ADD field | `freshness_sla_s: int = 0` to `TaskSpec` |
| 12 | `backend/app/flows/notebook.py` | new file ~100 lines | CREATE | `NotebookSpec`, `CellSpec`, `NotebookRuntimeConfig`, `infer_notebook_edges()`, `notebook_to_flow()`, `flow_to_notebook()` |
| 13 | `backend/app/flows/materialize.py` | 75 | MODIFY | `blend_database_path(flow_id, env="prod")` — stamp env in filename |
| 14 | `backend/app/routes/flows.py` | new routes | ADD | `POST /flows/{id}/plan` + `POST /flows/{id}/promote` |
| 15 | `backend/app/lineage/column.py` | new file ~60 lines | CREATE | `extract_column_lineage(sql, dialect, sources, schema)` via `sqlglot.lineage.lineage` |
| 16 | `backend/app/lineage/extract.py` | 327 | MODIFY | Add `column_edges` key from `extract_column_lineage()` |
| 17 | `backend/app/lineage/graph.py` | 81 | MODIFY | Add `column_flow` field to `LineageGraph`; second pass in `build_graph()` |
| 18 | `backend/app/routes/lineage.py` | 144, new routes | MODIFY + ADD | Extend `GET /query/{id}` response; add `GET /impact/{id}` and `POST /cell` |
| 19 | `backend/nubi/flows/_nodes.py` | end | ADD | `def outputs(*cols)` decorator for Python cell column declarations |
| 20 | `backend/app/flows/codegen.py` | ~488 | MODIFY | Emit `@outputs(...)` decorator in SDK codegen |
| 21 | `src/pages/app/QueryWorkspace.jsx` | ~1186 | MODIFY | Stable cell keys (UUID slugs) + ordinal display labels |
| 22 | `src/pages/app/QueryWorkspace.jsx` | ~1149 | MODIFY | Persist `scratchCells` as `NotebookSpec.tasks` on Save |
| 23 | `src/pages/app/QueryWorkspace.jsx` or new page | new | ADD | Notebook ↔ DAG view toggle |
| 24 | `src/pages/app/QueryWorkspace.jsx` | top of cell list | ADD | Pinned parameter cell UI from `NotebookSpec.params` |
| 25 | `src/pages/app/QueryWorkspace.jsx` | "Run All (durable)" | ADD | Plan gate modal: `POST /flows/{id}/plan` → impact panel → confirmation |
| 26 | `src/pages/app/QueryWorkspace.jsx` | after cell run | ADD | Column lineage panel: `POST /lineage/cell` → collapsed lineage section |

Files that need **zero changes**:
- `backend/app/flows/executor.py` — template resolution already handles `{{ inputs.k.f }}` and `{{ params.x }}`
- `backend/app/flows/runtime.py` — `drain_flow_run` and `run_worker_pool` run `FlowSpec` tasks unchanged
- `backend/nubi/flows/_builder.py` — `@flow`, `@task`, `FlowParam` SDK primitives unchanged
- `src/lib/wasmRuntime.js` — `registerArrowTable`, `runLocalSqlForCell`, `runPythonCell` already wired
- `backend/app/connectors/planner.py` — `plan(sql, dialect, limit)` signature already supports all needs
- `backend/app/lineage/extract.py` — `extract_lineage()` used as-is by `infer_notebook_edges()`
