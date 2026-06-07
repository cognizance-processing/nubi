# Flows — a lightweight, LLM-native workflow orchestrator (Prefect alternative)

Status: in progress. This file is the **authoritative contract** for the build.
Every agent reads this file first and implements against the shapes defined here.

## Goal

Add a Prefect-style workflow orchestrator that fits Nubi's existing patterns:
durable Postgres state, a deterministic clock-injected tick loop, pluggable
registries, RLS-aware execution, and an LLM that can both *author* and *be a step
in* flows. Plus a **React Flow DAG builder UI** like Prefect/n8n.

Core insight: we already have the hard parts (`jobs`/`job_runs`, the scheduler in
`backend/app/jobs/`, the connector/query/tool registries, the planner with RLS).
What's missing is exactly three things: **a DAG**, **a task-level state machine**,
and **a claim-based worker**. We use **Postgres as the work queue** via
`SELECT ... FOR UPDATE SKIP LOCKED` — no Redis, no Celery.

Mirror these existing modules for style and structure:
- `backend/app/dashboards/spec.py`  → declarative spec + `validate_spec` + json schema
- `backend/app/jobs/store.py`        → `InMemory*Store` + `Pg*Store` + provider singleton
- `backend/app/jobs/schedule.py`     → clock-free core, explicit `now`
- `backend/app/jobs/runtime.py`      → asyncio tick loop lifecycle
- `backend/app/routes/jobs.py`       → org-scoped REST, `_get_user_org`, serializers
- `backend/app/ai/tools.py`          → `ToolDef` registry
- `database/migrations/0007_jobs.sql`→ migration DDL style (forward-only)

Conventions that are NON-NEGOTIABLE:
- All core logic takes an explicit `now: datetime` — **never** call
  `datetime.now()` inside core scheduling/state logic (tests inject the clock).
- Stores: `InMemory*` (tests) + `Pg*` (asyncpg, `$N` params, `_row_to_*` coercion)
  + module-level `get_*_store()` / `set_*_store()` singleton provider.
- Everything org-scoped; cross-org access returns 404.
- RLS: data tasks pass `claims` to `app.connectors.planner.plan(...)`.
- New deps: none for backend. Frontend uses `reactflow` (already in package.json).

---

## The FlowSpec (declarative DAG — the single source of truth)

JSON document, version 1. Mirrors `DashboardSpec`. Pydantic v2 models in
`backend/app/flows/spec.py`.

```jsonc
{
  "version": 1,
  "name": "daily_revenue",
  "params": [
    { "name": "region", "type": "text", "default": "us", "required": false }
    // type ∈ text|number|date|daterange|select|multiselect  (reuse query param types)
  ],
  "tasks": [
    {
      "key": "pull",                 // unique slug within the flow
      "kind": "query",               // query|python|agent|noop
      "needs": [],                   // upstream task keys (edges)
      "config": { "query_id": "demo_all" },
      "retries": 0,                  // int >= 0
      "retry_backoff_s": 30,         // seconds between attempts
      "timeout_s": 60,               // per-attempt timeout
      "cache_ttl_s": 0,              // 0 = no caching; >0 = memoize by cache_key
      "ui": { "x": 0, "y": 0 }       // builder canvas position (ignored by engine)
    },
    {
      "key": "enrich", "kind": "python", "needs": ["pull"],
      "config": { "code": "result = {'rows': inputs['pull']['row_count']}" }
    },
    {
      "key": "summary", "kind": "agent", "needs": ["enrich"],
      "config": { "prompt": "Summarize the enriched result.", "max_steps": 4 }
    }
  ]
}
```

### Per-kind `config`
- `query`:  `{ query_id?: str, sql?: str, named_params?: {..} }` — one of query_id|sql required.
- `python`: `{ code: str }` — runs via the SAME path `jobs/executor.py` uses
  (`LocalSubprocessRunner`); `inputs` (upstream results) and `params` are injected
  as variables; the task result is the value bound to `result`.
- `agent`:  `{ prompt: str, max_steps?: int }` — calls `app.ai.agent.run_agent`
  with `app.ai.provider.get_provider()` and the caller's `claims`. NullProvider
  keeps it deterministic in tests.
- `noop`:   `{}` — pass-through; used as explicit fan-in/fan-out join nodes.

### Templating
Strings inside `config` and `params` may contain `{{ ... }}` referencing
`params.<name>` and `inputs.<task_key>...`. Reuse
`app.connectors.planner.resolve_named_params` / the Jinja template helper where
practical; minimally support `{{ params.x }}` and `{{ inputs.k.field }}`.

### validate_flow_spec(data) -> (FlowSpec | None, list[str])
Steps (mirror `validate_spec`):
1. Pydantic parse → on failure return `(None, [issues...])`.
2. Task `key` uniqueness (hard error).
3. Every `needs` references a declared task key (hard error).
4. **DAG is acyclic** — topological sort; on cycle report the cycle (hard error).
5. Kind-specific config required fields (hard error): query→query_id|sql,
   python→code, agent→prompt.
6. `query_id` checked against the live query registry (soft warning).
"Hard error" = present in issues AND callers treat as invalid; soft = warning only.
Provide `flow_spec_is_valid(issues) -> bool` helper (valid = no hard errors).

### flow_spec_json_schema() -> dict
`FlowSpec.model_json_schema()` — used to ground the LLM author tool.

---

## Database — `database/migrations/0012_flows.sql`

Forward-only, `CREATE TABLE IF NOT EXISTS`, match `0007_jobs.sql` style.

```sql
CREATE TABLE IF NOT EXISTS flows (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      uuid NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    created_by  uuid NOT NULL REFERENCES users(id) ON DELETE SET NULL,
    name        text NOT NULL,
    spec        jsonb NOT NULL,
    version     integer NOT NULL DEFAULT 1,
    enabled     boolean NOT NULL DEFAULT true,
    schedule    text,                       -- optional "interval:Ns" | cron | NULL
    next_run_at timestamptz,
    last_run_at timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS flows_org_id_idx ON flows (org_id);

CREATE TABLE IF NOT EXISTS flow_runs (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    flow_id      uuid NOT NULL REFERENCES flows(id) ON DELETE CASCADE,
    org_id       uuid NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    state        text NOT NULL DEFAULT 'pending'
                 CHECK (state IN ('pending','running','success','failed','cancelled')),
    params       jsonb NOT NULL DEFAULT '{}'::jsonb,
    trigger      text NOT NULL DEFAULT 'manual'
                 CHECK (trigger IN ('manual','schedule','event','agent')),
    scheduled_at timestamptz,
    started_at   timestamptz,
    finished_at  timestamptz,
    error        text,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS flow_runs_flow_id_idx ON flow_runs (flow_id);
CREATE INDEX IF NOT EXISTS flow_runs_state_idx ON flow_runs (state);

CREATE TABLE IF NOT EXISTS task_runs (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    flow_run_id  uuid NOT NULL REFERENCES flow_runs(id) ON DELETE CASCADE,
    org_id       uuid NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    task_key     text NOT NULL,
    state        text NOT NULL DEFAULT 'pending'
                 CHECK (state IN ('pending','ready','running','success','failed','retrying','skipped')),
    attempt      integer NOT NULL DEFAULT 0,
    depends_on   text[] NOT NULL DEFAULT '{}',
    cache_key    text,
    result       jsonb,
    error        text,
    scheduled_at timestamptz,
    started_at   timestamptz,
    finished_at  timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS task_runs_flow_run_id_idx ON task_runs (flow_run_id);
CREATE INDEX IF NOT EXISTS task_runs_claim_idx ON task_runs (state, scheduled_at);
```

State machines:
- `flow_run.state`: pending → running → success | failed | cancelled
- `task_run.state`: pending → ready → running → success | failed | retrying | skipped
- A task is **ready** when every `depends_on` task_run is `success`.
- If any upstream is `failed`/`skipped`, the downstream task → `skipped`.
- Flow run is `success` when all task_runs terminal and none failed; `failed` if any failed.

---

## Store — `backend/app/flows/store.py`

Mirror `jobs/store.py` exactly (InMemory + Pg + `get_flow_store()`/`set_flow_store()`).
All dicts JSON-serialisable; datetimes tz-aware UTC; uuids as strings.

```
FlowStore interface (both InMemory + Pg implement; Pg methods are async):
  # flows
  create_flow(org_id, created_by, name, spec: dict, enabled=True,
              schedule=None, next_run_at=None) -> flow
  get_flow(flow_id) -> flow | None
  list_flows(org_id) -> list[flow]
  update_flow(flow_id, fields: dict) -> flow | None
  delete_flow(flow_id) -> bool
  # flow runs
  create_flow_run(flow_id, org_id, params, trigger, scheduled_at=None) -> flow_run
  get_flow_run(run_id) -> flow_run | None
  list_flow_runs(flow_id) -> list[flow_run]      # newest first
  update_flow_run(run_id, fields: dict) -> flow_run | None
  # task runs
  add_task_runs(flow_run_id, task_runs: list[dict]) -> list[dict]   # bulk insert
  list_task_runs(flow_run_id) -> list[task_run]  # by created_at then task_key
  get_task_run(task_run_id) -> task_run | None
  update_task_run(task_run_id, fields: dict) -> task_run | None
  claim_ready_task_run(now) -> task_run | None
      # InMemory: return+mark 'running' the oldest task_run with state='ready'
      #   and scheduled_at <= now (or null). Pg: FOR UPDATE SKIP LOCKED claim.
```

Flow shape: `{id, org_id, created_by, name, spec(dict), version, enabled,
schedule, next_run_at, last_run_at, created_at, updated_at}`.
FlowRun shape: `{id, flow_id, org_id, state, params(dict), trigger,
scheduled_at, started_at, finished_at, error, created_at}`.
TaskRun shape: `{id, flow_run_id, org_id, task_key, state, attempt,
depends_on(list[str]), cache_key, result(dict|None), error, scheduled_at,
started_at, finished_at, created_at}`.

---

## Engine — `backend/app/flows/registry.py`, `executor.py`, `runtime.py`

### registry.py — task-kind registry (mirror `connectors/registry.py`)
`TaskKindRegistry.register(kind, handler)`, `.get(kind)`, `.all()`; pre-register
`query|python|agent|noop`. `reset_for_tests()`. Handler signature:
`handler(config: dict, ctx: TaskContext, claims: dict) -> dict` returning the
task result dict (JSON-serialisable).

### executor.py — `execute_task(...)`
```
@dataclass TaskContext: flow_params: dict; inputs: dict[task_key, result]; now: datetime
execute_task(task: dict, ctx: TaskContext, claims: dict) -> dict
  # resolves {{ }} templating in config using ctx, dispatches to the kind handler,
  # returns {"state": "success"|"failed", "result": {...}|None, "error": str|None}.
  # Honor timeout_s. Broad try/except → failed + error message (mirror execute_job).
```
Handlers reuse existing machinery:
- query  → `app.connectors.planner.plan(...)` + DuckDB (copy the pattern in
  `app/ai/tools.py::_tool_run_query`, including `_seed_demo_table`), RLS via claims.
- python → reuse the `LocalSubprocessRunner` path from `jobs/executor.py`; inject
  `inputs`, `params`; capture `result`.
- agent  → `run_agent(messages, get_provider(), claims, max_steps)`; result =
  `{"reply": ..., "actions": [...]}`.
- noop   → `{"inputs": ctx.inputs}`.

### runtime.py — materializer + worker + tick (clock-free core; explicit `now`)
```
materialize_flow_run(store, flow, params, trigger, now) -> flow_run
  # validate flow["spec"] via validate_flow_spec; create flow_run (state='running');
  # insert one task_run per task (depends_on from needs); mark root tasks (no needs)
  # 'ready' (scheduled_at=now), others 'pending'. Return the flow_run.

advance_readiness(store, flow_run_id, now) -> None
  # for each 'pending' task: if all depends_on succeeded → 'ready';
  # if any dep failed/skipped → 'skipped'. Then if all tasks terminal,
  # finalize flow_run state (success/failed) + finished_at.

run_one_ready_task(store, now, claims) -> task_run | None
  # claim_ready_task_run(now); if none return None. Build TaskContext from
  # upstream results; cache check (cache_ttl_s); execute_task; on success →
  # 'success' + result; on failure → if attempt < retries: 'retrying' +
  # scheduled_at = now + retry_backoff_s + bump attempt; else 'failed'.
  # Then advance_readiness. Return the updated task_run.

drain_flow_run(store, flow_run_id, now, claims, max_steps=200) -> flow_run
  # loop run_one_ready_task until no ready tasks remain in this flow_run or
  # max_steps hit. Used by POST /flows/{id}/run for synchronous execution.

flow_tick(store, now, claims=None) -> dict
  # (a) materialize due scheduled flows (flows with schedule and next_run_at<=now),
  #     advancing next_run_at via app.jobs.schedule.next_run; (b) drain a bounded
  #     number of ready task_runs across all running flow_runs. Returns a summary.
```
Also add lifecycle `start_flow_worker(app)` / `stop_flow_worker()` mirroring
`jobs/runtime.py` (gated by a new setting `FLOWS_WORKER_ENABLED: bool=False`,
`FLOWS_WORKER_INTERVAL_S: int=5` in `app/config.py`). Keep it OFF in tests.

---

## API — `backend/app/routes/flows.py` (mirror `routes/jobs.py`)

Org-scoped, `current_user`, replicate the local `_get_user_org` helper (do NOT
import from resources — same circular-import note as jobs.py). Register via
`api_router.include_router(router)` with `prefix="/flows"`.

```
POST   /flows                 {name, spec}            -> 201 flow   (validate spec; 400 on hard errors)
GET    /flows                                          -> [flow]
GET    /flows/{id}                                     -> flow      (404 cross-org)
PUT    /flows/{id}            {name?, spec?, enabled?, schedule?} -> flow
DELETE /flows/{id}                                     -> 204
POST   /flows/validate        {spec}                  -> {valid, issues}
POST   /flows/{id}/run        {params?}               -> flow_run + {task_runs:[...]}  (synchronous drain)
GET    /flows/{id}/runs                                -> [flow_run]
GET    /flows/runs/{run_id}                            -> flow_run + {task_runs:[...]} (for live polling)
```
Serializers `_serialize_flow/_serialize_run/_serialize_task_run` with `_dt_iso`
(copy from jobs.py). `POST /flows/{id}/run` resolves the caller's `claims`
(see how `app/routes/ai.py` builds claims) and calls
`materialize_flow_run` then `drain_flow_run`.

---

## AI tools — `backend/app/ai/flow_tools.py` + register in `app/ai/tools.py`

New `ToolDef`s (each `fn(claims, **kwargs) -> dict`, JSON-schema'd like existing):
- `list_flows` → `{flows:[{id,name}]}`
- `create_flow` `{name, spec}` → validate + `get_flow_store().create_flow(...)`;
  return `{id, valid, issues}`. (Sync store calls — tests use InMemory.)
- `run_flow` `{flow_id, params?}` → materialize + drain; return
  `{flow_run_id, state, task_runs:[{task_key,state}]}`.
- `get_flow_run` `{flow_run_id}` → `{state, task_runs:[...]}`.
- `generate_flow` `{question}` → NL → FlowSpec. With NullProvider, return a
  deterministic 2-task demo flow (pull demo_all → summarize) so it's testable.
In `tools.py`, import these and extend `_make_registry()`'s tool list (only this
file's `_make_registry` is touched here).

Org note: the AI tools operate via `get_flow_store()`; resolve org from
`claims` (claims carry `org_id` — confirm by reading `app/routes/ai.py`).

---

## Frontend — React Flow DAG builder (like Prefect)

Uses `reactflow` (already a dependency). Files:
- `src/lib/flows.js` — API client mirroring `src/lib/api.js` style (import the
  `get/post/put/del` helpers from `./api.js`): `listFlows, getFlow, createFlow,
  updateFlow, deleteFlow, runFlow, listFlowRuns, getFlowRun, validateFlow`.
- `src/flows/specGraph.js` — pure converters:
  `specToGraph(spec) -> {nodes, edges}` and `graphToSpec(nodes, edges, meta) -> spec`.
  Node id = task key; edges from `needs`. Node position from `task.ui` (fallback
  to an auto dagre-less grid layout). Include unit-ish tests as `.test.mjs` if cheap.
- `src/flows/nodes/TaskNode.jsx` — custom React Flow node: shows task key, kind
  badge, and a status dot colored by `task_run.state`
  (pending=slate, ready=blue, running=amber pulse, success=green, failed=red,
  skipped=gray, retrying=orange). Has source+target handles.
- `src/flows/NodeInspector.jsx` — right-hand panel to edit the selected task:
  key, kind (select), needs (derived from edges, read-only), and a kind-specific
  config form (query_id/sql, python code via existing `@monaco-editor/react`,
  agent prompt). Retries/timeout/cache fields.
- `src/flows/FlowBuilder.jsx` — the canvas: `<ReactFlow>` with node palette
  (add query/python/agent/noop), drag to connect (creates `needs` edge), select
  to inspect, "Validate" (calls `validateFlow`, shows issues), "Save"
  (create/update), "Run" (calls `runFlow`, then switches to run view).
- `src/flows/FlowRunView.jsx` — read-only DAG colored live by task_run states;
  polls `getFlowRun(runId)` every ~1.5s until terminal; shows per-task result/error
  on click; overall run state banner.
- `src/pages/app/FlowsPage.jsx` — list of flows (left), and the builder/run
  tabs (right). "New flow" seeds an empty spec. Route `/flows` and `/flows/:id`.

Design: match the existing app shell aesthetic (tailwind, lucide-react icons).
Make it feel like Prefect/n8n: clean canvas, rounded nodes, subtle grid bg,
MiniMap + Controls from reactflow, a node palette, and an inspector drawer.

---

## Integration wiring (owner: orchestrator, done after agents land)
- `backend/main.py`: `import app.routes.flows` (before resources catch-all);
  optionally `start_flow_worker` in lifespan when `FLOWS_WORKER_ENABLED`.
- `backend/app/config.py`: add `FLOWS_WORKER_ENABLED`, `FLOWS_WORKER_INTERVAL_S`.
- `backend/tests/conftest.py`: reset `set_flow_store(InMemoryFlowStore())` in
  `_reset_state` (autouse).
- `src/App.jsx`: add `/flows` + `/flows/:id` routes (authed shell).
- App shell sidebar: add a "Flows" nav item.
- Run `database/migrate.py` if a DB is available.

## Test expectations (each backend module ships pytest tests, in-memory, deterministic)
- `tests/test_flows_spec.py` — validate: good spec, cycle, missing dep, bad kind config.
- `tests/test_flows_store.py` — InMemory CRUD + claim_ready_task_run ordering.
- `tests/test_flows_engine.py` — materialize → drain a 3-task linear flow to success;
  a failing task marks downstream skipped + flow failed; retries path.
- `tests/test_flows_api.py` — POST/GET/run/runs endpoints, 404 cross-org, 401 no-auth
  (copy harness from `tests/test_jobs.py`).
- `tests/test_flow_tools.py` — create_flow/run_flow/get_flow_run via NullProvider.
Run the suite with: `cd backend && python -m pytest tests/test_flows*.py tests/test_flow_tools.py -q`.
