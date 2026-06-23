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
an external scheduler (e.g. a cron machine or scheduled job) can drive the
engine when no always-on worker runs.
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

import asyncio
import hmac
import os
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Sweep / backfill safety caps (env-configurable)
# ---------------------------------------------------------------------------
_SWEEP_TIMEOUT_S: float = float(os.environ.get("SWEEP_TIMEOUT_S", "300"))
_BACKFILL_TIMEOUT_S: float = float(os.environ.get("BACKFILL_TIMEOUT_S", "600"))
_MAX_SWEEP_CELLS: int = int(os.environ.get("MAX_SWEEP_CELLS", "50"))
_MAX_BACKFILL_WINDOWS: int = int(os.environ.get("MAX_BACKFILL_WINDOWS", "500"))
_MAX_WRITEBACK_ROWS: int = int(os.environ.get("NUBI_MAX_WRITEBACK_ROWS", "10000"))

# ---------------------------------------------------------------------------
# Per-org concurrency caps for long-running sweep / backfill operations
# ---------------------------------------------------------------------------
# Each backfill holds a worker for up to _BACKFILL_TIMEOUT_S (600 s).  Without
# a per-org cap, a single org can exhaust the entire worker pool with concurrent
# backfills, leaving no interactive capacity for other orgs.
#
# Semaphores are created lazily on first use (one per org) and live for the
# process lifetime.  A non-blocking ``acquire()`` check (try_acquire pattern via
# ``asyncio.wait_for(..., timeout=0)``) gives an instant 429 rather than
# queuing behind the in-flight backfill — callers must retry explicitly.
#
# Sweep has the same 300 s shape; apply the same guard there.
_MAX_CONCURRENT_BACKFILLS_PER_ORG: int = int(
    os.environ.get("MAX_CONCURRENT_BACKFILLS_PER_ORG", "2")
)
_MAX_CONCURRENT_SWEEPS_PER_ORG: int = int(
    os.environ.get("MAX_CONCURRENT_SWEEPS_PER_ORG", "2")
)

# ---------------------------------------------------------------------------
# LRU-capped semaphore registries (LOW memory fix)
# ---------------------------------------------------------------------------
# Plain dict[str, Semaphore] keyed by org_id grows monotonically — one entry
# per distinct org, forever.  In multi-tenant deployments with many short-lived
# trial orgs this is a slow but unbounded memory leak.
#
# Fix: use an LRU-capped OrderedDict (insertion/access order = LRU order).
# When the dict is at capacity and a NEW org_id arrives, we evict the
# least-recently-used entry that is currently IDLE (value == max AND no
# waiters) so an in-use semaphore is never dropped mid-operation.
#
# asyncio is single-threaded so no lock is needed here; all mutations happen
# on the event loop thread.
_MAX_ORG_SEMAPHORES: int = int(os.environ.get("NUBI_MAX_ORG_SEMAPHORES", "4096"))

# OrderedDict preserves LRU order: least-recently-used at the left (first),
# most-recently-used at the right (last).  move_to_end(key) promotes on access.
_backfill_sems: OrderedDict[str, asyncio.Semaphore] = OrderedDict()
_sweep_sems: OrderedDict[str, asyncio.Semaphore] = OrderedDict()


def _sem_is_idle(sem: asyncio.Semaphore, max_value: int) -> bool:
    """Return True when *sem* is fully idle (no slots acquired, no waiters).

    A semaphore is idle when:
    - ``_value == max_value`` (all slots returned)
    - ``_waiters`` is empty or None (no coroutine blocked on acquire)

    We NEVER evict a non-idle semaphore: dropping it while a task holds a slot
    would silently free the slot, letting a second task exceed the per-org cap
    and breaking the 429-on-contention invariant.
    """
    if sem._value != max_value:
        return False
    waiters = getattr(sem, "_waiters", None)
    return not waiters


def _evict_idle_lru(
    registry: "OrderedDict[str, asyncio.Semaphore]",
    max_value: int,
) -> None:
    """Evict the least-recently-used IDLE entry from *registry* (in-place).

    Iterates from the LRU end (left / first) and removes the first idle entry
    found.  If no idle entry exists (all in use) the registry is left unchanged
    — the new entry will still be inserted, temporarily exceeding *_MAX_ORG_SEMAPHORES*
    by one.  This is intentional: we never drop a live semaphore.
    """
    for key in list(registry):  # iterate LRU→MRU
        if _sem_is_idle(registry[key], max_value):
            del registry[key]
            return


def _get_backfill_sem(org_id: str) -> asyncio.Semaphore:
    """Return (creating if needed) the per-org backfill concurrency semaphore.

    On a cache hit the entry is promoted to MRU so recently-active orgs are
    retained longest.  On a cache miss the LRU idle entry is evicted first when
    the registry is at capacity before the new semaphore is inserted.
    """
    sem = _backfill_sems.get(org_id)
    if sem is not None:
        _backfill_sems.move_to_end(org_id)  # promote to MRU
        return sem
    # Cache miss: evict LRU idle entry if at cap, then insert new semaphore.
    if len(_backfill_sems) >= _MAX_ORG_SEMAPHORES:
        _evict_idle_lru(_backfill_sems, _MAX_CONCURRENT_BACKFILLS_PER_ORG)
    sem = asyncio.Semaphore(_MAX_CONCURRENT_BACKFILLS_PER_ORG)
    _backfill_sems[org_id] = sem
    return sem


def _get_sweep_sem(org_id: str) -> asyncio.Semaphore:
    """Return (creating if needed) the per-org sweep concurrency semaphore.

    Same LRU eviction policy as :func:`_get_backfill_sem`.
    """
    sem = _sweep_sems.get(org_id)
    if sem is not None:
        _sweep_sems.move_to_end(org_id)  # promote to MRU
        return sem
    # Cache miss: evict LRU idle entry if at cap, then insert new semaphore.
    if len(_sweep_sems) >= _MAX_ORG_SEMAPHORES:
        _evict_idle_lru(_sweep_sems, _MAX_CONCURRENT_SWEEPS_PER_ORG)
    sem = asyncio.Semaphore(_MAX_CONCURRENT_SWEEPS_PER_ORG)
    _sweep_sems[org_id] = sem
    return sem

# ---------------------------------------------------------------------------
# Single-run wall-clock cap (HIGH resource — unbounded drain on request path)
# ---------------------------------------------------------------------------
# POST /flows/{id}/run, /flows/blend, and /flows/run-cell call drain_flow_run
# on the REQUEST coroutine.  Without a wall-clock bound a pathological flow
# (deep DAG, slow tasks, hot retry loops) pins the worker for thousands of
# seconds.  We mirror the sweep/backfill/provider paths: pass
# ``wall_timeout_s=_RUN_TIMEOUT_S`` into the engine AND wrap the call in an
# outer ``asyncio.wait_for`` as a hard belt-and-braces ceiling.  Either bound
# firing surfaces as ``AppError('run_timeout', 504)``.
_RUN_TIMEOUT_S: float = float(os.environ.get("RUN_TIMEOUT_S", "300"))

# Max byte size of a single sweep param_sets entry (echoed back uncapped in the
# sweep response).  Enforced at parse time by a SweepIn validator -> 422.
_MAX_PARAM_SET_BYTES: int = int(
    os.environ.get("NUBI_MAX_PARAM_SET_BYTES", str(64 * 1024))
)

# ---------------------------------------------------------------------------
# Task-log response cap (MED resource — unbounded log response)
# ---------------------------------------------------------------------------
# GET /flows/runs/{run_id}/tasks/{task_key}/logs returns the captured task log
# lines.  Without a cap a single long-running task can accumulate megabytes of
# logs and return them in one unbounded response.
#
# NUBI_MAX_TASK_LOG_LINES  — max number of log lines returned (default 1 000).
# NUBI_MAX_TASK_LOG_BYTES  — max total byte length of all lines joined
#                            (default 512 KiB).  Whichever limit is hit first
#                            triggers truncation; the response includes a
#                            ``truncated: true`` field and a ``truncated_at``
#                            indicator so callers can detect partial output.
_MAX_TASK_LOG_LINES: int = int(os.environ.get("NUBI_MAX_TASK_LOG_LINES", "1000"))
_MAX_TASK_LOG_BYTES: int = int(os.environ.get("NUBI_MAX_TASK_LOG_BYTES", str(512 * 1024)))

# ---------------------------------------------------------------------------
# Task-run result-blob cap (MED resource — unbounded result in run-detail)
# ---------------------------------------------------------------------------
# GET /flows/runs/{run_id} serialises ALL task_runs including full result blobs.
# Without a cap, a run with large per-task results returns an unbounded payload.
#
# NUBI_MAX_RESULT_BLOB_BYTES — max byte length of a serialised per-task result
#                              included inline in GET /flows/runs/{run_id}.
#                              Results exceeding this are replaced with a metadata
#                              stub ``{result_omitted: true, result_size_bytes: N}``.
#                              Pass ``?include_results=1`` to bypass the cap and
#                              receive the full blob (useful for debuggers / CLI).
#                              Default: 64 KiB.
_MAX_RESULT_BLOB_BYTES: int = int(
    os.environ.get("NUBI_MAX_RESULT_BLOB_BYTES", str(64 * 1024))
)

# ---------------------------------------------------------------------------
# Task-run row cap (HIGH resource — unbounded task_run count in run-detail)
# ---------------------------------------------------------------------------
# GET /flows/runs/{run_id} calls store.list_task_runs(run_id) and serialises
# EVERY task_run.  For map fan-out flows this can return thousands of rows in
# one response.
#
# NUBI_MAX_TASK_RUNS_DEFAULT — default number of task_run rows returned inline
#                              (default 2 000).  When the actual count exceeds
#                              this, the response includes
#                              ``task_runs_truncated: true``.
# NUBI_MAX_TASK_RUNS_CEILING — hard upper bound callers may request via
#                              ``?task_runs_limit=N`` (default 10 000).
_MAX_TASK_RUNS_DEFAULT: int = int(os.environ.get("NUBI_MAX_TASK_RUNS_DEFAULT", "2000"))
_MAX_TASK_RUNS_CEILING: int = int(os.environ.get("NUBI_MAX_TASK_RUNS_CEILING", "10000"))

# ---------------------------------------------------------------------------
# Per-run output cap (MED resource — unbounded outputs in run-history)
# ---------------------------------------------------------------------------
# The run-history endpoint batch-fetches flow_run_outputs for up to 500 runs.
# Without a per-run cap this could return 500 x N rows (e.g. 25 M rows when
# N = 50 000).  This constant mirrors store._MAX_OUTPUTS_PER_RUN — both read
# the same env var so they stay in sync without a cross-import.
_MAX_OUTPUTS_PER_RUN: int = int(os.environ.get("NUBI_MAX_OUTPUTS_PER_RUN", "200"))


def _cap_task_logs(
    logs: list[str],
    max_lines: int = _MAX_TASK_LOG_LINES,
    max_bytes: int = _MAX_TASK_LOG_BYTES,
) -> tuple[list[str], bool]:
    """Return ``(capped_lines, truncated)`` applying line and byte caps.

    Whichever limit is exhausted first terminates the output.  The returned
    bool is ``True`` when at least one line was dropped due to either cap.
    """
    capped: list[str] = []
    total_bytes = 0
    for line in logs:
        if len(capped) >= max_lines:
            return capped, True
        line_bytes = len(line.encode("utf-8", errors="replace"))
        if total_bytes + line_bytes > max_bytes:
            return capped, True
        capped.append(line)
        total_bytes += line_bytes
    return capped, False


def _truncate_result_blob(
    result: Any,
    max_bytes: int = _MAX_RESULT_BLOB_BYTES,
) -> dict[str, Any]:
    """Return either the original result or a metadata stub if it is too large.

    Serialises *result* to JSON once to measure its byte footprint.  When it
    fits within *max_bytes* the raw value is returned unchanged.  When it
    exceeds the cap the original is discarded and a lightweight stub is
    returned instead::

        {"result_omitted": True, "result_size_bytes": <N>}

    Callers that need the full blob should re-request with
    ``?include_results=1``.

    Parameters
    ----------
    result:
        The raw task_run result (any JSON-serialisable value, or ``None``).
    max_bytes:
        Byte cap.  Defaults to ``_MAX_RESULT_BLOB_BYTES``.

    Returns
    -------
    dict
        Either ``{"result": <original>}`` or
        ``{"result_omitted": True, "result_size_bytes": N}``.
    """
    import json as _json  # noqa: PLC0415

    if result is None:
        return {"result": None}

    try:
        serialised = _json.dumps(result, default=str)
        size = len(serialised.encode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        # Non-serialisable result — treat as omitted for safety.
        return {"result_omitted": True, "result_size_bytes": 0}

    if size <= max_bytes:
        return {"result": result}

    return {"result_omitted": True, "result_size_bytes": size}


# ---------------------------------------------------------------------------
# Writeback governance — server-side approval policy
# ---------------------------------------------------------------------------
# When NUBI_WRITEBACK_REQUIRE_APPROVAL=true (or "1"/"yes"), every writeback
# submitted via POST /flows/writeback is forced into approval_required=True
# regardless of what the caller passes.  The caller may only INCREASE
# strictness (opt in), never bypass a server-required gate.
#
# Precedence (highest wins):
#   server policy (NUBI_WRITEBACK_REQUIRE_APPROVAL=true)
#   > caller value (approval_required=True from request body)
#   > caller value (approval_required=False from request body)   ← weakest
#
# This prevents a caller from submitting approval_required=False to
# auto-commit when the org's deployment policy mandates human review.
_WRITEBACK_REQUIRE_APPROVAL: bool = os.environ.get(
    "NUBI_WRITEBACK_REQUIRE_APPROVAL", ""
).strip().lower() in ("1", "true", "yes")


def _enforce_approval_policy(caller_value: bool) -> bool:
    """Return the effective approval_required flag after applying server policy.

    SECURITY (writeback authz): the caller may only INCREASE strictness.
    If the server-wide policy mandates approval, the result is always True
    regardless of what the caller sent.  The caller may always opt IN to
    approval even when the server does not require it.

    Parameters
    ----------
    caller_value:
        The ``approval_required`` value from the request body.

    Returns
    -------
    bool
        ``True`` if approval is required (server policy OR caller opt-in).
        ``False`` only when neither the server policy nor the caller requires it.
    """
    return _WRITEBACK_REQUIRE_APPROVAL or caller_value


def _make_connector_write_fn(org_id: str) -> "Any":
    """Build the REAL ``connector_write_fn`` for the write-back commit path.

    The returned callable performs an ACTUAL physical write through the existing
    pipeline (:func:`app.flows.handlers.connector_write.handle`): it resolves the
    target connector from ``target['connector_id']``, stages the rows as Parquet
    under the server-pinned per-run staging prefix, and loads them into the
    target (promote / bulk / stream).  It returns the REAL ``rows_written``
    reported by the loader layer — never a fabricated count.

    This is wired into ``submit_writeback`` / ``approve_writeback`` (which own the
    RBAC / approval / CAS state gates); this helper only performs the commit-time
    write once those gates have passed.  Dry-run NEVER reaches here — the routes
    short-circuit dry-run to ``dry_run_writeback`` before any record is created.

    Parameters
    ----------
    org_id:
        The verified org that owns the write-back.  Pinned into the
        ``TaskContext`` for per-tenant isolation (the staging prefix and the
        connector lookup are both scoped to this org); it is NEVER taken from
        user-supplied task config.
    """
    from app.flows.executor import TaskContext  # noqa: PLC0415
    from app.flows.handlers import connector_write  # noqa: PLC0415

    def _write(rows: "list[dict[str, Any]]", target: "dict[str, Any]", mode: str) -> "dict[str, Any]":
        # The handler reads the upstream rows from ctx.inputs[<input_key>];
        # we bind them under a stable synthetic key and point the config at it.
        _INPUT_KEY = "_writeback_rows"
        config = {
            "input": _INPUT_KEY,
            "target": {
                "connector_id": str(target.get("connector_id") or ""),
                "object": str(target.get("object") or ""),
            },
            "mode": mode,
        }
        ctx = TaskContext(
            inputs={_INPUT_KEY: {"rows": list(rows)}},
            org_id=org_id,
        )
        # claims carries org context as a fallback; org isolation is server-pinned.
        return connector_write.handle(config, ctx, {"org_id": org_id})

    return _write


from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel, Field, field_validator

from app.auth.deps import current_user, verified_identity
from app.auth.roles import require_approver_default, require_writer_default
from app.auth.verify import VerifiedIdentity
from app.config import get_settings
from app.errors import AppError
from app.flows.runtime import drain_flow_run, flow_tick, materialize_flow_run
from app.flows.spec import flow_spec_is_valid, validate_flow_spec
from app.flows.store import get_flow_store
from app.repos.provider import Repo, get_repo
from app.routes import api_router
from app.routes._org import get_user_org as _get_user_org

# ---------------------------------------------------------------------------
# Sub-router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/flows", tags=["flows"])


# ---------------------------------------------------------------------------
# Org resolution helper — shared, pin-aware (app.routes._org). Imported above as
# ``_get_user_org``; the shared helper honours the API-key org pin, which the
# old local copy did not (cross-tenant data op for API-key callers).
# ---------------------------------------------------------------------------


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
    env: str | None = None


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


# Key under which the flow OWNER's RLS policy snapshot is stashed inside the
# flow spec's free-form ``runtime_config`` dict (SECURITY B2).  ``runtime_config``
# is the only spec field that survives ``validate_flow_spec`` re-validation
# unchanged (FlowSpec ignores unknown top-level keys), and ``spec`` is the only
# JSONB column the flow store lets us update — so this is the least-invasive,
# store-compatible spot to persist the snapshot without a schema migration.
OWNER_POLICIES_KEY = "__owner_policies__"


def _snapshot_owner_policies(
    spec_data: dict[str, Any] | None,
    identity: VerifiedIdentity,
) -> dict[str, Any] | None:
    """Return *spec_data* with the owner's RLS policy snapshot stashed in it.

    SECURITY (B2 — OWNER-POLICY SNAPSHOT): scheduled flow runs drain with
    ``claims=None`` (no caller identity), so without a snapshot a flow query
    cell would apply NO RLS.  We snapshot the creating/enabling identity's RLS
    policies onto the flow (under ``spec.runtime_config[OWNER_POLICIES_KEY]``)
    so the scheduler can run the flow under the OWNER's policies at tick time.

    The snapshot REFRESHES every time the flow is created, edited, or enabled
    (i.e. each time this runs in create_flow / update_flow), so a flow always
    runs under the policies of whoever last persisted it.

    Only RLS policy claims (e.g. ``{"tenant_id": "acme"}``) are stored — these
    are row-filter values, never secrets.  An empty policy set (admin/unscoped
    owner) is stored as ``{}``; the tick path treats that as "no extra filter",
    preserving current behaviour.

    Returns ``None`` when *spec_data* is falsy (nothing to snapshot onto).
    """
    if not spec_data:
        return spec_data
    spec_copy = dict(spec_data)
    runtime_config = dict(spec_copy.get("runtime_config") or {})
    runtime_config[OWNER_POLICIES_KEY] = dict(identity.policies or {})
    spec_copy["runtime_config"] = runtime_config
    return spec_copy


def _strip_owner_policies(spec: Any) -> Any:
    """Return *spec* with ``OWNER_POLICIES_KEY`` removed from runtime_config.

    SECURITY (B2 — single source of truth): the owner's RLS predicate snapshot
    is stashed under ``spec.runtime_config[OWNER_POLICIES_KEY]`` so the
    scheduler tick can run flows under the owner's policies.  That snapshot must
    NEVER reach an API client on ANY path (``_serialize_flow``, the pinned-spec
    branch of ``GET /flows/{id}?env=…``, etc.).  This helper is the ONE place
    that performs the strip; all outbound paths route through it.

    Returns *spec* unchanged when it is not a dict or carries no snapshot.  Does
    NOT mutate the input — a shallow copy is made only when a strip is needed,
    so the live store value is left intact.
    """
    if not spec or not isinstance(spec, dict):
        return spec
    rc = spec.get("runtime_config")
    if rc and isinstance(rc, dict) and OWNER_POLICIES_KEY in rc:
        rc_copy = {k: v for k, v in rc.items() if k != OWNER_POLICIES_KEY}
        return {**spec, "runtime_config": rc_copy}
    return spec


def _serialize_flow(flow: dict[str, Any]) -> dict[str, Any]:
    """Convert a flow dict to a JSON-serialisable form.

    SECURITY: strips ``OWNER_POLICIES_KEY`` from the outbound ``spec`` so the
    owner's RLS predicate snapshot is never exposed to API clients (incl.
    viewer-role org members).  The stored DB value is intentionally left intact
    so the scheduler tick can still read the snapshot at run time.
    """
    spec = _strip_owner_policies(flow["spec"])

    return {
        "id": flow["id"],
        "org_id": flow["org_id"],
        "created_by": flow["created_by"],
        "name": flow["name"],
        "spec": spec,
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
    # Run-level duration in seconds (None until both timestamps exist).
    started = run.get("started_at")
    finished = run.get("finished_at")
    duration_s: float | None = None
    if started and finished:
        try:
            duration_s = (finished - started).total_seconds()
        except Exception:  # noqa: BLE001
            pass

    return {
        "id": run["id"],
        "flow_id": run["flow_id"],
        "org_id": run["org_id"],
        "state": run["state"],
        "params": run.get("params", {}),
        "trigger": run["trigger"],
        "env": run.get("env"),
        "scheduled_at": _dt_iso(run.get("scheduled_at")),
        "started_at": _dt_iso(run.get("started_at")),
        "finished_at": _dt_iso(run.get("finished_at")),
        "duration_s": duration_s,
        "error": run.get("error"),
        "created_at": _dt_iso(run.get("created_at")),
    }


def _serialize_task_run(
    tr: dict[str, Any],
    include_results: bool = False,
) -> dict[str, Any]:
    """Convert a task_run dict to a JSON-serialisable form.

    Parameters
    ----------
    tr:
        The raw task_run dict from the store.
    include_results:
        When ``True`` the full ``result`` blob is included verbatim.
        When ``False`` (default) the blob is passed through
        :func:`_truncate_result_blob`: if it fits within
        ``_MAX_RESULT_BLOB_BYTES`` it is included; otherwise it is replaced
        with ``{"result_omitted": True, "result_size_bytes": N}`` so callers
        can detect the truncation without fetching the whole payload.
    """
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

    raw_result = tr.get("result")
    if include_results:
        result_payload: dict[str, Any] = {"result": raw_result}
    else:
        result_payload = _truncate_result_blob(raw_result)

    serialised: dict[str, Any] = {
        "id": tr["id"],
        "flow_run_id": tr["flow_run_id"],
        "org_id": tr["org_id"],
        "task_key": tr["task_key"],
        "state": tr["state"],
        "attempt": tr.get("attempt", 0),
        "depends_on": tr.get("depends_on", []),
        "cache_key": tr.get("cache_key"),
        "error": tr.get("error"),
        "logs": tr.get("logs") or [],
        "duration_s": duration_s,
        "scheduled_at": _dt_iso(tr.get("scheduled_at")),
        "started_at": _dt_iso(tr.get("started_at")),
        "finished_at": _dt_iso(tr.get("finished_at")),
        "created_at": _dt_iso(tr.get("created_at")),
    }
    # Merge result payload: either {"result": ...} or
    # {"result_omitted": True, "result_size_bytes": N}.
    serialised.update(result_payload)
    return serialised


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


async def _pinned_version_for_env(
    flow: dict[str, Any], org_id: str, env_key: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return ``(pinned_spec, {id, version})`` for *env_key*, or ``(None, None)``.

    Resolves the flow's project (falling back to the org default), looks up the
    environment by key, and follows its pointer to the snapshotted spec.
    Returns ``(None, None)`` when the project, environment, pointer, or version
    is missing — callers then serve/run the draft spec.
    """
    from app.environments.store import get_env_store  # noqa: PLC0415
    from app.routes._org import resolve_org_default_project_id  # noqa: PLC0415

    project_id = flow.get("project_id")
    if not project_id:
        project_id = await resolve_org_default_project_id(org_id)
    if not project_id:
        return None, None

    env_store = get_env_store()
    env = await env_store.get_environment_by_key(str(project_id), env_key)
    if env is None:
        return None, None
    pointer = await env_store.get_pointer("flow", str(flow["id"]), env["id"])
    if pointer is None:
        return None, None
    version = await env_store.get_version_by_id(pointer["version_id"])
    if version is None:
        return None, None
    return version["config"] or {}, {"id": version["id"], "version": version["version"]}


async def _drain_with_timeout(
    store: Any,
    flow_run_id: str,
    now: datetime,
    claims: dict[str, Any] | None,
) -> dict[str, Any]:
    """Drain *flow_run_id* under a hard wall-clock bound.

    SECURITY/RESOURCE (HIGH): every request-path ``drain_flow_run`` call MUST
    route through here so a pathological flow cannot pin the worker.  We mirror
    the sweep/backfill/provider paths: pass ``wall_timeout_s=_RUN_TIMEOUT_S``
    into the engine (so the drain loop self-aborts between steps) AND wrap the
    coroutine in an outer ``asyncio.wait_for`` as a belt-and-braces ceiling for
    a single task that blocks past the loop check.  Either bound firing →
    ``AppError('run_timeout', 504)``.
    """
    try:
        return await asyncio.wait_for(
            drain_flow_run(
                store,
                flow_run_id,
                now,
                claims=claims,
                wall_timeout_s=_RUN_TIMEOUT_S,
            ),
            timeout=_RUN_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        raise AppError(
            "run_timeout",
            f"Flow run exceeded the server wall-clock limit of {_RUN_TIMEOUT_S}s.",
            504,
        )


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


@router.get("/ingest-templates", status_code=200)
async def list_ingest_templates_route(
    _user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """Return the Python-cell ingest starter templates (design §6.4).

    Selectable snippets for the python cell builder: offset-/cursor-paginated
    REST, OAuth token refresh, and since-timestamp incremental.  Each reads
    creds from ``secrets[...]``, stages via ``ctx.staging.write(...)``, and
    returns ``{"rows": …, "watermark": …}``.  Served read-only so the frontend
    presents them without baking copy into ``src/``.
    """
    from app.flows.ingest_templates import list_ingest_templates  # noqa: PLC0415

    return {"templates": list_ingest_templates()}


@router.post("/scheduled-query", status_code=201, dependencies=[Depends(require_writer_default)])
async def create_scheduled_query(
    body: ScheduledQueryIn,
    user: dict[str, Any] = Depends(current_user),
    identity: VerifiedIdentity = Depends(verified_identity),
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

    # SECURITY (B2): this endpoint persists an ENABLED, SCHEDULED flow, so the
    # owner's RLS policies MUST be snapshotted onto the stored spec — otherwise
    # the scheduler drains the query with claims=None → NO RLS → cross-tenant
    # leak on every tick. Snapshot before persist (mirrors create_flow).
    spec_to_store = _snapshot_owner_policies(
        spec.model_dump() if spec is not None else spec_data, identity
    )

    store = get_flow_store()
    flow = await store.create_flow(
        org_id=org_id,
        created_by=str(user["id"]),
        name=body.name,
        spec=spec_to_store,
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
    identity: VerifiedIdentity = Depends(verified_identity),
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

    # SECURITY (B2): a blend is an ENABLED, SCHEDULED flow whose refresh task
    # re-runs on cron with claims=None. Snapshot the owner's RLS policies onto
    # the PERSISTED spec (not just the transient immediate-run claims below), so
    # every scheduled refresh row-filters under the owner's scope.
    spec_to_store = _snapshot_owner_policies(
        spec.model_dump() if spec is not None else spec_data, identity
    )

    store = get_flow_store()
    flow = await store.create_flow(
        org_id=org_id,
        created_by=str(user["id"]),
        name=body.name,
        spec=spec_to_store,
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
        # SECURITY (B2): RLS policies come from the VERIFIED caller identity, not
        # an empty dict — so a flow query cell is row-filtered exactly like the
        # /query endpoint. Empty policies (admin/unscoped caller) → unchanged.
        # NOTE: scheduled runs (flows_tick, claims=None) are a separate path and
        # still need an explicit owner/service policy context (design decision).
        "policies": dict(identity.policies),
        "scope": ["read:*", "write:*"],
    }
    flow_run = await materialize_flow_run(store, flow, {}, "manual", now)
    flow_run = await _drain_with_timeout(store, flow_run["id"], now, claims=claims)

    # Cap task_runs in the response (mirrors GET /flows/runs/{run_id}).
    raw_task_runs = await store.list_task_runs(flow_run["id"], limit=_MAX_TASK_RUNS_CEILING + 1)
    task_runs_total = len(raw_task_runs)
    task_runs_truncated = task_runs_total > _MAX_TASK_RUNS_DEFAULT
    task_runs_page = raw_task_runs[:_MAX_TASK_RUNS_DEFAULT]
    # Surface a hard materialize failure (e.g. rls_key_dropped) to the caller.
    # Check in the capped page; blend is a single task so it always falls within the cap.
    for tr in task_runs_page:
        if tr.get("task_key") == "blend" and tr.get("state") == "failed":
            raise AppError("blend_materialize_failed", tr.get("error") or "Materialize failed.", 400)

    result = _serialize_flow_run(flow_run)
    result["task_runs"] = [_serialize_task_run(tr) for tr in task_runs_page]
    result["task_runs_truncated"] = task_runs_truncated
    result["task_runs_total"] = task_runs_total

    return {
        "flow": _serialize_flow(flow),
        "materialized": {"datastore_id": datastore_id, "query_id": query_id},
        "run": result,
    }


@router.post("/tick", status_code=200)
async def flows_tick(
    x_nubi_tick_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    """Run ONE flow tick (internal — driven by an external scheduler/cron).

    Authenticated via a shared-secret header (``X-Nubi-Tick-Secret``) that must
    match the ``FLOWS_TICK_SECRET`` setting — NOT a user JWT.  This replaces the
    always-on worker on platforms that throttle CPU outside requests or scale
    to zero: an external scheduler (e.g. a cron machine or Fly.io scheduled
    machine) POSTs here on cron, and each call runs one ``flow_tick`` which
    (a) materializes due scheduled flows (atomic claim → multi-instance safe)
    and (b) drains a bounded number of ready task_runs.

    Returns ``{materialised, tasks_run}``.
    """
    settings = get_settings()
    secret = getattr(settings, "FLOWS_TICK_SECRET", "") or ""
    if not secret:
        # SECURITY (tick authz): In production, an unset secret is treated the
        # same as a wrong secret (401) — it provides no information about
        # whether the endpoint is configured, which prevents probing.  In
        # non-production environments (dev/test) we return 503 with a clear
        # diagnostic message so developers immediately know what is missing.
        env = (getattr(settings, "ENV", "") or os.environ.get("ENV", "")).lower()
        if env == "production":
            raise AppError(
                "unauthorized",
                "Invalid or missing X-Nubi-Tick-Secret.",
                401,
            )
        raise AppError(
            "tick_not_configured",
            "FLOWS_TICK_SECRET is not set; the /flows/tick endpoint is disabled.",
            503,
        )
    # Constant-time comparison (B7) — a plain != leaks the secret via timing.
    if not x_nubi_tick_secret or not hmac.compare_digest(x_nubi_tick_secret, secret):
        raise AppError("unauthorized", "Invalid or missing X-Nubi-Tick-Secret.", 401)

    store = get_flow_store()
    now = datetime.now(timezone.utc)
    summary = await flow_tick(store, now, claims=None)
    return summary


@router.get("/runs/{run_id}", status_code=200)
async def get_flow_run_by_id(
    run_id: str,
    include_results: int = Query(default=0, ge=0, le=1),
    task_runs_limit: int = Query(default=0, ge=0),
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Get a flow_run by run_id, including its task_runs.

    Returns ``flow_run + {task_runs: [...]}`` for live polling.
    Each task_run includes ``logs``, ``error``, ``attempt``, ``duration_s``.
    Returns 404 if the run does not exist or belongs to a different org.

    Result blobs
    ------------
    By default large per-task ``result`` blobs are omitted from each
    ``task_run`` entry and replaced with metadata::

        {"result_omitted": true, "result_size_bytes": <N>}

    Small results (≤ ``NUBI_MAX_RESULT_BLOB_BYTES``, default 64 KiB) are
    included verbatim.  Pass ``?include_results=1`` to receive the full
    blob regardless of size (useful for debugging / CLI tooling).

    Task-run row cap
    ----------------
    The response includes at most ``NUBI_MAX_TASK_RUNS_DEFAULT`` (default
    2 000) task_run rows.  When the total count exceeds the cap the response
    includes ``task_runs_truncated: true`` and ``task_runs_total`` so the
    caller can decide whether to paginate.  Pass ``?task_runs_limit=N``
    (bounded by ``NUBI_MAX_TASK_RUNS_CEILING``, default 10 000) to raise the
    cap for this request.

    Each task_run's ``logs`` field is also capped via the existing
    ``_cap_task_logs`` helper.
    """
    org_id = await _get_user_org(str(user["id"]), repo)
    store = get_flow_store()
    run = await store.get_flow_run(run_id)
    if run is None or str(run["org_id"]) != str(org_id):
        raise AppError("not_found", "Flow run not found.", 404)

    # Resolve the effective task-run limit for this request.
    # ?task_runs_limit=0 (default) → use the server default cap.
    # ?task_runs_limit=N (N > 0)  → use min(N, ceiling).
    if task_runs_limit > 0:
        effective_limit = min(task_runs_limit, _MAX_TASK_RUNS_CEILING)
    else:
        effective_limit = _MAX_TASK_RUNS_DEFAULT

    _include_results: bool = bool(include_results)
    # Fetch up to ceiling+1 rows so we can detect truncation without a
    # separate COUNT query.
    raw_task_runs = await store.list_task_runs(run_id, limit=_MAX_TASK_RUNS_CEILING + 1)
    task_runs_total = len(raw_task_runs)
    truncated = task_runs_total > effective_limit
    task_runs_page = raw_task_runs[:effective_limit]

    def _serialize_with_log_cap(tr: dict[str, Any]) -> dict[str, Any]:
        serialised = _serialize_task_run(tr, include_results=_include_results)
        raw_logs: list[str] = serialised.get("logs") or []
        if raw_logs:
            capped_logs, logs_truncated = _cap_task_logs(raw_logs)
            serialised["logs"] = capped_logs
            if logs_truncated:
                serialised["logs_truncated"] = True
        return serialised

    result = _serialize_flow_run(run)
    result["task_runs"] = [_serialize_with_log_cap(tr) for tr in task_runs_page]
    result["task_runs_truncated"] = truncated
    result["task_runs_total"] = task_runs_total
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

    # O(1) targeted lookup — avoids fetching ALL task_runs when we only need one.
    tr = await store.get_task_run_by_key(run_id, task_key)
    if tr is None:
        raise AppError("not_found", f"Task '{task_key}' not found in this flow run.", 404)

    raw_logs: list[str] = tr.get("logs") or []
    capped_logs, truncated = _cap_task_logs(raw_logs)

    return {
        "task_key": tr["task_key"],
        "state": tr["state"],
        "attempt": tr.get("attempt", 0),
        "logs": capped_logs,
        "truncated": truncated,
        "total_log_lines": len(raw_logs),
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


@router.post("/compile", status_code=200, dependencies=[Depends(require_writer_default)])
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
    - Execution goes through ``app.compute.sandbox.run_sandboxed`` (the SAME
      M4-SEC hardened path as the ``'python'`` task handler ``_handle_python``):
      a scrubbed/minimal env, ``start_new_session=True`` (new process group)
      with process-GROUP SIGKILL on timeout (so grandchildren cannot survive),
      POSIX rlimits (RLIMIT_CPU = timeout + grace, plus memory / file-size /
      nproc caps), and 1 MiB stdout/stderr caps with a truncation marker.
    - Source is written to a NamedTemporaryFile (cleaned up in ``finally``).
    - Only a minimal environment (``PATH``, ``PYTHONPATH``, ``HOME``, site
      packages) is forwarded so the subprocess can import nubi.flows.
    - Execution is bounded by a hard 15-second wall-clock timeout
      (``timed_out`` maps to ``compile_error`` / 400).

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
    import sys  # noqa: PLC0415
    import tempfile  # noqa: PLC0415
    import textwrap  # noqa: PLC0415

    from app.compute.sandbox import (  # noqa: PLC0415
        RLIMIT_CPU_GRACE_S,
        STDERR_CAP_BYTES,
        STDOUT_CAP_BYTES,
        run_sandboxed,
    )

    # Hard compile timeout (wall-clock seconds).  Mirrors _handle_python's
    # bounded execution; RLIMIT_CPU gets timeout + grace.
    _COMPILE_TIMEOUT_S = 15

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

    # Run via the shared M4-SEC hardened sandbox (same helper as
    # LocalSubprocessRunner / _handle_python): new process group + group-SIGKILL
    # on timeout, POSIX rlimits, and 1 MiB stdout/stderr caps.  A raw
    # subprocess.run here would inherit the parent's full env and leave orphan
    # grandchildren alive on timeout.
    try:
        run = run_sandboxed(
            [sys.executable, _tmp_path],
            env=env,
            timeout_s=_COMPILE_TIMEOUT_S,
            cpu_limit_s=_COMPILE_TIMEOUT_S + RLIMIT_CPU_GRACE_S,
            stdout_cap=STDOUT_CAP_BYTES,
            stderr_cap=STDERR_CAP_BYTES,
        )
    finally:
        try:
            os.unlink(_tmp_path)
        except OSError:
            pass

    if run.timed_out:
        # The process GROUP has already been SIGKILLed inside run_sandboxed.
        raise AppError(
            "compile_error",
            f"Compile timed out after {_COMPILE_TIMEOUT_S} seconds.",
            400,
        )

    _stdout_text = run.stdout.decode("utf-8", errors="replace")
    _stderr_text = run.stderr.decode("utf-8", errors="replace")

    # Parse sentinel line from stdout.
    spec_dict: dict[str, Any] | None = None
    for _line in _stdout_text.splitlines():
        if _line.startswith("__FLOW_SPEC__:"):
            try:
                spec_dict = _json.loads(_line[len("__FLOW_SPEC__:"):])
            except Exception:  # noqa: BLE001
                spec_dict = None
            break

    if run.returncode != 0 or spec_dict is None:
        stderr = _stderr_text.strip()
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


@router.post("/preview", status_code=200, dependencies=[Depends(require_writer_default)])
async def preview_cell(
    body: PreviewCellIn,
    user: dict[str, Any] = Depends(current_user),
    identity: VerifiedIdentity = Depends(verified_identity),
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
    from app.flows.runtime import _resolve_secrets  # noqa: PLC0415

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
        # SECURITY (B2): RLS policies come from the VERIFIED caller identity, not
        # an empty dict — so a flow query cell is row-filtered exactly like the
        # /query endpoint. Empty policies (admin/unscoped caller) → unchanged.
        # NOTE: scheduled runs (flows_tick, claims=None) are a separate path and
        # still need an explicit owner/service policy context (design decision).
        "policies": dict(identity.policies),
        "scope": ["read:*", "write:*"],
    }

    # ── 4. Resolve preview_limit ───────────────────────────────────────────
    preview_limit: int = max(1, min(body.preview_limit, 10_000))

    # ── 5. Execute cells in order, collecting inputs ───────────────────────
    inputs: dict[str, Any] = {}
    now = datetime.now(timezone.utc)

    # Resolve org secrets exactly like the durable path (same helper, same org
    # scoping) so `{{ secrets.NAME }}` templates and the python `secrets` dict
    # work identically in notebook "Run cell" previews.  Plaintext values never
    # reach the client: execute_task redacts them from errors + captured logs.
    secrets: dict[str, str] = await _resolve_secrets(org_id)

    # Org variable namespace for {{ vars.* }} in preview cells (A5). Loaded once
    # for the whole preview (org-global scope; best-effort → {} on any error).
    from app.vars.store import load_vars_namespace  # noqa: PLC0415

    _preview_vars = await load_vars_namespace(org_id, None)

    # Captured stdout/stderr of the TARGET cell, surfaced so notebook users can
    # see print() debugging in the preview. Already secret-redacted by
    # execute_task before it reaches us.
    target_logs: list[str] = []

    for task in tasks_to_run:
        # Inject preview_limit into query task config so the handler respects it.
        task_config = dict(task.config)
        if task.kind == "query" and "preview_limit" not in task_config:
            task_config["preview_limit"] = preview_limit

        ctx = TaskContext(
            flow_params=body.params,
            inputs=inputs,
            now=now,
            secrets=secrets,
            org_id=org_id,
            vars=_preview_vars,
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

        exec_result = await asyncio.to_thread(execute_task, task_dict, ctx, claims)

        if task.key == cell_key:
            target_logs = exec_result.get("logs") or []

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
        "logs": target_logs,
    }


@router.post("/run-cell", status_code=200, dependencies=[Depends(require_writer_default)])
async def run_cell(
    body: RunCellIn,
    user: dict[str, Any] = Depends(current_user),
    identity: VerifiedIdentity = Depends(verified_identity),
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
    source_project_id: str | None = None

    if body.spec is not None:
        spec_data = body.spec
    elif body.flow_id is not None:
        store_ref = get_flow_store()
        flow = await _require_flow_in_org(body.flow_id, org_id, store_ref)
        spec_data = flow.get("spec") or {}
        source_project_id = flow.get("project_id")
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
        # SECURITY (B2): RLS policies come from the VERIFIED caller identity, not
        # an empty dict — so a flow query cell is row-filtered exactly like the
        # /query endpoint. Empty policies (admin/unscoped caller) → unchanged.
        # NOTE: scheduled runs (flows_tick, claims=None) are a separate path and
        # still need an explicit owner/service policy context (design decision).
        "policies": dict(identity.policies),
        "scope": ["read:*", "write:*"],
    }

    store = get_flow_store()
    now = datetime.now(timezone.utc)

    # Transient flows inherit the source flow's project; inline specs fall
    # back to the org's default project (resolved by the store — project_id
    # is NOT NULL on flows).
    transient_flow = await store.create_flow(
        org_id=org_id,
        created_by=str(user["id"]),
        name=trimmed_spec_data["name"],
        spec=trimmed_spec_data,
        enabled=False,
        schedule=None,
        next_run_at=None,
        project_id=source_project_id or await _resolve_project_id(org_id, None),
    )

    try:
        flow_run = await materialize_flow_run(store, transient_flow, body.params, "manual", now)
        flow_run = await _drain_with_timeout(store, flow_run["id"], now, claims=claims)

        # Cap task_runs in the response (mirrors GET /flows/runs/{run_id}).
        raw_task_runs = await store.list_task_runs(flow_run["id"], limit=_MAX_TASK_RUNS_CEILING + 1)
        task_runs_total = len(raw_task_runs)
        task_runs_truncated = task_runs_total > _MAX_TASK_RUNS_DEFAULT
        task_runs_page = raw_task_runs[:_MAX_TASK_RUNS_DEFAULT]

        # Extract the target cell's result.
        target_tr = next(
            (tr for tr in task_runs_page if tr["task_key"] == cell_key), None
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
            "task_runs": [_serialize_task_run(tr) for tr in task_runs_page],
            "task_runs_truncated": task_runs_truncated,
            "task_runs_total": task_runs_total,
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
    identity: VerifiedIdentity = Depends(verified_identity),
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
            compiled_spec = spec.model_dump() if spec else spec_data
            # SECURITY (B2): if the existing notebook flow is scheduled/enabled,
            # re-snapshot the owner's RLS policies onto the new spec so the
            # scheduler always runs under the policies of whoever last saved it.
            # Mirrors the behaviour of create_flow / update_flow.
            if existing.get("schedule") or existing.get("enabled"):
                compiled_spec = _snapshot_owner_policies(compiled_spec, identity)
            updated = await store.update_flow(
                notebook_id,
                {"name": nb.name, "spec": compiled_spec},
            )
            if updated is not None:
                return _serialize_flow(updated)

    # Create new.
    # Always snapshot owner policies on create so any future schedule activation
    # has a valid RLS baseline from the start.
    spec_to_store = _snapshot_owner_policies(
        spec.model_dump() if spec is not None else spec_data, identity
    )
    flow = await store.create_flow(
        org_id=org_id,
        created_by=str(user["id"]),
        name=nb.name,
        spec=spec_to_store,
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
    identity: VerifiedIdentity = Depends(verified_identity),
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

    # SECURITY (B2): snapshot the OWNER's RLS policies onto the flow so scheduled
    # runs (which drain with claims=None) row-filter exactly like the owner does.
    spec_to_store = _snapshot_owner_policies(
        spec.model_dump() if spec is not None else body.spec, identity
    )

    store = get_flow_store()
    flow = await store.create_flow(
        org_id=org_id,
        created_by=str(user["id"]),
        name=body.name,
        spec=spec_to_store,
        enabled=body.enabled,
        schedule=body.schedule,
        next_run_at=next_run_at,
        project_id=project_id,
    )
    return _serialize_flow(flow)


@router.get("", status_code=200)
async def list_flows(
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> list[dict[str, Any]]:
    """List flows for the caller's org, scoped to the active project.

    ``X-Project-Id`` / ``?project_id=`` select the project (validated against
    the org); otherwise the org's default project is used.  When no project
    can be resolved (test doubles without a projects table) the list is
    org-wide.  Each row carries ``pinned_envs``: the env keys that have a
    pinned version of the flow (empty list when unversioned).
    """
    from app.routes._org import resolve_project_filter  # noqa: PLC0415

    org_id = await _get_user_org(str(user["id"]), repo)
    project_id = await resolve_project_filter(org_id, request)
    store = get_flow_store()
    flows = await store.list_flows(org_id, project_id)
    rows = [_serialize_flow(f) for f in flows]

    from app.environments.store import attach_pinned_envs  # noqa: PLC0415

    await attach_pinned_envs("flow", rows)
    return rows


# B3: writeback GET routes must be registered BEFORE /{flow_id} so FastAPI
# does not swallow /writeback as a flow_id parameter.
@router.get("/writeback", status_code=200, dependencies=[Depends(require_writer_default)])
async def _list_writebacks_route_early(
    limit: int = Query(default=50, ge=1, le=500),
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """List write-back requests for the caller's org (newest first).

    Returns up to *limit* records.  Org-scoped from the verified token.
    RBAC: caller must have writer role (owner/admin/member); viewers are blocked.
    Write-back payloads may contain recommendation data — not suitable for
    read-only viewer access.
    """
    from app.connectors.writeback import get_writeback_store  # noqa: PLC0415

    user_id = str(user["id"])
    org_id = await _get_user_org(user_id, repo)
    store = get_writeback_store()
    records = await store.list(org_id, limit=limit)
    return {"writebacks": records, "count": len(records)}


@router.get("/writeback/{wb_id}", status_code=200, dependencies=[Depends(require_writer_default)])
async def _get_writeback_route_early(
    wb_id: str,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Return a single write-back request by id.

    Returns 404 when the record does not exist or belongs to a different org.
    RBAC: caller must have writer role (owner/admin/member); viewers are blocked.
    """
    from app.connectors.writeback import get_writeback_store  # noqa: PLC0415

    user_id = str(user["id"])
    org_id = await _get_user_org(user_id, repo)
    store = get_writeback_store()
    record = await store.get(org_id, wb_id)
    if record is None:
        raise AppError("not_found", f"Write-back {wb_id!r} not found.", 404)
    return record


@router.get("/{flow_id}", status_code=200)
async def get_flow(
    flow_id: str,
    env: str | None = None,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Get a single flow by ID.

    ``?env=<key>``: when that environment has a version pinned for this flow,
    the response ``spec`` is the pinned snapshot and ``resolved_version``
    carries ``{id, version}``; otherwise the draft spec is returned with
    ``resolved_version: null``.

    Returns 404 if the flow does not exist or belongs to a different org.
    """
    org_id = await _get_user_org(str(user["id"]), repo)
    store = get_flow_store()
    flow = await _require_flow_in_org(flow_id, org_id, store)
    result = _serialize_flow(flow)

    env_key = (env or "").strip()
    if env_key:
        pinned_spec, resolved = await _pinned_version_for_env(flow, org_id, env_key)
        if pinned_spec is not None:
            # SECURITY (B2): the pinned snapshot is the RAW version['config'] and
            # may carry the owner's RLS policy snapshot — strip it on this path
            # too so NO route exposes OWNER_POLICIES_KEY (single source of truth).
            result["spec"] = _strip_owner_policies(pinned_spec)
        result["resolved_version"] = resolved
    return result


@router.put("/{flow_id}", status_code=200, dependencies=[Depends(require_writer_default)])
async def update_flow(
    flow_id: str,
    body: UpdateFlowIn,
    user: dict[str, Any] = Depends(current_user),
    identity: VerifiedIdentity = Depends(verified_identity),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Update a flow's name, spec, enabled status, or schedule.

    Validates the spec if provided; returns 400 on hard errors.
    Returns 404 if the flow does not exist or belongs to a different org.

    SECURITY (B2): editing or (re-)enabling a flow REFRESHES the owner RLS
    policy snapshot stashed on the spec, so scheduled runs always row-filter
    under the policies of whoever last persisted the flow.
    """
    org_id = await _get_user_org(str(user["id"]), repo)
    store = get_flow_store()
    existing = await _require_flow_in_org(flow_id, org_id, store)

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
        # Refresh the owner-policy snapshot onto the new spec being persisted.
        fields["spec"] = _snapshot_owner_policies(
            spec.model_dump() if spec is not None else body.spec, identity
        )
    else:
        # No new spec supplied: refresh the snapshot in-place on the existing
        # spec so an edit/enable that changes only name/enabled/schedule still
        # re-anchors scheduled-run RLS to the current owner.  Re-persist spec
        # only when the snapshot actually changed (avoid pointless writes).
        existing_spec = existing.get("spec") or {}
        if isinstance(existing_spec, dict):
            refreshed = _snapshot_owner_policies(existing_spec, identity)
            if refreshed is not None and refreshed != existing_spec:
                fields["spec"] = refreshed

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

    # Best-effort cleanup of versions + environment pointers (polymorphic
    # tables — no FK cascade from the flows row).
    try:
        from app.environments.store import get_env_store  # noqa: PLC0415

        await get_env_store().delete_resource_data("flow", flow_id)
    except Exception:  # noqa: BLE001 — never fail the delete on cleanup
        pass
    return Response(status_code=204)


@router.post("/{flow_id}/run", status_code=200, dependencies=[Depends(require_writer_default)])
async def run_flow(
    flow_id: str,
    body: RunFlowIn = RunFlowIn(),
    user: dict[str, Any] = Depends(current_user),
    identity: VerifiedIdentity = Depends(verified_identity),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Run a flow synchronously (drain all tasks).

    Materialises a flow_run, drains all ready tasks to completion, and returns
    the flow_run dict with a ``task_runs`` array.

    ``body.env`` optionally overrides the execution environment (resolution:
    override → the flow's project default env → "prod").  When the resolved
    environment has a spec version pinned for this flow, the PINNED spec is
    materialized instead of the draft.

    Returns 404 if the flow does not exist or belongs to a different org.
    """
    org_id = await _get_user_org(str(user["id"]), repo)
    store = get_flow_store()
    flow = await _require_flow_in_org(flow_id, org_id, store)

    # ── ENVIRONMENTS: run the pinned spec when the resolved env has one ──────
    from app.flows.runtime import _resolve_env  # noqa: PLC0415

    resolved_env = await _resolve_env(body.env, flow)
    try:
        pinned_spec, _resolved = await _pinned_version_for_env(
            flow, org_id, resolved_env
        )
        if pinned_spec is not None:
            flow["spec"] = pinned_spec
    except Exception:  # noqa: BLE001 — never fail the run on env resolution
        pass

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
        # SECURITY (B2): RLS policies come from the VERIFIED caller identity, not
        # an empty dict — so a flow query cell is row-filtered exactly like the
        # /query endpoint. Empty policies (admin/unscoped caller) → unchanged.
        # NOTE: scheduled runs (flows_tick, claims=None) are a separate path and
        # still need an explicit owner/service policy context (design decision).
        "policies": dict(identity.policies),
        "scope": ["read:*", "write:*"],
    }

    now = datetime.now(timezone.utc)

    flow_run = await materialize_flow_run(
        store, flow, body.params, "manual", now, env=body.env
    )
    flow_run = await _drain_with_timeout(store, flow_run["id"], now, claims=claims)

    # Cap task_runs in the response (mirrors GET /flows/runs/{run_id}).
    raw_task_runs = await store.list_task_runs(flow_run["id"], limit=_MAX_TASK_RUNS_CEILING + 1)
    task_runs_total = len(raw_task_runs)
    task_runs_truncated = task_runs_total > _MAX_TASK_RUNS_DEFAULT
    task_runs_page = raw_task_runs[:_MAX_TASK_RUNS_DEFAULT]
    result = _serialize_flow_run(flow_run)
    result["task_runs"] = [_serialize_task_run(tr) for tr in task_runs_page]
    result["task_runs_truncated"] = task_runs_truncated
    result["task_runs_total"] = task_runs_total
    return result


@router.get("/{flow_id}/runs", status_code=200)
async def list_flow_runs(
    flow_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> list[dict[str, Any]]:
    """List runs for a flow (newest first), with pagination.

    Query params:
    - ``limit``: max rows returned (1–500, default 50)
    - ``offset``: number of rows to skip for pagination (default 0)

    Returns 404 if the flow does not exist or belongs to a different org.
    """
    org_id = await _get_user_org(str(user["id"]), repo)
    store = get_flow_store()
    await _require_flow_in_org(flow_id, org_id, store)
    runs = await store.list_flow_runs(flow_id, limit=limit, offset=offset)
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
# B5: Sweep + Backfill request schemas
# ---------------------------------------------------------------------------


class SweepIn(BaseModel):
    """Request body for ``POST /flows/{id}/sweep``.

    Supply either *param_sets* (a list of param dicts — one flow run per entry)
    or *grid* (a name→[values] dict whose Cartesian product is expanded into
    param sets).  *param_sets* takes precedence when both are supplied.

    Input caps (applied at parse time, before any expansion):
    - ``param_sets`` is limited to ``_MAX_SWEEP_CELLS`` entries so the full
      list is never materialised in memory before the ceiling check.
    - ``grid`` value-list product is bounded to ``_MAX_SWEEP_CELLS`` by a
      validator so ``expand_grid`` is never called with a hopelessly huge
      grid that would OOM before the in-loop cap could fire.
    """

    param_sets: list[dict[str, Any]] | None = Field(
        default=None,
        max_length=_MAX_SWEEP_CELLS,
        description=(
            "Explicit list of param dicts; capped at MAX_SWEEP_CELLS at parse time."
        ),
    )
    grid: dict[str, list[Any]] | None = None
    max_cells: int = Field(default=200, ge=1, le=10000)

    @field_validator("param_sets")
    @classmethod
    def _cap_param_set_size(
        cls, v: list[dict[str, Any]] | None
    ) -> list[dict[str, Any]] | None:
        """Reject oversized param_sets entries at parse time (-> 422).

        RESOURCE (MED): the sweep response echoes each cell's ``params`` back to
        the caller VERBATIM and uncapped.  A caller could stuff megabytes into a
        single param dict and have it reflected (amplified per cell).  Cap each
        entry's serialised size at ``_MAX_PARAM_SET_BYTES`` (64 KiB default).
        """
        if not v:
            return v
        import json as _json  # noqa: PLC0415

        for idx, entry in enumerate(v):
            try:
                size = len(_json.dumps(entry, default=str).encode("utf-8", "replace"))
            except Exception as exc:  # noqa: BLE001
                raise ValueError(
                    f"param_sets[{idx}] is not JSON-serialisable: {exc}"
                ) from exc
            if size > _MAX_PARAM_SET_BYTES:
                raise ValueError(
                    f"param_sets[{idx}] serialised size ({size} bytes) exceeds the "
                    f"server cap of {_MAX_PARAM_SET_BYTES} bytes "
                    "(NUBI_MAX_PARAM_SET_BYTES)."
                )
        return v

    @field_validator("grid", mode="before")
    @classmethod
    def _cap_grid_product(cls, v: Any) -> Any:
        """Reject grids whose Cartesian product would exceed _MAX_SWEEP_CELLS.

        Computed as the product of each value-list's length.  This fires at
        *parse time* (before ``expand_grid`` is called) so a payload like
        ``{"a": range(1000), "b": range(1000)}`` is rejected with a 422 rather
        than OOM-ing the server during expansion.
        """
        if v is None:
            return v
        product = 1
        for key, values in v.items():
            if not isinstance(values, list):
                # Let Pydantic's type coercion handle this.
                continue
            product *= len(values)
            if product > _MAX_SWEEP_CELLS:
                raise ValueError(
                    f"grid Cartesian-product size ({product}) exceeds the server "
                    f"cap of {_MAX_SWEEP_CELLS} cells (MAX_SWEEP_CELLS). "
                    "Reduce the number of values per dimension."
                )
        return v


class BackfillIn(BaseModel):
    """Request body for ``POST /flows/{id}/backfill``.

    Iterates ``[start, end)`` in *window*-sized steps (e.g. ``'1d'``,
    ``'daily'``, ``'PT1H'``), creating one flow run per window with
    ``params.__window_start__`` / ``params.__window_end__`` set.
    """

    start: str  # ISO-8601 datetime string
    end: str  # ISO-8601 datetime string
    window: str  # e.g. '1d', 'daily', 'PT1H'
    # Schema ceiling mirrors the server cap (_MAX_BACKFILL_WINDOWS) so the
    # Pydantic bound never exceeds what the route enforces (the route still
    # clamps via min(...) as defence-in-depth).
    max_windows: int = Field(
        default=min(500, _MAX_BACKFILL_WINDOWS), ge=1, le=_MAX_BACKFILL_WINDOWS
    )
    params: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# B6: Trigger request schemas
# ---------------------------------------------------------------------------


class RegisterTriggerIn(BaseModel):
    """Request body for ``POST /flows/triggers``."""

    flow_id: str
    kind: str  # 'event' | 'webhook' | 'downstream'
    source: str  # event_key OR upstream flow_id
    secret: str | None = None
    extra: dict[str, Any] = {}
    enabled: bool = True


class FireEventIn(BaseModel):
    """Request body for ``POST /flows/triggers/fire``."""

    event_key: str
    payload: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# B5: Sweep endpoint
# ---------------------------------------------------------------------------


@router.post("/{flow_id}/sweep", status_code=200, dependencies=[Depends(require_writer_default)])
async def sweep_flow(
    flow_id: str,
    body: SweepIn,
    user: dict[str, Any] = Depends(current_user),
    identity: VerifiedIdentity = Depends(verified_identity),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Run a param sweep over a flow — one run per param set in the matrix.

    Either ``param_sets`` (explicit list) or ``grid`` (Cartesian-product
    expansion) must be supplied.  Each cell is a real flow_run that participates
    in lineage.  Returns a diff surface (per-param-set outputs keyed for diffing).
    """
    from app.flows.sweep import run_sweep  # noqa: PLC0415

    org_id = await _get_user_org(str(user["id"]), repo)
    store = get_flow_store()
    flow = await _require_flow_in_org(flow_id, org_id, store)

    if not body.param_sets and not body.grid:
        raise AppError("bad_request", "Supply 'param_sets' or 'grid'.", 400)

    claims: dict[str, Any] = {
        "kind": "access",
        "sub": str(user.get("id", "")),
        "org_id": org_id,
        "policies": dict(identity.policies),
        "scope": ["read:*", "write:*"],
    }

    now = datetime.now(timezone.utc)

    # Enforce a hard server cap so callers cannot bypass it via the request body.
    effective_max = min(body.max_cells, _MAX_SWEEP_CELLS)

    # When param_sets is supplied directly the grid-expansion path (and its
    # in-loop cap) is bypassed.  Apply the same server ceiling here so an
    # explicit list cannot exceed the cap either.
    if body.param_sets is not None and len(body.param_sets) > effective_max:
        raise AppError(
            "bad_request",
            f"param_sets length {len(body.param_sets)} exceeds the server cap "
            f"of {effective_max} cells (MAX_SWEEP_CELLS={_MAX_SWEEP_CELLS}). "
            "Reduce the number of param_sets or use the grid path.",
            400,
        )

    # Per-org sweep concurrency cap: a sweep holds a worker for up to
    # _SWEEP_TIMEOUT_S.  Reject immediately (non-blocking check) when the org
    # has reached its concurrent-sweep limit so other requests keep getting served.
    sweep_sem = _get_sweep_sem(org_id)
    if not sweep_sem._value:  # fast non-blocking peek (no slot available)
        raise AppError(
            "too_many_requests",
            f"Too many concurrent sweeps for this org "
            f"(limit: {_MAX_CONCURRENT_SWEEPS_PER_ORG}). Retry later.",
            429,
        )

    try:
        async with sweep_sem:
            result = await asyncio.wait_for(
                run_sweep(
                    store=store,
                    flow=flow,
                    param_sets=body.param_sets,
                    trigger="sweep",
                    now=now,
                    claims=claims,
                    grid=body.grid,
                    max_cells=effective_max,
                ),
                timeout=_SWEEP_TIMEOUT_S,
            )
    except asyncio.TimeoutError:
        raise AppError(
            "sweep_timeout",
            f"Sweep exceeded the server wall-clock limit of {_SWEEP_TIMEOUT_S}s.",
            504,
        )
    except ValueError as exc:
        raise AppError("bad_request", str(exc), 400)

    return {
        "sweep_id": result.sweep_id,
        "flow_id": result.flow_id,
        "total": result.total,
        "succeeded": result.succeeded,
        "failed": result.failed,
        "diff_surface": result.diff_surface(),
        "cells": [
            {
                "index": c.index,
                "params": c.params,
                "run_id": c.run_id,
                "state": c.state,
                "error": c.error,
            }
            for c in result.cells
        ],
    }


# ---------------------------------------------------------------------------
# B5: Backfill endpoint
# ---------------------------------------------------------------------------


def _parse_iso_dt(value: str, field_name: str) -> datetime:
    """Parse an ISO-8601 string into a tz-aware UTC datetime."""
    from datetime import timezone as _tz  # noqa: PLC0415

    try:
        # Try fromisoformat first (Python 3.11+ handles 'Z' suffix).
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise AppError(
            "bad_request",
            f"Field '{field_name}' is not a valid ISO-8601 datetime: {value!r}",
            400,
        )
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_tz.utc)
    return dt


@router.post("/{flow_id}/backfill", status_code=200, dependencies=[Depends(require_writer_default)])
async def backfill_flow(
    flow_id: str,
    body: BackfillIn,
    user: dict[str, Any] = Depends(current_user),
    identity: VerifiedIdentity = Depends(verified_identity),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Re-run a flow over a date range (one run per window).

    Iterates ``[start, end)`` in *window*-sized steps.  Each run receives
    ``params.__window_start__`` / ``params.__window_end__`` (ISO strings).
    Best-effort: a failing window is recorded but does NOT abort the rest.
    """
    from app.flows.sweep import run_backfill  # noqa: PLC0415

    org_id = await _get_user_org(str(user["id"]), repo)
    store = get_flow_store()
    flow = await _require_flow_in_org(flow_id, org_id, store)

    start = _parse_iso_dt(body.start, "start")
    end = _parse_iso_dt(body.end, "end")

    if end <= start:
        raise AppError("bad_request", "'end' must be after 'start'.", 400)

    claims: dict[str, Any] = {
        "kind": "access",
        "sub": str(user.get("id", "")),
        "org_id": org_id,
        "policies": dict(identity.policies),
        "scope": ["read:*", "write:*"],
    }

    now = datetime.now(timezone.utc)

    # Apply server-side ceiling so callers cannot bypass the cap.
    effective_max_windows = min(body.max_windows, _MAX_BACKFILL_WINDOWS)

    # Per-org backfill concurrency cap: a backfill holds a worker for up to
    # _BACKFILL_TIMEOUT_S (600 s).  Reject immediately (non-blocking check) when
    # the org has reached its concurrent-backfill limit so interactive requests
    # keep getting served and the worker pool is never fully drained.
    backfill_sem = _get_backfill_sem(org_id)
    if not backfill_sem._value:  # fast non-blocking peek (no slot available)
        raise AppError(
            "too_many_requests",
            f"Too many concurrent backfills for this org "
            f"(limit: {_MAX_CONCURRENT_BACKFILLS_PER_ORG}). Retry later.",
            429,
        )

    try:
        async with backfill_sem:
            result = await asyncio.wait_for(
                run_backfill(
                    store=store,
                    flow=flow,
                    start=start,
                    end=end,
                    window=body.window,
                    trigger="backfill",
                    now=now,
                    claims=claims,
                    max_windows=effective_max_windows,
                    extra_params=body.params or {},
                ),
                timeout=_BACKFILL_TIMEOUT_S,
            )
    except asyncio.TimeoutError:
        raise AppError(
            "backfill_timeout",
            f"Backfill exceeded the server wall-clock limit of {_BACKFILL_TIMEOUT_S}s.",
            504,
        )
    except ValueError as exc:
        raise AppError("bad_request", str(exc), 400)

    return {
        "backfill_id": result.backfill_id,
        "flow_id": result.flow_id,
        "total": result.total,
        "succeeded": result.succeeded,
        "failed": result.failed,
        "skipped": result.skipped,
        "windows": [
            {
                "index": w.index,
                "window_start": w.window_start.isoformat(),
                "window_end": w.window_end.isoformat(),
                "run_id": w.run_id,
                "state": w.state,
                "error": w.error,
            }
            for w in result.windows
        ],
    }


# ---------------------------------------------------------------------------
# B6: Trigger management endpoints
# ---------------------------------------------------------------------------


@router.post("/triggers", status_code=201, dependencies=[Depends(require_writer_default)])
async def create_trigger(
    body: RegisterTriggerIn,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Register a new flow trigger (event/webhook/downstream).

    ``kind`` must be one of ``'event'``, ``'webhook'``, or ``'downstream'``.
    For ``'event'`` / ``'webhook'``: ``source`` is the event key.
    For ``'downstream'``: ``source`` is the upstream flow_id.

    Triggers are org-scoped.
    """
    from app.flows.triggers import register_trigger  # noqa: PLC0415

    org_id = await _get_user_org(str(user["id"]), repo)
    store = get_flow_store()

    if body.kind not in ("event", "webhook", "downstream"):
        raise AppError("bad_request", f"Invalid trigger kind {body.kind!r}; must be 'event', 'webhook', or 'downstream'.", 400)

    # Verify the target flow belongs to this org.
    await _require_flow_in_org(body.flow_id, org_id, store)

    # For downstream triggers, also verify the source (upstream) flow belongs to this org.
    if body.kind == "downstream":
        await _require_flow_in_org(body.source, org_id, store)

    trigger = await register_trigger(
        flow_id=body.flow_id,
        kind=body.kind,
        source=body.source,
        org_id=org_id,
        secret=body.secret,
        extra=body.extra,
        enabled=body.enabled,
    )

    return {
        "id": trigger.id,
        "flow_id": trigger.flow_id,
        "kind": trigger.kind,
        "source": trigger.source,
        "org_id": trigger.org_id,
        "enabled": trigger.enabled,
        "created_at": trigger.created_at.isoformat(),
    }


@router.get("/triggers", status_code=200)
async def list_triggers(
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> list[dict[str, Any]]:
    """List all flow triggers for the caller's org."""
    from app.flows.triggers import get_trigger_registry  # noqa: PLC0415

    org_id = await _get_user_org(str(user["id"]), repo)
    registry = get_trigger_registry()
    triggers = await registry.list_all(org_id)

    return [
        {
            "id": t.id,
            "flow_id": t.flow_id,
            "kind": t.kind,
            "source": t.source,
            "org_id": t.org_id,
            "enabled": t.enabled,
            "created_at": t.created_at.isoformat(),
        }
        for t in triggers
    ]


@router.post("/triggers/fire", status_code=200, dependencies=[Depends(require_writer_default)])
async def fire_trigger_event(
    body: FireEventIn,
    user: dict[str, Any] = Depends(current_user),
    identity: VerifiedIdentity = Depends(verified_identity),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Fire an event, triggering all matching flows.

    Fires all enabled event/webhook triggers registered for *event_key* in the
    caller's org.  Each matching flow is materialised (run starts asynchronously
    via the work pool).

    Returns the list of run_ids created.
    """
    from app.flows.triggers import fire_event  # noqa: PLC0415

    org_id = await _get_user_org(str(user["id"]), repo)
    store = get_flow_store()

    claims: dict[str, Any] = {
        "kind": "access",
        "sub": str(user.get("id", "")),
        "org_id": org_id,
        "policies": dict(identity.policies),
        "scope": list(identity.scope),
    }

    now = datetime.now(timezone.utc)

    run_ids = await fire_event(
        event_key=body.event_key,
        payload=body.payload,
        org_id=org_id,
        store=store,
        now=now,
        claims=claims,
    )

    return {
        "event_key": body.event_key,
        "run_ids": run_ids,
        "fired": len(run_ids),
    }


# ---------------------------------------------------------------------------
# B6: Enhanced run-history endpoints with lineage + SLA
# ---------------------------------------------------------------------------


@router.get("/{flow_id}/runs/history", status_code=200)
async def get_flow_run_history(
    flow_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Return enriched run history for a flow including lineage + SLA flags.

    Returns up to *limit* runs (newest first, max 500) with:
    - Full run metadata (state, trigger, duration, params_snapshot).
    - Per-run output lineage (``outputs`` — which keys were written).
    - SLA breach flag (``sla_exceeded``) when the flow spec carries a
      ``freshness_sla_s`` hint in ``runtime_config``.

    Returns 404 if the flow does not exist or belongs to a different org.
    """
    from app.flows.triggers import flag_sla_breach  # noqa: PLC0415

    org_id = await _get_user_org(str(user["id"]), repo)
    store = get_flow_store()
    flow = await _require_flow_in_org(flow_id, org_id, store)

    # Pass limit into the store so the DB query is bounded, not post-hoc sliced.
    runs = await store.list_flow_runs(flow_id, limit=limit)

    # Extract SLA hint from flow spec.runtime_config (optional).
    spec = flow.get("spec") or {}
    rc = spec.get("runtime_config") or {}
    expected_s: float | None = rc.get("freshness_sla_s") or None

    now = datetime.now(timezone.utc)

    # Batch-fetch all run outputs in ONE query (avoids N+1).
    run_ids = [r["id"] for r in runs]
    try:
        outputs_by_run = await store.list_run_outputs_for_runs(run_ids)
    except Exception:  # noqa: BLE001
        outputs_by_run = {}

    enriched: list[dict[str, Any]] = []
    for run in runs:
        serialized = _serialize_flow_run(run)

        # Attach lineage outputs (already fetched in single batch query).
        # Defensive slice: store already caps via ROW_NUMBER()/list slice but
        # apply _MAX_OUTPUTS_PER_RUN here too in case of an injected store or
        # a future store implementation that lacks the cap.
        run_outputs = outputs_by_run.get(run["id"], [])[:_MAX_OUTPUTS_PER_RUN]
        serialized["outputs"] = [
            {
                "output_key": o["output_key"],
                "output_type": o["output_type"],
                "output_uri": o.get("output_uri"),
                "task_key": o["task_key"],
                "created_at": _dt_iso(o.get("created_at")),
            }
            for o in run_outputs
        ]

        # Attach lineage: params_snapshot + code_version.
        serialized["params_snapshot"] = run.get("params_snapshot")
        serialized["code_version"] = run.get("code_version")
        serialized["seed"] = run.get("seed")

        # SLA breach flag.
        serialized["sla_exceeded"] = flag_sla_breach(run, expected_s, now)

        enriched.append(serialized)

    return {
        "flow_id": flow_id,
        "runs": enriched,
        "count": len(enriched),
    }


# ---------------------------------------------------------------------------
# B3: Governed write-back endpoints
# ---------------------------------------------------------------------------
# Routes:
#   POST /flows/writeback/preview  — dry-run: returns diff without committing
#   POST /flows/writeback          — submit (dry_run|commit), idempotent
#   POST /flows/writeback/{id}/approval — approve/reject/edit a pending request
#   GET  /flows/writeback          — list write-back requests for the caller's org
#   GET  /flows/writeback/{id}     — get a single write-back request
#
# Security:
#   - writers (owner/admin/member) may POST submit/preview
#   - approvers (owner/admin) may POST approval
#   - RLS: every lookup is org-scoped from the verified token; no cross-org access


class WritebackTargetIn(BaseModel):
    """Target descriptor for a write-back request."""
    connector_id: str
    object: str


class WritebackSubmitIn(BaseModel):
    """Request body for ``POST /flows/writeback``."""
    idempotency_key: str
    rows: list[dict[str, Any]] = Field(default=..., max_length=_MAX_WRITEBACK_ROWS)
    target: WritebackTargetIn
    mode: str = "append"
    approval_required: bool = False
    dry_run: bool = False
    meta: dict[str, Any] = {}


class WritebackApprovalIn(BaseModel):
    """Request body for ``POST /flows/writeback/{id}/approval``."""
    action: str  # 'approve' | 'reject' | 'edit'
    rows_override: list[dict[str, Any]] | None = None


def _get_caller_role(user_id: str, org_id: str, repo: Any) -> str | None:
    """Return the caller's role in *org_id* synchronously (for sync route bodies).

    Uses InMemoryRepo._org_members when available (test path), else falls back
    to None (callers must handle the live-DB case via get_org_role).
    """
    if hasattr(repo, "_org_members"):
        entry = repo._org_members.get(f"{org_id}:{user_id}")
        return entry["role"] if entry else None
    return None


@router.post("/writeback/preview", status_code=200, dependencies=[Depends(require_writer_default)])
async def writeback_preview(
    body: WritebackSubmitIn,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Dry-run a write-back: return the rows/diff that WOULD be written.

    No data is committed to the target connector.  RBAC: caller must have
    writer role (owner/admin/member).  Returns ``{rows, row_count,
    target_object, mode, dry_run: True}``.
    """
    from app.auth.roles import get_org_role  # noqa: PLC0415
    from app.connectors.writeback import (  # noqa: PLC0415
        _require_writer_role,
        dry_run_writeback,
    )

    user_id = str(user["id"])
    org_id = await _get_user_org(user_id, repo)
    role = await get_org_role(user_id, org_id, repo)
    _require_writer_role(role)

    if len(body.rows) > _MAX_WRITEBACK_ROWS:
        raise AppError("row_cap_exceeded", f"rows exceeds server cap of {_MAX_WRITEBACK_ROWS}", 400)

    return dry_run_writeback(
        rows=body.rows,
        target=body.target.model_dump(),
        mode=body.mode,
    )


@router.post("/writeback", status_code=201, dependencies=[Depends(require_writer_default)])
async def submit_writeback_route(
    body: WritebackSubmitIn,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Submit an idempotent write-back request (commit or gate for approval).

    RBAC: caller must have writer role (owner/admin/member).  When
    ``dry_run=True`` the request is handled identically to ``/writeback/preview``
    (no record created, no commit).  When ``approval_required=True`` the record
    enters ``pending_approval`` and waits for an approver action; otherwise the
    write is committed immediately.

    Idempotent: a second call with the same ``idempotency_key`` returns the
    existing record without re-applying the write.

    Returns the write-back record (or the dry-run diff).
    """
    from app.auth.roles import get_org_role  # noqa: PLC0415
    from app.connectors.writeback import (  # noqa: PLC0415
        _require_writer_role,
        dry_run_writeback,
        get_writeback_store,
        submit_writeback,
    )

    user_id = str(user["id"])
    org_id = await _get_user_org(user_id, repo)
    role = await get_org_role(user_id, org_id, repo)
    _require_writer_role(role)

    if len(body.rows) > _MAX_WRITEBACK_ROWS:
        raise AppError("row_cap_exceeded", f"rows exceeds server cap of {_MAX_WRITEBACK_ROWS}", 400)

    if body.dry_run:
        return dry_run_writeback(
            rows=body.rows,
            target=body.target.model_dump(),
            mode=body.mode,
        )

    # REAL connector write: on commit (approval_required=False) submit_writeback
    # invokes this fn, which stages the rows and physically loads them into the
    # target connector via app.flows.handlers.connector_write.handle, returning
    # the loader's REAL rows_written.  Dry-run never reaches here (handled above).
    connector_write_fn = _make_connector_write_fn(org_id)

    # SECURITY (writeback authz): enforce server-side approval policy.
    # The caller may only INCREASE strictness (opt in to approval); they cannot
    # bypass a server-required gate by passing approval_required=False.
    # _enforce_approval_policy returns True when either the server policy
    # (NUBI_WRITEBACK_REQUIRE_APPROVAL env var) OR the caller's value is True.
    effective_approval_required = _enforce_approval_policy(body.approval_required)

    store = get_writeback_store()
    record = await submit_writeback(
        org_id=org_id,
        idempotency_key=body.idempotency_key,
        rows=body.rows,
        target=body.target.model_dump(),
        mode=body.mode,
        created_by=user_id,
        approval_required=effective_approval_required,
        connector_write_fn=connector_write_fn,
        store=store,
        meta=body.meta,
    )
    return record


@router.post("/writeback/{wb_id}/approval", status_code=200, dependencies=[Depends(require_approver_default)])
async def writeback_approval_route(
    wb_id: str,
    body: WritebackApprovalIn,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Approve, reject, or edit a pending write-back request.

    RBAC: caller must have approver role (owner/admin).  The record must be in
    ``pending_approval`` state.

    Actions:
    - ``'approve'`` — commit the write as-is.
    - ``'reject'``  — mark as rejected (no write committed).
    - ``'edit'``    — replace the rows (``rows_override`` required) and commit.

    Returns the updated write-back record.
    """
    from app.auth.roles import get_org_role  # noqa: PLC0415
    from app.connectors.writeback import (  # noqa: PLC0415
        _require_approver_role,
        approve_writeback,
        get_writeback_store,
    )

    user_id = str(user["id"])
    org_id = await _get_user_org(user_id, repo)
    role = await get_org_role(user_id, org_id, repo)
    _require_approver_role(role)

    if body.rows_override is not None and len(body.rows_override) > _MAX_WRITEBACK_ROWS:
        raise AppError(
            "row_cap_exceeded",
            f"rows_override exceeds server cap of {_MAX_WRITEBACK_ROWS}",
            400,
        )

    # REAL connector write: approve_writeback invokes this on the approve/edit
    # commit path; it physically loads the rows into the target connector via
    # app.flows.handlers.connector_write.handle and returns the loader's REAL
    # rows_written.  reject never calls it (no write committed).
    connector_write_fn = _make_connector_write_fn(org_id)

    store = get_writeback_store()
    record = await approve_writeback(
        org_id=org_id,
        wb_id=wb_id,
        action=body.action,
        approver_id=user_id,
        connector_write_fn=connector_write_fn,
        store=store,
        rows_override=body.rows_override,
    )
    return record


# ---------------------------------------------------------------------------
# Register on the shared api_router
# ---------------------------------------------------------------------------

api_router.include_router(router)
