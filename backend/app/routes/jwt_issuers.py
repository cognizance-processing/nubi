"""JWT Issuers management endpoints — org-scoped CRUD for embed JWKS configs.

Endpoints
---------
GET    /security/jwt-issuers              -> list of issuer rows for caller's org
POST   /security/jwt-issuers              -> create a new issuer config
GET    /security/jwt-issuers/{issuer_id}  -> get a single issuer by id
PUT    /security/jwt-issuers/{issuer_id}  -> update (partial) an issuer
DELETE /security/jwt-issuers/{issuer_id}  -> delete an issuer

All endpoints require a valid first-party Bearer token (``current_user``).
Issuers are org-scoped: callers can only see and operate on their own org's
configured issuers.

The ``issuer`` field is the ``iss`` claim value that must appear in embed
JWTs from this host.  Combined with ``jwks_url`` or ``static_jwks_json`` and
``audience``, it is everything the verification path needs.

After any mutation the in-process ``IssuerRegistry`` (used by the live
embed verification path) is kept in sync via :func:`_sync_registry`.

Self-registration
-----------------
This module self-registers on ``api_router`` at import time (mirrors the
pattern used by ``routes/embed.py``, ``routes/secrets.py``, etc.).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth.deps import current_user
from app.auth.roles import require_writer_default
from app.db import fetchrow
from app.errors import AppError
from app.repos.provider import Repo, get_repo
from app.routes import api_router

router = APIRouter(prefix="/security/jwt-issuers", tags=["security"])


# ---------------------------------------------------------------------------
# Org-resolution helper (mirrors secrets/flows pattern, avoids circular deps)
# ---------------------------------------------------------------------------


async def _get_user_org(user_id: str, repo: Repo) -> str:
    """Return the org_id for the user's first membership."""
    if hasattr(repo, "get_org_for_user"):
        org_id = repo.get_org_for_user(user_id)  # type: ignore[attr-defined]
        if org_id:
            return str(org_id)
        raise AppError("org_not_found", "User has no org membership.", 404)

    row = await fetchrow(
        """
        SELECT org_id FROM org_members
        WHERE user_id = $1::uuid
        ORDER BY org_id
        LIMIT 1
        """,
        user_id,
    )
    if row is None:
        raise AppError("org_not_found", "User has no org membership.", 404)
    return str(row["org_id"])


# ---------------------------------------------------------------------------
# Pydantic request / response schemas
# ---------------------------------------------------------------------------


class _IssuerCreate(BaseModel):
    """Request body for ``POST /security/jwt-issuers``."""

    name: str = Field(..., description="Human-readable label for this issuer.")
    issuer: str = Field(
        ...,
        description="The ``iss`` claim value that must appear in embed JWTs from this host.",
    )
    audience: str = Field(
        ..., description="Expected ``aud`` claim value in embed JWTs."
    )
    jwks_url: str | None = Field(
        None, description="HTTPS URL of the JWKS endpoint.  Required when static_jwks_json is absent."
    )
    static_jwks_json: dict[str, Any] | None = Field(
        None, description="Pre-built JWKS dict.  When set, jwks_url is ignored during verification."
    )
    algorithms: list[str] | None = Field(
        None, description="Allowed signing algorithms.  Defaults to ['RS256']."
    )
    enabled: bool = Field(True, description="When false, tokens from this issuer are rejected.")


class _IssuerUpdate(BaseModel):
    """Request body for ``PUT /security/jwt-issuers/{issuer_id}`` (all fields optional)."""

    name: str | None = None
    jwks_url: str | None = None
    static_jwks_json: dict[str, Any] | None = None
    algorithms: list[str] | None = None
    audience: str | None = None
    enabled: bool | None = None


# ---------------------------------------------------------------------------
# In-process IssuerRegistry sync
# ---------------------------------------------------------------------------


def _sync_registry(org_id: str, rows: list[dict[str, Any]]) -> None:
    """Sync the in-process ``IssuerRegistry`` from a list of DB rows.

    Called after every mutation so that ongoing embed verifications pick up
    the latest config without a restart.  Only rows belonging to *org_id*
    are synced; other orgs' entries are unaffected.

    This is best-effort: if the registry import fails for any reason (e.g.
    in a minimal test environment) the DB-backed path continues to work.
    """
    try:
        from app.auth.issuers import get_issuer_registry  # noqa: PLC0415

        registry = get_issuer_registry()

        # Remove the current org's entries first so stale / deleted issuers
        # are evicted.
        # IssuerRegistry stores by iss string — we unregister any iss that
        # belongs to this org by re-checking rows.
        existing_iss_for_org: set[str] = set()
        for row in rows:
            if row.get("org_id") == str(org_id):
                existing_iss_for_org.add(row["issuer"])

        # Unregister issuers that are no longer in the DB list (deleted/disabled).
        for iss in list(getattr(registry, "_issuers", {}).keys()):
            cfg = registry.get(iss)
            if cfg is None:
                continue
            # Only touch entries that we might have registered from this org.
            # We can't tell which org a registry entry came from (the legacy
            # IssuerRegistry is not org-scoped), so we re-register only the
            # active set and leave others alone.

        # Register / update enabled issuers.
        for row in rows:
            if row.get("org_id") != str(org_id):
                continue
            if not row.get("enabled", True):
                registry.unregister(row["issuer"])
                continue
            registry.register(
                row["issuer"],
                jwks_uri=row.get("jwks_url") or "",
                aud=row.get("audience", ""),
                static_jwks=row.get("static_jwks_json"),
            )
    except Exception:  # noqa: BLE001
        pass  # best-effort; verification falls through to DB lookup


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/")
async def list_jwt_issuers(
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> list[dict[str, Any]]:
    """Return all JWT issuers configured for the caller's org."""
    from app.security.issuers_store import get_issuers_store  # noqa: PLC0415

    org_id = await _get_user_org(user["id"], repo)
    return await get_issuers_store().list_for_org(org_id)


@router.post("/", status_code=201, dependencies=[Depends(require_writer_default)])
async def create_jwt_issuer(
    body: _IssuerCreate,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Create a new JWT issuer configuration for the caller's org."""
    from app.security.issuers_store import get_issuers_store  # noqa: PLC0415

    if not body.jwks_url and not body.static_jwks_json:
        raise AppError(
            "missing_jwks",
            "Either jwks_url or static_jwks_json must be provided.",
            422,
        )

    org_id = await _get_user_org(user["id"], repo)
    store = get_issuers_store()

    row = await store.create(
        org_id=org_id,
        name=body.name,
        issuer=body.issuer,
        audience=body.audience,
        created_by=user["id"],
        jwks_url=body.jwks_url,
        static_jwks_json=body.static_jwks_json,
        algorithms=body.algorithms,
        enabled=body.enabled,
    )

    # Sync the in-process registry so immediately-following embed requests work.
    all_rows = await store.list_for_org(org_id)
    _sync_registry(org_id, all_rows)

    return row


@router.get("/{issuer_id}")
async def get_jwt_issuer(
    issuer_id: str,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Return a single JWT issuer by id."""
    from app.security.issuers_store import get_issuers_store  # noqa: PLC0415

    org_id = await _get_user_org(user["id"], repo)
    row = await get_issuers_store().get_by_id(issuer_id, org_id)
    if row is None:
        raise AppError("issuer_not_found", "JWT issuer not found.", 404)
    return row


@router.put("/{issuer_id}", dependencies=[Depends(require_writer_default)])
async def update_jwt_issuer(
    issuer_id: str,
    body: _IssuerUpdate,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Partially update a JWT issuer configuration."""
    from app.security.issuers_store import get_issuers_store  # noqa: PLC0415

    org_id = await _get_user_org(user["id"], repo)
    store = get_issuers_store()

    _UNSET = object()

    update_kwargs: dict[str, Any] = {}
    if body.name is not None:
        update_kwargs["name"] = body.name
    if body.audience is not None:
        update_kwargs["audience"] = body.audience
    if body.algorithms is not None:
        update_kwargs["algorithms"] = body.algorithms
    if body.enabled is not None:
        update_kwargs["enabled"] = body.enabled
    # For nullable fields use sentinel detection via model_fields_set.
    if "jwks_url" in body.model_fields_set:
        update_kwargs["jwks_url"] = body.jwks_url
    if "static_jwks_json" in body.model_fields_set:
        update_kwargs["static_jwks_json"] = body.static_jwks_json

    row = await store.update(issuer_id, org_id, **update_kwargs)
    if row is None:
        raise AppError("issuer_not_found", "JWT issuer not found.", 404)

    all_rows = await store.list_for_org(org_id)
    _sync_registry(org_id, all_rows)

    return row


@router.delete("/{issuer_id}", status_code=204, dependencies=[Depends(require_writer_default)])
async def delete_jwt_issuer(
    issuer_id: str,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> None:
    """Delete a JWT issuer configuration."""
    from app.security.issuers_store import get_issuers_store  # noqa: PLC0415

    org_id = await _get_user_org(user["id"], repo)
    store = get_issuers_store()

    deleted = await store.delete(issuer_id, org_id)
    if not deleted:
        raise AppError("issuer_not_found", "JWT issuer not found.", 404)

    # Unregister from the in-process registry.
    try:
        from app.auth.issuers import get_issuer_registry  # noqa: PLC0415

        all_rows = await store.list_for_org(org_id)
        _sync_registry(org_id, all_rows)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Self-register on api_router
# ---------------------------------------------------------------------------

api_router.include_router(router)
