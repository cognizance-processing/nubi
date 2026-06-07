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


@router.post("/scheduled-query", status_code=201)
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


@router.post("/blend", status_code=201)
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


@router.post("", status_code=201)
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


@router.put("/{flow_id}", status_code=200)
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


@router.delete("/{flow_id}", status_code=204)
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


@router.post("/{flow_id}/run", status_code=200)
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


# ---------------------------------------------------------------------------
# Register on the shared api_router
# ---------------------------------------------------------------------------

api_router.include_router(router)
