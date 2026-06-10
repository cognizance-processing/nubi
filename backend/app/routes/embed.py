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

POST /embed-token  (DEV ONLY — gated by EMBED_DEV_TOKEN_ENABLED=true)
    Mint a first-party HS256 JWT carrying per-tenant RLS claims, an org claim,
    and a ``read:*`` scope.  Intended for local embed demos where a host page
    needs a backend-verified token without going through the full auth flow.

    The minted token is a standard Nubi first-party access token (HS256,
    JWT_SECRET) and is therefore verified by ``verified_identity`` exactly like
    any other access token.  It carries the caller-supplied ``org``,
    ``policies`` (RLS claims), ``scope``, and ``sub`` from the request body.

    SECURITY: This endpoint is a development convenience ONLY.  It MUST NOT be
    enabled in production (EMBED_DEV_TOKEN_ENABLED defaults to false/off).

Algorithm note
--------------
The standard embed path (RS256/ES256) uses asymmetric host-signed JWTs via the
issuer registry (see ``app.auth.issuers``).  The dev helper uses HS256 instead
because it is minted by the backend itself using the same JWT_SECRET as all
first-party access tokens.  The ``verified_identity`` dependency accepts it
transparently on the ``kind="access"`` path.  A real production embed would use
RS256/ES256 so the host can sign tokens without sharing the backend secret.

Org resolution
--------------
- Embed tokens (``identity.kind == "embed"``) carry an ``org`` claim that is
  used directly as the ``org_id``.
- First-party tokens (including dev embed tokens) use ``get_user_org(user_id, repo)``
  (same helper as the resources CRUD route).  For dev embed tokens that carry an
  ``org`` claim in the JWT payload, callers should ensure the user_id exists in
  the DB OR pass an org that is resolvable.  A self-contained dev demo can mount
  a FakeRepo that returns the org from the token's ``org`` claim directly.

Security
--------
- Requires a valid bearer token (first-party OR embed JWT).
- Requires at least a read scope (``read:query``, ``read:*``,
  ``read:dashboard:*`` all satisfy this via wildcard matching).
- ``verified_identity`` enforces embed-origin pinning and token expiry.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth.deps import verified_identity
from app.auth.scopes import has_scope
from app.auth.verify import VerifiedIdentity
from app.errors import AppError
from app.repos.provider import get_repo, Repo
from app.routes import api_router

router = APIRouter(prefix="/embed", tags=["embed"])


# ---------------------------------------------------------------------------
# Dev embed-token helper (GATED — EMBED_DEV_TOKEN_ENABLED=true required)
# ---------------------------------------------------------------------------


class _EmbedTokenIn(BaseModel):
    """Request body for ``POST /embed-token``."""

    sub: str = "dev-embed-user"
    """The ``sub`` (subject) claim for the minted token.

    In a real embed flow this would be a user or session identifier from the
    host application.  Any non-empty string is accepted here.
    """

    org: str | None = None
    """Org ID to embed in the token.  Used for org-scoped RLS in the backend."""

    policies: dict[str, Any] | None = None
    """Per-tenant RLS claims, e.g. ``{"tenant_id": "acme"}``.

    These are forwarded verbatim into the JWT payload and surfaced on
    ``VerifiedIdentity.policies`` after verification.
    """

    scope: list[str] | None = None
    """OAuth-style scope strings.  Defaults to ``["read:*"]`` when omitted."""

    ttl_minutes: int = 60
    """Token lifetime in minutes.  Capped at 1440 (24 hours) for safety."""


@router.post("/embed-token", status_code=200)
async def mint_embed_dev_token(body: _EmbedTokenIn) -> dict[str, Any]:
    """Mint a backend-verified HS256 embed token for local development.

    This endpoint is **disabled by default** and must be explicitly enabled via
    the ``EMBED_DEV_TOKEN_ENABLED=true`` environment variable.  It MUST NOT be
    turned on in production deployments.

    The minted JWT is a first-party Nubi access token (HS256, ``JWT_SECRET``)
    carrying the caller-supplied ``org``, ``policies``, and ``scope`` claims in
    addition to the standard ``sub``/``iat``/``exp``/``typ``/``jti`` fields.
    It is accepted by any endpoint that uses the ``verified_identity`` dependency.

    Parameters
    ----------
    body:
        Request body (see :class:`_EmbedTokenIn`).

    Returns
    -------
    dict
        ``{"token": "<jwt>", "expires_in": <seconds>}``

    Raises
    ------
    HTTPException(503)
        When ``EMBED_DEV_TOKEN_ENABLED`` is not set to ``true``.
    """
    _enabled = os.getenv("EMBED_DEV_TOKEN_ENABLED", "").lower() in ("1", "true", "yes")
    if not _enabled:
        raise HTTPException(
            status_code=503,
            detail="embed-token endpoint is disabled (set EMBED_DEV_TOKEN_ENABLED=true for local dev only)",
        )

    import uuid  # noqa: PLC0415
    from datetime import datetime, timedelta, timezone  # noqa: PLC0415

    import jwt as _jwt  # noqa: PLC0415

    from app.config import get_settings  # noqa: PLC0415

    settings = get_settings()
    ttl = min(max(body.ttl_minutes, 1), 1440)  # clamp to [1, 1440] minutes
    now = datetime.now(tz=timezone.utc)
    exp = now + timedelta(minutes=ttl)

    scopes = body.scope if body.scope else ["read:*"]

    payload: dict[str, Any] = {
        "sub": body.sub,
        "iat": now,
        "exp": exp,
        "typ": "access",
        "jti": str(uuid.uuid4()),
        "scope": " ".join(scopes),
    }
    if body.org is not None:
        payload["org"] = body.org
    if body.policies:
        payload["policies"] = body.policies

    token = _jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")
    return {"token": token, "expires_in": ttl * 60}


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

    # ── BILLING: embedded sessions are a metered dimension ───────────────────
    # Each embed-token config fetch starts one embedded view session
    # (tiers.max_embedded_sessions_per_month).  First-party tokens are NOT
    # metered here — internal dashboard views are never billed.  Quota
    # enforcement is a no-op in OSS builds (no EE checker registered); FREE
    # tier (0 sessions, no overage rate) hard-stops with 402.
    if identity.kind == "embed":
        from app.compute.metering import record_usage  # noqa: PLC0415
        from app.features import enforce_quota  # noqa: PLC0415

        await enforce_quota(org_id, "embedded_sessions", amount=1.0)
        await record_usage(
            kind="embedded_session",
            user_id=identity.user_id,
            org_id=org_id,
            units=1.0,
            tier="embed_config",
        )

    # ── Load board from repo ──────────────────────────────────────────────────
    board = await repo.get("boards", org_id, dashboard_id)
    if board is None:
        raise AppError(
            "dashboard_not_found",
            f"Dashboard {dashboard_id!r} not found.",
            404,
        )

    # ── STRICT ENV VISIBILITY (DECISION 4) — embed identities only ───────────
    # Embed/viewer tokens resolve the board through the project's DEFAULT
    # environment (the protected ``prod`` env in standard projects):
    #   - a version pinned there → its snapshot config replaces the draft;
    #   - default env PROTECTED with no pointer → 404 (drafts are never
    #     visible to embed identities in a protected environment);
    #   - no project/env data resolvable → draft (environments layer is
    #     optional; first-party identities always see the draft).
    if identity.kind == "embed":
        from app.environments.store import resolve_default_env_config  # noqa: PLC0415

        try:
            pinned_config = await resolve_default_env_config(
                "board", str(board["id"]), board.get("project_id"), org_id
            )
        except AppError:
            # Uniform 404 shape with the missing-board case — no draft leak.
            raise AppError(
                "dashboard_not_found",
                f"Dashboard {dashboard_id!r} not found.",
                404,
            )
        if pinned_config is not None:
            board = {**board, "config": pinned_config}

    # ── Build and return descriptor ───────────────────────────────────────────
    return _board_to_descriptor(dashboard_id, board)


# ---------------------------------------------------------------------------
# Register this router on the shared api_router
# ---------------------------------------------------------------------------

api_router.include_router(router)
