"""Variables REST endpoints — persistent, org/project-scoped key/value store.

Endpoints
---------
GET    /variables                       -> [variable]   (caller's org + scope)
GET    /variables/{key}                 -> variable     (404 if missing / cross-org)
PUT    /variables/{key}   {value, project_id?}  -> variable (upsert, writer role)
DELETE /variables/{key}                 -> 204          (writer role)

Scoping
-------
Variables are org-scoped and optionally project-scoped.  The effective org is
resolved via ``resolve_org_id`` (honours ``X-Org-Id`` with membership check).
The scope is project-scoped when a project is resolved (``X-Project-Id`` /
``?project_id=`` / the org's default project) and org-global otherwise — a
project var and an org-global var with the same key never collide.

Cross-org access returns 404 (no information leak): resolution always scopes to
the caller's org, so another org's variable is simply not found.

Reader role for GETs; ``require_writer`` (header-aware, mirrors
``resolve_org_id``) gates PUT/DELETE so viewers get 403.

This module attaches itself to the shared ``api_router`` at import time.  It is
imported in ``main.py`` BEFORE ``app.routes.resources`` so the concrete
``/variables`` routes register ahead of the generic ``/{resource}`` catch-all.

Variable store
--------------
State is held by the configured store (``get_var_store()``; ``PgVarStore`` in
production).  Tests inject an ``InMemoryVarStore`` via ``set_var_store(store)``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from app.auth.deps import current_user
from app.auth.roles import require_writer
from app.errors import AppError
from app.repos.provider import Repo, get_repo
from app.routes import api_router

# Import-safe org/project helpers (no route registration side effects).
from app.routes._org import (
    resolve_org_id,
    resolve_project_filter,
    resolve_project_id_for_create,
)
from app.vars.store import get_var_store

# ---------------------------------------------------------------------------
# Sub-router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/variables", tags=["variables"])


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class SetVarIn(BaseModel):
    """Request body for PUT /variables/{key}."""

    value: Any = {}
    project_id: str | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("")
async def list_variables(
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> list[dict[str, Any]]:
    """List variables for the caller's org in the resolved scope.

    Honours ``X-Org-Id`` (membership checked) and the project filter
    (``X-Project-Id`` / ``?project_id=`` / default project).
    """
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    project_id = await resolve_project_filter(org_id, request)
    return await get_var_store().list_vars(org_id, project_id)


@router.get("/{key}")
async def get_variable(
    key: str,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Fetch a single variable by key.

    Returns 404 if the variable does not exist in the caller's org+scope —
    including when it belongs to a different org (no cross-org leak).
    """
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    project_id = await resolve_project_filter(org_id, request)
    row = await get_var_store().get_var(org_id, key, project_id)
    if row is None:
        raise AppError("not_found", "Variable not found.", 404)
    return row


@router.put("/{key}", dependencies=[Depends(require_writer)])
async def set_variable(
    key: str,
    body: SetVarIn,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Upsert a variable's ``value`` for the resolved org+scope (writer role).

    The project scope is resolved from the body's ``project_id`` (when set) or
    the request's project context (``X-Project-Id`` / ``?project_id=`` /
    default project); org-global when none resolves.
    """
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    project_id = await _resolve_set_project(org_id, body.project_id, request)
    return await get_var_store().set_var(
        org_id,
        key,
        body.value,
        project_id=project_id,
        updated_by=str(user["id"]),
    )


@router.delete("/{key}", status_code=204, dependencies=[Depends(require_writer)])
async def delete_variable(
    key: str,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> Response:
    """Delete a variable (writer role).

    Returns 204 on success, 404 if not found in the caller's org+scope.
    """
    org_id = await resolve_org_id(str(user["id"]), repo, request)
    project_id = await resolve_project_filter(org_id, request)
    deleted = await get_var_store().delete_var(org_id, key, project_id)
    if not deleted:
        raise AppError("not_found", "Variable not found.", 404)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _resolve_set_project(
    org_id: str, body_project_id: str | None, request: Request
) -> str | None:
    """Resolve the project scope for a PUT upsert.

    A ``project_id`` in the body takes precedence (validated against the org);
    otherwise we fall back to the request's project context. Returns ``None``
    for an org-global write.
    """
    if body_project_id is not None:
        from app.repos import projects as projects_repo  # noqa: PLC0415

        if await projects_repo.project_belongs_to_org(body_project_id, org_id):
            return body_project_id
        # Foreign / invalid project_id → treat as org-global rather than write
        # into another org's scope.
        return None
    return await resolve_project_id_for_create(org_id, request)


# ---------------------------------------------------------------------------
# Register on the shared api_router
# ---------------------------------------------------------------------------
api_router.include_router(router)
