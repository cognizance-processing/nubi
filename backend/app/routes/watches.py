"""Watches endpoints — monitored metric thresholds with AI-explained alerts.

Routes (all under ``/api/v1``)
------------------------------
- ``GET    /watches``                — list watches visible to the caller's org.
- ``GET    /watches/{id}``           — one watch's full record.
- ``POST   /watches``                — create a watch (first-party only).
- ``PUT    /watches/{id}``           — update a watch (re-register + persist).
- ``DELETE /watches/{id}``           — unregister + delete the persisted row.
- ``POST   /watches/{id}/evaluate``  — evaluate now (run_watch) and return the
                                       result (manual trigger + test surface).
- ``POST   /watches/tick``           — shared-secret: evaluate all enabled
                                       in-memory watches (mirror flows_tick).

Design
------
A watch monitors a GOVERNED metric (``app.metrics``). Evaluation REUSES the exact
``POST /metrics/{id}/query`` execution path (compile → plan with RLS from
``identity.policies`` → connector.execute) via ``app.ai.watch``. On breach the
watch generates an AI explanation (deterministic under NullProvider) and
dispatches it through ``app.chat.notify`` channels — the same dispatch the
flow-run alert hook uses.

Persistence mirrors ``app.routes.metrics``: an in-process registry keyed by watch
id (so a created watch is evaluable immediately) plus a best-effort upsert into
the ``watches`` table (migration 0009), loaded back at startup is not wired here
to keep the surface bounded — the registry + lazy DB hydrate on a miss suffice.
Auth mirrors ``/metrics``: a verified identity with a read scope; writes are
first-party only (embed tokens rejected).
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from typing import Any

from fastapi import Depends, Header, Request
from pydantic import BaseModel

from app.ai.watch import Watch, run_watch
from app.auth.deps import verified_identity
from app.auth.scopes import has_scope
from app.auth.verify import VerifiedIdentity
from app.errors import AppError
from app.metrics.registry import ensure_persisted_metric, get_metric_registry
from app.repos.provider import get_repo
from app.routes import api_router

logger = logging.getLogger("nubi.watches")


# ---------------------------------------------------------------------------
# In-process watch registry (mirror of the metric registry, kept local)
# ---------------------------------------------------------------------------


_WATCHES: dict[str, dict[str, Any]] = {}


def _registry_put(record: dict[str, Any]) -> dict[str, Any]:
    _WATCHES[str(record["id"])] = record
    return record


def _registry_get(watch_id: str) -> dict[str, Any] | None:
    return _WATCHES.get(watch_id)


def _registry_all() -> list[dict[str, Any]]:
    return list(_WATCHES.values())


def _registry_del(watch_id: str) -> None:
    _WATCHES.pop(watch_id, None)


def reset_for_tests() -> None:
    """Clear the in-process watch registry (test-only helper)."""
    _WATCHES.clear()


# ---------------------------------------------------------------------------
# Auth / scope helpers (mirror the metrics route conventions)
# ---------------------------------------------------------------------------


def _require_read_scope(identity: VerifiedIdentity) -> None:
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
    if identity.kind == "embed":
        raise AppError("forbidden", "Embed tokens cannot manage watches.", 403)
    _require_read_scope(identity)


def _slugify(value: str) -> str:
    slug = re.sub(r"[\s\-]+", "_", value.lower())
    slug = re.sub(r"[^a-z0-9_]", "", slug)
    return slug.strip("_") or "watch"


# ---------------------------------------------------------------------------
# Persistence (best-effort, mirrors _persist_metric)
# ---------------------------------------------------------------------------


async def _persist_watch(
    record: dict[str, Any], identity: VerifiedIdentity, request: Request
) -> str:
    """Best-effort upsert into the ``watches`` table; return the canonical id."""
    import json

    slug = _slugify(record.get("name") or record["id"])
    config_json = json.dumps(record.get("config") or {})

    try:
        from app.db import execute, fetchrow
        from app.routes._org import get_user_org, resolve_project_id_for_create

        repo = get_repo()
        org_id = await get_user_org(identity.user_id, repo)
        project_id = await resolve_project_id_for_create(org_id, request)

        row = await fetchrow(
            """
            INSERT INTO watches
                (id, org_id, project_id, created_by, slug, name, metric_id, config)
            VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5, $6, $7, $8::jsonb)
            ON CONFLICT (org_id, slug) DO UPDATE
                SET name = EXCLUDED.name,
                    metric_id = EXCLUDED.metric_id,
                    config = EXCLUDED.config,
                    updated_at = now()
            RETURNING id
            """,
            str(uuid.uuid4()),
            org_id,
            project_id,
            identity.user_id,
            slug,
            record.get("name"),
            record.get("metric_id"),
            config_json,
        )
        if row is not None and row.get("id"):
            return str(row["id"])
        await execute("SELECT 1")
    except Exception:  # noqa: BLE001 — persistence is best-effort.
        pass
    return record["id"]


async def _hydrate_watch(watch_id: str) -> dict[str, Any] | None:
    """Lazily load one watch from the DB on a registry miss (best-effort)."""
    try:
        from app.db import fetchrow

        row = await fetchrow(
            "SELECT id, name, metric_id, config FROM watches WHERE id = $1::uuid",
            watch_id,
        )
    except Exception:  # noqa: BLE001
        return None
    if row is None:
        return None
    import json

    config = row["config"]
    if isinstance(config, str):
        try:
            config = json.loads(config)
        except (ValueError, TypeError):
            config = {}
    record = {
        "id": str(row["id"]),
        "name": row["name"],
        "metric_id": row["metric_id"],
        "config": config or {},
    }
    return _registry_put(record)


async def _resolve_watch(watch_id: str) -> dict[str, Any]:
    record = _registry_get(watch_id) or await _hydrate_watch(watch_id)
    if record is None:
        raise AppError("watch_not_found", f"No watch found for id={watch_id!r}.", 404)
    return record


def _watch_from_record(record: dict[str, Any]) -> Watch:
    return Watch.from_config(
        id=record["id"],
        name=record.get("name") or record["id"],
        metric_id=record.get("metric_id"),
        config=record.get("config") or {},
    )


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class WatchIn(BaseModel):
    """Request body for POST/PUT /watches.

    ``name`` + ``metric_id`` identify the watch and the metric it monitors;
    ``config`` carries the runtime rule (dimensions, time_grain, threshold OR
    comparison, channel_config, enabled). Extra keys are allowed and folded into
    ``config`` when not at the top level.
    """

    model_config = {"extra": "allow"}

    name: str = ""
    metric_id: str = ""
    config: dict[str, Any] = {}


def _build_record(data: dict[str, Any], *, watch_id: str) -> dict[str, Any]:
    """Validate a request body into a watch record (raises AppError on bad input)."""
    name = str(data.get("name") or "").strip()
    if not name:
        raise AppError("validation_error", "name must not be empty.", 400)
    metric_id = str(data.get("metric_id") or "").strip()
    if not metric_id:
        raise AppError("validation_error", "metric_id must not be empty.", 400)

    config = dict(data.get("config") or {})
    # Tolerate top-level rule keys (dimensions/time_grain/threshold/...).
    for key in (
        "dimensions",
        "time_grain",
        "threshold",
        "comparison",
        "change",
        "channel_config",
        "channel",
        "enabled",
    ):
        if key in data and key not in config:
            config[key] = data[key]

    # A watch must carry SOME breach rule.
    if not (config.get("threshold") or config.get("comparison") or config.get("change")):
        raise AppError(
            "invalid_watch",
            "A watch must declare a threshold or a comparison/change rule.",
            400,
        )

    return {
        "id": watch_id,
        "name": name,
        "metric_id": metric_id,
        "config": config,
    }


def _record_view(record: dict[str, Any]) -> dict[str, Any]:
    """Public JSON shape of a watch record."""
    return {
        "id": record["id"],
        "name": record.get("name"),
        "metric_id": record.get("metric_id"),
        "config": record.get("config") or {},
    }


# ---------------------------------------------------------------------------
# GET /watches — list
# ---------------------------------------------------------------------------


@api_router.get("/watches")
async def list_watches(
    identity: VerifiedIdentity = Depends(verified_identity),
) -> dict:
    """List watches in the in-process registry (auth mirrors GET /metrics)."""
    _require_read_scope(identity)
    return {"watches": [_record_view(r) for r in _registry_all()]}


# ---------------------------------------------------------------------------
# GET /watches/{id}
# ---------------------------------------------------------------------------


@api_router.get("/watches/{watch_id}")
async def get_watch(
    watch_id: str,
    identity: VerifiedIdentity = Depends(verified_identity),
) -> dict:
    """Return a single watch's full record."""
    _require_read_scope(identity)
    record = await _resolve_watch(watch_id)
    return _record_view(record)


# ---------------------------------------------------------------------------
# POST /watches — create
# ---------------------------------------------------------------------------


@api_router.post("/watches", status_code=201)
async def create_watch(
    body: WatchIn,
    request: Request,
    identity: VerifiedIdentity = Depends(verified_identity),
) -> dict:
    """Create + register a watch. First-party only."""
    _require_first_party_write(identity)

    data = body.model_dump()
    provisional_id = _slugify(str(data.get("name") or ""))
    record = _build_record(data, watch_id=provisional_id)

    canonical_id = await _persist_watch(record, identity, request)
    if canonical_id != record["id"]:
        record["id"] = canonical_id

    _registry_put(record)
    return _record_view(record)


# ---------------------------------------------------------------------------
# PUT /watches/{id} — update
# ---------------------------------------------------------------------------


@api_router.put("/watches/{watch_id}")
async def update_watch(
    watch_id: str,
    body: WatchIn,
    request: Request,
    identity: VerifiedIdentity = Depends(verified_identity),
) -> dict:
    """Update a watch: re-validate, re-register, re-persist."""
    _require_first_party_write(identity)

    data = body.model_dump()
    # Carry existing name/metric forward when the body omits them.
    existing = _registry_get(watch_id) or await _hydrate_watch(watch_id)
    if existing is not None:
        if not str(data.get("name") or "").strip():
            data["name"] = existing.get("name")
        if not str(data.get("metric_id") or "").strip():
            data["metric_id"] = existing.get("metric_id")

    record = _build_record(data, watch_id=watch_id)
    await _persist_watch(record, identity, request)
    _registry_put(record)
    return _record_view(record)


# ---------------------------------------------------------------------------
# DELETE /watches/{id}
# ---------------------------------------------------------------------------


@api_router.delete("/watches/{watch_id}")
async def delete_watch(
    watch_id: str,
    identity: VerifiedIdentity = Depends(verified_identity),
) -> dict:
    """Unregister the watch and delete its persisted row (best-effort)."""
    _require_first_party_write(identity)

    _registry_del(watch_id)
    try:
        from app.db import execute

        await execute("DELETE FROM watches WHERE id = $1::uuid", watch_id)
    except Exception:  # noqa: BLE001 — row deletion is best-effort.
        pass
    return {"id": watch_id, "deleted": True}


# ---------------------------------------------------------------------------
# POST /watches/{id}/evaluate — evaluate now
# ---------------------------------------------------------------------------


async def _resolve_metric_for_watch(metric_id: str):
    registry = get_metric_registry()
    metric = registry.get(metric_id) or await ensure_persisted_metric(metric_id)
    if metric is None:
        raise AppError(
            "metric_not_found",
            f"Watch references unknown metric id={metric_id!r}.",
            404,
        )
    return metric


@api_router.post("/watches/{watch_id}/evaluate")
async def evaluate_watch_now(
    watch_id: str,
    identity: VerifiedIdentity = Depends(verified_identity),
) -> dict:
    """Evaluate the watch NOW and return the run summary.

    Runs the full ``run_watch`` pass: evaluate the metric (RLS via
    ``identity.policies``) → on breach, generate the explanation + dispatch the
    alert. Returns ``{breached, value, state, explanation?, sent, result}``.
    """
    _require_read_scope(identity)
    record = await _resolve_watch(watch_id)
    watch = _watch_from_record(record)
    metric = await _resolve_metric_for_watch(watch.metric_id)

    claims = {"policies": identity.policies}
    summary = await run_watch(watch, metric, claims)
    summary["id"] = watch.id
    return summary


# ---------------------------------------------------------------------------
# POST /watches/tick — shared-secret bulk evaluate (mirror flows_tick)
# ---------------------------------------------------------------------------


@api_router.post("/watches/tick")
async def watches_tick(
    x_nubi_tick_secret: str | None = Header(default=None),
) -> dict:
    """Evaluate all ENABLED in-process watches (shared-secret gated).

    Mirrors the flows tick: a ``X-Nubi-Tick-Secret`` header must match the
    ``WATCHES_TICK_SECRET`` (or ``FLOWS_TICK_SECRET``) env var. Each enabled
    watch is evaluated with empty RLS policies (system context) — best-effort;
    one failure never aborts the sweep. Returns per-watch summaries.

    NOTE: this is a lightweight global sweep over the in-process registry rather
    than a DB-driven due-scan; per-tenant scheduling/RLS for a production tick
    would resolve each watch's owning identity, which is out of scope here.
    """
    secret = os.getenv("WATCHES_TICK_SECRET") or os.getenv("FLOWS_TICK_SECRET")
    if not secret or x_nubi_tick_secret != secret:
        raise AppError("forbidden", "Invalid or missing tick secret.", 403)

    results: list[dict[str, Any]] = []
    for record in _registry_all():
        watch = _watch_from_record(record)
        if not watch.enabled:
            continue
        try:
            metric = await _resolve_metric_for_watch(watch.metric_id)
            summary = await run_watch(watch, metric, {"policies": []})
            summary["id"] = watch.id
            results.append(summary)
        except Exception as exc:  # noqa: BLE001 — never abort the sweep.
            logger.warning("watches_tick: watch %s failed: %s", watch.id, exc)
            results.append({"id": watch.id, "state": "error", "error": str(exc)})
    return {"evaluated": len(results), "watches": results}
