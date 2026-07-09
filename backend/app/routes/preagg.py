"""Auto pre-aggregation endpoints (ROADMAP §4 "Cube weapon").

Org-scoped + authenticated endpoints over the pre-aggregation engine:

    GET  /api/v1/preagg/suggestions
        Ranked rollup candidates mined from the query log
        (frequency × scanned-bytes), each
        ``{table, dimensions, measures, filters, score, sample_count, ...}``.

    POST /api/v1/preagg/build
        Materialize a rollup for a chosen shape (on-demand; also schedulable by
        a caller that hits this endpoint on a cron).  Preserves RLS-key columns
        and registers the rollup so the router can route matching queries to it.

    GET  /api/v1/preagg
        List the rollups that have been built (with their HIT counts).

Authentication / org scoping
----------------------------
Every endpoint requires a valid first-party Bearer token (``current_user``).
The caller's ``org_id`` is resolved via ``resolve_org_id`` (honouring the
``X-Org-Id`` header) exactly like the rest of the org-scoped API, so the
endpoints are namespaced to a real org even though the mined query log and the
rollup registry are currently process-wide singletons.

Legacy endpoints (M2-C, retained for backwards-compatibility)
-------------------------------------------------------------
    GET  /api/v1/_preagg/suggestions   — sig-based suggester (``suggest``)
    POST /api/v1/_preagg/register      — register a rollup table for a sig

This module self-registers on ``api_router`` at import time.  The wiring line in
``main.py`` is already present::

    import app.routes.preagg  # noqa: F401, E402
"""

from __future__ import annotations

import asyncio
import functools
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.auth.deps import current_user
from app.auth.roles import require_writer
from app.connectors.preagg import (
    RollupCandidate,
    build_rollup,
    build_rollup_for_metric,
    get_registry,
    mine,
    suggest,
)
from app.connectors.query_log import get_query_log
from app.errors import AppError
from app.repos.provider import get_repo
from app.routes import api_router
from app.routes._org import resolve_org_id

router = APIRouter(tags=["preagg"])


# ---------------------------------------------------------------------------
# GET /preagg/suggestions — ranked miner output
# ---------------------------------------------------------------------------


@router.get("/preagg/suggestions")
async def preagg_suggestions(
    request: Request,
    min_hits: int = 3,
    user: dict = Depends(current_user),
) -> list[dict[str, Any]]:
    """Return ranked pre-aggregation candidates mined from the query log.

    Parses the logged SQL into structured shapes, clusters compatible shapes
    (same base table + dimension set), and ranks them by
    ``frequency × scanned-bytes``.

    Parameters
    ----------
    min_hits:
        Minimum ``sample_count`` for a candidate to be surfaced.  Default ``3``.

    Returns
    -------
    list[dict]
        Each candidate: ``{table, dimensions, measures, filters, score,
        sample_count, est_bytes, cluster_key}``, highest score first.
    """
    # Resolve org for scoping/membership enforcement (raises 403/404 on misuse).
    await resolve_org_id(str(user["id"]), get_repo(), request)

    candidates = mine(get_query_log(), min_hits=min_hits)
    return [c.to_dict() for c in candidates]


# ---------------------------------------------------------------------------
# POST /preagg/build — materialize a rollup for a chosen shape
# ---------------------------------------------------------------------------


class BuildRollupIn(BaseModel):
    """Request body for POST /preagg/build.

    The shape may be supplied explicitly (``table`` + ``dimensions`` +
    ``measures``) or selected from the current miner output by ``cluster_key``.

    Attributes
    ----------
    cluster_key:
        Select a mined candidate by its ``cluster_key`` (from
        ``GET /preagg/suggestions``).  When set, ``table`` / ``dimensions`` /
        ``measures`` are taken from that candidate (and may be omitted).
    table:
        Base fact table to roll up (required when ``cluster_key`` is absent).
    dimensions:
        GROUP BY columns the rollup is grouped on.
    measures:
        ``func(col)`` measure strings to materialize (e.g. ``["sum(amount)"]``).
    rls_keys:
        RLS-key columns that MUST be preserved (and grouped on) so read-time
        ``WHERE <key> = <claim>`` predicate injection stays sound per tenant.
    source_database:
        Absolute path to the DuckDB file holding the base fact table.  When
        omitted, the base table must already be resolvable in a fresh DuckDB
        context (test/demo path).
    datastore_id:
        Datastore the materialized rollup is served through (read path wiring).
    """

    cluster_key: str | None = None
    table: str | None = None
    dimensions: list[str] | None = None
    measures: list[str] | None = None
    rls_keys: list[str] = []
    source_database: str | None = None
    datastore_id: str | None = None


@router.post("/preagg/build", status_code=201, dependencies=[Depends(require_writer)])
async def preagg_build(
    body: BuildRollupIn,
    request: Request,
    min_hits: int = 1,
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    """Materialize and register a rollup for the requested shape.

    On-demand build (and schedulable: a scheduler/cron caller invokes this same
    endpoint).  The rollup is materialized via the DuckDB write path, its RLS
    keys are verified to survive, and it is recorded in the registry so the
    planner-level router can route matching queries to it.

    Returns
    -------
    dict
        The :class:`~app.connectors.preagg.BuiltRollup` manifest.
    """
    # TENANT-ISOLATION: tag the rollup with the resolved caller org so the
    # router only ever serves it back to this org.  An untagged (org_id=None)
    # rollup is demo-only and is NEVER routed for a real caller.
    resolved_org_id = await resolve_org_id(str(user["id"]), get_repo(), request)

    # Resolve the shape: explicit fields take precedence, else pick by cluster_key.
    table = body.table
    dimensions = list(body.dimensions or [])
    measures = list(body.measures or [])

    if body.cluster_key:
        candidates = mine(get_query_log(), min_hits=min_hits)
        match = next(
            (c for c in candidates if c.cluster_key == body.cluster_key), None
        )
        if match is None:
            raise AppError(
                "rollup_candidate_not_found",
                f"No mined candidate with cluster_key {body.cluster_key!r}.",
                404,
            )
        table = table or match.table
        dimensions = dimensions or list(match.dimensions)
        measures = measures or list(match.measures)

    if not table:
        raise AppError(
            "invalid_rollup_request",
            "Provide either a 'cluster_key' or an explicit 'table'.",
            400,
        )
    if not measures:
        raise AppError(
            "invalid_rollup_request",
            "A rollup needs at least one measure (e.g. ['sum(amount)']).",
            400,
        )

    candidate = RollupCandidate(
        table=table,
        dimensions=sorted(dimensions),
        measures=sorted(measures),
    )

    try:
        built = await asyncio.to_thread(
            functools.partial(
                build_rollup,
                candidate,
                rls_keys=list(body.rls_keys),
                source_database=body.source_database,
                datastore_id=body.datastore_id,
                org_id=resolved_org_id,
            )
        )
    except ValueError as exc:
        # e.g. an unsupported/malformed measure — see
        # app.connectors.preagg._measure_select_sql's allowlist guard.
        raise AppError("invalid_rollup_request", str(exc), 400) from exc
    return built.to_dict()


# ---------------------------------------------------------------------------
# POST /preagg/build_from_metric — build a rollup driven by a metric definition
# ---------------------------------------------------------------------------


class BuildFromMetricIn(BaseModel):
    """Request body for POST /preagg/build_from_metric.

    Attributes
    ----------
    metric_id:
        The id of a registered MetricDefinition to materialize a rollup for.
        The metric's base measures, dimensions, time column, and rls_keys are
        used as the rollup shape (best-effort, first-party metrics only).
    grains:
        Time grains to include the time column for.  When ``None`` the time
        column is included if the metric declares one.  Pass ``[]`` to skip.
    source_database:
        Absolute path to the DuckDB file holding the base fact table.
    datastore_id:
        Datastore the materialized rollup is served through.
    rls_keys:
        Override the metric's declared rls_keys (merged with metric's).
    """

    metric_id: str
    grains: list[str] | None = None
    source_database: str | None = None
    datastore_id: str | None = None
    rls_keys: list[str] = []


@router.post("/preagg/build_from_metric", status_code=201, dependencies=[Depends(require_writer)])
async def preagg_build_from_metric(
    body: BuildFromMetricIn,
    request: Request,
    user: dict = Depends(current_user),
) -> dict[str, Any]:
    """Build a rollup driven by a declared MetricDefinition (auto-shape).

    Resolves the metric by id from the metrics registry, extracts the base
    measures (additive components), dimensions, time column, and rls_keys, and
    materializes a rollup whose shape exactly matches the metric compiler's
    ``__base`` CTE.  This enables both flat and layered (derived / windowed)
    metric queries to route to the rollup transparently.

    Returns 404 when the metric is not registered.  Returns 400 when the
    metric has no additive base measures (non-additive metrics cannot be
    rolled up).
    """
    # TENANT-ISOLATION: tag the rollup with the resolved caller org (see
    # preagg_build) so the router never serves it across orgs.
    resolved_org_id = await resolve_org_id(str(user["id"]), get_repo(), request)

    # Resolve the metric from the metrics registry.
    try:
        from app.metrics.registry import get_metric_registry  # noqa: PLC0415
        metric_reg = get_metric_registry()
        metric = metric_reg.get(body.metric_id)
    except ImportError:
        metric = None

    if metric is None:
        raise AppError(
            "metric_not_found",
            f"Metric {body.metric_id!r} is not registered.",
            404,
        )

    try:
        built = await asyncio.to_thread(
            functools.partial(
                build_rollup_for_metric,
                metric,
                grains=body.grains,
                source_database=body.source_database,
                registry=get_registry(),
                register_query=True,
                datastore_id=body.datastore_id,
                org_id=resolved_org_id,
            )
        )
    except (ValueError, Exception) as exc:
        raise AppError(
            "rollup_build_failed",
            str(exc),
            400,
        ) from exc

    return built.to_dict()


# ---------------------------------------------------------------------------
# GET /preagg — list built rollups
# ---------------------------------------------------------------------------


@router.get("/preagg")
async def preagg_list(
    request: Request,
    user: dict = Depends(current_user),
) -> list[dict[str, Any]]:
    """List the rollups that have been built, with their routed-query HIT counts."""
    await resolve_org_id(str(user["id"]), get_repo(), request)
    return [r.to_dict() for r in get_registry().all_rollups()]


# ===========================================================================
# Legacy M2-C endpoints (sig-based) — retained for backwards-compatibility.
# ===========================================================================


@router.get("/_preagg/suggestions")
async def legacy_preagg_suggestions(
    min_hits: int = 3,
    _user: dict = Depends(current_user),
) -> list[dict]:
    """Legacy: sig-based pre-aggregation suggestions from the query log."""
    suggestions = suggest(get_query_log(), min_hits=min_hits)
    return [s.to_dict() for s in suggestions]


class RegisterRollupIn(BaseModel):
    """Request body for the legacy POST /_preagg/register."""

    sig: str
    table: str


@router.post("/_preagg/register", dependencies=[Depends(require_writer)])
async def legacy_register_rollup(
    body: RegisterRollupIn,
    _user: dict = Depends(current_user),
) -> dict:
    """Legacy: register a rollup table for an exact ``groupby_sig``."""
    get_registry().register(body.sig, body.table)
    return {"registered": True, "sig": body.sig, "table": body.table}


# ---------------------------------------------------------------------------
# Register on the shared api_router
# ---------------------------------------------------------------------------

api_router.include_router(router)
