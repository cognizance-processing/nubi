"""Canvas resource CRUD + validation endpoints (Canvas Wave 1).

Endpoints
---------
POST /canvas/validate
    Stateless validation oracle for CanvasDoc (mirrors POST /dashboards/validate).
    Never persists anything.  Returns ``{valid, errors, warnings}``.

GET /canvases
    List all canvases for the caller's org.

POST /canvases
    Create a new canvas.

GET /canvases/{canvas_id}
    Retrieve a canvas by id (org-scoped).

PUT /canvases/{canvas_id}
    Update name and/or config for an existing canvas.

DELETE /canvases/{canvas_id}
    Delete a canvas (org-scoped).

All mutation endpoints require a valid first-party Bearer token.  The org is
resolved from the token; cross-org access is impossible by design.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Depends
from pydantic import BaseModel

from app.auth.deps import current_user
from app.errors import AppError
from app.repos.provider import Repo, get_repo
from app.routes import api_router

logger = logging.getLogger("nubi.routes.canvas")


# ---------------------------------------------------------------------------
# Org resolution helper
# ---------------------------------------------------------------------------


async def _get_org(user: dict[str, Any]) -> str:
    """Resolve the caller's org_id from their user record.

    Mirrors the pattern used by ``app.routes._org.get_user_org``.
    Raises ``AppError("org_not_found", 404)`` when the user has no org.
    """
    try:
        from app.routes._org import get_user_org  # noqa: PLC0415

        org_id = await get_user_org(str(user["id"]), get_repo())
        if not org_id:
            raise AppError("org_not_found", "User has no organisation.", 404)
        return org_id
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AppError("org_not_found", f"Could not resolve org: {exc}", 404) from exc


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class CanvasValidateRequest(BaseModel):
    """Request body for POST /canvas/validate."""

    doc: dict[str, Any]


class CanvasValidateResponse(BaseModel):
    """Response body for POST /canvas/validate."""

    valid: bool
    errors: list[str]
    warnings: list[str]


class CanvasCreateRequest(BaseModel):
    """Request body for POST /canvases."""

    name: str
    config: dict[str, Any] = {}


class CanvasUpdateRequest(BaseModel):
    """Request body for PUT /canvases/{canvas_id}."""

    name: str | None = None
    config: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# POST /canvas/validate — stateless oracle
# ---------------------------------------------------------------------------


@api_router.post(
    "/canvas/validate",
    response_model=CanvasValidateResponse,
    tags=["canvas"],
)
async def validate_canvas(
    body: CanvasValidateRequest,
    _user: dict[str, Any] = Depends(current_user),
) -> CanvasValidateResponse:
    """Validate a CanvasDoc and return issues split into errors and warnings.

    This endpoint is **read-only** — it validates and reports; it never saves
    anything.  Mirrors ``POST /dashboards/validate``.

    Parameters
    ----------
    body:
        ``{doc: <canvas doc dict>}``.
    _user:
        Injected by ``current_user``; ensures the endpoint requires auth.

    Returns
    -------
    CanvasValidateResponse
        ``{valid, errors, warnings}``
    """
    from app.dashboards.canvas import CanvasDoc, validate_canvas_doc  # noqa: PLC0415

    # Parse the CanvasDoc — Pydantic parse errors are hard errors.
    try:
        doc = CanvasDoc.model_validate(body.doc)
    except Exception as exc:  # noqa: BLE001
        return CanvasValidateResponse(
            valid=False,
            errors=[f"Canvas doc parse error: {exc}"],
            warnings=[],
        )

    _ok, issues = validate_canvas_doc(doc, org_id=None)

    errors = [i for i in issues if not i.lstrip().lower().startswith("[warn]")]
    warnings = [i for i in issues if i.lstrip().lower().startswith("[warn]")]

    return CanvasValidateResponse(
        valid=not errors,
        errors=errors,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# GET /canvases — list
# ---------------------------------------------------------------------------


@api_router.get("/canvases", tags=["canvas"])
async def list_canvases(
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> list[dict[str, Any]]:
    """List all canvases for the caller's org."""
    org_id = await _get_org(user)
    return await repo.list("canvases", org_id)


# ---------------------------------------------------------------------------
# POST /canvases — create
# ---------------------------------------------------------------------------


@api_router.post("/canvases", tags=["canvas"], status_code=201)
async def create_canvas(
    body: CanvasCreateRequest,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Create a new canvas resource."""
    org_id = await _get_org(user)
    return await repo.create(
        "canvases",
        org_id=org_id,
        created_by=str(user["id"]),
        name=body.name,
        config=body.config,
    )


# ---------------------------------------------------------------------------
# GET /canvases/{canvas_id} — retrieve
# ---------------------------------------------------------------------------


@api_router.get("/canvases/{canvas_id}", tags=["canvas"])
async def get_canvas(
    canvas_id: str,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Retrieve a canvas by id (org-scoped)."""
    org_id = await _get_org(user)
    canvas = await repo.get("canvases", org_id, canvas_id)
    if canvas is None:
        raise AppError("canvas_not_found", f"Canvas {canvas_id!r} not found.", 404)
    return canvas


# ---------------------------------------------------------------------------
# PUT /canvases/{canvas_id} — update
# ---------------------------------------------------------------------------


@api_router.put("/canvases/{canvas_id}", tags=["canvas"])
async def update_canvas(
    canvas_id: str,
    body: CanvasUpdateRequest,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Update a canvas's name and/or config (org-scoped)."""
    org_id = await _get_org(user)
    fields: dict[str, Any] = {}
    if body.name is not None:
        fields["name"] = body.name
    if body.config is not None:
        fields["config"] = body.config
    if not fields:
        raise AppError("no_fields", "At least one of name or config must be provided.", 400)
    updated = await repo.update("canvases", org_id, canvas_id, fields)
    if updated is None:
        raise AppError("canvas_not_found", f"Canvas {canvas_id!r} not found.", 404)
    return updated


# ---------------------------------------------------------------------------
# DELETE /canvases/{canvas_id} — delete
# ---------------------------------------------------------------------------


@api_router.delete("/canvases/{canvas_id}", tags=["canvas"], status_code=204)
async def delete_canvas(
    canvas_id: str,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> None:
    """Delete a canvas (org-scoped)."""
    org_id = await _get_org(user)
    deleted = await repo.delete("canvases", org_id, canvas_id)
    if not deleted:
        raise AppError("canvas_not_found", f"Canvas {canvas_id!r} not found.", 404)
