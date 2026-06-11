"""Managed lakehouse endpoints (``/lakehouse``) — provision / use / delete a
Nubi-managed, per-org ISOLATED storage area without handling buckets yourself.

Routes (all under ``/api/v1``)
------------------------------
- ``GET    /lakehouse``           — status: configured?, provisioned?, prefix,
                                    datastore id, demo_seeded?, usage_bytes/gb.
- ``POST   /lakehouse/provision`` — idempotently provision the managed lake.
                                    ``?seed_demo=true`` also seeds demo parquet.
- ``POST   /lakehouse/demo``      — seed demo parquet into the managed lake.
- ``DELETE /lakehouse``           — deprovision: delete the org's objects + row.

Isolation + security
--------------------
Each org's managed lake is the isolated prefix ``orgs/<org_id>/lake/`` in the
central bucket. The storage path is SERVER-PINNED from the resolved org id — a
caller can never repoint it cross-org or at an arbitrary URL (the connectors PUT
route also refuses managed rows). Central credentials live encrypted in the
secret store and are never returned. All ops are org-scoped via the standard
``current_user`` + ``resolve_org_id`` pattern; another org's lake is invisible.

Degrade
-------
When no central bucket/creds are configured (OSS local dev), ``GET /lakehouse``
returns ``{configured: false}`` and provision/demo return 409 explaining that
the managed lakehouse needs central storage (BYO connectors still work).

Self-registers on the shared ``api_router`` at import time with the literal
``/lakehouse`` prefix — imported in ``main.py`` before the ``/{resource}``
catch-all (mirrors ``connectors`` / ``usage`` / ``watches``).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from app.auth.deps import current_user
from app.auth.roles import require_writer
from app.errors import AppError
from app.lakehouse.managed import (
    ManagedLakehouseError,
    emit_storage_usage,
    get_provider,
)
from app.repos.provider import Repo, get_repo
from app.routes import api_router
from app.routes._org import resolve_org_id

logger = logging.getLogger("nubi.routes.lakehouse")

router = APIRouter(prefix="/lakehouse", tags=["lakehouse"])


async def _caller_org(user: dict[str, Any], request: Request, repo: Repo) -> str:
    return await resolve_org_id(str(user["id"]), repo, request)


def _not_configured_payload(org_id: str) -> dict[str, Any]:
    """Status body when central storage is unconfigured (degrade path)."""
    return {
        "configured": False,
        "provisioned": False,
        "datastore_id": None,
        "prefix": None,
        "uri": None,
        "demo_seeded": False,
        "usage_bytes": 0,
        "usage_gb": 0.0,
        "detail": (
            "Managed lakehouse is not available: no central storage is configured "
            "on this deployment. You can still connect your own bucket (BYO) via "
            "/connectors."
        ),
    }


@router.get("")
async def get_lakehouse(
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Return the caller's managed-lakehouse status + on-demand usage_bytes."""
    org_id = await _caller_org(user, request, repo)

    provider = get_provider(repo)
    if provider is None:
        return _not_configured_payload(org_id)

    status = await provider.status(org_id)
    body = status.to_dict()

    if status.provisioned:
        # Compute usage on demand (not on every request elsewhere) and emit a
        # storage snapshot so GET /usage reflects the managed lake.
        used = await provider.usage_bytes(org_id)
        body["usage_bytes"] = used
        body["usage_gb"] = round(used / (1024.0 ** 3), 6)
        await emit_storage_usage(org_id, str(user["id"]), used)

    return body


@router.post("/provision", dependencies=[Depends(require_writer)])
async def provision_lakehouse(
    request: Request,
    seed_demo: bool = False,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Idempotently provision the managed lake for the caller's org.

    ``?seed_demo=true`` also exports the demo datasets into it.
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

    project_id = _resolve_project(request)
    try:
        await provider.provision(org_id, project_id, str(user["id"]))
        if seed_demo:
            await provider.seed_demo(org_id, project_id, str(user["id"]))
    except ManagedLakehouseError as exc:
        raise AppError(exc.code, exc.message, exc.status) from exc

    status = await provider.status(org_id)
    body = status.to_dict()
    used = await provider.usage_bytes(org_id)
    body["usage_bytes"] = used
    body["usage_gb"] = round(used / (1024.0 ** 3), 6)
    await emit_storage_usage(org_id, str(user["id"]), used)
    return body


@router.post("/demo", dependencies=[Depends(require_writer)])
async def seed_lakehouse_demo(
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Seed the demo datasets into the caller's managed lake (idempotent)."""
    org_id = await _caller_org(user, request, repo)

    provider = get_provider(repo)
    if provider is None:
        raise AppError(
            "managed_lake_unconfigured",
            "Managed lakehouse needs central storage configured on this deployment.",
            409,
        )

    project_id = _resolve_project(request)
    try:
        result = await provider.seed_demo(org_id, project_id, str(user["id"]))
    except ManagedLakehouseError as exc:
        raise AppError(exc.code, exc.message, exc.status) from exc

    used = await provider.usage_bytes(org_id)
    await emit_storage_usage(org_id, str(user["id"]), used)
    result["usage_bytes"] = used
    return result


@router.delete("", status_code=204, dependencies=[Depends(require_writer)])
async def deprovision_lakehouse(
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> Response:
    """Deprovision: delete the org's objects under its prefix + the managed row.

    Idempotent — returns 204 even if nothing was provisioned (and cross-org is a
    no-op because the lookup is org-scoped).
    """
    org_id = await _caller_org(user, request, repo)

    provider = get_provider(repo)
    if provider is None:
        # Nothing to remove when unconfigured — treat as a successful no-op.
        return Response(status_code=204)

    await provider.deprovision(org_id)
    return Response(status_code=204)


def _resolve_project(request: Request) -> str | None:
    """Best-effort project id from the X-Project-Id / ?project_id surface."""
    pid = request.headers.get("x-project-id", "").strip()
    if pid:
        return pid
    return (request.query_params.get("project_id") or "").strip() or None


# ── Register on the shared api_router ─────────────────────────────────────────

api_router.include_router(router)
