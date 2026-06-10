# Flows v1 Redesign — Implementation Design

Status: approved for implementation (v1, pinned scope).
Audience: one backend engineer + one frontend engineer, one pass.
Constraint: additive only. Existing saved specs load + run, all existing backend
tests stay green, no public route shape changes.

This doc is the concrete plan for four pinned changes:

1. Canonical FlowSpec `tasks[]` model + lossless canvas / notebook / code projections.
2. SQLMesh-style inferred dependencies for `query` tasks (sqlglot backend, regex frontend).
3. DataFrame-native Python task contract (`dataframes` input + returned DataFrame serialisation).
4. Template additions + primary/secondary task-kind grouping in the builder UI.

Out of scope for v1 (must stay forward-compatible, do NOT build now): persistent
materialization to external warehouses, dev/prod environments, column-level lineage UI.

---

## (a) Canonical model + projection rules

### Canonical model

The single source of truth is `FlowSpec` in `backend/app/flows/spec.py`. It is an
ordered list `tasks: list[TaskSpec]`. Each `TaskSpec` has:

- `key` (unique slug), `kind`, `needs: list[str]` (explicit deps),
- `config` (kind-specific dict), `retries`, `retry_backoff_s`, `timeout_s`,
  `cache_ttl_s`, `ui.{x,y}` (canvas position),
- optional cell extensions: `cell_type` (`sql`|`python`|`markdown`), `execution_mode`,
  `freshness_sla_s`.

No new top-level FlowSpec fields are required for v1. `ui`, `cell_type`, and `config`
already carry everything the three projections need. The three editors (Canvas,
Notebook, Code) are **views** over the same `FlowSpec`; switching views must be lossless.

### Projection rules

| View | Holds working state as | Edits | Lossless because |
|------|------------------------|-------|------------------|
| Canvas | React Flow `nodes`+`edges` (`specGraph.js`) | node config via `NodeInspector`, edges = `needs` | `graphToSpec` writes `key/kind/needs/config/retries/.../ui` back; `config` (incl. `body`, `conditions`) passes through verbatim |
| Notebook | `spec.tasks` directly (`NotebookView.jsx`) | cell code + order | edits `spec.tasks` in place; no transform |
| Code | Python SDK source (`CodePanel.jsx`) | round-trips through backend `POST /flows/compile` → `flow_spec_to_sdk`/`compile` | scaffold-grade: preserves `tasks/kinds/configs/needs/params`; `ui.{x,y}` is reset to 0 and re-derived by auto-layout |

The canvas↔notebook hand-off already works in `FlowBuilder.handleViewChange`
(leaving canvas → `buildSpec()` flush; entering canvas → `specToGraph(spec)` rebuild).
**Keep it.** This redesign only touches the inferred-edge concern (b).

#### Code round-trip caveat (must hold)

`flow_spec_to_sdk` (codegen) deliberately drops `ui.x/ui.y` (documented "canvas
concern, not a code concern"). That is acceptable and already covered by
`test_flows_codegen.py` / `test_sdk_compile.py`, which assert `tasks/kinds/configs/needs/params`
round-trip but NOT `ui`. **Do not** start emitting `ui` in codegen — it would break the
scaffold-grade contract those tests encode.

The one consistency item: codegen must emit only the **explicit** `needs` of a task,
never inferred edges (see (b)). Since inferred edges are computed at run-time / render-time
and never written into `task.needs`, codegen is already correct — it serialises
`task.config['sql']` verbatim and `task.needs` verbatim, so a recompiled spec re-infers the
same edges. Add one regression test to lock this (see (f)).

---

## (b) Inferred dependencies for SQL tasks (SQLMesh-style)

### Principle

For a `query` task with raw `config['sql']`, dependencies are inferred by parsing the
SQL and matching referenced table identifiers to **sibling task keys** in the same flow.

```
effective_needs(task, all_task_keys) =
    union( explicit task.needs ,
           { ref for ref in referenced_tables(task) if ref in all_task_keys and ref != task.key } )
```

- Explicit `needs` always remain in effect (override/extra) — required for ordering
  non-SQL tasks and for `query_id`-based queries that have no parseable SQL.
- Inferred refs are NEVER persisted into `task.needs`. They are computed on demand so
  the canonical spec stays minimal and codegen stays stable.
- A `query` task that uses `query_id` (not `sql`) contributes no inferred refs.
- Only sibling-key matches count; references to real warehouse tables (e.g. `demo`,
  `sales`) that are not task keys are ignored.

### Backend (authoritative — sqlglot)

New pure helper module `backend/app/flows/deps.py`:

```python
def referenced_table_names(sql: str, dialect: str | None = None) -> set[str]:
    """Parse SQL with sqlglot; return the set of base table identifiers referenced
    (FROM / JOIN / CTE bodies). Returns {} on parse failure (best-effort, never raises).
    Excludes CTE-defined names (a WITH name is a local alias, not a sibling dep)."""

def effective_needs(task: dict, all_task_keys: set[str]) -> list[str]:
    """union(explicit needs, inferred sibling refs). Deterministic order:
    explicit needs first (original order), then inferred extras sorted."""
```

Implementation notes:
- Parse via `sqlglot.parse_one(sql, read=dialect)` inside try/except; on any exception
  return an empty inferred set (degrade to explicit-needs-only — matches the codebase's
  best-effort transpile pattern in `registry._handle_query`).
- Collect identifiers from `exp.Table` nodes via `parsed.find_all(sqlglot.exp.Table)`,
  taking `.name` (base name, unqualified). Subtract CTE names found via
  `parsed.find_all(sqlglot.exp.CTE)` `.alias`.
- `dialect` defaults to `config.get('source_dialect')` when the caller has it, else None
  (sqlglot's default parser is permissive enough for identifier extraction).

### Wiring effective deps into ordering

The DAG/run-order is driven by `task_run.depends_on`, set in
`runtime.materialize_flow_run` from `task.needs`, and by `runtime.preview_cell`'s
`_upstream` walk over `task.get('needs')`. Both must use effective deps.

- `materialize_flow_run`: compute `all_keys = {t.key for t in flow_spec.tasks}` once, then
  set `depends_on = effective_needs(task_dict, all_keys)` instead of `list(task.needs)`.
  Keep `is_root = len(depends_on) == 0` derived from the **effective** list so an
  inferred-only dependent is correctly non-root.
- `preview_cell`: in the `_upstream` recursion, replace `task.get("needs") or []` with the
  effective deps (compute `all_keys` from the `tasks` arg once, call
  `effective_needs(task, all_keys)`). This makes "run up to here" pull the SQL-referenced
  upstream Python/SQL cells even when the user never drew an edge.

Validation (`spec.py`) is unchanged: it still validates explicit `needs` references and
acyclicity over explicit edges only. Inferred edges cannot reference undeclared keys (they
are filtered to `all_task_keys`), and a self-reference is excluded, so they cannot
introduce a validation regression. (A future enhancement could run the cycle check over
effective deps; out of scope for v1 to avoid touching the validation contract that many
tests assert against.)

### Frontend (render-only — regex)

`src/flows/specGraph.js` `specToGraph`: after building explicit `needs` edges, add
auto-derived edges for `query` tasks.

```js
// naive sibling-ref scan: FROM/JOIN <identifier>
function inferredRefs(sql, siblingKeys) {
  if (!sql) return []
  const refs = new Set()
  const re = /\b(?:from|join)\s+["'`]?([A-Za-z_][\w]*)["'`]?/gi
  let m
  while ((m = re.exec(sql))) {
    const id = m[1]
    if (siblingKeys.has(id)) refs.add(id)
  }
  return [...refs]
}
```

- In `specToGraph`, build `siblingKeys = new Set(tasks.map(t => t.key))`. For each `query`
  task, for each inferred ref not already an explicit `need` and not already an edge,
  push an edge `{ id: `${ref}=>${task.key}`, source: ref, target: task.key, ...,
  data: { inferred: true }, style: { strokeDasharray: '4 3', ... } }` (dashed to signal
  auto-derived).
- These edges must NOT be written back into `needs`. In `graphToSpec`, and in
  `FlowBuilder.onEdgesChangeWrapped` / `onConnect`, the needs-rebuild loops must skip edges
  with `e.data?.inferred` — exactly mirroring the existing `'branchCondIndex' in e.data`
  skip. This is the "auto-derived edges must not be lost when round-tripping" guarantee:
  they are re-derived from `config.sql` on every `specToGraph`, and excluded from `needs`
  on every `graphToSpec`.

`src/flows/specGraph.test.mjs`: add cases asserting (1) a `query` task whose `sql` is
`SELECT * FROM other_cell` gets an inferred dashed edge from `other_cell`, (2) that edge is
absent from the round-tripped `graphToSpec` `needs`, (3) a non-sibling table (`demo`) yields
no edge.

---

## (c) DataFrame-native Python contract

Target: `backend/app/flows/registry.py` `_handle_python` (the subprocess wrapper).

### Inputs exposed to user code (in the subprocess)

| Local | Type | Source | Backcompat |
|-------|------|--------|------------|
| `inputs` | dict | `json.dumps(ctx.inputs)` (existing) | UNCHANGED — keep exactly |
| `params` | dict | `json.dumps(ctx.flow_params)` (existing) | UNCHANGED |
| `dataframes` | `dict[str, pandas.DataFrame]` | NEW — for each upstream key whose result has `rows`+`columns`, build `pd.DataFrame` | additive |

`dataframes` construction (inside the wrapper script, lazy/guarded):

```python
import pandas as _pd
dataframes = {}
for _k, _v in inputs.items():
    if isinstance(_v, dict) and "rows" in _v and "columns" in _v:
        try:
            dataframes[_k] = _pd.DataFrame(_v["rows"], columns=_v["columns"])
        except Exception:
            pass
```

Each upstream `rows` value may be a list of dicts (DuckDB `to_pylist`) or a list of
lists; `pd.DataFrame(rows, columns=columns)` handles dict-rows by ignoring `columns` when
rows are dicts — acceptable. If pandas import fails, `dataframes = {}` and a one-line
warning is printed (still captured as a log line); `inputs` continues to work, so no
existing test regresses.

### Returned value serialisation

The wrapper already supports `result` as dict / scalar / unset. Add ONE branch: if
`result` is a `pandas.DataFrame`, auto-serialise to the canonical row-result shape so it
flows into the Python→SQL bridge and downstream `dataframes` identically to a `query`
result:

```python
elif _pd is not None and isinstance(_result_val, _pd.DataFrame):
    _df = _result_val
    _out = {
        "columns": [str(c) for c in _df.columns],
        "rows": _df.to_dict(orient="records"),
        "row_count": int(len(_df)),
    }
```

- Place this branch BEFORE the existing `isinstance(_result_val, dict)` branch is reached
  for DataFrames (DataFrame is not a dict, so ordering is naturally safe; just add it as a
  new `elif` in the `_result_val` type ladder).
- `rows` uses `orient="records"` (list-of-dicts) to match DuckDB `to_pylist()` so the
  executor's `_collect_bridge_tables` (`pa.Table.from_pylist(rows)`) and the new
  `dataframes` builder both consume it unchanged.
- Existing dict/scalar/None behaviour is untouched — only the new DataFrame type is
  intercepted.

### Dependency

pandas is currently a transitive dependency only. Pin it explicitly in
`/Users/pc/code/exo/nubi/requirements.txt` under the connector/query pipeline block:
`pandas>=2.0`. (Locally installed: pandas 2.3.3, sqlglot 30.8.0 — both already importable,
so no test infra change is needed.)

---

## (d) Template additions + UI task-kind grouping

### `src/flows/pythonExamples.js`

Keep the three existing entries (Extract archive, HTTP fetch JSON, Transform rows). Add:

1. **"Transform with pandas (DataFrame)"** — uses `dataframes[...]` and returns a DataFrame:
   ```python
   # Operate on an upstream cell's rows as a pandas DataFrame and return a DataFrame.
   # `dataframes` maps each upstream key with {rows, columns} to a pandas.DataFrame.
   df = dataframes.get("query_task")
   if df is None:
       raise ValueError("Upstream 'query_task' produced no rows/columns")
   df = df[df["value"] > 0].copy()
   df["value_x2"] = df["value"] * 2
   result = df   # auto-serialised to {rows, columns, row_count}
   ```
2. **"Call agent (template)"** — template showing how to invoke the agent kind from a
   Python cell narratively / or a stub that posts to the agent (kept dependency-free; it
   documents the `agent` task as the real path and provides a callable placeholder):
   ```python
   # Prefer a dedicated `agent` task for LLM steps. This Python template shows how to
   # shape a prompt from upstream rows and hand off to an agent task downstream.
   df = dataframes.get("query_task")
   summary = "" if df is None else df.head(20).to_csv(index=False)
   result = {"prompt": f"Summarise these rows:\n{summary}"}
   ```

### Builder UI grouping (primary vs advanced)

`src/flows/AddTaskPanel.jsx`: split `PALETTE_ITEMS` into two groups WITHOUT deleting any
kind:

- **Primary:** `query` (SQL), `python`.
- **Advanced / template-driven (collapsed section "Advanced"):** `agent`, `bucket_load`,
  `map`, `branch`, `materialize`, `preagg_refresh`, `noop`.

Render Primary as the top two large buttons; Advanced under a collapsible
`<details>`/disclosure. All `defaultConfig` values stay as-is. Backend handlers for every
advanced kind remain registered (`registry._bootstrap` unchanged). The `agent` and
pandas templates are surfaced in `NodeInspector`'s Python snippet picker via the new
`pythonExamples.js` entries (no code change needed there beyond the data).

`NodeInspector.jsx` `kind` select keeps all kinds so existing specs with advanced kinds
remain fully editable.

---

## (e) Files to change (one line each)

Backend:
- `backend/app/flows/deps.py` — NEW: `referenced_table_names()` + `effective_needs()` (sqlglot, best-effort).
- `backend/app/flows/runtime.py` — `materialize_flow_run`: set `depends_on`/`is_root` from `effective_needs`; `preview_cell._upstream`: walk effective deps.
- `backend/app/flows/registry.py` — `_handle_python`: inject `dataframes` local; intercept returned `pandas.DataFrame` → `{rows, columns, row_count}`.
- `requirements.txt` — add explicit `pandas>=2.0` pin.

Frontend:
- `src/flows/specGraph.js` — `specToGraph`: add `inferredRefs()` + dashed inferred edges (`data.inferred`); ensure `graphToSpec` already ignores them.
- `src/flows/FlowBuilder.jsx` — `onConnect` + `onEdgesChangeWrapped` needs-rebuild loops: skip edges with `e.data?.inferred` (mirror branch-edge skip).
- `src/flows/pythonExamples.js` — add "Transform with pandas (DataFrame)" + "Call agent (template)" entries.
- `src/flows/AddTaskPanel.jsx` — group palette into Primary (query, python) + collapsible Advanced (rest); no kind removed.

Tests (additive):
- `src/flows/specGraph.test.mjs` — inferred-edge presence, round-trip exclusion from `needs`, non-sibling ignored.
- `backend/tests/test_flow_deps.py` — NEW: unit tests for `referenced_table_names`/`effective_needs` (sibling match, CTE exclusion, parse-failure → explicit only, query_id contributes nothing).
- `backend/tests/test_flows_engine.py` (or new `test_flow_inferred_deps.py`) — a flow with two `query` cells where the second `SELECT * FROM first` (no explicit `needs`) runs in correct order.
- `backend/tests/` python-handler test — a python cell returning a `pandas.DataFrame` yields `{rows, columns, row_count}` and a downstream SQL cell reads it via the bridge; and `dataframes[...]` is populated.

---

## (f) Backward compatibility + test notes

- **Saved specs load + run unchanged.** No FlowSpec field added/removed; `validate_flow_spec`
  untouched. Effective deps are a superset of explicit deps, so any spec that ran before
  still runs (same or stricter ordering). A spec that relied purely on explicit `needs`
  (incl. non-SQL kinds) is unaffected because inferred refs only apply to `query` tasks
  with parseable `sql` matching sibling keys.
- **No route shape changes.** `/flows/*`, `/flows/codegen`, `/flows/compile`, `/flows/preview`,
  `/flows/{id}/run` keep their request/response shapes.
- **Codegen stays scaffold-grade.** Inferred edges are never written to `task.needs`, so
  `flow_spec_to_sdk` keeps emitting explicit-only `needs`; `test_flows_codegen.py` and
  `test_sdk_compile.py` stay green. Add one regression test: a 2-cell SQL flow with an
  inferred-only dependency compiles, and the recompiled spec re-infers the same edge in
  `specToGraph` (frontend) — the backend codegen `needs` list stays empty for the dependent.
- **Python handler backcompat.** `inputs`/`params` locals and dict/scalar/`None` `result`
  paths are byte-for-byte preserved; `dataframes` and DataFrame-return are pure additions
  guarded by `try/except` and `isinstance`. `test_flow_robustness.py` / engine tests that
  return dicts/scalars are unaffected.
- **sqlglot/pandas already importable** in the test env (verified: sqlglot 30.8.0, pandas
  2.3.3). `deps.py` and the `dataframes` builder both degrade to no-op on import/parse
  failure, so environments without pandas (if any) keep the existing `inputs`-only behaviour
  and do not fail.
- **Run the full backend suite** (`backend/tests/test_flow*.py` + `test_flows*.py`) plus the
  frontend `specGraph.test.mjs` after the change; all must pass with the four new tests added.

### Forward-compat with out-of-scope items
- Persistent materialization / dev-prod envs / column-level lineage are untouched. The
  `materialize` handler, `env` field, and `lineage.py` remain as-is. `deps.py`'s
  `referenced_table_names` is the natural seam a future column-lineage feature can build on,
  but v1 only uses the table-level set.
