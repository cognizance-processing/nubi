"""Projects routes — all endpoints under /projects.

A *project* is the workspace / deploy / git unit that groups resources within
an org. Every org owns at least one project (a "Default" project created at
org-creation time), so deleting the last project is blocked.

Endpoints
---------
GET    /projects                          — list the projects in the caller's org.
POST   /projects {name}                   — create a project (slugified, unique per org).
GET    /projects/{id}                     — fetch a single project (404 if wrong org / missing).
PATCH  /projects/{id}                     — rename a project (partial update).
PUT    /projects/{id}                     — update name and/or git config (full update).
GET    /projects/{id}/deletion-impact     — describe what deleting the project would affect.
DELETE /projects/{id}                     — delete (requires confirm_name; blocked when last project).

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


class PatchProjectIn(BaseModel):
    """Request body for PATCH /projects/{id}."""

    name: str | None = None
    git: dict[str, Any] | None = None


class DeleteProjectIn(BaseModel):
    """Request body for DELETE /projects/{id}."""

    confirm_name: str


# ── Pydantic response schemas ─────────────────────────────────────────────────

class ProjectImpactBlocker(BaseModel):
    """A blocker that prevents deletion."""

    type: str
    count: int
    reason: str


class ProjectImpactDelete(BaseModel):
    """A resource category that would be deleted."""

    type: str
    count: int


class ProjectDeletionImpactResponse(BaseModel):
    """Response body for GET /projects/{id}/deletion-impact."""

    can_delete: bool
    blockers: list[ProjectImpactBlocker]
    deletes: list[ProjectImpactDelete]
    name: str


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


@router.get("/{project_id}/deletion-impact", response_model=ProjectDeletionImpactResponse)
async def get_project_deletion_impact(
    project_id: str,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> ProjectDeletionImpactResponse:
    """Return a summary of what deleting this project would affect.

    Describes the number of dependent resources per type. A project that is
    the org's only project cannot be deleted (``can_delete=false`` with a
    ``last_project`` blocker).

    Parameters
    ----------
    project_id:
        UUID of the project to inspect.

    Returns
    -------
    200 {can_delete, blockers, deletes, name}

    Raises
    ------
    AppError("not_found", 404)
        If the project doesn't exist in the caller's org.
    """
    org_id = await _resolve_org_id(str(user["id"]), repo, request)
    proj = await projects_repo.get_project(org_id, project_id)
    if proj is None:
        raise AppError("not_found", "Project not found.", 404)

    proj_name = str(proj["name"])
    blockers: list[ProjectImpactBlocker] = []
    deletes: list[ProjectImpactDelete] = []

    # Block deletion of the last project in an org.
    total_projects = await projects_repo.count_projects(org_id)
    if total_projects <= 1:
        blockers.append(
            ProjectImpactBlocker(
                type="last_project",
                count=1,
                reason="Cannot delete the last project in an organisation.",
            )
        )

    # Count project-scoped resources that would be deleted.
    # Use repo.list only for resource types in the allowlist; others are skipped
    # gracefully (InMemory has no flows/secrets table; PgRepo enforces the same
    # allowlist). The impact list is best-effort — it reports what the repo knows.
    from app.repos.base import VALID_RESOURCES  # noqa: PLC0415

    for resource_type in ["datastores", "boards", "queries", "widgets"]:
        if resource_type not in VALID_RESOURCES:
            continue
        try:
            count = len(await repo.list(resource_type, org_id, project_id))
        except Exception:
            count = 0
        if count > 0:
            deletes.append(ProjectImpactDelete(type=resource_type, count=count))

    can_delete = len(blockers) == 0
    return ProjectDeletionImpactResponse(
        can_delete=can_delete,
        blockers=blockers,
        deletes=deletes,
        name=proj_name,
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


@router.patch("/{project_id}")
async def patch_project(
    project_id: str,
    body: PatchProjectIn,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Partially update a project's ``name`` and/or ``git`` config.

    Identical to PUT but accepts a partial body — fields not present in the
    request are left unchanged.

    Parameters
    ----------
    project_id:
        UUID of the project to update.
    body.name:
        New name (optional).
    body.git:
        New git config dict (optional).

    Returns
    -------
    200 updated project dict

    Raises
    ------
    AppError("not_found", 404)
        If the project doesn't exist in the caller's org.
    AppError("invalid_request", 400)
        If ``name`` is provided but empty.
    """
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
    body: DeleteProjectIn,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> Response:
    """Delete a project.

    Rules
    -----
    1.  The project must exist in the caller's org.
    2.  The project must not be the org's last/only project — every org must
        keep at least one project so resource creation always has a home.
    3.  The caller must supply ``confirm_name`` matching the project's exact
        name (case-sensitive). Mismatch → 422.
    4.  On success, the project row is deleted; project-scoped resources with
        ``ON DELETE SET NULL`` on ``project_id`` are unlinked (not removed)
        unless they have stricter FK rules.

    Parameters
    ----------
    project_id:
        UUID of the project to delete.
    body.confirm_name:
        Must exactly equal the project's current name.

    Returns
    -------
    204 No Content

    Raises
    ------
    AppError("not_found", 404)
        If the project doesn't exist in the caller's org.
    AppError("cannot_delete_last_project", 409)
        If it is the org's last/only project.
    AppError("confirm_name_mismatch", 422)
        If ``confirm_name`` does not match the project's name exactly.
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

    # Confirm name must match exactly.
    proj_name = str(existing["name"])
    if body.confirm_name != proj_name:
        raise AppError(
            "confirm_name_mismatch",
            (
                f"The confirmation name '{body.confirm_name}' does not match "
                f"the project name '{proj_name}'. Please type the exact project name to confirm deletion."
            ),
            422,
        )

    deleted = await projects_repo.delete_project(org_id, project_id)
    if not deleted:
        raise AppError("not_found", "Project not found.", 404)
    return Response(status_code=204)


# ── Sample bundle remove / restore ────────────────────────────────────────────

@router.post("/sample/remove")
async def remove_sample(
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Bulk-remove every ``sample=true`` resource for the active org/project.

    Scoped to the requested project (``X-Project-Id`` / ``?project_id=``) when
    present, otherwise the org's default project. Idempotent.
    """
    from app.routes.resources import resolve_project_filter  # noqa: PLC0415
    from app.sample import remove_sample_bundle  # noqa: PLC0415

    org_id = await _resolve_org_id(str(user["id"]), repo, request)
    project_id = await resolve_project_filter(org_id, request)
    counts = await remove_sample_bundle(org_id, project_id, repo)
    return {"removed": counts, "project_id": project_id}


@router.post("/sample/restore")
async def restore_sample(
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Re-seed the onboarding sample bundle for the active org/project.

    Idempotent — re-running when the bundle still exists is a no-op (existing
    rows are reused, nothing duplicated).
    """
    from app.routes.resources import resolve_project_id_for_create  # noqa: PLC0415
    from app.sample import seed_sample_bundle  # noqa: PLC0415

    org_id = await _resolve_org_id(str(user["id"]), repo, request)
    project_id = await resolve_project_id_for_create(org_id, request)
    summary = await seed_sample_bundle(
        org_id=org_id,
        project_id=project_id,
        created_by=str(user["id"]),
        repo=repo,
    )
    return summary


# ── Register on the shared api_router ─────────────────────────────────────────
api_router.include_router(router)
