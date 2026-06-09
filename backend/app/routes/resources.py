"""Generic CRUD router for org-scoped resources.

Exposes five endpoints for each resource in the allowlist
(``datastores``, ``boards``, ``queries``, ``widgets``)::

    GET    /{resource}         — list all rows for the caller's org.
    POST   /{resource}         — create a new row; returns 201.
    GET    /{resource}/{id}    — fetch a single row (404 if wrong org or missing).
    PUT    /{resource}/{id}    — update name/config (404 if wrong org or missing).
    DELETE /{resource}/{id}    — delete the row; returns 204.

Authentication
--------------
Every endpoint requires a valid first-party Bearer token (``current_user``
dependency).  The caller's ``org_id`` is resolved via ``resolve_org_id``
which honours the ``X-Org-Id`` request header when present (the user must be
a member of that org — otherwise 403; or falls back to their default org).

Cross-org protection
--------------------
``get`` / ``update`` / ``delete`` return 404 (not 403) for rows that exist
but belong to a different org — no information leaks about other orgs'
resources.

Unknown resource names in the URL path also return 404.

This module attaches itself to the shared ``api_router`` at import time so
that ``main.py``'s ``include_router(api_router, prefix="/api/v1")`` picks
it up automatically.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from app.auth.deps import current_user
from app.auth.roles import require_writer
from app.db import fetchrow, fetch
from app.errors import AppError
from app.repos.base import VALID_RESOURCES
from app.repos.provider import get_repo, Repo
from app.routes import api_router

# ── Sub-router ────────────────────────────────────────────────────────────────
router = APIRouter(tags=["resources"])


# ── Pydantic request schemas ──────────────────────────────────────────────────

class CreateIn(BaseModel):
    """Request body for POST /{resource}."""

    name: str
    config: dict[str, Any] = {}


class UpdateIn(BaseModel):
    """Request body for PUT /{resource}/{id}."""

    name: str | None = None
    config: dict[str, Any] | None = None


# ── Org / project resolution helpers ──────────────────────────────────────────
# These live in the route-free ``app.routes._org`` module so that other routers
# can reuse them WITHOUT importing this module (which would trigger the greedy
# ``/{resource}`` catch-all to register ahead of their own prefixed routes).
# Re-exported here for backwards compatibility with existing call sites.
from app.routes._org import (  # noqa: E402
    _requested_project_id,
    _user_is_member,
    get_user_org,
    resolve_org_id,
    resolve_project_filter,
    resolve_project_id_for_create,
)


# ── Validation helper ─────────────────────────────────────────────────────────

def _require_valid_resource(resource: str) -> None:
    """Raise AppError 404 if *resource* is not in the allowlist."""
    if resource not in VALID_RESOURCES:
        raise AppError("not_found", f"Unknown resource: {resource!r}.", 404)


# ── Environment / version helpers ─────────────────────────────────────────────

# Versionable resources → polymorphic kind used by the environments store.
_RESOURCE_KIND: dict[str, str] = {"boards": "board", "queries": "query"}


async def _apply_env_resolution(
    resource: str, row: dict[str, Any], org_id: str, env_key: str
) -> None:
    """Resolve ``?env=<key>`` for a get-one response (in place).

    When the resource's project has an environment named *env_key* with a
    pinned version for this row, the response ``config`` is replaced by the
    pinned snapshot and ``resolved_version: {id, version}`` is added.  When no
    pointer exists the draft is returned unchanged with
    ``resolved_version: null``.
    """
    kind = _RESOURCE_KIND.get(resource)
    if kind is None:
        return
    from app.environments.store import get_env_store  # noqa: PLC0415
    from app.repos import projects as projects_repo  # noqa: PLC0415

    row["resolved_version"] = None
    project_id = row.get("project_id") or await projects_repo.get_default_project_id(
        org_id
    )
    if not project_id:
        return
    env_store = get_env_store()
    env = await env_store.get_environment_by_key(str(project_id), env_key)
    if env is None:
        return
    pointer = await env_store.get_pointer(kind, str(row["id"]), env["id"])
    if pointer is None:
        return
    version = await env_store.get_version_by_id(pointer["version_id"])
    if version is None:
        return
    row["config"] = version["config"]
    row["resolved_version"] = {"id": version["id"], "version": version["version"]}


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/{resource}")
async def list_resources(
    resource: str,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> list[dict[str, Any]]:
    """List all rows for the caller's org.

    Honoures the ``X-Org-Id`` header to switch org context (membership checked).

    Returns
    -------
    list[dict]
        Possibly empty list of resource rows.
    """
    _require_valid_resource(resource)
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    project_id = await resolve_project_filter(org_id, request)
    return await repo.list(resource, org_id, project_id)


@router.post("/{resource}", status_code=201, dependencies=[Depends(require_writer)])
async def create_resource(
    resource: str,
    body: CreateIn,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Create a new resource row.

    Honoures the ``X-Org-Id`` header to switch org context (membership checked).

    Returns
    -------
    dict
        The newly created row (includes ``id``, ``created_at``, etc.).
    """
    _require_valid_resource(resource)
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    project_id = await resolve_project_id_for_create(org_id, request)
    return await repo.create(
        resource=resource,
        org_id=org_id,
        created_by=str(user["id"]),
        name=body.name,
        config=body.config,
        project_id=project_id,
    )


@router.get("/{resource}/{id}")
async def get_resource(
    resource: str,
    id: str,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Fetch a single resource row.

    Returns 404 if the row does not exist OR belongs to a different org —
    no cross-org information leaks.
    """
    _require_valid_resource(resource)
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    row = await repo.get(resource, org_id, id)
    if row is None:
        raise AppError("not_found", f"{resource[:-1].capitalize()} not found.", 404)

    # ``?env=<key>``: serve the version pinned to that environment (boards /
    # queries only); draft + resolved_version=null when nothing is pinned.
    env_key = (request.query_params.get("env") or "").strip()
    if env_key:
        await _apply_env_resolution(resource, row, org_id, env_key)
    return row


@router.put("/{resource}/{id}", dependencies=[Depends(require_writer)])
async def update_resource(
    resource: str,
    id: str,
    body: UpdateIn,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Update a resource row's ``name`` and/or ``config``.

    Returns 404 if the row does not exist OR belongs to a different org.
    """
    _require_valid_resource(resource)
    org_id = await resolve_org_id(str(user["id"]), repo, request)

    fields: dict[str, Any] = {}
    if body.name is not None:
        fields["name"] = body.name
    if body.config is not None:
        fields["config"] = body.config

    row = await repo.update(resource, org_id, id, fields)
    if row is None:
        raise AppError("not_found", f"{resource[:-1].capitalize()} not found.", 404)
    return row


@router.delete("/{resource}/{id}", status_code=204, dependencies=[Depends(require_writer)])
async def delete_resource(
    resource: str,
    id: str,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> Response:
    """Delete a resource row.

    Returns 204 on success, 404 if not found or wrong org.
    """
    _require_valid_resource(resource)
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    deleted = await repo.delete(resource, org_id, id)
    if not deleted:
        raise AppError("not_found", f"{resource[:-1].capitalize()} not found.", 404)

    # Best-effort cleanup of versions + environment pointers (polymorphic
    # tables — no FK cascade from the resource row).
    kind = _RESOURCE_KIND.get(resource)
    if kind is not None:
        try:
            from app.environments.store import get_env_store  # noqa: PLC0415

            await get_env_store().delete_resource_data(kind, id)
        except Exception:  # noqa: BLE001 — never fail the delete on cleanup
            pass
    return Response(status_code=204)


# ── Register on the shared api_router ─────────────────────────────────────────
api_router.include_router(router)
