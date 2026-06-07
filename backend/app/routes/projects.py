"""Projects routes — all endpoints under /projects.

A *project* is the workspace / deploy / git unit that groups resources within
an org. Every org owns at least one project (a "Default" project created at
org-creation time), so deleting the last project is blocked.

Endpoints
---------
GET    /projects          — list the projects in the caller's org.
POST   /projects {name}   — create a project (slugified, unique per org).
GET    /projects/{id}     — fetch a single project (404 if wrong org / missing).
PUT    /projects/{id}     — update name and/or git config.
DELETE /projects/{id}     — delete (blocked when it is the org's last project).

Authentication
--------------
Every endpoint requires a valid first-party Bearer token (``current_user``).
The active org is resolved via ``resolve_org_id`` (honours the ``X-Org-Id``
header, membership-checked) — identical to the resource CRUD routes.

This module attaches itself to the shared ``api_router`` at import time so
that ``main.py``'s ``include_router(api_router, prefix="/api/v1")`` picks it
up automatically. It MUST be registered before the generic /{resource}
catch-all in resources.py.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from app.auth.deps import current_user
from app.errors import AppError
from app.repos import projects as projects_repo
from app.repos.provider import Repo, get_repo
from app.routes import api_router

# ── Sub-router ────────────────────────────────────────────────────────────────
router = APIRouter(prefix="/projects", tags=["projects"])


async def _resolve_org_id(user_id: str, repo: Repo, request: Request) -> str:
    """Resolve the active org for the request (honours ``X-Org-Id``).

    Imported lazily from ``routes.resources`` so that importing this module does
    NOT pull in resources.py at load time — that would let the generic
    ``/{resource}`` catch-all self-register ahead of the ``/projects`` routes.
    """
    from app.routes.resources import resolve_org_id  # noqa: PLC0415

    return await resolve_org_id(user_id, repo, request)


# ── Pydantic request schemas ──────────────────────────────────────────────────

class CreateProjectIn(BaseModel):
    """Request body for POST /projects."""

    name: str
    git: dict[str, Any] | None = None


class UpdateProjectIn(BaseModel):
    """Request body for PUT /projects/{id}."""

    name: str | None = None
    git: dict[str, Any] | None = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("")
async def list_projects(
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> list[dict[str, Any]]:
    """List all projects for the caller's org (default project first)."""
    org_id = await _resolve_org_id(str(user["id"]), repo, request)
    return await projects_repo.list_projects(org_id)


@router.post("", status_code=201)
async def create_project(
    body: CreateProjectIn,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Create a new project in the caller's org (slug unique per org)."""
    name = body.name.strip()
    if not name:
        raise AppError("invalid_request", "Project name is required.", 400)
    org_id = await _resolve_org_id(str(user["id"]), repo, request)
    return await projects_repo.create_project(
        org_id=org_id,
        name=name,
        created_by=str(user["id"]),
        git=body.git,
    )


@router.get("/{project_id}")
async def get_project(
    project_id: str,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Fetch a single project. Returns 404 if missing or in a different org."""
    org_id = await _resolve_org_id(str(user["id"]), repo, request)
    proj = await projects_repo.get_project(org_id, project_id)
    if proj is None:
        raise AppError("not_found", "Project not found.", 404)
    return proj


@router.put("/{project_id}")
async def update_project(
    project_id: str,
    body: UpdateProjectIn,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Update a project's ``name`` and/or ``git`` config."""
    org_id = await _resolve_org_id(str(user["id"]), repo, request)

    fields: dict[str, Any] = {}
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise AppError("invalid_request", "Project name cannot be empty.", 400)
        fields["name"] = name
    if body.git is not None:
        fields["git"] = body.git

    proj = await projects_repo.update_project(org_id, project_id, fields)
    if proj is None:
        raise AppError("not_found", "Project not found.", 404)
    return proj


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: str,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> Response:
    """Delete a project.

    Blocked (409) when it is the org's last/only project — every org must keep
    at least one (default) project so resource creation always has a home.
    """
    org_id = await _resolve_org_id(str(user["id"]), repo, request)

    existing = await projects_repo.get_project(org_id, project_id)
    if existing is None:
        raise AppError("not_found", "Project not found.", 404)

    if await projects_repo.count_projects(org_id) <= 1:
        raise AppError(
            "cannot_delete_last_project",
            "Cannot delete the last project in an organisation.",
            409,
        )

    deleted = await projects_repo.delete_project(org_id, project_id)
    if not deleted:
        raise AppError("not_found", "Project not found.", 404)
    return Response(status_code=204)


# ── Register on the shared api_router ─────────────────────────────────────────
api_router.include_router(router)
