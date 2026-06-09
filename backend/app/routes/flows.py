"""Flows REST endpoints — workflow orchestrator API.

Endpoints
---------
POST   /flows                   {name, spec}                -> 201 flow
GET    /flows                                                -> [flow]
GET    /flows/{id}                                           -> flow (404 cross-org)
PUT    /flows/{id}              {name?, spec?, enabled?, schedule?} -> flow
DELETE /flows/{id}                                           -> 204
POST   /flows/validate          {spec}                       -> {valid, issues}
POST   /flows/{id}/run          {params?}                    -> flow_run + {task_runs:[...]}
GET    /flows/{id}/runs                                      -> [flow_run]
GET    /flows/runs/{run_id}                                  -> flow_run + {task_runs:[...]}
POST   /flows/blend             {name,sources,combine_sql,…} -> {flow, materialized:{datastore_id,query_id}}
POST   /flows/tick              (X-Nubi-Tick-Secret header)  -> {materialised, tasks_run}
POST   /flows/codegen           {spec}                       -> {source: str}
POST   /flows/{id}/codegen                                   -> {source: str}

Notebook / cell endpoints (added by NotebookSpec sprint)
---------------------------------------------------------
POST   /flows/preview           {spec|flow_id, cell_key?, params, preview_limit} -> {columns, rows, row_count, cell_key}
POST   /flows/run-cell          {spec|flow_id, cell_key?, params}                -> {columns, rows, row_count, flow_run_id}
POST   /flows/notebooks         {notebook: NotebookSpec, name?}                  -> 201 flow
GET    /flows/notebooks/{id}                                                     -> flow + {notebook: NotebookSpec}

All endpoints EXCEPT ``/flows/tick`` require a valid first-party Bearer token
(``current_user``).  ``/flows/tick`` is an internal endpoint authed via a
shared-secret header (``X-Nubi-Tick-Secret`` matching ``FLOWS_TICK_SECRET``) so
Google Cloud Scheduler can drive the engine on Cloud Run (no always-on worker).
Flows are org-scoped: callers can only see and operate on flows belonging to
their own org.  Cross-org access returns 404 (no information leak).

Organisation resolution
-----------------------
Replicated from ``routes/jobs.py`` to avoid the circular import that arises
when importing ``get_user_org`` from ``routes.resources``.

Flow store
----------
All flow state is held in an ``InMemoryFlowStore`` (singleton via
``get_flow_store()``).  Tests may inject their own store via
``set_flow_store(store)`` before issuing requests.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel

from app.auth.deps import current_user
from app.auth.roles import require_writer_default
from app.config import get_settings
from app.db import fetchrow
from app.errors import AppError
from app.flows.runtime import drain_flow_run, flow_tick, materialize_flow_run
from app.flows.spec import flow_spec_is_valid, validate_flow_spec
from app.flows.store import get_flow_store
from app.repos.provider import Repo, get_repo
from app.routes import api_router

# ---------------------------------------------------------------------------
# Sub-router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/flows", tags=["flows"])


# ---------------------------------------------------------------------------
# Org resolution helper (replicated from routes/jobs.py to avoid the
# circular import that would arise if we imported from resources here, which
# causes resources.py's module-level api_router.include_router() to fire
# before our own, putting the generic /{resource} catch-all ahead of /flows).
# ---------------------------------------------------------------------------


async def _get_user_org(user_id: str, repo: Repo) -> str:
    """Return the org_id for the user's first membership.

    Mirrors ``routes.resources.get_user_org`` without importing it.
    """
    if hasattr(repo, "get_org_for_user"):
        org_id = repo.get_org_for_user(user_id)  # type: ignore[attr-defined]
        if org_id:
            return org_id
        raise AppError("org_not_found", "User has no org membership.", 404)

    row = await fetchrow(
        """
        SELECT org_id FROM org_members
        WHERE user_id = $1::uuid
        ORDER BY org_id
        LIMIT 1
        """,
        user_id,
    )
    if row is None:
        raise AppError("org_not_found", "User has no org membership.", 404)
    return str(row["org_id"])


async def _resolve_project_id(org_id: str, requested: str | None) -> str | None:
    """Resolve the project a new flow belongs to.

    Honours ``X-Project-Id`` when valid for *org_id*, else falls back to the
    org's default project. Returns ``None`` when no default exists (e.g. test
    doubles without a projects table).
    """
    from app.repos import projects as projects_repo  # noqa: PLC0415

    requested = (requested or "").strip()
    if requested and await projects_repo.project_belongs_to_org(requested, org_id):
        return requested
    return await projects_repo.get_default_project_id(org_id)


# ---------------------------------------------------------------------------
# Pydantic request schemas
# ---------------------------------------------------------------------------


class CreateFlowIn(BaseModel):
    name: str
    spec: dict[str, Any]
    schedule: str | None = None
    enabled: bool = True


class UpdateFlowIn(BaseModel):
    name: str | None = None
    spec: dict[str, Any] | None = None
    enabled: bool | None = None
    schedule: str | None = None


class ValidateFlowIn(BaseModel):
    spec: dict[str, Any]


class RunFlowIn(BaseModel):
    params: dict[str, Any] = {}


class CodegenSpecIn(BaseModel):
    """Request body for ``POST /flows/codegen`` (inline spec variant).

    Accepts a raw FlowSpec dict and returns generated Python SDK source.
    """

    spec: dict[str, Any]


class CompileCodeIn(BaseModel):
    """Request body for ``POST /flows/compile``.

    Accepts nubi.flows Python SDK source code and returns the compiled
    FlowSpec dict by tracing the code in a sandboxed subprocess.
    """

    code: str


class PreviewCellIn(BaseModel):
    """Request body for ``POST /flows/preview``.

    Runs a single cell (or all cells up-to-and-including *cell_key*) in
    **interactive / preview mode** — DuckDB in-process, row-capped, fast.
    The execution never touches the durable work-pool or task store.

    Supply EITHER ``spec`` (inline NotebookSpec/FlowSpec dict) OR
    ``flow_id`` (a persisted flow); ``cell_key`` selects the target cell.
    When ``cell_key`` is omitted, ALL cells are executed in order.

    The ``params`` dict overrides flow-level param defaults for this run.
    ``preview_limit`` caps the returned rows (default 500, max 10 000).

    Returns ``{columns, rows, row_count, cell_key}``.
    """

    spec: dict[str, Any] | None = None
    flow_id: str | None = None
    cell_key: str | None = None
    params: dict[str, Any] = {}
    preview_limit: int = 500
    mode: str = "preview"  # reserved for future modes; currently always "preview"


class RunCellIn(BaseModel):
    """Request body for ``POST /flows/run-cell``.

    Runs a single cell durably: creates a temporary single-cell flow run
    through the normal work-pool path and returns ``{columns, rows, row_count}``.

    Supply EITHER ``spec`` (inline) OR ``flow_id`` + ``cell_key``.
    When running a specific cell from a persisted flow, all upstream
    dependencies are also included so the cell has its ``inputs`` resolved.
    """

    spec: dict[str, Any] | None = None
    flow_id: str | None = None
    cell_key: str | None = None
    params: dict[str, Any] = {}


class NotebookSaveIn(BaseModel):
    """Request body for ``POST /flows/notebooks``.

    Save-or-create a notebook (NotebookSpec → FlowSpec) as a persisted flow.
    Returns the created/updated flow.
    """

    notebook: dict[str, Any]  # NotebookSpec dict
    name: str | None = None  # override notebook.name


class ScheduledQueryIn(BaseModel):
    """Request body for ``POST /flows/scheduled-query``.

    Builds a single-task flow that runs one saved query on a schedule — the
    clean contract behind the frontend "Schedule this query" action.
    """

    name: str
    query_id: str
    schedule: str
    params: dict[str, Any] = {}


class BlendSourceIn(BaseModel):
    """One source of a materialized blend.

    Each source becomes a single-source ``query`` task (so per-source predicate
    pushdown + RLS stay intact).  Provide ``query_id`` (a registered query) OR
    ``sql`` (ad-hoc SELECT).  ``datastore_id`` optionally binds the source to a
    specific connector; ``named_params`` overrides query params.
    """

    key: str
    query_id: str | None = None
    sql: str | None = None
    datastore_id: str | None = None
    named_params: dict[str, Any] = {}


class CreateBlendIn(BaseModel):
    """Request body for ``POST /flows/blend``.

    Materialized multi-source blend: fans out to N source queries, merges them
    in DuckDB via ``combine_sql``, and materializes the combined result to a
    cheap single-source dataset that dashboards read.  The blend runs once
    immediately (to materialize) and, if ``schedule`` is given, on a schedule
    thereafter.
    """

    name: str
    sources: list[BlendSourceIn]
    combine_sql: str
    schedule: str | None = None
    rls_keys: list[str] = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dt_iso(dt: datetime | None) -> str | None:
    """Convert a datetime to ISO-8601 string, or None."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _serialize_flow(flow: dict[str, Any]) -> dict[str, Any]:
    """Convert a flow dict to a JSON-serialisable form."""
    return {
        "id": flow["id"],
        "org_id": flow["org_id"],
        "created_by": flow["created_by"],
        "name": flow["name"],
        "spec": flow["spec"],
        "version": flow["version"],
        "enabled": flow["enabled"],
        "schedule": flow.get("schedule"),
        "next_run_at": _dt_iso(flow.get("next_run_at")),
        "last_run_at": _dt_iso(flow.get("last_run_at")),
        "created_at": _dt_iso(flow.get("created_at")),
        "updated_at": _dt_iso(flow.get("updated_at")),
    }


def _serialize_flow_run(run: dict[str, Any]) -> dict[str, Any]:
    """Convert a flow_run dict to a JSON-serialisable form."""
    return {
        "id": run["id"],
        "flow_id": run["flow_id"],
        "org_id": run["org_id"],
        "state": run["state"],
        "params": run.get("params", {}),
        "trigger": run["trigger"],
        "scheduled_at": _dt_iso(run.get("scheduled_at")),
        "started_at": _dt_iso(run.get("started_at")),
        "finished_at": _dt_iso(run.get("finished_at")),
        "error": run.get("error"),
        "created_at": _dt_iso(run.get("created_at")),
    }


def _serialize_task_run(tr: dict[str, Any]) -> dict[str, Any]:
    """Convert a task_run dict to a JSON-serialisable form."""
    # Duration in seconds (None if not started or not finished).
    started = tr.get("started_at")
    finished = tr.get("finished_at")
    duration_s: float | None = None
    if started and finished:
        try:
            delta = finished - started
            duration_s = delta.total_seconds()
        except Exception:  # noqa: BLE001
            pass

    return {
        "id": tr["id"],
        "flow_run_id": tr["flow_run_id"],
        "org_id": tr["org_id"],
        "task_key": tr["task_key"],
        "state": tr["state"],
        "attempt": tr.get("attempt", 0),
        "depends_on": tr.get("depends_on", []),
        "cache_key": tr.get("cache_key"),
        "result": tr.get("result"),
        "error": tr.get("error"),
        "logs": tr.get("logs") or [],
        "duration_s": duration_s,
        "scheduled_at": _dt_iso(tr.get("scheduled_at")),
        "started_at": _dt_iso(tr.get("started_at")),
        "finished_at": _dt_iso(tr.get("finished_at")),
        "created_at": _dt_iso(tr.get("created_at")),
    }


def _compute_next_run_at(schedule: str | None, now: datetime) -> datetime | None:
    """Return the next run time for *schedule*, or None when there is no schedule.

    Raises ``AppError("bad_schedule", 400)`` (propagated from
    ``app.jobs.schedule.next_run``) when the schedule string is invalid.
    """
    if not schedule:
        return None
    from app.jobs.schedule import next_run  # noqa: PLC0415

    return next_run(schedule, now)


async def _require_flow_in_org(
    flow_id: str,
    org_id: str,
    store: Any,
) -> dict[str, Any]:
    """Return the flow if it exists and belongs to *org_id*, else raise 404."""
    flow = await store.get_flow(flow_id)
    if flow is None or str(flow["org_id"]) != str(org_id):
        raise AppError("not_found", "Flow not found.", 404)
    return flow


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

# NOTE: /flows/validate and /flows/runs/{run_id} are registered BEFORE the
# parameterised /{id} routes so FastAPI doesn't treat "validate" or "runs" as
# a flow id.


@router.post("/validate", status_code=200)
async def validate_flow(
    body: ValidateFlowIn,
    _user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """Validate a flow spec without persisting it.

    Returns ``{valid: bool, issues: list[str]}``.
    """
    _spec, issues = validate_flow_spec(body.spec)
    valid = flow_spec_is_valid(issues)
    return {"valid": valid, "issues": issues}


@router.post("/scheduled-query", status_code=201, dependencies=[Depends(require_writer_default)])
async def create_scheduled_query(
    body: ScheduledQueryIn,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
    x_project_id: str | None = Header(default=None, alias="X-Project-Id"),
) -> dict[str, Any]:
    """Create a scheduled flow that runs a single saved query on a schedule.

    This is a convenience wrapper around ``POST /flows``: it builds a one-task
    flow spec (a single ``query`` task referencing ``query_id``), validates it
    with the shared validator, and creates the flow enabled with ``schedule``
    set (and ``next_run_at`` computed) so the flow tick picks it up.

    Returns the created flow in the same shape as ``POST /flows`` (201).
    """
    org_id = await _get_user_org(str(user["id"]), repo)

    # Build a 1-task flow spec: a single `query` task referencing query_id.
    task_config: dict[str, Any] = {"query_id": body.query_id}
    if body.params:
        task_config["params"] = dict(body.params)

    spec_data: dict[str, Any] = {
        "version": 1,
        "name": body.name,
        "tasks": [
            {
                "key": "query",
                "kind": "query",
                "needs": [],
                "config": task_config,
            }
        ],
    }

    spec, issues = validate_flow_spec(spec_data)
    if not flow_spec_is_valid(issues):
        hard = [i for i in issues if not i.startswith("[warn]")]
        raise AppError("bad_flow_spec", "; ".join(hard), 400)

    now = datetime.now(timezone.utc)
    next_run_at = _compute_next_run_at(body.schedule, now)

    project_id = await _resolve_project_id(org_id, x_project_id)

    store = get_flow_store()
    flow = await store.create_flow(
        org_id=org_id,
        created_by=str(user["id"]),
        name=body.name,
        spec=spec.model_dump() if spec is not None else spec_data,
        enabled=True,
        schedule=body.schedule,
        next_run_at=next_run_at,
        project_id=project_id,
    )
    return _serialize_flow(flow)


@router.post("/blend", status_code=201, dependencies=[Depends(require_writer_default)])
async def create_blend(
    body: CreateBlendIn,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
    x_project_id: str | None = Header(default=None, alias="X-Project-Id"),
) -> dict[str, Any]:
    """Create a MATERIALIZED multi-source blend (and run it once immediately).

    A blend is a scheduled flow that fans out to N single-source ``query`` tasks
    (per-source predicate pushdown + RLS preserved), merges them in DuckDB via
    ``combine_sql``, and materializes the combined result to a cheap
    single-source DuckDB dataset that dashboards read (cached + pushdown-able).
    The expensive multi-source join runs on a SCHEDULE, never per dashboard view
    — this preserves the cost wedge (materialize-then-serve, NOT federation).

    RLS contract
    ------------
    ``rls_keys`` (e.g. ``["tenant_id"]``) MUST survive the merge: the combined
    table keeps those columns so the planner can inject
    ``WHERE tenant_id = <claim>`` at READ time on the materialized source.  The
    materialize step verifies this and fails (400 ``rls_key_dropped``) if a
    declared key was flattened away.

    Returns
    -------
    dict
        ``{flow, materialized: {datastore_id, query_id}}``.  The frontend binds
        a widget to ``materialized.query_id``.
    """
    from app.flows.materialize import (  # noqa: PLC0415
        DEFAULT_BLEND_TABLE,
        blend_database_path,
        build_blend_spec,
    )

    org_id = await _get_user_org(str(user["id"]), repo)
    project_id = await _resolve_project_id(org_id, x_project_id)

    if not body.sources:
        raise AppError("bad_blend", "A blend requires at least one source.", 400)
    for src in body.sources:
        if not src.query_id and not src.sql:
            raise AppError(
                "bad_blend",
                f"Blend source {src.key!r} requires 'query_id' or 'sql'.",
                400,
            )

    # ── 1. Pre-create the datastore + query rows the blend is served through.
    # The DuckDB file path is keyed by the datastore id so each blend is isolated.
    datastore = await repo.create(
        "datastores",
        org_id=org_id,
        created_by=str(user["id"]),
        name=f"{body.name} (blend)",
        config={"type": "duckdb", "database": ""},  # database filled in below
        project_id=project_id,
    )
    datastore_id = str(datastore["id"])
    database = blend_database_path(datastore_id)

    # Persist the resolved database path on the datastore config so the read
    # path (routes/query.py) opens the materialized file.
    await repo.update(
        "datastores",
        org_id=org_id,
        id=datastore_id,
        fields={"config": {"type": "duckdb", "database": database}},
    )

    query_row = await repo.create(
        "queries",
        org_id=org_id,
        created_by=str(user["id"]),
        name=f"{body.name} (blend)",
        config={
            "sql": f'SELECT * FROM "{DEFAULT_BLEND_TABLE}"',
            "datastore_id": datastore_id,
            "params": [],
            "name": f"{body.name} (blend)",
        },
        project_id=project_id,
    )
    query_id = str(query_row["id"])

    # ── 2. Build + validate the blend flow spec.
    spec_data = build_blend_spec(
        name=body.name,
        sources=[s.model_dump() for s in body.sources],
        combine_sql=body.combine_sql,
        rls_keys=body.rls_keys,
        table=DEFAULT_BLEND_TABLE,
        database=database,
        datastore_id=datastore_id,
        query_id=query_id,
    )
    spec, issues = validate_flow_spec(spec_data)
    if not flow_spec_is_valid(issues):
        hard = [i for i in issues if not i.startswith("[warn]")]
        raise AppError("bad_flow_spec", "; ".join(hard), 400)

    now = datetime.now(timezone.utc)
    next_run_at = _compute_next_run_at(body.schedule, now)

    store = get_flow_store()
    flow = await store.create_flow(
        org_id=org_id,
        created_by=str(user["id"]),
        name=body.name,
        spec=spec.model_dump() if spec is not None else spec_data,
        enabled=True,
        schedule=body.schedule,
        next_run_at=next_run_at,
        project_id=project_id,
    )

    # Register the served query into the runtime registry up-front so a widget
    # can resolve it the moment the first materialization completes (the
    # materialize task also registers it, but doing it here covers the read-
    # path lookup even before the runtime registry is reloaded).
    from app.flows.materialize import register_blend_query  # noqa: PLC0415

    register_blend_query(query_id, database, DEFAULT_BLEND_TABLE, datastore_id)

    # ── 3. Run once immediately to materialize.
    claims: dict[str, Any] = {
        "kind": "access",
        "sub": str(user.get("id", "")),
        "org_id": org_id,
        "policies": {},
        "scope": ["read:*", "write:*"],
    }
    flow_run = await materialize_flow_run(store, flow, {}, "manual", now)
    flow_run = await drain_flow_run(store, flow_run["id"], now, claims=claims)

    task_runs = await store.list_task_runs(flow_run["id"])
    # Surface a hard materialize failure (e.g. rls_key_dropped) to the caller.
    for tr in task_runs:
        if tr.get("task_key") == "blend" and tr.get("state") == "failed":
            raise AppError("blend_materialize_failed", tr.get("error") or "Materialize failed.", 400)

    result = _serialize_flow_run(flow_run)
    result["task_runs"] = [_serialize_task_run(tr) for tr in task_runs]

    return {
        "flow": _serialize_flow(flow),
        "materialized": {"datastore_id": datastore_id, "query_id": query_id},
        "run": result,
    }


@router.post("/tick", status_code=200)
async def flows_tick(
    x_nubi_tick_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    """Run ONE flow tick (internal — for Google Cloud Scheduler on Cloud Run).

    Authenticated via a shared-secret header (``X-Nubi-Tick-Secret``) that must
    match the ``FLOWS_TICK_SECRET`` setting — NOT a user JWT.  This replaces the
    always-on worker on Cloud Run (which throttles CPU + scales to zero): Cloud
    Scheduler POSTs here on cron, and each call runs one ``flow_tick`` which
    (a) materializes due scheduled flows (atomic claim → multi-instance safe)
    and (b) drains a bounded number of ready task_runs.

    Returns ``{materialised, tasks_run}``.
    """
    settings = get_settings()
    secret = getattr(settings, "FLOWS_TICK_SECRET", "") or ""
    if not secret:
        raise AppError(
            "tick_not_configured",
            "FLOWS_TICK_SECRET is not set; the /flows/tick endpoint is disabled.",
            503,
        )
    if not x_nubi_tick_secret or x_nubi_tick_secret != secret:
        raise AppError("unauthorized", "Invalid or missing X-Nubi-Tick-Secret.", 401)

    store = get_flow_store()
    now = datetime.now(timezone.utc)
    summary = await flow_tick(store, now, claims=None)
    return summary


@router.get("/runs/{run_id}", status_code=200)
async def get_flow_run_by_id(
    run_id: str,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Get a flow_run by run_id, including its task_runs.

    Returns ``flow_run + {task_runs: [...]}`` for live polling.
    Each task_run includes ``logs``, ``error``, ``attempt``, ``duration_s``.
    Returns 404 if the run does not exist or belongs to a different org.
    """
    org_id = await _get_user_org(str(user["id"]), repo)
    store = get_flow_store()
    run = await store.get_flow_run(run_id)
    if run is None or str(run["org_id"]) != str(org_id):
        raise AppError("not_found", "Flow run not found.", 404)

    task_runs = await store.list_task_runs(run_id)
    result = _serialize_flow_run(run)
    result["task_runs"] = [_serialize_task_run(tr) for tr in task_runs]
    return result


@router.get("/runs/{run_id}/tasks/{task_key}/logs", status_code=200)
async def get_task_run_logs(
    run_id: str,
    task_key: str,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Get the captured logs for a specific task_run.

    Returns ``{task_key, state, attempt, logs: list[str], error}``
    for the most recent task_run with the given task_key within this flow_run.
    Returns 404 if the run or task does not exist or belongs to a different org.
    """
    org_id = await _get_user_org(str(user["id"]), repo)
    store = get_flow_store()
    run = await store.get_flow_run(run_id)
    if run is None or str(run["org_id"]) != str(org_id):
        raise AppError("not_found", "Flow run not found.", 404)

    task_runs = await store.list_task_runs(run_id)
    # Find the task_run with the matching key (there may be only one per key per run).
    matching = [tr for tr in task_runs if tr["task_key"] == task_key]
    if not matching:
        raise AppError("not_found", f"Task '{task_key}' not found in this flow run.", 404)

    tr = matching[-1]  # most recent (last inserted) by created_at ordering
    return {
        "task_key": tr["task_key"],
        "state": tr["state"],
        "attempt": tr.get("attempt", 0),
        "logs": tr.get("logs") or [],
        "error": tr.get("error"),
    }


@router.post("/codegen", status_code=200)
async def codegen_from_spec(
    body: CodegenSpecIn,
    _user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """Generate Python SDK scaffold source from an inline FlowSpec dict.

    Validates the spec first (returns 400 on hard errors), then runs
    :func:`~app.flows.codegen.flow_spec_to_sdk` and returns the generated
    source string.

    This endpoint does NOT persist anything — it is a pure transformation
    from FlowSpec JSON to Python source code.

    Request body
    ------------
    ``{"spec": { ...FlowSpec dict... }}``

    Returns
    -------
    ``{"source": "<python source code>"}``

    Example
    -------
    .. code-block:: http

        POST /flows/codegen
        Content-Type: application/json

        {
          "spec": {
            "version": 1,
            "name": "my_flow",
            "params": [],
            "tasks": [
              {
                "key": "pull",
                "kind": "query",
                "needs": [],
                "config": {"sql": "SELECT 1"},
                "retries": 0, "retry_backoff_s": 30,
                "timeout_s": 60, "cache_ttl_s": 0,
                "ui": {"x": 0, "y": 0}
              }
            ]
          }
        }

    Returns::

        {
          "source": "# Auto-generated scaffold ...\\n\\nfrom nubi.flows import ..."
        }
    """
    from app.flows.codegen import flow_spec_to_sdk  # noqa: PLC0415

    spec, issues = validate_flow_spec(body.spec)
    if not flow_spec_is_valid(issues):
        hard = [i for i in issues if not i.startswith("[warn]")]
        raise AppError("bad_flow_spec", "; ".join(hard), 400)

    if spec is None:
        raise AppError("bad_flow_spec", "Spec could not be parsed.", 400)

    source = flow_spec_to_sdk(spec)
    return {"source": source, "issues": issues}


@router.post("/compile", status_code=200)
async def compile_code(
    body: CompileCodeIn,
    _user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """Compile nubi.flows Python SDK source to a FlowSpec dict.

    Executes the caller-supplied Python code in a **sandboxed subprocess**
    (same pattern as the ``'python'`` task handler in
    ``app/flows/registry._handle_python``).  The subprocess runs the source,
    calls ``.compile()`` on the ``@flow``-decorated function it finds, and
    prints the resulting FlowSpec as a JSON sentinel line on stdout.

    The main process never ``exec``s or ``eval``s the source directly — it only
    spawns ``sys.executable`` with a tempfile and reads the stdout sentinel.

    Security
    --------
    - Source is written to a NamedTemporaryFile (cleaned up in ``finally``).
    - Only a minimal environment (``PATH``, ``PYTHONPATH``, ``HOME``, site
      packages) is forwarded so the subprocess can import nubi.flows.
    - Execution is bounded by a hard 15-second timeout.

    Request body
    ------------
    ``{"code": "<nubi.flows Python source>"}``

    Returns
    -------
    ``{"spec": { ...FlowSpec dict... }, "issues": [...]}``

    Raises
    ------
    400 ``compile_error``
        When the subprocess exits non-zero, times out, or produces no
        valid FlowSpec sentinel.

    Example
    -------
    .. code-block:: http

        POST /flows/compile
        Content-Type: application/json

        {
          "code": "from nubi.flows import flow, task\\n\\n@task(kind=\\"noop\\")\\ndef step(): pass\\n\\n@flow\\ndef my_flow():\\n    step()\\n\\nspec = my_flow.compile()\\n"
        }

    Returns::

        {
          "spec": {
            "version": 1,
            "name": "my_flow",
            "params": [],
            "tasks": [{"key": "step", "kind": "noop", ...}]
          },
          "issues": []
        }
    """
    import json as _json  # noqa: PLC0415
    import os  # noqa: PLC0415
    import subprocess  # noqa: PLC0415
    import sys  # noqa: PLC0415
    import tempfile  # noqa: PLC0415
    import textwrap  # noqa: PLC0415

    code: str = (body.code or "").strip()
    if not code:
        raise AppError("compile_error", "No code provided.", 400)

    # ---------------------------------------------------------------------------
    # Build the subprocess wrapper.
    #
    # The wrapper:
    # 1. Executes the user's source (which defines @task / @flow stubs and
    #    calls .compile()).
    # 2. Looks for a variable named ``spec`` in the exec namespace — that is
    #    the conventional name produced by the codegen scaffold.
    # 3. Prints the spec as ``__FLOW_SPEC__:<json>`` on stdout.
    #
    # We intentionally do NOT inspect or mutate ``spec`` in-process; the entire
    # point is that the user's code runs isolated in the subprocess.
    # ---------------------------------------------------------------------------

    wrapper = textwrap.dedent(f"""\
        import json as _json
        import sys as _sys

        # ── User source ──────────────────────────────────────────────────────
{textwrap.indent(code, '        ')}
        # ── End user source ──────────────────────────────────────────────────

        # Locate the compiled spec: the scaffold codegen assigns it to `spec`.
        try:
            _spec_val = spec  # noqa: F821
        except NameError:
            _sys.stderr.write("compile_error: no `spec` variable found after executing source.\\n")
            _sys.exit(1)

        # Accept both Pydantic model dumps and plain dicts.
        if hasattr(_spec_val, "model_dump"):
            _spec_dict = _spec_val.model_dump()
        elif hasattr(_spec_val, "dict"):
            _spec_dict = _spec_val.dict()
        elif isinstance(_spec_val, dict):
            _spec_dict = _spec_val
        else:
            _sys.stderr.write(f"compile_error: `spec` must be a dict or FlowSpec, got {{type(_spec_val).__name__}}\\n")
            _sys.exit(1)

        print("__FLOW_SPEC__:" + _json.dumps(_spec_dict))
    """)

    # Build a safe, minimal environment — mirrors _handle_python in registry.py.
    env: dict[str, str] = {}
    for _key in (
        "PATH", "PYTHONPATH", "HOME", "TMPDIR", "TEMP", "TMP",
        "LANG", "LC_ALL", "LC_CTYPE", "VIRTUAL_ENV",
    ):
        _val = os.environ.get(_key)
        if _val is not None:
            env[_key] = _val

    # Ensure the nubi package (backend/nubi/) is importable inside the subprocess.
    # We compute the backend root from the location of this file:
    # backend/app/routes/flows.py → strip 3 levels → backend/
    import pathlib  # noqa: PLC0415
    _backend_root = str(pathlib.Path(__file__).resolve().parent.parent.parent)
    site_paths = [p for p in sys.path if p and "site-packages" in p]
    existing_pp = env.get("PYTHONPATH", "")
    combined_pp = ":".join(filter(None, [_backend_root, existing_pp] + site_paths))
    if combined_pp:
        env["PYTHONPATH"] = combined_pp

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as _tmp:
        _tmp.write(wrapper)
        _tmp_path = _tmp.name

    try:
        proc = subprocess.run(
            [sys.executable, _tmp_path],
            capture_output=True,
            text=True,
            timeout=15,
            env=env,
        )
    except subprocess.TimeoutExpired:
        try:
            os.unlink(_tmp_path)
        except OSError:
            pass
        raise AppError("compile_error", "Compile timed out after 15 seconds.", 400)
    finally:
        try:
            os.unlink(_tmp_path)
        except OSError:
            pass

    # Parse sentinel line from stdout.
    spec_dict: dict[str, Any] | None = None
    for _line in (proc.stdout or "").splitlines():
        if _line.startswith("__FLOW_SPEC__:"):
            try:
                spec_dict = _json.loads(_line[len("__FLOW_SPEC__:"):])
            except Exception:  # noqa: BLE001
                spec_dict = None
            break

    if proc.returncode != 0 or spec_dict is None:
        stderr = (proc.stderr or "").strip()
        msg = stderr[:600] if stderr else "No FlowSpec produced by compile()."
        raise AppError("compile_error", msg, 400)

    # Validate the compiled spec so we surface structural errors immediately.
    _spec, issues = validate_flow_spec(spec_dict)
    hard_issues = [i for i in issues if not i.startswith("[warn]")]
    if hard_issues:
        raise AppError("compile_error", "; ".join(hard_issues), 400)

    return {
        "spec": _spec.model_dump() if _spec is not None else spec_dict,
        "issues": issues,
    }


@router.post("/preview", status_code=200)
async def preview_cell(
    body: PreviewCellIn,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Run a notebook cell (or cells up-to-cell) in interactive/preview mode.

    **Fast path** — runs entirely in-process using DuckDB (no work-pool, no
    task store).  Row output is capped at ``preview_limit`` (default 500,
    max 10 000) to keep latency low and warehouse costs at zero.

    Provide EITHER ``spec`` (inline FlowSpec/NotebookSpec dict) OR
    ``flow_id`` (a persisted flow).  ``cell_key`` selects which cell to
    run; when omitted the last cell in the spec is used.

    All upstream cells in the dependency chain are executed first so the
    target cell has access to ``inputs`` from each of them.

    RLS is preserved: the same ``claims`` object used by ``run_flow`` is
    passed to each cell's handler, so row-level policies are enforced on
    every warehouse connector call.

    Returns
    -------
    ``{columns: list[str], rows: list[dict], row_count: int, cell_key: str}``

    Raises
    ------
    400 ``bad_request``
        When neither ``spec`` nor ``flow_id`` is supplied, or ``cell_key``
        does not name a task in the resolved spec.
    400 ``cell_execution_failed``
        When the target cell raises an exception during preview execution.
    """
    from app.flows.executor import TaskContext, execute_task  # noqa: PLC0415

    org_id = await _get_user_org(str(user["id"]), repo)

    # ── 1. Resolve the spec ────────────────────────────────────────────────
    spec_data: dict[str, Any] | None = None

    if body.spec is not None:
        spec_data = body.spec
    elif body.flow_id is not None:
        store = get_flow_store()
        flow = await _require_flow_in_org(body.flow_id, org_id, store)
        spec_data = flow.get("spec") or {}
    else:
        raise AppError("bad_request", "Supply 'spec' or 'flow_id'.", 400)

    spec, issues = validate_flow_spec(spec_data)
    if not flow_spec_is_valid(issues):
        hard = [i for i in issues if not i.startswith("[warn]")]
        raise AppError("bad_flow_spec", "; ".join(hard), 400)

    if spec is None or not spec.tasks:
        raise AppError("bad_request", "Spec has no tasks.", 400)

    # ── 2. Determine target cell ───────────────────────────────────────────
    cell_key: str = body.cell_key or spec.tasks[-1].key

    # Build a key→index map; collect all tasks that are upstream dependencies
    # of the target cell (topological order, inclusive).
    task_map: dict[str, Any] = {t.key: t for t in spec.tasks}
    if cell_key not in task_map:
        raise AppError(
            "bad_request",
            f"cell_key {cell_key!r} is not a task in this spec. "
            f"Available keys: {[t.key for t in spec.tasks]}",
            400,
        )

    # Walk DAG to collect tasks needed for this cell (inclusive, topo order).
    def _collect_ancestors(key: str, visited: set[str]) -> list[str]:
        if key in visited:
            return []
        visited.add(key)
        task = task_map.get(key)
        if task is None:
            return []
        result: list[str] = []
        for dep in task.needs:
            result.extend(_collect_ancestors(dep, visited))
        result.append(key)
        return result

    ordered_keys = _collect_ancestors(cell_key, set())
    tasks_to_run = [task_map[k] for k in ordered_keys]

    # ── 3. Build RLS claims ────────────────────────────────────────────────
    claims: dict[str, Any] = {
        "kind": "access",
        "sub": str(user.get("id", "")),
        "org_id": org_id,
        "policies": {},
        "scope": ["read:*", "write:*"],
    }

    # ── 4. Resolve preview_limit ───────────────────────────────────────────
    preview_limit: int = max(1, min(body.preview_limit, 10_000))

    # ── 5. Execute cells in order, collecting inputs ───────────────────────
    inputs: dict[str, Any] = {}
    now = datetime.now(timezone.utc)

    for task in tasks_to_run:
        # Inject preview_limit into query task config so the handler respects it.
        task_config = dict(task.config)
        if task.kind == "query" and "preview_limit" not in task_config:
            task_config["preview_limit"] = preview_limit

        ctx = TaskContext(
            flow_params=body.params,
            inputs=inputs,
            now=now,
            secrets={},
        )

        task_dict: dict[str, Any] = {
            "key": task.key,
            "kind": task.kind,
            "config": task_config,
            "timeout_s": task.timeout_s,
            "retries": task.retries,
            "retry_backoff_s": task.retry_backoff_s,
            "cache_ttl_s": task.cache_ttl_s,
        }

        exec_result = execute_task(task_dict, ctx, claims)

        if exec_result["state"] not in ("success",):
            if task.key == cell_key:
                raise AppError(
                    "cell_execution_failed",
                    exec_result.get("error") or f"Cell {cell_key!r} failed.",
                    400,
                )
            # Non-target upstream cell failure — still provide partial inputs.
            # The target cell may still succeed if it doesn't depend on this cell.
        else:
            inputs[task.key] = exec_result.get("result") or {}

    # ── 6. Extract result from target cell ────────────────────────────────
    target_result = inputs.get(cell_key) or {}
    raw_rows: list[dict[str, Any]] = target_result.get("rows") or []
    columns: list[str] = target_result.get("columns") or (
        list(raw_rows[0].keys()) if raw_rows else []
    )

    # Cap rows at preview_limit.
    capped_rows = raw_rows[:preview_limit]

    return {
        "cell_key": cell_key,
        "columns": columns,
        "rows": capped_rows,
        "row_count": len(capped_rows),
        "total_row_count": len(raw_rows),
    }


@router.post("/run-cell", status_code=200)
async def run_cell(
    body: RunCellIn,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Run a single notebook cell durably via the work-pool runtime.

    Builds a temporary single-task flow spec (containing the target cell
    and all its upstream dependencies) and runs it synchronously via
    ``drain_flow_run``, exactly as ``POST /flows/{id}/run`` does.

    Provide EITHER ``spec`` (inline) OR ``flow_id`` + ``cell_key``.
    When only ``flow_id`` is given without ``cell_key``, the last task
    in the spec is used.

    Returns
    -------
    ``{columns, rows, row_count, cell_key}`` extracted from the target
    cell's task_run result, plus ``{flow_run_id}`` for log polling.
    """
    from app.flows.spec import validate_flow_spec, flow_spec_is_valid  # noqa: PLC0415

    org_id = await _get_user_org(str(user["id"]), repo)

    # ── 1. Resolve spec ────────────────────────────────────────────────────
    spec_data: dict[str, Any] | None = None

    if body.spec is not None:
        spec_data = body.spec
    elif body.flow_id is not None:
        store_ref = get_flow_store()
        flow = await _require_flow_in_org(body.flow_id, org_id, store_ref)
        spec_data = flow.get("spec") or {}
    else:
        raise AppError("bad_request", "Supply 'spec' or 'flow_id'.", 400)

    spec, issues = validate_flow_spec(spec_data)
    if not flow_spec_is_valid(issues):
        hard = [i for i in issues if not i.startswith("[warn]")]
        raise AppError("bad_flow_spec", "; ".join(hard), 400)

    if spec is None or not spec.tasks:
        raise AppError("bad_request", "Spec has no tasks.", 400)

    cell_key: str = body.cell_key or spec.tasks[-1].key

    task_map: dict[str, Any] = {t.key: t for t in spec.tasks}
    if cell_key not in task_map:
        raise AppError(
            "bad_request",
            f"cell_key {cell_key!r} not found. "
            f"Available keys: {[t.key for t in spec.tasks]}",
            400,
        )

    # ── 2. Build a trimmed spec with only needed tasks ─────────────────────
    def _collect_ancestors(key: str, visited: set[str]) -> list[str]:
        if key in visited:
            return []
        visited.add(key)
        task = task_map.get(key)
        if task is None:
            return []
        result_keys: list[str] = []
        for dep in task.needs:
            result_keys.extend(_collect_ancestors(dep, visited))
        result_keys.append(key)
        return result_keys

    ordered_keys = _collect_ancestors(cell_key, set())
    tasks_to_run = [task_map[k].model_dump() for k in ordered_keys]

    trimmed_spec_data: dict[str, Any] = {
        "version": spec_data.get("version", 1),
        "name": f"{spec_data.get('name', 'notebook')}__cell_{cell_key}",
        "params": spec_data.get("params", []),
        "tasks": tasks_to_run,
    }

    # ── 3. Create a transient flow, run it, return result ─────────────────
    claims: dict[str, Any] = {
        "kind": "access",
        "sub": str(user.get("id", "")),
        "org_id": org_id,
        "policies": {},
        "scope": ["read:*", "write:*"],
    }

    store = get_flow_store()
    now = datetime.now(timezone.utc)

    transient_flow = await store.create_flow(
        org_id=org_id,
        created_by=str(user["id"]),
        name=trimmed_spec_data["name"],
        spec=trimmed_spec_data,
        enabled=False,
        schedule=None,
        next_run_at=None,
        project_id=None,
    )

    try:
        flow_run = await materialize_flow_run(store, transient_flow, body.params, "manual", now)
        flow_run = await drain_flow_run(store, flow_run["id"], now, claims=claims)

        task_runs = await store.list_task_runs(flow_run["id"])

        # Extract the target cell's result.
        target_tr = next(
            (tr for tr in task_runs if tr["task_key"] == cell_key), None
        )
        if target_tr is None or target_tr.get("state") != "success":
            error_msg = (target_tr or {}).get("error") or "Cell execution failed."
            raise AppError("cell_execution_failed", error_msg, 400)

        cell_result = target_tr.get("result") or {}
        rows: list[dict[str, Any]] = cell_result.get("rows") or []
        columns: list[str] = cell_result.get("columns") or (
            list(rows[0].keys()) if rows else []
        )

        return {
            "cell_key": cell_key,
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "flow_run_id": flow_run["id"],
            "task_runs": [_serialize_task_run(tr) for tr in task_runs],
        }
    finally:
        # Clean up the transient flow to avoid polluting the store.
        try:
            await store.delete_flow(transient_flow["id"])
        except Exception:  # noqa: BLE001
            pass


@router.post("/notebooks", status_code=201, dependencies=[Depends(require_writer_default)])
async def save_notebook(
    body: NotebookSaveIn,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
    x_project_id: str | None = Header(default=None, alias="X-Project-Id"),
) -> dict[str, Any]:
    """Save (create or update) a notebook as a persisted flow.

    Accepts a ``NotebookSpec`` dict, compiles it to a ``FlowSpec`` via
    ``notebook_to_flow()``, validates the result, and persists it as a flow.

    If ``notebook.notebook_id`` names an existing flow in this org, the
    flow is UPDATED (PUT semantics).  Otherwise a new flow is created (POST
    semantics, 201).

    Returns the serialised flow in the same shape as ``POST /flows``.
    """
    from app.flows.notebook import NotebookSpec, notebook_to_flow  # noqa: PLC0415

    org_id = await _get_user_org(str(user["id"]), repo)

    # Parse the NotebookSpec.
    try:
        nb = NotebookSpec.model_validate(body.notebook)
    except Exception as exc:  # noqa: BLE001
        raise AppError("bad_notebook_spec", str(exc), 400)

    # Override name if caller supplied one.
    if body.name:
        nb = nb.model_copy(update={"name": body.name})

    # Compile to FlowSpec.
    flow_spec = notebook_to_flow(nb, infer_edges=(nb.view == "notebook"))
    spec_data = flow_spec.model_dump()

    spec, issues = validate_flow_spec(spec_data)
    if not flow_spec_is_valid(issues):
        hard = [i for i in issues if not i.startswith("[warn]")]
        raise AppError("bad_flow_spec", "; ".join(hard), 400)

    project_id = await _resolve_project_id(org_id, x_project_id)
    store = get_flow_store()

    # Update if notebook_id references an existing flow.
    notebook_id = (nb.notebook_id or "").strip()
    if notebook_id:
        existing = await store.get_flow(notebook_id)
        if existing and str(existing["org_id"]) == str(org_id):
            updated = await store.update_flow(
                notebook_id,
                {"name": nb.name, "spec": spec.model_dump() if spec else spec_data},
            )
            if updated is not None:
                return _serialize_flow(updated)

    # Create new.
    flow = await store.create_flow(
        org_id=org_id,
        created_by=str(user["id"]),
        name=nb.name,
        spec=spec.model_dump() if spec is not None else spec_data,
        enabled=True,
        schedule=None,
        next_run_at=None,
        project_id=project_id,
    )
    return _serialize_flow(flow)


@router.get("/notebooks/{flow_id}", status_code=200)
async def get_notebook(
    flow_id: str,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Fetch a persisted flow and return it as a NotebookSpec dict.

    Returns the serialised flow augmented with a ``notebook`` key containing
    the ``NotebookSpec`` representation of the flow (cells with ``cell_type``
    and ``execution_mode`` fields derived from task kinds).

    Returns 404 if the flow does not exist or belongs to a different org.
    """
    from app.flows.notebook import flow_to_notebook  # noqa: PLC0415
    from app.flows.spec import validate_flow_spec  # noqa: PLC0415

    org_id = await _get_user_org(str(user["id"]), repo)
    store = get_flow_store()
    flow = await _require_flow_in_org(flow_id, org_id, store)

    spec_data = flow.get("spec") or {}
    spec, _ = validate_flow_spec(spec_data)

    notebook_dict: dict[str, Any] = {}
    if spec is not None:
        nb = flow_to_notebook(spec, notebook_id=flow_id)
        notebook_dict = nb.model_dump()

    result = _serialize_flow(flow)
    result["notebook"] = notebook_dict
    return result


@router.post("", status_code=201, dependencies=[Depends(require_writer_default)])
async def create_flow(
    body: CreateFlowIn,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
    x_project_id: str | None = Header(default=None, alias="X-Project-Id"),
) -> dict[str, Any]:
    """Create a new flow.

    Validates the spec; returns 400 on hard errors.
    Returns 201 with the created flow on success.

    The flow is scoped to the project named by ``X-Project-Id`` when valid for
    the org, else the org's default project.
    """
    org_id = await _get_user_org(str(user["id"]), repo)

    spec, issues = validate_flow_spec(body.spec)
    if not flow_spec_is_valid(issues):
        hard = [i for i in issues if not i.startswith("[warn]")]
        raise AppError("bad_flow_spec", "; ".join(hard), 400)

    now = datetime.now(timezone.utc)
    next_run_at = _compute_next_run_at(body.schedule, now)

    project_id = await _resolve_project_id(org_id, x_project_id)

    store = get_flow_store()
    flow = await store.create_flow(
        org_id=org_id,
        created_by=str(user["id"]),
        name=body.name,
        spec=spec.model_dump() if spec is not None else body.spec,
        enabled=body.enabled,
        schedule=body.schedule,
        next_run_at=next_run_at,
        project_id=project_id,
    )
    return _serialize_flow(flow)


@router.get("", status_code=200)
async def list_flows(
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> list[dict[str, Any]]:
    """List all flows for the caller's org."""
    org_id = await _get_user_org(str(user["id"]), repo)
    store = get_flow_store()
    flows = await store.list_flows(org_id)
    return [_serialize_flow(f) for f in flows]


@router.get("/{flow_id}", status_code=200)
async def get_flow(
    flow_id: str,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Get a single flow by ID.

    Returns 404 if the flow does not exist or belongs to a different org.
    """
    org_id = await _get_user_org(str(user["id"]), repo)
    store = get_flow_store()
    flow = await _require_flow_in_org(flow_id, org_id, store)
    return _serialize_flow(flow)


@router.put("/{flow_id}", status_code=200, dependencies=[Depends(require_writer_default)])
async def update_flow(
    flow_id: str,
    body: UpdateFlowIn,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Update a flow's name, spec, enabled status, or schedule.

    Validates the spec if provided; returns 400 on hard errors.
    Returns 404 if the flow does not exist or belongs to a different org.
    """
    org_id = await _get_user_org(str(user["id"]), repo)
    store = get_flow_store()
    await _require_flow_in_org(flow_id, org_id, store)

    fields: dict[str, Any] = {}
    if body.name is not None:
        fields["name"] = body.name
    if body.enabled is not None:
        fields["enabled"] = body.enabled

    # ``schedule`` is special: it must be possible both to *set* a schedule and
    # to *clear* it (schedule=null).  We treat the field as "explicitly
    # provided" only when it was present in the request body, then recompute
    # next_run_at so the flow tick picks up (or stops picking up) this flow.
    if "schedule" in body.model_fields_set:
        fields["schedule"] = body.schedule
        now = datetime.now(timezone.utc)
        fields["next_run_at"] = _compute_next_run_at(body.schedule, now)

    if body.spec is not None:
        spec, issues = validate_flow_spec(body.spec)
        if not flow_spec_is_valid(issues):
            hard = [i for i in issues if not i.startswith("[warn]")]
            raise AppError("bad_flow_spec", "; ".join(hard), 400)
        fields["spec"] = spec.model_dump() if spec is not None else body.spec

    updated = await store.update_flow(flow_id, fields)
    if updated is None:
        raise AppError("not_found", "Flow not found.", 404)
    return _serialize_flow(updated)


@router.delete("/{flow_id}", status_code=204, dependencies=[Depends(require_writer_default)])
async def delete_flow(
    flow_id: str,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> Response:
    """Delete a flow and all its runs.

    Returns 204 on success; 404 if the flow does not exist or is cross-org.
    """
    org_id = await _get_user_org(str(user["id"]), repo)
    store = get_flow_store()
    await _require_flow_in_org(flow_id, org_id, store)
    await store.delete_flow(flow_id)
    return Response(status_code=204)


@router.post("/{flow_id}/run", status_code=200, dependencies=[Depends(require_writer_default)])
async def run_flow(
    flow_id: str,
    body: RunFlowIn = RunFlowIn(),
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Run a flow synchronously (drain all tasks).

    Materialises a flow_run, drains all ready tasks to completion, and returns
    the flow_run dict with a ``task_runs`` array.

    Returns 404 if the flow does not exist or belongs to a different org.
    """
    org_id = await _get_user_org(str(user["id"]), repo)
    store = get_flow_store()
    flow = await _require_flow_in_org(flow_id, org_id, store)

    # ── BILLING: flow task execution consumes compute units ──────────────────
    # Enforce the org's compute-unit quota before draining (the executor
    # meters each task against the same counters).  No-op in OSS builds; on
    # FREE (no overage billing) an exhausted quota hard-stops with 402.
    from app.features import enforce_quota  # noqa: PLC0415

    await enforce_quota(org_id, "compute_units", amount=1.0)

    # Build first-party claims (mirror routes/ai.py pattern).
    claims: dict[str, Any] = {
        "kind": "access",
        "sub": str(user.get("id", "")),
        "org_id": org_id,
        "policies": {},
        "scope": ["read:*", "write:*"],
    }

    now = datetime.now(timezone.utc)

    flow_run = await materialize_flow_run(store, flow, body.params, "manual", now)
    flow_run = await drain_flow_run(store, flow_run["id"], now, claims=claims)

    task_runs = await store.list_task_runs(flow_run["id"])
    result = _serialize_flow_run(flow_run)
    result["task_runs"] = [_serialize_task_run(tr) for tr in task_runs]
    return result


@router.get("/{flow_id}/runs", status_code=200)
async def list_flow_runs(
    flow_id: str,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> list[dict[str, Any]]:
    """List all runs for a flow (newest first).

    Returns 404 if the flow does not exist or belongs to a different org.
    """
    org_id = await _get_user_org(str(user["id"]), repo)
    store = get_flow_store()
    await _require_flow_in_org(flow_id, org_id, store)
    runs = await store.list_flow_runs(flow_id)
    return [_serialize_flow_run(r) for r in runs]


@router.post("/{flow_id}/codegen", status_code=200)
async def codegen_flow(
    flow_id: str,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Generate Python SDK scaffold source from a persisted flow's current spec.

    Fetches the flow by ``flow_id`` (org-scoped), runs
    :func:`~app.flows.codegen.flow_spec_to_sdk` on its spec, and returns the
    generated Python source string.  This is the inverse of ``compile()``:
    it turns the canonical FlowSpec IR back into editable SDK Python.

    Returns 404 if the flow does not exist or belongs to a different org.

    Returns
    -------
    ``{"source": "<python source code>", "flow_id": "<id>", "flow_name": "<name>"}``
    """
    from app.flows.codegen import flow_spec_to_sdk  # noqa: PLC0415

    org_id = await _get_user_org(str(user["id"]), repo)
    store = get_flow_store()
    flow = await _require_flow_in_org(flow_id, org_id, store)

    spec_data = flow.get("spec") or {}
    spec, issues = validate_flow_spec(spec_data)
    if not flow_spec_is_valid(issues):
        hard = [i for i in issues if not i.startswith("[warn]")]
        raise AppError("bad_flow_spec", "; ".join(hard), 400)

    if spec is None:
        raise AppError("bad_flow_spec", "Persisted spec could not be parsed.", 400)

    source = flow_spec_to_sdk(spec)
    return {
        "source": source,
        "flow_id": flow_id,
        "flow_name": flow.get("name", ""),
        "issues": issues,
    }


# ---------------------------------------------------------------------------
# Register on the shared api_router
# ---------------------------------------------------------------------------

api_router.include_router(router)
