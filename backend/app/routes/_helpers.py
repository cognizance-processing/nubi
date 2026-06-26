"""Shared route helpers — import-safe, no routes registered.

These helpers reduce repetitive boilerplate in route handlers without
introducing any import-time side effects (no APIRouter, no include_router).

Available helpers
-----------------
``get_or_404``
    Fetch a row via ``repo.get()`` and raise ``AppError("not_found", ..., 404)``
    when it is absent.  Preserves exact error codes and messages per call-site
    via the ``error_code`` and ``detail`` keyword arguments.

``resolved_ctx_scoped``  (preferred for header-aware routes)
    FastAPI dependency that resolves ``(user_id, org_id)`` via
    ``resolve_org_id`` (honours ``X-Org-Id`` header, verifies membership).
    Use ONLY on handlers that currently call ``resolve_org_id``; never as a
    substitute for the ``get_user_org`` / ``_get_user_org`` variant.

``resolved_ctx_default``  (preferred for default-org routes)
    FastAPI dependency that resolves ``(user_id, org_id)`` via
    ``get_user_org`` (ignores ``X-Org-Id`` — uses the user's default org).
    Use ONLY on handlers that currently call ``get_user_org`` / ``_get_user_org``.
    NEVER use this as a substitute for ``resolved_ctx_scoped`` — different
    security semantics.

``resolved_ctx``
    Alias for ``resolved_ctx_scoped`` kept for backwards compatibility with
    any call-sites that imported the original name.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, Request

from app.auth.deps import current_user
from app.errors import AppError
from app.repos.provider import Repo, get_repo
from app.routes._org import get_user_org, resolve_org_id


# ---------------------------------------------------------------------------
# Dedup 1: get_or_404
# ---------------------------------------------------------------------------

async def get_or_404(
    repo: Repo,
    kind: str,
    org_id: str,
    resource_id: str,
    *,
    error_code: str = "not_found",
    detail: str | None = None,
) -> dict[str, Any]:
    """Fetch *resource_id* of *kind* in *org_id* from *repo*, or raise 404.

    Parameters
    ----------
    repo:
        The active Repo implementation.
    kind:
        Resource table name (e.g. ``"boards"``, ``"queries"``).
    org_id:
        Caller's verified org id — used by repo for tenant isolation.
    resource_id:
        UUID string of the target resource.
    error_code:
        The ``AppError`` code to raise on miss (default: ``"not_found"``).
    detail:
        Human-readable error message.  When omitted a generic
        ``"<kind> not found."`` message is constructed from *kind*.

    Returns
    -------
    dict[str, Any]
        The resource row.

    Raises
    ------
    AppError(error_code, detail, 404)
        When the row is absent or belongs to a different org.
    """
    row = await repo.get(kind, org_id, resource_id)
    if row is None:
        msg = detail or f"{kind.rstrip('s').capitalize()} not found."
        raise AppError(error_code, msg, 404)
    return row


# ---------------------------------------------------------------------------
# Dedup 2a: resolved_ctx_scoped  (header-aware, honours X-Org-Id)
# ---------------------------------------------------------------------------

async def resolved_ctx_scoped(
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> tuple[str, str]:
    """FastAPI dependency: resolve ``(user_id, org_id)`` via ``resolve_org_id``.

    Implements the header-aware org resolution: honours ``X-Org-Id`` when
    present and verifies the caller is a member of that org (403 if not).
    Falls back to the user's default org when no header is provided.

    IMPORTANT / SECURITY
    --------------------
    Use this dependency ONLY on handlers that currently call
    ``resolve_org_id(str(user["id"]), repo, request)`` directly.  Do NOT use
    it to replace ``get_user_org`` / ``_get_user_org`` call sites — those
    ignore ``X-Org-Id`` by design (connectors, bridges, flows, etc.) and
    switching them to this dependency would silently break tenant isolation
    for API-key callers that pass ``X-Org-Id``.

    Returns
    -------
    tuple[str, str]
        ``(user_id, org_id)`` — both are UUID strings.
    """
    user_id = str(user["id"])
    org_id = await resolve_org_id(user_id, repo, request)
    return user_id, org_id


# ---------------------------------------------------------------------------
# Dedup 2b: resolved_ctx_default  (default-org, ignores X-Org-Id)
# ---------------------------------------------------------------------------

async def resolved_ctx_default(
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> tuple[str, str]:
    """FastAPI dependency: resolve ``(user_id, org_id)`` via ``get_user_org``.

    Implements the default-org resolution: always uses the user's default
    (first) org regardless of any ``X-Org-Id`` header.  This matches the
    behaviour of the ``get_user_org`` / ``_get_user_org`` pattern used by
    connectors, bridges, flows, mcp, jwt_issuers, etc.

    IMPORTANT / SECURITY
    --------------------
    Use this dependency ONLY on handlers that currently call
    ``get_user_org(str(user["id"]), repo)`` / ``_get_user_org(...)`` directly.
    Do NOT use it to replace ``resolve_org_id`` call sites — those honour
    ``X-Org-Id`` by design and switching them here would silently drop the
    membership check, allowing cross-org access via header.

    Returns
    -------
    tuple[str, str]
        ``(user_id, org_id)`` — both are UUID strings.
    """
    user_id = str(user["id"])
    org_id = await get_user_org(user_id, repo)
    return user_id, org_id


# ---------------------------------------------------------------------------
# Backwards-compat alias: resolved_ctx → resolved_ctx_scoped
# ---------------------------------------------------------------------------

#: Alias kept for call-sites that imported the original ``resolved_ctx`` name.
#: New code should use ``resolved_ctx_scoped`` explicitly.
resolved_ctx = resolved_ctx_scoped
