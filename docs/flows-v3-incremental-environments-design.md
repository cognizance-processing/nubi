# Flows v3 — Incremental Materialization + Dev/Prod Environments (v1 design)

Status: PINNED for parallel implementation (backend / frontend / flow-code engineers).
Author: architect pass, 2026-06-09.
Scope: SQLMesh-style incremental materialization (view/full/incremental), env-scoped
targets (dev/prod virtual environments), and a verify/fix of the Flow-code round-trip.

This document is the contract. The three field-name / shape sections below
(`§2 Pinned config shape`, `§3 Env contract`, `§5 Watermark store`) are normative —
implement against them exactly. Everything else is guidance.

---

## 0. Context recap (what already exists — DO NOT remove)

- `FlowSpec` (`backend/app/flows/spec.py`) with `env: str = "prod"` field, `tasks`, `params`,
  `runtime_config`, `schedule` driven by an external scheduler ticking `POST /flows/tick`
  (no always-on worker).
- A `materialize` task kind (`backend/app/flows/materialize.py` + `registry._handle_materialize`)
  that blends N upstream `query` results in DuckDB via `combine_sql`, writes to a DuckDB file,
  preserves `rls_keys`, and registers a runtime query. Required config key: `combine_sql`.
- Inferred SQL deps (`backend/app/flows/deps.py`), dataframe-native Python, retries/timeouts/cache.
- Persisted task logs, live `FlowRunView`.
- Object-storage-aware DuckDB connector: `backend/app/connectors/duckdb_storage.py`
  (`DuckDBStorageConnector.from_config` / `.write_result(sql, dest_uri)` → Parquet,
  `.read_parquet(uri)`), with s3://-style httpfs + secret bootstrap. This is the write/read
  path for materialized targets on stateless/ephemeral containers.
- Flow-code SDK at `nubi.flows` (`_builder.py`, `_combinators.py`, `_nodes.py`, `_compile.py`,
  `_run.py`) + `POST /flows/codegen` + `POST /flows/compile` + `src/flows/CodePanel.jsx`.

Deployment constraint: production containers are stateless/ephemeral. Materialized/incremental
TARGETS live in OBJECT STORAGE (s3:// DuckDB/Parquet via `duckdb_storage` — any S3-compatible
store, e.g. Cloudflare R2). State/watermarks live in Postgres.

---

## 1. Design decisions (PINNED)

### D1 — Reuse the `materialize` task; add a `materialized` block to its config.
We do NOT add a parallel `materialized` block to `query` tasks in v1. Rationale:
- `materialize` already owns the DuckDB write path, `rls_keys` preservation, query registration,
  and a clean handler seam. Bolting persistence onto every `query` task would fork two write paths
  and complicate the Python→SQL bridge / preview semantics.
- The `materialize` task's `combine_sql` is exactly the "model SQL" SQLMesh would persist.

So: a materialization config is a single nested dict on the **materialize task config** under the
key `materialized` (see §2). When absent, behaviour is identical to today (`kind: "view"` default).

### D2 — Watermarks live in a NEW minimal Postgres table `flow_watermarks`.
We do NOT overload `task_runs.result` or `flows.spec` jsonb (those are per-run / per-spec and would
race or get clobbered on spec edits). One small forward-only migration (`0026_flow_watermarks.sql`).
Keyed by `(flow_id, model_key, env)`. See §5.

### D3 — Environments are a TARGET NAMESPACE via path prefix `<env>/<target>`.
`FlowSpec.env` is the active environment. A flow-run carries a resolved `env` (trigger-time override
allowed). The env is prepended to the materialized target path/prefix so dev and prod never clobber.
No SQLMesh plan/apply/view-diff engine in v1. "Promote" v1 = run with `env="prod"` (+ optional
watermark copy helper). See §3.

### D4 — Flow-code round-trip is BROKEN today; fix is mandatory (see §6).
`backend/app/flows/codegen.py` emits `from nubi.sdk import ...` but there is no `nubi.sdk` module
(the SDK is `nubi.flows`). Generated source fails to import/compile. Fix: emit
`from nubi.flows import flow, task, map_node, branch_node, FlowParam`. Also thread the new
`materialized` block + `env` through codegen/compile. See §6.

---

## 2. PINNED materialization config shape

The materialization config is a nested dict stored under config key **`materialized`** on a
`materialize`-kind task. All fields optional except `kind` (defaulted) — fully backward-compatible
(absent block ⇒ `view`).

```jsonc
// TaskSpec for a materialize task:
{
  "key": "blend",
  "kind": "materialize",
  "needs": ["src_a", "src_b"],
  "config": {
    "combine_sql": "SELECT * FROM src_a UNION ALL SELECT * FROM src_b",  // existing, required
    "sources":   ["src_a", "src_b"],   // existing
    "rls_keys":  ["tenant_id"],        // existing
    "table":     "blend",              // existing — target table name inside the duckdb file
    "datastore_id": "…",               // existing — read-side exposure
    "query_id":     "…",               // existing — read-side exposure

    // ── NEW: materialization config (the pinned block) ──────────────────────
    "materialized": {
      "kind":        "incremental",      // "view" | "full" | "incremental"  (default "view")
      "target":      "blends/revenue",   // logical target path (NO env prefix; engine prepends it)
      "time_column": "event_date",       // required when kind="incremental"
      "unique_key":  ["order_id"],       // optional; when present → upsert/merge on these cols
      "lookback":    "3 days"            // optional; ISO-ish duration string, see §2.3
    }
  }
}
```

### 2.1 Field semantics (NORMATIVE)

| Field | Type | Required | Meaning |
|---|---|---|---|
| `kind` | `"view"｜"full"｜"incremental"` | no (default `"view"`) | Persistence strategy. |
| `target` | `str` | required when `kind != "view"` | Logical target path/key WITHOUT env prefix. The engine writes to `<env>/<target>` (see §3.2). May be a duckdb-relative table name or an object-store key stem; format pinned in §3.2. |
| `time_column` | `str` | required when `kind == "incremental"` | Column compared against the stored watermark. |
| `unique_key` | `list[str]` | optional | When present + incremental ⇒ upsert/merge (delete-then-insert on these keys). When absent ⇒ append-only. |
| `lookback` | `str` | optional | Reprocess window subtracted from the watermark before filtering (late-arriving data). Parsed by `parse_lookback()` (§2.3). |

- `kind="view"` → no persistence; current `materialize_blend` behaviour, env prefix ignored.
- `kind="full"` → overwrite the target table/file each run (replace). Watermark NOT used/advanced.
- `kind="incremental"` → process only rows where `time_column > (watermark - lookback)`, then append
  (or upsert if `unique_key`) into the target, then advance the watermark to `max(time_column)` seen.

### 2.2 Why a nested `materialized` block (not flat keys)
Keeps the namespace clean, makes codegen/compile trivial (one dict kwarg), and lets the frontend
inspector bind to one object. Flat keys (`mat_kind`, `mat_target`, …) were rejected to avoid
polluting the already-large `materialize` config and to keep the SDK call readable.

### 2.3 `lookback` grammar (PINNED, minimal)
A string like `"3 days"`, `"6 hours"`, `"45 minutes"`, `"0"`/empty (=no lookback). Implement
`backend/app/flows/incremental.py::parse_lookback(s) -> datetime.timedelta`. Accept
`<int> <unit>` where unit ∈ {second(s), minute(s), hour(s), day(s), week(s)}. Empty/None/`"0"` → zero.
Unparseable → zero (never raise; log a `[warn]`).

---

## 3. PINNED env contract (dev/prod virtual environments)

### 3.1 Field names
- `FlowSpec.env: str` — already exists; the flow's DEFAULT/active environment. Keep default `"prod"`
  for backward-compat with saved specs (changing it would silently re-route existing flows).
- Flow-run resolved env: stored on the flow_run row as a NEW nullable column `env text`
  (migration `0026`, see §5) AND mirrored into `params["__env__"]` is NOT used — use the column.
  Resolution order at trigger time (PINNED):
  1. explicit trigger override (`RunFlowIn.env`, see §4.2) if non-empty,
  2. else `flow.spec.env`,
  3. else `"prod"`.
- TaskContext: add `env: str = "prod"` field (`backend/app/flows/executor.py::TaskContext`).
  The runtime populates it from `flow_run["env"]`. The materialize handler reads `ctx.env`.

### 3.2 Env-scoped target path format (PINNED)
The engine computes the physical target from `(env, materialized.target, base_uri)`:

```
physical_target = join(base_uri, env, target)
```

- `base_uri` source (PINNED precedence):
  1. `task.config["materialized"]["base_uri"]` if set (rare, explicit override), else
  2. `flow.runtime_config["materialize_base_uri"]` if set, else
  3. settings `FLOWS_MATERIALIZE_BASE_URI` (env var; e.g. `s3://nubi-materialized`), else
  4. local fallback `<backend>/seed_data/materialized` (dev/offline; mirrors existing
     `blend_database_path`).
- Path join rules: strip leading/trailing slashes on each segment; preserve the `s3://` scheme.
  e.g. `s3://nubi-materialized` + `dev` + `blends/revenue` →
  `s3://nubi-materialized/dev/blends/revenue.duckdb` (duckdb file) — the `.duckdb` suffix is added
  when the target is a DuckDB database; Parquet write-back uses `.parquet`. v1 writes a DuckDB file
  (reuses `materialize_blend`'s write path) so the existing read path (`routes/query.py`,
  `datastore.config.database`) keeps working. For `s3://` targets the read-side datastore config
  must carry `connector_type: "duckdb_storage"` and the `database` URI.
- Helper to implement: `backend/app/flows/incremental.py::resolve_target_uri(env, materialized, flow, settings) -> str`.

### 3.3 Backward compat
- A `materialize` task with NO `materialized` block writes to its existing `config["database"]`
  path exactly as today (env prefix NOT applied — preserves all current blend tests).
- Only when a `materialized` block is present does the engine compute `physical_target` and override
  the effective database path.

---

## 4. Backend implementation plan (file-level)

### 4.1 New module `backend/app/flows/incremental.py`
- `parse_lookback(s) -> timedelta` (§2.3).
- `resolve_target_uri(env, materialized, flow, settings) -> str` (§3.2).
- `apply_incremental(connector, combined_table, materialized, watermark, now) -> (rows_written, new_watermark)`:
  Given the combined Arrow table (output of `combine_sql`) and the stored watermark:
  - `view`: no-op (caller handles).
  - `full`: replace target table with `combined_table`.
  - `incremental`: filter `combined_table` to `time_column > (watermark - lookback)`; if `unique_key`
    set, delete matching keys in target then insert (DuckDB `DELETE … WHERE key IN (…)` + append),
    else append; compute `new_watermark = max(time_column)`.
  Keep all DuckDB work in-process via the existing duckdb connection pattern in `materialize.py`.

### 4.2 Edit `backend/app/flows/spec.py`
- Add a Pydantic model `MaterializedConfig(BaseModel)` with the §2 fields (all optional, `kind`
  default `"view"`). Do NOT make it a TaskSpec field — it lives inside `config["materialized"]`,
  which is a free-form dict. Instead, add VALIDATION in `validate_flow_spec`:
  - For `kind=="materialize"` tasks, if `config.get("materialized")` present, validate it:
    `materialized.kind` ∈ {view,full,incremental}; if `incremental`, `time_column` required and
    `target` required; if `full`, `target` required. Emit hard errors on violation, mirroring the
    existing per-kind validation block.
- Keep `combine_sql` required as today.
- The `MaterializedConfig` model is still useful for `flow_spec_json_schema()` grounding and for the
  SDK; export it. Validation reads the raw dict (don't change `config` typing).

### 4.3 Edit `backend/app/flows/materialize.py`
- `materialize_blend(config, inputs, *, env="prod", flow=None, watermark=None, store=None)`:
  - Parse `materialized = config.get("materialized")`. If absent or `kind=="view"` → EXISTING path
    unchanged (writes to `config["database"]`, registers query). Return manifest as today.
  - If present and `kind` ∈ {full, incremental}:
    - Compute `physical_target = resolve_target_uri(env, materialized, flow, get_settings())`.
    - Run `combine_sql` over registered inputs → `combined` Arrow table (existing code).
    - RLS-key preservation check (existing) still runs.
    - Build/open the target via `DuckDBStorageConnector` when the target is `s3://`, else the local
      duckdb file path. Apply `apply_incremental(...)`.
    - Return manifest extended with `materialized_kind`, `physical_target`, `env`,
      `rows_written`, `new_watermark` (ISO string or None).
  - The handler must NOT itself read/write Postgres — watermark read/advance is done by the RUNTIME
    around the handler call (handlers stay sync + DB-free; mirrors current architecture). The handler
    receives `watermark` (in) and returns `new_watermark` (out) in its result dict.
- Keep `build_blend_spec`, `blend_database_path`, `register_blend_query` intact.

### 4.4 Edit `backend/app/flows/registry.py::_handle_materialize`
- Pass `env=ctx.env` and the flow object through to `materialize_blend`. The handler can read the
  stored watermark from `ctx` — add `ctx.watermark` (see 4.6) and `ctx.flow` (the flow dict) OR keep
  the handler pure and have the runtime do watermark read/write. PINNED: keep handler pure; the
  runtime resolves watermark BEFORE calling the handler and persists `new_watermark` AFTER. To keep
  the handler signature `(config, ctx, claims)`, thread `env` + `watermark` + flow base_uri via
  `ctx` (new fields `ctx.env`, `ctx.watermark`, `ctx.flow`).

### 4.5 Edit `backend/app/flows/executor.py::TaskContext`
- Add fields: `env: str = "prod"`, `watermark: str | None = None`, `flow: dict[str, Any] | None = None`.
  (All defaulted ⇒ no caller breaks; `preview_cell` and SDK paths keep working.)

### 4.6 Edit `backend/app/flows/runtime.py`
- `materialize_flow_run`: read resolved `env` from `flow_run["env"]` (set by the route/store) and
  carry it forward. No watermark work here.
- In `run_one_ready_task` / `_execute_claimed_task_run`: when building `TaskContext`, set
  `ctx.env = flow_run.get("env") or (flow.spec.env) or "prod"` and `ctx.flow = flow`.
  For `kind=="materialize"` tasks with an incremental `materialized` block: BEFORE executing, read the
  watermark via the store (`store.get_watermark(flow_id, model_key, env)`), set `ctx.watermark`.
  AFTER a successful materialize result that returns `new_watermark`, persist it via
  `store.set_watermark(flow_id, model_key, env, new_watermark)`. `model_key = task_key`.
- `preview_cell`: pass `env="dev"` by default (preview is always dev-ish) but do NOT persist
  watermarks in preview (pass `watermark=None`, skip store writes).

### 4.7 Edit store layer
- `backend/app/flows/store.py` (InMemoryFlowStore) + `backend/app/repos/pg.py` (PgFlowStore):
  - `create_flow_run(..., env: str = "prod")` → store `env` on the run dict / row.
  - Add `get_watermark(flow_id, model_key, env) -> str | None` and
    `set_watermark(flow_id, model_key, env, value)`.
    - InMemory: a `dict[(flow_id, model_key, env)] -> str`.
    - Pg: read/upsert `flow_watermarks` (§5).
- `materialize_flow_run` calls `create_flow_run` with the resolved env.

### 4.8 Edit `backend/app/routes/flows.py`
- `RunFlowIn`: add `env: str | None = None` (trigger-time override).
- `run_flow`: resolve env (§3.1 order), pass to `materialize_flow_run` (which passes to
  `create_flow_run`). Include `env` in `_serialize_flow_run` output so the UI shows the active env.
- `flow_tick` (scheduled path): resolve env from `flow.spec.env` (no override at schedule time).

### 4.9 Settings
- `backend/app/config.py`: add `FLOWS_MATERIALIZE_BASE_URI: str = ""` (read via `getattr` defensively
  in `incremental.py` so import stays safe before config update).

---

## 5. PINNED watermark store (migration 0026)

New forward-only migration `database/migrations/0026_flow_watermarks.sql`:

```sql
-- Migration 0026: per-(flow, model, env) incremental watermarks for materialize tasks.
CREATE TABLE IF NOT EXISTS flow_watermarks (
    flow_id     uuid        NOT NULL REFERENCES flows(id) ON DELETE CASCADE,
    model_key   text        NOT NULL,         -- the materialize task_key
    env         text        NOT NULL DEFAULT 'prod',
    watermark   text        NOT NULL,         -- ISO-8601 string (max time_column seen)
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (flow_id, model_key, env)
);

-- Add env to flow_runs (nullable; defaults preserve old rows as prod at read time).
ALTER TABLE flow_runs
  ADD COLUMN IF NOT EXISTS env text;
```

Watermark stored as a string (ISO datetime or the raw max value of `time_column` when non-temporal —
v1 assumes a sortable/temporal column; document that `time_column` must be comparable). The runtime
treats `None`/absent watermark as "process everything" on first run.

"Promote dev→prod" v1 helper (optional, can ship later in v1): `store.copy_watermark(flow_id,
model_key, src_env, dst_env)` — used by a future promote endpoint; not required for the env-scoped
target MVP.

---

## 6. Flow-code (nubi.flows SDK) — verify + fix (PINNED)

### 6.1 CONFIRMED REGRESSION (must fix)
`backend/app/flows/codegen.py::flow_spec_to_sdk` emits header:
`from nubi.sdk import flow, task, map_node, branch_node`
But there is NO `nubi.sdk` module — the SDK package is `nubi.flows` (exports `flow, task, map_node,
branch_node, FlowParam`). Executing generated source raises `ModuleNotFoundError: No module named
'nubi.sdk'`, so `codegen → compile` round-trip is BROKEN end-to-end. Verified by executing the
generated source in this analysis.

Note there are TWO codegen implementations:
- `backend/app/flows/codegen.py` — used by routes (`/flows/codegen`, `/flows/{id}/codegen`) and by
  `tests/test_flows_codegen.py`. **Emits the broken `nubi.sdk` import.**
- `backend/nubi/flows/_compile.py` (exported as `nubi.flows.flow_spec_to_sdk`) — emits the CORRECT
  `from nubi.flows import flow, task, map_node, branch_node, FlowParam`. Used by `test_sdk_compile.py`.

These have diverged. PINNED resolution for v1 (minimal, low-risk): keep `app.flows.codegen` as the
route-facing generator but FIX its import line to `from nubi.flows import flow, task, map_node,
branch_node, FlowParam`. Do NOT delete `_compile.py` (it is exported and separately tested).
(Consolidating the two is out of v1 scope — note it as tech debt in §8.)

### 6.2 Fix list for `backend/app/flows/codegen.py`
1. Header import → `from nubi.flows import flow, task, map_node, branch_node, FlowParam`.
2. Params that emit a dict default currently produce `name={"type": "date", ...}` — `compile()` in
   `_builder.py` accepts a dict with a `"type"` key, so this works, but the round-trip is cleaner via
   `FlowParam(...)`. Keep the dict form (already supported by `compile`) to minimize change; just
   ensure `FlowParam` is imported (it now is). Add a round-trip test that actually `exec`s + compiles.
3. The `materialized` block is just another config dict key, so `_config_to_kwargs` already serializes
   it via `materialized={...}` kwarg on `@task(kind="materialize", ...)`. VERIFY nested-dict
   `_repr_value` handles it (it does — recursive). Add a round-trip test asserting the `materialized`
   block survives codegen→compile and equals the input.
4. The `env` field is FLOW-LEVEL, not a task config. `compile()` in `_builder.py` does NOT currently
   emit `env` (it hardcodes the spec dict without env). PINNED: thread `env` through:
   - `_builder.py::compile(**flow_params)` — accept the flow's env. Cleanest v1: read it from a
     module-level convention is messy; instead add an OPTIONAL `__env__` kwarg to `compile`
     (`compile(__env__="dev", **flow_params)`) that sets `spec["env"]`. Codegen emits
     `spec = <flow>.compile(__env__="dev", …)` when `spec.env != "prod"`. When env is default
     `"prod"`, omit it (keeps existing generated code stable + existing tests passing).
   - Validate `__env__` is filtered OUT of params (it is a reserved kwarg, not a FlowParam).

### 6.3 compile route (`/flows/compile`) — no shape change needed
The subprocess executes the source and reads `spec` (a FlowSpec dict). Once §6.2 fixes land, the
emitted source imports correctly, traces, and `compile()` returns the env + materialized block in the
spec dict. The route already validates the compiled spec via `validate_flow_spec` (which §4.2 extends
to validate the `materialized` block). No route code change required beyond what §4.8 adds.

### 6.4 Tests
- `tests/test_flows_codegen.py`: ADD a test that `exec`s the generated source in-process and asserts
  `compile()` reproduces tasks/kinds/configs/needs/params 1:1 (this would have caught the regression).
  Add cases for: (a) a `materialize` task with an incremental `materialized` block, (b) a flow with
  `env="dev"`.
- `tests/test_sdk_compile.py`: ADD round-trip for `materialized` block + `__env__` via the
  `nubi.flows` SDK (`_compile.py` already emits the correct import — verify the block survives).

---

## 7. Frontend implementation plan (file-level)

### 7.1 Env selector (top bar)
- `src/pages/app/FlowsPage.jsx`: add an environment selector into the `topbarSlot` next to the
  existing Run/Save/Validate actions. State `const [env, setEnv] = useState(activeSpec.env || 'prod')`.
  Options: `dev`, `prod`, plus any custom env already on the spec. Show the active env on the run view.
- `src/lib/flows.js::runFlow(id, params = {}, env)` → `post('/flows/{id}/run', { params, env })`.
  (Add the `env` arg; default omitted → backend resolves from spec.)
- `src/flows/FlowRunView.jsx`: display `run.env` (now serialized by the backend, §4.8) as a small
  badge so users see which environment a run targeted.

### 7.2 Materialization config in the node inspector
- `src/flows/NodeInspector.jsx`: when the selected node `kind === 'materialize'`, render a
  "Materialization" section bound to `config.materialized`:
  - `kind` select: View / Full table / Incremental.
  - When Full/Incremental: `target` text input.
  - When Incremental: `time_column` text input, `unique_key` (comma-separated → array), `lookback`
    text input (e.g. `3 days`).
  - Write back into `config.materialized` on the task spec (matching §2 shape EXACTLY).
- The frontend must NOT invent flat keys — it binds to the nested `materialized` object.

### 7.3 Code panel
- `src/flows/CodePanel.jsx`: no structural change. Once backend codegen is fixed, the generated source
  imports `nubi.flows` and round-trips. The env + materialized block flow through unchanged because
  CodePanel just ships `spec`/`code` to the existing endpoints.

---

## 8. Risks / tech debt / out-of-scope

- TWO codegen implementations (`app/flows/codegen.py` vs `nubi/flows/_compile.py`) remain after v1;
  consolidation deferred. Both must emit `from nubi.flows import …`. Risk: future drift.
- `time_column` is assumed comparable/sortable (temporal). Non-temporal incremental keys are not
  supported in v1 (document in user-facing flows docs).
- Object-store writes require `httpfs` + creds at runtime (production containers). The s3 secret/creds
  resolution reuses `DuckDBStorageConnector.from_config` precedence (config keys → env vars). Ensure
  the worker process env carries `AWS_*` (R2/S3-compatible creds) or the datastore config provides them.
- `unique_key` upsert in v1 is delete-then-insert within a single DuckDB connection — not
  transactional across the object store. Acceptable for scheduled (non-concurrent) materialize runs.
- No SQLMesh plan/apply/view-diff. "Promote" is run-with-env=prod (+ optional watermark copy).
- Watermark stored as text; first run (no watermark) processes all rows.
- BACKWARD COMPAT: materialize tasks with no `materialized` block are byte-for-byte unchanged; all
  existing blend tests + flow_run env-less rows keep working (env read defaults to `prod`).

## 9. Verification (no DB reset, no commit — human does those)
- `pytest backend/tests/test_flows_codegen.py backend/tests/test_sdk_compile.py
  backend/tests/test_flows_blend.py backend/tests/test_flows_spec.py
  backend/tests/test_flows_engine.py -q` must pass.
- New incremental tests: a fresh `tests/test_flow_incremental.py` covering view/full/incremental +
  env-scoped path + watermark advance (use InMemoryFlowStore + a local duckdb target).
- `npm run build` + eslint clean for the touched frontend files.
- Every backend edit must import cleanly (live uvicorn --reload server must not crash).
