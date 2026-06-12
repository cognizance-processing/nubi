"""Managed lakehouse provisioning entrypoint (``/lakehouse``).

A managed lakehouse is a Nubi-managed, ISOLATED storage area you provision /
use / delete WITHOUT handling buckets or credentials yourself. As of the
multi-instance model it is just a **normal connector**:

* **Existence == provisioned.** ``POST /lakehouse/provision`` creates a NEW
  managed-lakehouse connector each call (multiple may coexist per org) and
  returns its row. An optional ``{name}`` body names the card.
* **Deletion == deprovisioned.** ``DELETE /connectors/{id}`` on a managed row
  deletes its storage objects + the row + its secret (see ``routes/connectors``).
* **Surfaces in the normal list.** Managed rows appear in ``GET /connectors``
  carrying ``config.managed_lake: true`` + ``usage_bytes``/``usage_gb`` so the UI
  renders them as distinct cards.

``GET /lakehouse`` is kept as a convenience that returns the LIST of the caller's
managed lakehouses (with usage) — the singleton status/deprovision/demo routes
are gone; the managed lake is no longer a per-org singleton.

Isolation + security
--------------------
Each managed lake is the isolated prefix ``orgs/<org_id>/lake/<datastore_id>/``
in the central bucket — server-pinned from the trusted org id + the row's OWN id,
never user input. Central credentials live encrypted in the secret store and are
never returned. All ops are org-scoped (cross-org → 404 / no-op).

Degrade
-------
When no central bucket/creds are configured (OSS local dev), provision returns
409 explaining the managed lakehouse needs central storage (BYO connectors still
work); ``GET /lakehouse`` returns ``{configured: false, lakehouses: []}``.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, Request

from app.auth.deps import current_user
from app.auth.roles import require_writer
from app.errors import AppError
from app.lakehouse.managed import (
    ManagedLakehouseError,
    emit_storage_usage,
    get_provider,
    usage_fields,
)
from app.repos.provider import Repo, get_repo
from app.routes import api_router
from app.routes._org import resolve_org_id

logger = logging.getLogger("nubi.routes.lakehouse")

router = APIRouter(prefix="/lakehouse", tags=["lakehouse"])


async def _caller_org(user: dict[str, Any], request: Request, repo: Repo) -> str:
    return await resolve_org_id(str(user["id"]), repo, request)


def _row_with_usage(row: dict[str, Any], used_bytes: int) -> dict[str, Any]:
    """Return the managed row annotated with usage_bytes/usage_gb (no secrets)."""
    cfg = row.get("config")
    safe_cfg = dict(cfg) if isinstance(cfg, dict) else {}
    safe_cfg.pop("aws_secret_access_key", None)
    out = dict(row)
    out["config"] = safe_cfg
    out.update(usage_fields(used_bytes))
    return out


@router.get("")
async def list_lakehouses(
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """List the caller's managed lakehouses (each with on-demand usage).

    Replaces the old singleton status route — managed lakehouses are now normal,
    multi-instance connectors. ``configured: false`` when central storage is not
    available on this deployment (degrade path).
    """
    org_id = await _caller_org(user, request, repo)

    provider = get_provider(repo)
    if provider is None:
        return {
            "configured": False,
            "lakehouses": [],
            "detail": (
                "Managed lakehouse is not available: no central storage is "
                "configured on this deployment. You can still connect your own "
                "bucket (BYO) via /connectors."
            ),
        }

    rows = await provider.list_managed(org_id)
    out: list[dict[str, Any]] = []
    total = 0
    for row in rows:
        used = await provider.usage_bytes(org_id, str(row["id"]))
        total += used
        out.append(_row_with_usage(row, used))
    if total:
        await emit_storage_usage(org_id, str(user["id"]), total)
    return {"configured": True, "lakehouses": out}


@router.post("/provision", dependencies=[Depends(require_writer)])
async def provision_lakehouse(
    request: Request,
    seed_demo: bool = False,
    body: dict[str, Any] | None = Body(default=None),
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Provision a NEW managed lakehouse connector for the caller's org.

    Multi-instance: each call creates a fresh managed connector (the prefix is
    pinned to its own id). Optional ``{name}`` body names the card.
    ``?seed_demo=true`` also exports the demo datasets into the new lake.
    """
    org_id = await _caller_org(user, request, repo)

    provider = get_provider(repo)
    if provider is None:
        raise AppError(
            "managed_lake_unconfigured",
            "Managed lakehouse needs central storage configured on this "
            "deployment. Connect your own bucket (BYO) via /connectors instead.",
            409,
        )

    name = None
    if isinstance(body, dict):
        raw = body.get("name")
        if isinstance(raw, str) and raw.strip():
            name = raw.strip()

    project_id = _resolve_project(request)
    try:
        row = await provider.provision(org_id, project_id, str(user["id"]), name=name)
        if seed_demo:
            await provider.seed_demo(org_id, str(row["id"]), project_id, str(user["id"]))
    except ManagedLakehouseError as exc:
        raise AppError(exc.code, exc.message, exc.status) from exc

    used = await provider.usage_bytes(org_id, str(row["id"]))
    if used:
        await emit_storage_usage(org_id, str(user["id"]), used)
    return _row_with_usage(row, used)


def _resolve_project(request: Request) -> str | None:
    """Best-effort project id from the X-Project-Id / ?project_id surface."""
    pid = request.headers.get("x-project-id", "").strip()
    if pid:
        return pid
    return (request.query_params.get("project_id") or "").strip() or None


# ── Register on the shared api_router ─────────────────────────────────────────

api_router.include_router(router)
