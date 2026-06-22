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


class CanvasScheduleRequest(BaseModel):
    """Request body for POST /canvases/{canvas_id}/schedule.

    Creates a ``'report_send'``-style flow task that delivers the canvas on a
    schedule.  The canvas is referenced by ``canvas_id`` (injected from the
    URL parameter).

    Fields mirror the ``report_send`` task config shape documented in
    :mod:`app.flows.handlers.report_send`.
    """

    format: str = "html"
    """Render format: ``'html'`` (default) or ``'pdf'``."""

    recipients: list[str]
    """Email addresses to deliver the report to."""

    subject: str | None = None
    """Email subject.  Defaults to the canvas name."""

    body: str | None = None
    """Optional plain-text email body."""

    params: dict[str, Any] = {}
    """Base named query parameters."""

    locked_params: dict[str, dict[str, Any]] = {}
    """Per-recipient locked params for RLS — ``{email: {param: value}}``."""

    notify_channels: list[dict[str, Any]] = []
    """Additional notification channels (Slack/Teams/etc)."""

    cron: str | None = None
    """Optional cron expression for the flow trigger (e.g. ``'0 8 * * MON'``)."""

    flow_name: str | None = None
    """Optional human-readable name for the created flow."""


class CanvasScheduleResponse(BaseModel):
    """Response body for POST /canvases/{canvas_id}/schedule."""

    flow_id: str
    canvas_id: str
    format: str
    recipients_count: int


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
# POST /canvases/{canvas_id}/schedule — create a scheduled send flow
# ---------------------------------------------------------------------------


@api_router.post(
    "/canvases/{canvas_id}/schedule",
    response_model=CanvasScheduleResponse,
    tags=["canvas"],
    status_code=201,
)
async def schedule_canvas(
    canvas_id: str,
    body: CanvasScheduleRequest,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> CanvasScheduleResponse:
    """Create a scheduled ``report_send`` flow for a canvas.

    Writes a ``'report_send'``-style flow task config with ``canvas_id`` so
    the canvas is rendered and delivered to *recipients* on the configured
    schedule.  The canvas is resolved org-scoped before creating the flow to
    ensure it exists and belongs to the caller's org.

    Parameters
    ----------
    canvas_id:
        The canvas to schedule.
    body:
        Schedule config — see :class:`CanvasScheduleRequest`.

    Returns
    -------
    CanvasScheduleResponse
        ``{flow_id, canvas_id, format, recipients_count}``.

    Raises
    ------
    AppError("canvas_not_found", 404)
        When the canvas does not exist in the caller's org.
    AppError("invalid_task_config", 400)
        When ``format`` or ``recipients`` are invalid.
    """
    from app.flows.store import get_flow_store  # noqa: PLC0415

    org_id = await _get_org(user)

    # Verify the canvas exists and belongs to this org.
    canvas = await repo.get("canvases", org_id, canvas_id)
    if canvas is None:
        raise AppError("canvas_not_found", f"Canvas {canvas_id!r} not found.", 404)

    # Validate format.
    fmt = (body.format or "html").lower().strip()
    if fmt not in ("html", "pdf"):
        raise AppError(
            "invalid_task_config",
            f"schedule: unsupported format {fmt!r}. Expected 'html' or 'pdf'.",
            400,
        )

    # Validate recipients.
    if not body.recipients:
        raise AppError(
            "invalid_task_config",
            "schedule requires at least one recipient.",
            400,
        )

    canvas_name: str = canvas.get("name") or canvas_id
    flow_name: str = body.flow_name or f"canvas_report_{canvas_id[:8]}"
    subject: str = body.subject or f"Nubi Report: {canvas_name}"

    # Build the report_send task config.
    task_config: dict[str, Any] = {
        "canvas_id": canvas_id,
        "org_id": org_id,
        "format": fmt,
        "recipients": list(body.recipients),
        "subject": subject,
        "body": body.body or "",
        "params": dict(body.params or {}),
        "locked_params": dict(body.locked_params or {}),
        "notify_channels": list(body.notify_channels or []),
        "policies": {},  # populated at tick time from owner claims
    }

    # Build the flow spec.
    flow_spec: dict[str, Any] = {
        "version": 1,
        "name": flow_name,
        "tasks": [
            {
                "key": "send_canvas_report",
                "kind": "report_send",
                "needs": [],
                "config": task_config,
            }
        ],
    }

    # Add cron trigger if supplied.
    if body.cron:
        flow_spec["trigger"] = {"kind": "cron", "cron": body.cron}

    # Persist the flow via the flow store.
    store = get_flow_store()
    flow = await store.create_flow(
        org_id=org_id,
        created_by=str(user["id"]),
        name=flow_name,
        spec=flow_spec,
    )

    return CanvasScheduleResponse(
        flow_id=str(flow["id"]),
        canvas_id=canvas_id,
        format=fmt,
        recipients_count=len(body.recipients),
    )


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
