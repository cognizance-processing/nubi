"""Metrics endpoints — the semantic-layer HTTP surface (Wave C).

Routes (all under ``/api/v1``)
------------------------------
- ``GET    /metrics``             — list metrics visible to the caller's org.
- ``GET    /metrics/{id}``        — one metric's full definition.
- ``POST   /metrics``             — create/register a metric (first-party only).
- ``PUT    /metrics/{id}``        — update a metric definition (re-register + persist).
- ``DELETE /metrics/{id}``        — unregister + delete the persisted row.
- ``POST   /metrics/{id}/query``  — compile + execute (Arrow, like POST /query).
- ``POST   /metrics/{id}/sql``    — dry compile: returns ``{sql, params}`` only.

Design
------
A metric is a GOVERNED definition (allowed dimensions/grains/filters) compiled to
SQL on demand by ``app.metrics.compile.compile_metric``. The compiled SQL carries
``{{name}}`` placeholders for user filter values — exactly the shape the query
path already binds — so ``POST /metrics/{id}/query`` REUSES the /query execution
machinery rather than forking it:

  compile_metric(metric, mq)             → (sql_with_{{params}}, params_dict)
  resolve_named_params(sql, params)      → (positional_sql, $N params)   [planner helper]
  planner.plan(sql, claims, params)      → PhysicalPlan (RLS predicates injected
                                            from claims["policies"] = identity.policies)
  route_to_rollup_shape(plan, registry)  → rollup routing (same as /query)
  cache.get / cache.put                  → per-tenant cache isolation (RLS in key)
  _build_connector_for_plan(...)         → org-scoped datastore + secret + RLS gate
                                            (the SAME helper /query + /estimate use)
  connector.execute(plan)                → Arrow table → ipc bytes
  record_usage_safe(...)                 → compute + query_scan metering

RLS is threaded EXACTLY like /query: claims come EXCLUSIVELY from the verified
token (``identity.policies``); the request body never supplies policies. Embed
tokens can only run governed metric+dims+filters — never raw SQL — so the
endpoint is embed-safe by construction.

Governance errors from the compiler (unknown dimension, bad grain, bad filter
field, …) are ``MetricError``s mapped to a structured 400. Definition-validation
errors on create/update map to a structured 400 too.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.auth.deps import verified_identity
from app.auth.scopes import has_scope
from app.auth.verify import VerifiedIdentity
from app.connectors import plan as planner_plan
from app.connectors.arrow_io import ipc_stream_from_bytes, table_to_ipc_bytes
from app.connectors.cache import get_cache
from app.connectors.planner import resolve_named_params
from app.errors import AppError
from app.metrics.compile import compile_metric
from app.metrics.models import MetricDefinition, MetricError, MetricQuery
from app.metrics.registry import (
    ensure_persisted_metric,
    get_metric_registry,
)
from app.repos.provider import get_repo
from app.routes import api_router
from app.routes.query import (
    _ARROW_STREAM_MEDIA_TYPE,
    _build_connector_for_plan,
    _resolve_caller_org,
)

logger = logging.getLogger("nubi.metrics")


# ---------------------------------------------------------------------------
# Auth / scope helpers (mirror the query route conventions)
# ---------------------------------------------------------------------------


def _require_read_scope(identity: VerifiedIdentity) -> None:
    """Require at least one read scope — same gate as POST /query."""
    scopes = identity.scope
    has_read = has_scope(scopes, "read:query") or any(
        s.startswith("read:") for s in scopes
    )
    if not has_read:
        raise AppError(
            "insufficient_scope",
            "Token does not carry the required scope: read:query",
            403,
        )


def _require_first_party_write(identity: VerifiedIdentity) -> None:
    """Reject embed tokens from metric writes (mirror register_query)."""
    if identity.kind == "embed":
        raise AppError("forbidden", "Embed tokens cannot register metrics.", 403)
    _require_read_scope(identity)


async def _caller_org(identity: VerifiedIdentity, request: Request) -> str | None:
    """Resolve the caller's org id for tenant-scoping a metric resolution.

    Embed tokens carry the org in the token claim; first-party tokens require a
    membership lookup (``resolve_org_id`` honours ``X-Org-Id`` with a membership
    check). Returns ``None`` only when no org is resolvable (e.g. an org-less
    demo token) — callers treat a ``None`` org as "no tenant scoping available"
    and fall back to the seed-only / unscoped path. SECURITY: every request-
    facing metric resolution passes this through so a slug only resolves within
    the caller's tenant.
    """
    if identity.kind == "embed":
        return identity.org
    try:
        from app.routes._org import resolve_org_id  # noqa: PLC0415

        return await resolve_org_id(identity.user_id, get_repo(), request)
    except Exception:  # noqa: BLE001 — no org resolvable → unscoped/seed-only.
        return None


async def _resolve_metric(
    metric_id: str, org_id: str | None = None
) -> MetricDefinition:
    """Resolve a metric by id from the registry, falling back to the DB.

    TENANT ISOLATION (SEC): the metric registry is a process-GLOBAL singleton and
    slugs are only UNIQUE per (org, slug). When *org_id* is supplied (every
    request-facing caller MUST supply it) resolution is tenant-scoped two ways:

      1. the DB fallback (``ensure_persisted_metric``) is org-filtered, so a slug
         that exists only in ANOTHER org never loads; and
      2. a registry HIT (which may have been hydrated by another org on this
         shared process) is accepted only after ``metric_belongs_to_org``
         confirms the caller's org actually exposes that slug (in-code seeds such
         as ``demo_revenue`` belong to no tenant and are always allowed).

    Without this an org could read/compile/execute another org's metric by its
    slug (base_sql + datastore disclosure + cross-tenant execution). ``org_id``
    omitted preserves the legacy unscoped behaviour for trusted internal callers.

    Raises AppError 404 when the metric is unknown OR not owned by *org_id*
    (same 404 either way — no cross-org existence leak).
    """
    registry = get_metric_registry()
    metric = registry.get(metric_id)
    if metric is not None and org_id is not None:
        from app.metrics.registry import metric_belongs_to_org  # noqa: PLC0415

        if not await metric_belongs_to_org(metric_id, org_id):
            # Shared-registry hit owned by a different org → treat as not-found.
            metric = None
    if metric is None:
        metric = await ensure_persisted_metric(metric_id, org_id)
    if metric is None:
        raise AppError(
            "metric_not_found", f"No metric found for id={metric_id!r}.", 404
        )
    return metric


def _metric_summary(metric: MetricDefinition) -> dict[str, Any]:
    """Compact list-view shape: id/slug, name, measure, dims, grains, description."""
    td = metric.time_dimension
    return {
        "id": metric.id,
        "name": metric.name,
        "measure": {
            "name": metric.measure.name,
            "agg": metric.measure.agg,
            "expr": metric.measure.expr,
            "type": metric.measure.type,
            "format": metric.measure.format,
        },
        "dimensions": [d.name for d in metric.dimensions],
        "time_grains": list(td.grains) if td is not None else [],
        "description": metric.description,
    }


# ---------------------------------------------------------------------------
# Definition validation (create / update) — structured 400 on bad bodies
# ---------------------------------------------------------------------------


def _build_definition(data: dict[str, Any], *, metric_id: str) -> MetricDefinition:
    """Validate a request body into a :class:`MetricDefinition`.

    Rejects structurally-invalid definitions with ``MetricError`` (mapped to a
    400 by the route): no source (base_table/base_sql), or a measure that
    references nothing. Field-shape errors from ``from_dict`` (e.g. a missing
    measure name) are normalised to ``MetricError`` too.
    """
    payload = dict(data)
    payload["id"] = metric_id
    try:
        metric = MetricDefinition.from_dict(payload)
    except (KeyError, TypeError, ValueError) as exc:
        raise MetricError(
            "invalid_definition", f"Malformed metric definition: {exc}"
        ) from exc

    # Exactly one source must be set.
    has_table = bool(metric.base_table)
    has_sql = bool(metric.base_sql)
    if not has_table and not has_sql:
        raise MetricError(
            "no_source",
            "A metric must declare exactly one source: base_table or base_sql.",
        )
    if has_table and has_sql:
        raise MetricError(
            "ambiguous_source",
            "A metric must declare only ONE source — not both base_table and base_sql.",
        )

    # A measure must reference SOMETHING: a non-count agg needs a real expr.
    m = metric.measure
    if not m.name:
        raise MetricError("invalid_measure", "Measure must have a name.")
    if m.agg != "count" and (not m.expr or m.expr == "*"):
        raise MetricError(
            "invalid_measure",
            f"Measure {m.name!r} with agg={m.agg!r} must reference a column/"
            "expression (expr); only count may use '*'.",
        )
    return metric


def validate_query_metric_block(config: dict[str, Any]) -> None:
    """Validate a query's optional ``config.metric`` block (no-op if absent).

    A query becomes a governed metric when its ``config`` carries a ``metric``
    key (Section 1 of the query/metric unification). This reuses
    :func:`_build_definition` — the SAME validation the ``/metrics`` write path
    runs — so the rules are identical: a valid measure (a non-count agg needs a
    real ``expr``) over a source (here ``config.sql`` → ``base_sql``), with
    well-formed dimensions/time/rls. A plain query (no ``metric`` block) is
    unaffected.

    Raises :class:`AppError` (400) on an invalid block so the resource write
    route surfaces a structured error.
    """
    metric_block = config.get("metric")
    if metric_block is None:
        return
    if not isinstance(metric_block, dict):
        raise AppError(
            "invalid_metric",
            "config.metric must be an object describing the governed metric.",
            400,
        )
    slug = str(metric_block.get("slug") or "").strip()
    if not slug:
        raise AppError(
            "invalid_metric",
            "config.metric.slug is required (the stable metric id).",
            400,
        )
    # Assemble the MetricDefinition shape the metrics write path validates:
    # base_sql comes from config.sql (queries are SQL — base_table stays None).
    definition_data = {
        "name": slug,
        "measure": metric_block.get("measure") or {},
        "base_sql": config.get("sql"),
        "datastore_id": config.get("datastore_id"),
        "dimensions": metric_block.get("dimensions") or [],
        "time_dimension": metric_block.get("time_dimension"),
        "default_filters": metric_block.get("default_filters") or [],
        "rls_keys": metric_block.get("rls_keys") or [],
        "owner": metric_block.get("owner"),
        "description": metric_block.get("description") or "",
    }
    try:
        _build_definition(definition_data, metric_id=slug)
    except MetricError as exc:
        raise AppError(exc.code, exc.message, 400) from exc


# ---------------------------------------------------------------------------
# Persistence (best-effort) — UPSERT a query-with-`config.metric` by (org, slug)
# ---------------------------------------------------------------------------
# Metrics are now SOURCED from queries: a `queries` row whose `config` carries a
# `metric` block IS the governed metric (keyed by config.metric.slug). The
# /metrics write path therefore UPSERTs a query (not the deprecated `metrics`
# table) so legacy callers still work but land in the unified store. All DB work
# is wrapped so a FakeDB/no-DB path never fails the request: the in-memory
# registry mutation alone is sufficient for the route to succeed.


def _slugify(value: str) -> str:
    import re

    slug = re.sub(r"[\s\-]+", "_", value.lower())
    slug = re.sub(r"[^a-z0-9_]", "", slug)
    return slug.strip("_") or "metric"


def _query_config_from_metric(metric: MetricDefinition, slug: str) -> dict[str, Any]:
    """Build the ``queries.config`` (with a ``metric`` block) for *metric*.

    ``config.sql`` is the metric's base SQL — for a legacy ``base_table`` metric
    we synthesise ``SELECT * FROM <table>`` so the unified store always carries a
    SQL base grain (the same conversion the migration does). The ``metric`` block
    mirrors ``config.metric.*`` in the unification contract, keyed by *slug*.
    """
    td = metric.time_dimension
    sql = metric.base_sql or (
        f"SELECT * FROM {metric.base_table}" if metric.base_table else None
    )
    return {
        "sql": sql,
        "datastore_id": metric.datastore_id,
        "metric": {
            "slug": slug,
            "measure": {
                "name": metric.measure.name,
                "agg": metric.measure.agg,
                "expr": metric.measure.expr,
                "type": metric.measure.type,
                "format": metric.measure.format,
            },
            "dimensions": [
                {"name": d.name, "expr": d.expr, "type": d.type}
                for d in metric.dimensions
            ],
            "time_dimension": (
                {
                    "column": td.column,
                    "grains": list(td.grains),
                    "default_grain": td.default_grain,
                }
                if td is not None
                else None
            ),
            "default_filters": list(metric.default_filters),
            "rls_keys": list(metric.rls_keys),
            "owner": metric.owner,
            "description": metric.description,
        },
    }


async def _persist_metric(
    metric: MetricDefinition,
    identity: VerifiedIdentity,
    request: Request,
) -> str:
    """Best-effort UPSERT of a query-with-``config.metric``; return the slug id.

    The metric is stored as a ``queries`` row whose ``config`` carries a
    ``metric`` block, upserted by (org_id, ``config.metric.slug``). The canonical
    metric id IS the slug (stable across the migration), so this returns the slug
    regardless of whether the DB write lands.
    """
    import json
    import uuid

    slug = metric.id if not _is_uuid_str(metric.id) else _slugify(metric.name)
    config = _query_config_from_metric(metric, slug)
    config_json = json.dumps(config)

    try:
        from app.db import execute, fetchrow
        from app.routes._org import (
            get_user_org,
            resolve_project_id_for_create,
        )

        repo = get_repo()
        org_id = await get_user_org(identity.user_id, repo)
        project_id = await resolve_project_id_for_create(org_id, request)

        # UPSERT by (org_id, config.metric.slug): update an existing backing
        # query if one already exposes this slug in the org, else insert a new
        # one. (No DB UNIQUE on the slug — we resolve the existing id first.)
        existing = await fetchrow(
            "SELECT id FROM queries WHERE org_id = $1::uuid "
            "AND config->'metric'->>'slug' = $2 LIMIT 1",
            org_id,
            slug,
        )
        if existing is not None and existing.get("id"):
            await execute(
                "UPDATE queries SET name = $1, config = $2::jsonb, updated_at = now() "
                "WHERE id = $3::uuid",
                metric.name,
                config_json,
                str(existing["id"]),
            )
        else:
            await execute(
                """
                INSERT INTO queries
                    (id, org_id, project_id, created_by, name, config)
                VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5, $6::jsonb)
                """,
                str(uuid.uuid4()),
                org_id,
                project_id,
                identity.user_id,
                metric.name,
                config_json,
            )
    except Exception:  # noqa: BLE001 — persistence is best-effort.
        pass
    return slug


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class MetricIn(BaseModel):
    """Request body for POST/PUT /metrics — a serialized MetricDefinition.

    The body mirrors ``MetricDefinition.to_dict``: ``name``, ``measure``,
    ``base_table``/``base_sql``, ``dimensions``, ``time_dimension``,
    ``default_filters``, ``rls_keys``, ``description``, … The optional ``id`` is
    ignored on create (the persisted row id / slug becomes the canonical id) and
    overridden by the path id on update.
    """

    model_config = {"extra": "allow"}

    name: str = ""


# ---------------------------------------------------------------------------
# GET /metrics  — list
# ---------------------------------------------------------------------------


@api_router.get("/metrics")
async def list_metrics(
    request: Request,
    identity: VerifiedIdentity = Depends(verified_identity),
) -> dict:
    """List metrics visible to the caller (id/slug, name, measure, dims, grains).

    Auth mirrors POST /query: a verified identity with at least one read scope.
    Org scoping: the registry singleton is process-global, so we filter to the
    caller's org's persisted metric rows (best-effort) plus slug-only seeds
    (e.g. ``demo_revenue``) — exactly like the query-registry listing.
    """
    _require_read_scope(identity)

    registry = get_metric_registry()
    metrics = registry.all()

    # ── Org scoping (best-effort) ────────────────────────────────────────────
    # Metrics are query-backed: the visible set is the slugs exposed by this
    # org's queries-with-`config.metric`. In-code seeds (e.g. demo_revenue) have
    # no backing query row and are always visible. When scoping is unavailable
    # (no DB) the registry list is returned unfiltered.
    slugs: set[str] | None = None
    try:
        from app.db import fetch
        from app.routes._org import resolve_org_id, resolve_project_filter

        repo = get_repo()
        if identity.kind == "embed":
            org_id = identity.org
        else:
            org_id = await resolve_org_id(identity.user_id, repo, request)
        if org_id:
            project_id = (
                None
                if identity.kind == "embed"
                else await resolve_project_filter(org_id, request)
            )
            if project_id:
                rows = await fetch(
                    "SELECT config->'metric'->>'slug' AS slug FROM queries "
                    "WHERE org_id = $1::uuid AND project_id = $2::uuid "
                    "AND config ? 'metric'",
                    org_id,
                    project_id,
                )
            else:
                rows = await fetch(
                    "SELECT config->'metric'->>'slug' AS slug FROM queries "
                    "WHERE org_id = $1::uuid AND config ? 'metric'",
                    org_id,
                )
            slugs = {str(r["slug"]) for r in rows if r.get("slug")}
    except Exception:  # noqa: BLE001 — scoping unavailable → unfiltered list.
        slugs = None

    if slugs is not None:
        # Keep org-owned query-backed metrics + the in-code seeds (which belong
        # to no tenant, e.g. demo_revenue).
        from app.metrics.registry import SEED_METRIC_IDS  # noqa: PLC0415

        metrics = [m for m in metrics if m.id in slugs or m.id in SEED_METRIC_IDS]

    return {"metrics": [_metric_summary(m) for m in metrics]}


def _is_uuid_str(value: object) -> bool:
    import uuid

    try:
        uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return False
    return True


# ---------------------------------------------------------------------------
# GET /metrics/{id}  — one metric's full definition
# ---------------------------------------------------------------------------


@api_router.get("/metrics/{metric_id}")
async def get_metric(
    metric_id: str,
    request: Request,
    identity: VerifiedIdentity = Depends(verified_identity),
) -> dict:
    """Return a single metric's full serialized definition.

    TENANT ISOLATION (SEC): resolution is org-scoped — a slug only resolves
    within the caller's org (or an in-code seed), so org A cannot read org B's
    metric definition (base_sql / datastore binding) by its slug.
    """
    _require_read_scope(identity)
    org_id = await _caller_org(identity, request)
    metric = await _resolve_metric(metric_id, org_id)
    return metric.to_dict()


# ---------------------------------------------------------------------------
# POST /metrics  — create/register
# ---------------------------------------------------------------------------


@api_router.post("/metrics", status_code=201)
async def create_metric(
    body: MetricIn,
    request: Request,
    identity: VerifiedIdentity = Depends(verified_identity),
) -> dict:
    """Create + register a metric. First-party (kind='access') only.

    The body is validated into a :class:`MetricDefinition` (bad definitions →
    structured 400). The metric is registered in the singleton IMMEDIATELY so it
    is queryable right away, and persisted best-effort to the ``metrics`` table
    (loaded back at startup by ``load_persisted_metrics``).
    """
    _require_first_party_write(identity)

    data = body.model_dump()
    if not str(data.get("name") or "").strip():
        raise AppError("validation_error", "name must not be empty.", 400)

    # Provisional id = slug; adopt the persisted row id when persistence works.
    provisional_id = _slugify(str(data["name"]))
    try:
        metric = _build_definition(data, metric_id=provisional_id)
    except MetricError as exc:
        raise AppError(exc.code, exc.message, 400) from exc

    canonical_id = await _persist_metric(metric, identity, request)
    if canonical_id != metric.id:
        metric = MetricDefinition.from_dict({**metric.to_dict(), "id": canonical_id})

    get_metric_registry().register(metric)
    return metric.to_dict()


# ---------------------------------------------------------------------------
# PUT /metrics/{id}  — update
# ---------------------------------------------------------------------------


@api_router.put("/metrics/{metric_id}")
async def update_metric(
    metric_id: str,
    body: MetricIn,
    request: Request,
    identity: VerifiedIdentity = Depends(verified_identity),
) -> dict:
    """Update a metric definition: re-validate, re-register, and re-persist."""
    _require_first_party_write(identity)

    data = body.model_dump()
    # Carry the existing name forward when the update body omits it. SECURITY:
    # org-scope the lookup so the carried-forward name cannot leak from another
    # org's metric with the same slug (the write itself is already org-scoped via
    # _persist_metric's get_user_org → INSERT/UPDATE on this org's rows).
    if not str(data.get("name") or "").strip():
        org_id = await _caller_org(identity, request)
        existing = get_metric_registry().get(metric_id)
        if existing is not None and org_id is not None:
            from app.metrics.registry import metric_belongs_to_org  # noqa: PLC0415

            if not await metric_belongs_to_org(metric_id, org_id):
                existing = None
        if existing is None:
            existing = await ensure_persisted_metric(metric_id, org_id)
        if existing is not None:
            data["name"] = existing.name

    try:
        metric = _build_definition(data, metric_id=metric_id)
    except MetricError as exc:
        raise AppError(exc.code, exc.message, 400) from exc

    await _persist_metric(metric, identity, request)
    get_metric_registry().register(metric)
    return metric.to_dict()


# ---------------------------------------------------------------------------
# DELETE /metrics/{id}  — unregister + delete row
# ---------------------------------------------------------------------------


@api_router.delete("/metrics/{metric_id}")
async def delete_metric(
    metric_id: str,
    identity: VerifiedIdentity = Depends(verified_identity),
) -> dict:
    """Stop exposing the metric: unregister it + clear the backing query's block.

    DELETE semantics (the safe, non-destructive option): we CLEAR the ``metric``
    block from the backing query rather than deleting the query, so the query's
    SQL — which the author may still want as a plain query — survives. The metric
    id (slug) simply stops resolving. ``metric_id`` is the slug. Best-effort: the
    in-memory unregister alone makes the route effective in a no-DB context.
    """
    _require_first_party_write(identity)

    get_metric_registry().unregister(metric_id)
    try:
        from app.db import execute

        await execute(
            "UPDATE queries SET config = config - 'metric', updated_at = now() "
            "WHERE config->'metric'->>'slug' = $1",
            metric_id,
        )
    except Exception:  # noqa: BLE001 — clearing the block is best-effort.
        pass
    return {"id": metric_id, "deleted": True}


# ---------------------------------------------------------------------------
# POST /metrics/{id}/sql  — dry compile (no execution)
# ---------------------------------------------------------------------------


@api_router.post("/metrics/{metric_id}/sql")
async def compile_metric_dry(
    metric_id: str,
    body: dict,
    request: Request,
    identity: VerifiedIdentity = Depends(verified_identity),
) -> dict:
    """Compile a MetricQuery to ``{sql, params}`` WITHOUT executing it.

    For agent/debug introspection. Governance violations (unknown dimension,
    bad grain/filter) → structured 400 via the ``MetricError`` map.

    TENANT ISOLATION (SEC): org-scoped resolution — a slug only compiles within
    the caller's org, so org A cannot dump org B's metric base_sql.
    """
    _require_read_scope(identity)
    org_id = await _caller_org(identity, request)
    metric = await _resolve_metric(metric_id, org_id)

    payload = dict(body or {})
    payload["metric_id"] = metric_id
    mq = MetricQuery.from_dict(payload)

    try:
        sql, params = compile_metric(metric, mq)
    except MetricError as exc:
        raise AppError(exc.code, exc.message, 400) from exc

    return {"sql": sql, "params": params}


# ---------------------------------------------------------------------------
# POST /metrics/{id}/query  — compile + execute (Arrow, like POST /query)
# ---------------------------------------------------------------------------


@api_router.post("/metrics/{metric_id}/query")
async def query_metric(
    metric_id: str,
    body: dict,
    request: Request,
    identity: VerifiedIdentity = Depends(verified_identity),
) -> StreamingResponse:
    """Compile a metric query and execute it through the /query execution path.

    REUSES the existing machinery (no fork):
      compile_metric → resolve_named_params → planner.plan (RLS injected from
      identity.policies) → rollup routing → cache → _build_connector_for_plan
      (org-scoped datastore + secret + capability-gated RLS refusal) →
      connector.execute → Arrow IPC bytes → metering.

    Embed-safe: raw SQL is never accepted — only the governed metric + dims +
    filters. RLS is threaded EXACTLY like /query: claims come exclusively from
    the verified token.

    TENANT ISOLATION (SEC): org-scoped resolution — a slug only resolves +
    executes within the caller's org. Without this, org A could execute org B's
    base_sql (and bind it to B's datastore id). Combined with token-only RLS this
    closes both definition disclosure and cross-tenant execution.
    """
    _require_read_scope(identity)
    org_id = await _caller_org(identity, request)
    metric = await _resolve_metric(metric_id, org_id)

    payload = dict(body or {})
    payload["metric_id"] = metric_id
    mq = MetricQuery.from_dict(payload)

    # ── 1. Compile the governed metric → (sql with {{params}}, params dict) ──
    try:
        sql, named_params = compile_metric(metric, mq)
    except MetricError as exc:
        raise AppError(exc.code, exc.message, 400) from exc

    # ── 2. Resolve {{name}} → positional $N (the SAME planner helper /query uses)
    effective_sql, effective_params = resolve_named_params(sql, named_params)

    # ── 3. SECURITY: RLS claims from the VERIFIED identity ONLY (like /query) ─
    claims = {"policies": identity.policies}

    # ── 4. Plan (RLS predicates injected at the AST level) ───────────────────
    physical_plan = planner_plan(
        sql=effective_sql,
        claims=claims,
        params=effective_params,
    )

    # ── 5. Conservative rollup routing (RLS preserved) — same as /query ──────
    try:
        from app.connectors.planner import route_to_rollup_shape as _route_rollup
        from app.connectors.preagg import get_registry as _get_rollup_registry

        _route = _route_rollup(physical_plan, _get_rollup_registry())
        if _route.routed:
            physical_plan = _route.plan
            if _route.rollup_id:
                _get_rollup_registry().record_hit(_route.rollup_id)
    except Exception:  # noqa: BLE001 — routing must never break the query path.
        pass

    # ── 6. Cache lookup (per-tenant isolation: RLS claims are in the key) ────
    cache = get_cache()
    cached_bytes = cache.get(physical_plan.cache_key)
    if cached_bytes is not None:
        return StreamingResponse(
            ipc_stream_from_bytes(cached_bytes),
            media_type=_ARROW_STREAM_MEDIA_TYPE,
            headers={"X-Nubi-Cache": "HIT"},
        )

    # ── 7. Org attribution + compute quota (mirror /query) ───────────────────
    repo = get_repo()
    org_id, org_lookup_error = await _resolve_caller_org(identity, repo)

    from app.features import enforce_quota as _enforce_quota

    await _enforce_quota(org_id, "compute_units", amount=1.0)

    # ── 8. Build the connector — the SAME helper /query + /estimate use ──────
    # Honours the metric's bound datastore_id (org-scoped), secret injection,
    # network mode, and the capability-gated RLS refusal (source_unsupported_rls
    # 501). datastore_id=None → the built-in demo connector.
    effective_datastore_id = metric.datastore_id or None
    connector, conn_kind, net_cleanup = await _build_connector_for_plan(
        physical_plan,
        effective_datastore_id,
        org_id,
        org_lookup_error,
        repo,
    )

    # ── 9. Execute + serialise (net_cleanup torn down in finally) ────────────
    import time as _time

    _t0 = _time.perf_counter()
    try:
        arrow_table = connector.execute(physical_plan)
        full_bytes = table_to_ipc_bytes(arrow_table)
    finally:
        try:
            net_cleanup()
        except Exception:  # noqa: BLE001 — cleanup never masks the result/error.
            pass

    # ── 10. Meter (compute + query_scan) — best-effort, fire-and-forget ──────
    _elapsed_ms = int((_time.perf_counter() - _t0) * 1000)
    try:
        from app.compute.metering import record_usage_safe as _record_usage_safe

        _cu_multiplier = 1.0
        try:
            _cu_multiplier = max(float(os.getenv("NUBI_CU_MULTIPLIER", "1")), 1.0)
        except ValueError:
            pass
        _record_usage_safe(
            kind="compute",
            user_id=str(identity.user_id or "embed"),
            org_id=org_id,
            units=(_elapsed_ms / 1000.0) * _cu_multiplier,
            tier=conn_kind,
            elapsed_ms=_elapsed_ms,
            output_bytes=len(full_bytes),
        )
        _record_usage_safe(
            kind="query_scan",
            user_id=str(identity.user_id or "embed"),
            org_id=org_id,
            units=float(len(full_bytes)),
            tier=conn_kind,
            output_bytes=len(full_bytes),
        )
    except Exception:  # noqa: BLE001 — telemetry must never break the caller.
        pass

    # ── 11. Cache + stream the MISS response ─────────────────────────────────
    cache.put(physical_plan.cache_key, full_bytes)
    return StreamingResponse(
        ipc_stream_from_bytes(full_bytes),
        media_type=_ARROW_STREAM_MEDIA_TYPE,
        headers={"X-Nubi-Cache": "MISS"},
    )
