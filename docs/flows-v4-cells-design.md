# Flows v4 — "Cells, not kinds" Implementation Design

Status: approved for implementation (v1, pinned scope).
Audience: one backend engineer + one frontend engineer, implementing in parallel.
Constraint: ADDITIVE + BACKWARD-COMPATIBLE only. Every existing saved spec must still
validate and run; every existing backend test and frontend test must stay green; no DB
schema change (config lives in the `tasks[]` jsonb); no public route shape change.

---

## 0. The mental model

Three user-facing cell types, full stop:

| Cell type (`cell_type`) | Backend `kind` | What it is |
|--------------------------|----------------|------------|
| `sql`                    | `query`        | a SELECT |
| `python`                 | `python`       | a Python snippet |
| `markdown` (Note)        | `noop`         | prose / a divider — never executes data |

Everything "advanced" is a **config block on a SQL or Python cell**, not a separate kind:

- **`materialized`** — persist a SQL cell's result (view / full / incremental). Replaces the
  standalone `materialize` kind *for authoring*.
- **`for_each`** — run a SQL or Python cell body once per item. Replaces the `map` kind *for
  authoring*.
- **`run_when`** — a safe boolean over inputs/params; false ⇒ the cell is `skipped`. Replaces
  the `branch` kind *for authoring* (the "decision" is just a Python cell whose output a
  downstream cell references in its `run_when`).

The old kinds (`map`, `branch`, `materialize`, `agent`, `bucket_load`, `preagg_refresh`,
`map_collect`) **stay registered in the backend** so old specs keep running. They are merely
removed from the authoring palette and replaced by templates.

---

## 1. CELL CONFIG CONTRACT (PINNED — exact field names/shapes)

All three blocks live inside the existing `TaskSpec.config` dict (jsonb). They are optional;
absent ⇒ today's behaviour. Pin these shapes EXACTLY — backend and frontend both bind to them.

### 1.1 `config.materialized` — on a `query` (SQL) cell

```jsonc
config.materialized = {
  "kind": "view" | "full" | "incremental",  // default "view"; "view" ⇒ no persistence
  "target": "orders/daily",                  // required when kind != "view"; logical path, NO env prefix
  "time_column": "updated_at",               // required when kind == "incremental"
  "unique_key": ["id", "region"],            // optional; present ⇒ upsert/merge, absent ⇒ append
  "lookback": "3 days",                       // optional; reprocess window below the watermark
  "base_uri": "s3://bucket/mart"             // optional per-cell base-uri override
}
```

This is the **same `MaterializedConfig` shape already pinned in `spec.py`** and already edited
by `MaterializedSection` in `NodeInspector.jsx`. v4 only moves WHERE it is authored (onto SQL
cells) and adds a query-cell EXECUTION path that consumes it. The Pydantic model is unchanged.

`view` is the default and means exactly today's query-cell behaviour (no persistence). Only
`full`/`incremental` trigger the new persist step.

### 1.2 `config.for_each` — on a `query` OR `python` cell

```jsonc
config.for_each = {
  "items": "{{ inputs.get_regions.rows }}",  // template expr OR upstream ref; must resolve to a list
  "var": "item",                              // default "item"; the bound variable name
  "max_concurrency": 0                        // optional; 0 = unlimited (reserved, not enforced at task_run level in v1)
}
```

Field-name mapping to the existing `map` handler/runtime (the reused logic):

| `for_each` field | maps to legacy map field | notes |
|------------------|--------------------------|-------|
| `items`          | `item_expr`              | same template-expr semantics (`_resolve_native`) |
| `var`            | `item_var`               | default `"item"` |
| `max_concurrency`| `max_concurrency`        | passthrough |
| —                | `max_map_size`            | defaulted to 1000 internally (not surfaced in UI) |
| (implicit body)  | `body`                    | synthesised: the cell ITSELF is the single body task |
| (implicit)       | `collect_key`             | the synthetic body task's key (`__self__`) |

A `for_each` cell has NO separate `body` array. The cell's own `config` (`sql`/`code`) IS the
per-item body. The executor synthesises a one-task body at run time (§2.2).

### 1.3 `config.run_when` — on ANY cell (sql / python / markdown)

```jsonc
config.run_when = "inputs.classify.label == 'high_value'"
```

- A **string** holding a safe boolean expression. Empty / absent ⇒ always runs (today's
  behaviour). Stored as a plain string (NOT a template with `{{ }}` — see §3 for why).
- Namespaces available: `inputs`, `params`, `secrets` (same trust surface as `branch`).
- Evaluates false ⇒ the cell's task_run transitions to state `skipped` (a known terminal,
  non-blocking state already in `runtime._TERMINAL_STATES`).

Both `{{ inputs.x.label }} == 'high'` (template form) and bare `inputs.x.label == 'high'`
(namespace form) are accepted by the evaluator; the namespace form is canonical.

### 1.4 Precedence when blocks combine on one cell

Order of application at run time (PINNED):

1. **`run_when`** evaluated FIRST. False ⇒ `skipped`, nothing else runs. (Cheapest, gates all.)
2. **`for_each`** next. If present, fan out; the per-item body cell still carries `materialized`
   (each item persists — append/upsert semantics make this safe for incremental).
3. **`materialized`** applied to the (per-item) SQL result after its SELECT runs.

`run_when` + `for_each`: the gate is evaluated on the PARENT cell before fan-out, so a skipped
`for_each` never expands children.

---

## 2. EXECUTOR / RUNTIME SEMANTICS (detect → apply, reusing existing code)

The detection points are deliberately few and all in code the backend engineer owns. We DO NOT
change handler signatures, the registry, or task-state machine names.

### 2.1 `run_when` — gate in the executor (reuses branch's safe eval)

Detection: `execute_task` (`backend/app/flows/executor.py`), at the very top, after config is
resolved but BEFORE handler dispatch.

```python
run_when = (task.get("config") or {}).get("run_when")
if run_when:  # non-empty string
    from app.flows.run_when import evaluate_run_when  # safe evaluator (§3)
    if not evaluate_run_when(run_when, ctx):
        return {"state": "skipped", "result": None, "error": None, "logs": []}
```

- `"skipped"` is a NEW outcome state from `execute_task`. The runtime already treats `skipped`
  as terminal + non-blocking + non-flow-failing (`_TERMINAL_STATES`, NOT in `_FLOW_FAIL_STATES`,
  IS in `_BLOCKING_STATES`). **Decision (PINNED):** downstream cells that `need` a skipped cell
  go `upstream_failed` (same as today's skipped semantics). This matches the branch model: the
  "not taken" arm is dead. If a downstream cell should run regardless, the author puts the SAME
  `run_when` on it (cells gate independently), OR does not depend on the skipped cell.
- `run_one_ready_task` / `_execute_claimed_task_run`: when `outcome["state"] == "skipped"`,
  write the task_run with `{"state": "skipped", "finished_at": now}` and call
  `advance_readiness` — NO retry, NO watermark. Add this branch alongside the existing
  `timed_out` branch (timeouts already short-circuit retries; `skipped` does the same).
- `preview_cell`: a skipped target returns `{"state": "skipped", "result": None, ...}`; a
  skipped UPSTREAM cell contributes no `inputs[key]` entry (it simply isn't added), so
  downstream preview sees it as absent — acceptable for interactive preview.

Resolving templates: `run_when` MUST NOT go through `_resolve_config` string substitution
(that would coerce values to strings and break `==` typing). It is read from the RAW config and
handed to the evaluator, which resolves namespaces itself (mirrors `branch._eval_when`).
Implementation: in `execute_task`, capture `run_when` from `raw_config` BEFORE `_resolve_config`.

### 2.2 `for_each` — fan-out via the existing map runtime

Detection: `execute_task` for a `query`/`python` cell whose `config` has `for_each`. Rather than
adding a new state machine, we **reuse the map fan-out runtime verbatim** by normalizing the
cell into a map-shaped task at the executor boundary.

Approach (PINNED — "synthetic map adapter"):

1. New helper `app/flows/for_each.py :: to_map_config(task) -> dict | None`. Given a task dict
   whose `config.for_each` is set, returns a map-style config:
   ```python
   {
     "item_expr": for_each["items"],
     "item_var": for_each.get("var", "item"),
     "max_concurrency": for_each.get("max_concurrency", 0),
     "max_map_size": 1000,
     "collect_key": "__self__",
     "body": [{
       "key": "__self__",
       "kind": task["kind"],            # 'query' or 'python'
       "needs": [],
       "config": {k: v for k, v in task["config"].items()
                  if k not in ("for_each",)},   # body keeps materialized/run_when-free copy
       "retries": task.get("retries", 0),
       "retry_backoff_s": task.get("retry_backoff_s", 30),
       "timeout_s": task.get("timeout_s", 60),
       "cache_ttl_s": task.get("cache_ttl_s", 0),
     }],
   }
   ```
   The body task strips `for_each` (no nested fan-out) and `run_when` (already gated on the
   parent). It KEEPS `materialized` so each item persists.

2. In `run_one_ready_task` / `_execute_claimed_task_run` (runtime), BEFORE calling
   `execute_task`, detect `for_each` on the resolved `task_spec`. If present, treat the task as
   `kind == "map"` for this run: call `handle_map`-equivalent by setting
   `full_task["kind"] = "map"` and `full_task["config"] = to_map_config(task_spec)`. The existing
   map fan-out block (`if full_kind == "map" and map_items is not None`) then expands children
   using the synthetic body, transitions the cell to `waiting_children`, and collects results —
   ALL existing code, unchanged. The composite child key becomes `"{cell_key}[{i}].__self__"`.

   This means: `_get_task_spec`'s existing map-child resolver (which matches `kind == "map"` and
   reads `config.body`) must also find the synthetic body. **PINNED:** store the synthetic map
   config on the parent task_run's `result.__map_config__` when transitioning to
   `waiting_children`, and have `_get_task_spec` fall back to it for `"{cell}[{i}].__self__"`
   children when the spec task has `for_each` (it reconstructs `to_map_config` from the spec —
   deterministic, no persistence needed). Reconstruction is preferred over persistence to avoid
   touching the store.

3. The collected result on the parent cell is the existing map shape
   `{"items": [{"index": i, "result": {...}}, ...], "item_count": N}`. Downstream cells that
   reference the `for_each` cell get this aggregated structure (documented in the notebook
   annotation and the template).

Reuse summary: `handle_map`, `_expand_map_children`, `_collect_map_results`, the
`waiting_children` machinery, and the map fan-in in `advance_readiness` are ALL reused as-is.
The only new code is `to_map_config` + the two detection hooks (executor passthrough is not even
needed because the runtime rewrites `kind` before `execute_task`).

### 2.3 `materialized` (full/incremental) — on a SQL cell, reusing `incremental.py`

Detection: a `query` cell whose `config.materialized.kind` ∈ {`full`, `incremental`}.

The standalone `materialize` kind merges N upstream sources via `combine_sql`. A SQL CELL has no
`combine_sql` — its OWN SELECT result is what gets persisted. So we add a thin persist step in
the query path, reusing `apply_incremental` / `resolve_target_uri` exactly as `materialize_blend`
does (§ materialize.py lines 319-357).

New helper `app/flows/cell_materialize.py :: persist_query_result(rows, columns, materialized,
*, env, flow, watermark, now) -> dict`:

```python
def persist_query_result(rows, columns, materialized, *, env, flow, watermark, now):
    kind = str((materialized or {}).get("kind") or "view").lower()
    if kind not in ("full", "incremental"):
        return {}                       # view ⇒ no-op
    import pyarrow as pa
    from app.flows.incremental import apply_incremental, resolve_target_uri
    combined = pa.Table.from_pylist(rows)            # the cell's SELECT output
    settings = _get_settings()
    physical_target = resolve_target_uri(env, materialized, flow, settings)
    mat = dict(materialized); mat["__physical_target__"] = physical_target
    storage = _open_storage_connector(physical_target)   # reuse materialize._open_storage_connector
    try:
        rows_written, new_watermark = apply_incremental(storage, combined, mat, watermark, now)
    finally:
        _close_storage_connector(storage)
    return {"materialized_kind": kind, "physical_target": physical_target, "env": env,
            "rows_written": rows_written, "new_watermark": new_watermark}
```

Wiring (PINNED — keep it OUT of the shared `_handle_query` so the connector/bridge handlers stay
untouched, and so it never runs in preview):

- In `run_one_ready_task` success path, AFTER a successful `query` cell whose
  `config.materialized.kind` is full/incremental: call `persist_query_result` with
  `outcome["result"]["rows"]/["columns"]`, the run-context `env`, `flow_dict`, and the
  pre-read `watermark`, then MERGE the returned dict into `outcome["result"]` before persisting
  the task_run. This places `new_watermark` into the result so the EXISTING
  `_persist_watermark` path stores it.
- `_is_incremental_materialize(task_spec)` (runtime helper that decides whether to READ a
  watermark before the run, and PERSIST one after) must be widened: it currently checks
  `kind == "materialize"`. Change it to ALSO return True for a `query` task whose
  `config.materialized.kind` ∈ {`full`,`incremental`}. This makes the existing watermark
  read (`_resolve_run_env_context`) and write (`_persist_watermark`) cover SQL cells with zero
  new watermark plumbing.
- Preview mode (`preview_cell` / `_execute_query_with_bridge`): `materialized` is IGNORED in
  preview (no persistence on an interactive run). Preview never reaches the runtime success
  path, so this is automatic — just do NOT add the persist call into `execute_task`.

Result: incremental/full materialization on a SQL cell reuses 100% of `incremental.py` and the
watermark store (`get_watermark`/`set_watermark`), with `cell_materialize.persist_query_result`
as the only new ~25-line seam.

---

## 3. SAFE `run_when` EVALUATOR (no arbitrary eval / no builtins)

New module `backend/app/flows/run_when.py`. It MUST NOT use Python `eval`/`exec`. It is a
restricted-AST evaluator over `inputs`/`params`/`secrets`. (The existing `branch._eval_when`
uses `eval` with stripped builtins; v4 introduces a STRICTER evaluator and the branch handler
keeps its own code for back-compat — we do not weaken branch, we just don't reuse its `eval`.)

```python
def evaluate_run_when(expr: str, ctx) -> bool:
    """Safely evaluate a boolean expression over inputs/params/secrets.
    Returns True for empty expr. Raises ValueError on a disallowed/invalid expr."""
```

Design:

- Parse with `ast.parse(expr, mode="eval")`. Walk the tree; allow ONLY these node types:
  `Expression, BoolOp(And/Or), UnaryOp(Not), BinOp(Add/Sub/Mult/Div/Mod), Compare`
  (`Eq, NotEq, Lt, LtE, Gt, GtE, In, NotIn, Is, IsNot`), `Name, Attribute, Subscript,
  Constant, List, Tuple, Dict, Set, Index/Slice`. Optionally a tiny allowlist of pure
  functions via `Call` restricted to names in `{len, str, int, float, bool, abs, min, max}`
  resolved from a fixed table (NOT from builtins). ANY other node (`Call` to an arbitrary
  name, `Lambda`, comprehensions, `Starred`, attribute writes, etc.) ⇒ `ValueError`.
- Evaluate the allowed nodes by recursion. `Name` resolves ONLY against the namespace
  `{"inputs": ctx.inputs, "params": ctx.flow_params, "secrets": ctx.secrets, "True": True,
  "False": False, "None": None}`. `Attribute`/`Subscript` access is dict-style: `obj.x` and
  `obj["x"]` both map to `obj.get("x")` when `obj` is a dict (so `inputs.classify.label`
  works without attribute access into arbitrary Python objects). Unknown keys ⇒ `None` (soft),
  so a not-yet-run upstream never raises.
- Template tolerance: if the expr contains `{{ ... }}`, strip the braces around pure dot-paths
  first (`{{ inputs.x.label }} == 'high'` → `inputs.x.label == 'high'`) using the same brace
  regex as `branch._eval_when`, THEN parse the result with the safe AST walker. This keeps the
  notebook-authored template form working while never calling `eval`.
- On a parse/disallowed-node error: raise `ValueError` so the cell FAILS loudly (not silently
  skipped) — a malformed gate is an authoring bug, not a "don't run" signal.

Trust note: even though `inputs`/`params` can contain attacker-influenced data (e.g. an
upstream API fetch), the restricted AST means a malicious string in `run_when` cannot call
functions or reach builtins — it can at most produce a boolean. This is strictly safer than the
branch handler's `eval`-with-empty-builtins.

---

## 4. CANVAS BADGE RENDERING (`src/flows/specGraph.js` + `nodes/TaskNode.jsx`)

The canvas keeps showing `taskNode` for sql/python cells. Old `map`/`branch` specs keep their
`mapNode`/`branchNode` rendering (untouched, for back-compat). For the NEW config blocks on
sql/python cells, derive badges/shape from `config` — no new node TYPE is introduced.

### 4.1 `specGraph.js`

In `specToGraph`, when building a `taskNode`, derive a `data.cellBadges` object from config so
`TaskNode` doesn't re-parse config:

```js
const mat = task.config?.materialized
const cellBadges = {
  materialized: mat && mat.kind && mat.kind !== 'view'
    ? { kind: mat.kind, target: mat.target }            // 'full' | 'incremental'
    : null,
  forEach: task.config?.for_each
    ? { items: task.config.for_each.items, var: task.config.for_each.var ?? 'item' }
    : null,
  runWhen: typeof task.config?.run_when === 'string' && task.config.run_when.trim()
    ? task.config.run_when.trim()
    : null,
}
// attach to node.data.cellBadges
```

Conditional EDGES for `run_when`: for any task with a non-empty `run_when`, mark its INCOMING
dependency edges (from its `needs`) as conditional — `data.conditional = true`,
`style.strokeDasharray = '5 3'`, and `label` = a truncated `run_when` (reuse `_branchLabel`-style
truncation). These are styling-only; they still represent real `needs`, so `graphToSpec` writes
them back into `needs` normally (UNLIKE branch/inferred edges which are skipped). Do NOT add
`data.conditional` to the skip set in `graphToSpec`.

### 4.2 `TaskNode.jsx`

Render small badges in the node body (below the kind badge), driven by `data.cellBadges`:

- **materialized** → a DB/table icon (`Database` or `Table`) + label `→ table (<kind>)`
  e.g. `→ table (incremental)`. Cyan accent (reuse the cyan materialize palette).
- **for_each** → a stacked/fan-out icon (`Layers` or `Copy`) + label `for each` (tooltip:
  the `items` expr). Indigo accent.
- **run_when** → a conditional marker icon (`GitBranch` or `Filter`) + label `if` (tooltip: the
  expr). Amber accent. Also gives the node a subtle dashed left border to read as "conditional".

Keep badges compact (`text-[9px]`, single row, wrap allowed). They are purely informational; the
authoritative state still comes from `taskRun.state`.

---

## 5. NOTEBOOK ANNOTATIONS (`src/flows/NotebookView.jsx` + cells)

The notebook is the canonical mental model. Each cell shows an inline annotation strip when any
config block is set. Implement a small presentational `CellConfigAnnotations` component rendered
inside `SqlCell`/`PythonCell` (in the `CellToolbar` area or just under it), reading `cell.config`:

- `materialized.kind` ∈ {full, incremental} → `→ table (<kind>)` (+ target as a tooltip/subtext,
  e.g. `→ table (incremental) · orders/daily`).
- `for_each` → `for each: <items>` (truncate the items expr to ~32 chars).
- `run_when` → `runs when: <expr>` (truncate).
- `cell_type === 'markdown'` (Note) → render the cell as prose (markdown) with no run button.

These are read-only labels in the cell header. Editing the blocks happens in `NodeInspector`
(canvas) for v1; the notebook just SURFACES them so the two views stay consistent and lossless
(both read/write the same `config`). A "⚙ cell settings" affordance MAY open the same
materialized/for_each/run_when editors inline, but the minimum v1 requirement is the read-only
annotation strip + the Note cell type.

### 5.1 Note (markdown) cell

`makeBlankCell('markdown')` (extend `notebooks.js`): `{ kind: 'noop', cell_type: 'markdown',
config: { markdown: '' }, needs: [] , ...defaults }`. NotebookView's add bar gets a third "Note"
button; the cell renders the markdown (read mode) / a small textarea (edit mode). It never calls
`previewCell`. Because `kind: 'noop'`, it executes as the existing pass-through node in a durable
run (no behaviour change).

---

## 6. PALETTE + TEMPLATES (`AddTaskPanel.jsx` / `pythonExamples.js` / SQL snippet)

### 6.1 `AddTaskPanel.jsx`

`PRIMARY_ITEMS` becomes the ONLY palette:

```js
const PRIMARY_ITEMS = [
  { kind: 'query',  cell_type: 'sql',      label: 'SQL query', ... defaultConfig: { sql: '' } },
  { kind: 'python', cell_type: 'python',   label: 'Python',    ... defaultConfig: { code: '...' } },
  { kind: 'noop',   cell_type: 'markdown', label: 'Note',      ... defaultConfig: { markdown: '' } },
]
```

DELETE `ADVANCED_ITEMS` from the palette (agent, bucket_load, map, branch, materialize,
preagg_refresh, noop). `onAdd` must also stamp `cell_type` onto the created task.

### 6.2 `NodeInspector.jsx`

- `KINDS` (the kind `<select>`) keeps ALL kinds so OLD specs with `map`/`branch`/`materialize`/
  `agent`/etc. remain fully EDITABLE (back-compat). New cells are authored as `query`/`python`/
  `noop`, but the inspector must not hide a legacy kind it is handed.
- Add the cell-config editors for the THREE new blocks, shown for `query`/`python` cells:
  - **MaterializedSection** (already exists) — show it on `query` cells (move it out of
    `MaterializeConfig`, OR render it standalone when `task.kind === 'query'`).
  - **ForEachSection** (new) — `items`, `var`, `max_concurrency`. Shown for query + python.
  - **RunWhenSection** (new) — a single text input bound to `config.run_when`. Shown for all.

### 6.3 Templates (replacing the removed advanced kinds)

`pythonExamples.js` — ADD:

- **"Call agent"** — a Python template that shapes a prompt from upstream rows and returns
  `{prompt: ...}` (the existing "Call agent (template)" entry already does this — keep it; it is
  the agent replacement). Optionally a variant that documents POSTing to the agent route.

`SqlCell` snippet picker (new, mirrors Python's): a small SQL snippet menu with:

- **"Load from object storage (bucket)"** — SQL over the object-storage connector, e.g.
  `SELECT * FROM read_parquet('s3://bucket/path/*.parquet')` (the `bucket_load` replacement; runs
  against a datastore/connector). Documented to set `datastore_id` to the storage connector.

Existing python templates (Extract archive, HTTP fetch JSON, Transform rows, pandas) stay.

---

## 7. SPEC VALIDATION (`spec.py`) — additive only

`validate_flow_spec` keeps validating the legacy kinds exactly as today. Add OPTIONAL validation
for the new blocks on `query`/`python` cells (do NOT make them hard-required, do NOT touch legacy
paths):

- `config.materialized` on a `query` cell: validate with the EXISTING `MaterializedConfig` model
  + the EXISTING target/time_column requirement checks (factor the materialize-kind validation
  block so it also runs for `query` cells with `materialized`). Hard error only when
  `kind != 'view'` and `target` missing, or `kind == 'incremental'` and `time_column` missing.
- `config.for_each`: if present, `items` must be a non-empty string. (`var` optional.) Soft —
  hard error only on missing `items`.
- `config.run_when`: if present, must be a string. (Do NOT evaluate it at validation time — it
  references runtime inputs.) A future enhancement MAY AST-parse it for a syntax pre-check; v1
  keeps validation to "is a string".

No new top-level FlowSpec field. No change to the legacy `map`/`branch`/`materialize` validation
branches. The cycle/needs/dup-key checks are unchanged.

---

## 8. BACK-COMPAT STRATEGY (MANDATORY)

- **Registry unchanged.** `registry._bootstrap` keeps registering `query, python, agent,
  materialize, noop, bucket_load, preagg_refresh, map, branch, map_collect`. Old specs that use
  these kinds dispatch to the same handlers and run identically. `preagg_refresh` stays as an
  internal/scheduled mechanism — only removed from the palette.
- **No kind removed from `TaskSpec.kind` Literal.** All ten kinds stay valid so saved specs
  validate.
- **`map`/`branch` canvas rendering retained.** `specToGraph` still emits `mapNode`/`branchNode`
  for those kinds; `NodeInspector` keeps `MapConfig`/`BranchConfig`. Only the PALETTE and the
  default authoring path change.
- **`materialized` dual-home.** The block is consumed by BOTH the legacy `materialize` handler
  (multi-source blend, via `materialize_blend`) AND, now, query cells (via
  `persist_query_result`). The Pydantic shape is identical; no migration.
- **New states.** `skipped` is already a known terminal/non-blocking state in the runtime — we
  are only newly EMITTING it from `run_when`. No state-machine rename. Verify `TaskNode` /
  notebook handle a `skipped` taskRun state (add a slate/"skipped" dot + label).
- **Preview unaffected.** `materialized` no-ops in preview; `for_each`/`run_when` evaluate
  per-cell. The `/flows/preview` request/response shape is unchanged.
- **No DB/schema/migration changes.** Everything lives in `tasks[].config` jsonb. No new column.

---

## 9. FILES TO CHANGE (one line each)

Backend:
- `backend/app/flows/run_when.py` — NEW: safe restricted-AST `evaluate_run_when(expr, ctx)`.
- `backend/app/flows/for_each.py` — NEW: `to_map_config(task)` synthetic-map adapter.
- `backend/app/flows/cell_materialize.py` — NEW: `persist_query_result(...)` reusing `incremental.py`.
- `backend/app/flows/executor.py` — `execute_task`: capture raw `run_when`, gate → return `skipped` before dispatch.
- `backend/app/flows/runtime.py` — `run_one_ready_task`/`_execute_claimed_task_run`: handle `skipped` outcome; detect `for_each` (rewrite to map) before `execute_task`; call `persist_query_result` on materialized query cells; widen `_is_incremental_materialize`; teach `_get_task_spec` the `[i].__self__` synthetic body.
- `backend/app/flows/spec.py` — additive validation for `materialized` on query cells + `for_each`/`run_when` shape checks.

Frontend:
- `src/flows/AddTaskPanel.jsx` — palette = SQL / Python / Note only; stamp `cell_type`.
- `src/flows/specGraph.js` — derive `data.cellBadges`; conditional dashed incoming edges for `run_when` (NOT skipped from needs).
- `src/flows/nodes/TaskNode.jsx` — render materialized/for_each/run_when badges + `skipped` state dot/label.
- `src/flows/NotebookView.jsx` — third "Note" add button; per-cell annotation strip wiring.
- `src/flows/cells/SqlCell.jsx` / `PythonCell.jsx` — `CellConfigAnnotations` strip; SQL snippet picker; markdown render for Note.
- `src/flows/NodeInspector.jsx` — keep all `KINDS`; add ForEachSection + RunWhenSection; show MaterializedSection on query cells.
- `src/flows/pythonExamples.js` — keep "Call agent"; (bucket goes to SQL snippets).
- `src/lib/notebooks.js` — `makeBlankCell('markdown')`; `genCellKey` markdown variant.

Tests (additive):
- `backend/tests/test_run_when.py` — safe eval allows comparisons/bool ops, rejects `Call` to arbitrary names / lambdas / comprehensions; unknown key → None; template form works.
- `backend/tests/test_for_each_cell.py` — a query/python cell with `for_each` fans out (reuses map runtime), collects `{items, item_count}`.
- `backend/tests/test_cell_materialize.py` — a query cell with `materialized.kind=incremental` persists via `apply_incremental` and advances the watermark.
- `backend/tests/` skipped-state test — `run_when` false ⇒ task_run `skipped`, downstream `upstream_failed`, flow_run `success`.
- Frontend `specGraph.test.mjs` — `cellBadges` derivation; `run_when` dashed incoming edge survives `graphToSpec` round-trip into `needs`.
- Ensure existing `test_flow*`/`test_flows*` + `specGraph.test.mjs` stay green.

---

## 10. VERIFICATION

- `cd backend && pytest -q` (full flow suite green; new tests pass).
- `npm run build` (vite) + `npx eslint .` clean.
- Every new backend module imports cleanly under the live `uvicorn --reload` (no import-time
  side effects; all heavy imports are function-local, matching the codebase pattern).
- Best-effort live screenshots: add a SQL cell, set materialized=incremental, set a `run_when`,
  add a Note — confirm canvas badges, notebook annotations, and a durable run that skips a gated
  cell and persists a materialized cell.
