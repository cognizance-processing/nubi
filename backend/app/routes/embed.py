"""Embed configuration endpoint (PROD — reads real boards resource).

Endpoint
--------
GET /embed/config/{dashboard_id}
    Resolves the dashboard from the ``boards`` resource (org-scoped) and
    returns a read-only descriptor built from the board's ``config`` field.

    The board ``config`` may contain:
    - ``spec``    — a structured widget spec dict (EDITOR milestone format).
    - ``html``    — a raw HTML dashboard string (M8 format).
    - ``widgets`` — a list of widget dicts (legacy / M3-stub format).

    The response descriptor is built from whatever is present in the board
    config, without inventing fields that are not there.  If the board exists
    but has no widgets (or no spec/html), a minimal descriptor is returned
    (graceful fallback).

Org resolution
--------------
- Embed tokens (``identity.kind == "embed"``) carry an ``org`` claim that is
  used directly as the ``org_id``.
- First-party tokens use ``get_user_org(user_id, repo)`` (same helper as the
  resources CRUD route).

Security
--------
- Requires a valid bearer token (first-party OR embed JWT).
- Requires at least a read scope (``read:query``, ``read:*``,
  ``read:dashboard:*`` all satisfy this via wildcard matching).
- ``verified_identity`` enforces embed-origin pinning and token expiry.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.auth.deps import verified_identity
from app.auth.scopes import has_scope
from app.auth.verify import VerifiedIdentity
from app.errors import AppError
from app.repos.provider import get_repo, Repo
from app.routes import api_router

router = APIRouter(prefix="/embed", tags=["embed"])


# ---------------------------------------------------------------------------
# Descriptor builder
# ---------------------------------------------------------------------------


def _board_to_descriptor(dashboard_id: str, board: dict[str, Any]) -> dict[str, Any]:
    """Build a read-only embed descriptor from a *board* row.

    The descriptor shape follows what the ``<nubi-dashboard>`` web component
    and the SDK's ``embed.mount`` expect.

    Only fields that are present in ``board['config']`` are included — we
    never invent fields that do not exist.

    Graceful fallback: if the board has no widgets / spec / html, we return a
    minimal descriptor so the embed host gets a valid (if empty) response
    rather than a 500.

    Parameters
    ----------
    dashboard_id:
        The path parameter value (used as the descriptor's ``dashboard_id``).
    board:
        The raw board row dict from the repository (``id``, ``name``,
        ``config``, …).

    Returns
    -------
    dict
        Embed descriptor with ``dashboard_id``, ``title``, and at least one of
        ``spec``, ``html``, or ``widgets``.
    """
    config: dict[str, Any] = board.get("config") or {}

    descriptor: dict[str, Any] = {
        "dashboard_id": dashboard_id,
        "title": board.get("name") or f"Dashboard {dashboard_id}",
    }

    if "spec" in config:
        # EDITOR milestone format: structured spec with widgets array.
        descriptor["spec"] = config["spec"]
        # Expose top-level widgets list for clients that read it directly.
        spec = config["spec"]
        if isinstance(spec, dict) and "widgets" in spec:
            descriptor["widgets"] = spec["widgets"]

    if "html" in config:
        # M8 format: raw sanitized HTML dashboard doc.
        descriptor["html"] = config["html"]

    if "widgets" in config and "widgets" not in descriptor:
        # Legacy / M3-stub format: explicit widgets list in config.
        descriptor["widgets"] = config["widgets"]

    if "theme" in config:
        descriptor["theme"] = config["theme"]

    # Graceful fallback: ensure "widgets" key always exists (may be empty list).
    if "widgets" not in descriptor:
        descriptor["widgets"] = []

    return descriptor


# ---------------------------------------------------------------------------
# GET /embed/config/{dashboard_id}
# ---------------------------------------------------------------------------


@router.get("/config/{dashboard_id}")
async def get_embed_config(
    dashboard_id: str,
    identity: VerifiedIdentity = Depends(verified_identity),
    repo: Repo = Depends(get_repo),
) -> dict:
    """Return a read-only descriptor for *dashboard_id*.

    Parameters
    ----------
    dashboard_id:
        The dashboard identifier from the URL path.  Must match the ``id`` of
        an existing ``boards`` row in the caller's org.
    identity:
        Verified token identity (injected by ``verified_identity``).
    repo:
        Active repository implementation (injected by ``get_repo``).

    Returns
    -------
    dict
        A descriptor built from the board's ``config`` field::

            {
                "dashboard_id": str,
                "title": str,
                # One or more of:
                "spec": {...},        # if board.config.spec present
                "html": "...",        # if board.config.html present
                "widgets": [...],     # always present (may be [])
                "theme": {...},       # if board.config.theme present
            }

    Raises
    ------
    AppError("unauthorized", 401)
        If the token is missing or invalid.
    AppError("insufficient_scope", 403)
        If the token does not carry a qualifying read scope.
    AppError("origin_mismatch", 403)
        If the token's embed_origin does not match the request Origin header.
    AppError("dashboard_not_found", 404)
        If no board with *dashboard_id* exists in the caller's org.
    """
    # ── Scope gate ────────────────────────────────────────────────────────────
    # Accepted scopes: read:query, read:*, read:dashboard:* (any "read:" prefix).
    _scopes = identity.scope
    _has_read = has_scope(_scopes, "read:query") or any(
        s.startswith("read:") for s in _scopes
    )
    if not _has_read:
        raise AppError(
            "insufficient_scope",
            "Token does not carry the required scope: read:query",
            403,
        )

    # ── Resolve org_id ────────────────────────────────────────────────────────
    # Embed tokens carry the org in their claims; first-party tokens look it up.
    if identity.kind == "embed" and identity.org:
        org_id = identity.org
    else:
        # First-party path — reuse the helper from routes/resources.
        from app.routes.resources import get_user_org  # local import to avoid circulars

        org_id = await get_user_org(identity.user_id, repo)

    # ── Load board from repo ──────────────────────────────────────────────────
    board = await repo.get("boards", org_id, dashboard_id)
    if board is None:
        raise AppError(
            "dashboard_not_found",
            f"Dashboard {dashboard_id!r} not found.",
            404,
        )

    # ── Build and return descriptor ───────────────────────────────────────────
    return _board_to_descriptor(dashboard_id, board)


# ---------------------------------------------------------------------------
# Register this router on the shared api_router
# ---------------------------------------------------------------------------

api_router.include_router(router)
