"""Asset-serving and profile-update routes.

Endpoints
---------
GET  /assets/avatars/{key}
    Serve stored avatar bytes when ``ASSET_SERVE_MODE=local``.
    Returns 404 in Bunny mode (assets are served by the CDN directly).
    ``key`` may contain slashes (e.g. ``user/abc123/deadbeef.jpg``).

PATCH /auth/me
    Update the current user's profile name and/or avatar_url.
    If ``avatar_url`` looks like an external URL, it is ingested
    (re-hosted on our domain) before being stored.

Both endpoints are mounted on the shared ``api_router`` by
``backend/main.py`` via::

    from app.assets.routes import router as assets_router
    api_router.include_router(assets_router)

The PATCH /auth/me endpoint is intentionally placed on the ``/auth``
prefix (matching ``app.routes.auth``) so the frontend can use the
same ``/api/v1/auth/me`` base path for reads (GET) and writes (PATCH).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Response
from fastapi.responses import Response as FastAPIResponse
from pydantic import BaseModel

from app.assets.service import ingest_avatar_from_url
from app.auth.deps import current_user
from app.db import execute, fetchrow

logger = logging.getLogger(__name__)

router = APIRouter(tags=["assets"])


# ── Local asset serve ─────────────────────────────────────────────────────────

@router.get("/assets/avatars/{key:path}", include_in_schema=False)
async def serve_avatar(key: str) -> FastAPIResponse:
    """Serve a stored avatar in local mode.

    In Bunny mode this endpoint is unreachable (CDN serves the assets), but
    it still returns 404 cleanly rather than 500.

    Parameters
    ----------
    key:
        Relative storage key, e.g. ``user/abc/deadbeef.jpg``.

    Returns
    -------
    200  image/* bytes — asset found
    404  asset not found or Bunny mode active
    """
    from app.assets.config import get_asset_serve_mode  # noqa: PLC0415

    mode = get_asset_serve_mode()
    if mode == "bunny":
        return FastAPIResponse(status_code=404, content=b"Asset served by CDN")

    import asyncio  # noqa: PLC0415
    import mimetypes  # noqa: PLC0415
    import os as _os  # noqa: PLC0415

    from app.storage.local import LocalStorageClient  # noqa: PLC0415

    default_root = _os.path.join(_os.path.dirname(__file__), "..", "..", "storage_data")
    root = _os.path.abspath(_os.getenv("LOCAL_STORAGE_ROOT", default_root))
    client = LocalStorageClient(root=root)

    try:
        data: bytes = await asyncio.to_thread(client.download_bytes, key)
    except FileNotFoundError:
        return FastAPIResponse(status_code=404, content=b"Not found")

    mime, _ = mimetypes.guess_type(key)
    content_type = mime or "application/octet-stream"

    return FastAPIResponse(
        content=data,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


# ── Profile update ────────────────────────────────────────────────────────────

class PatchMeIn(BaseModel):
    """Request body for PATCH /auth/me."""

    name: str | None = None
    avatar_url: str | None = None


@router.patch("/auth/me")
async def patch_me(
    body: PatchMeIn,
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """Update the current user's profile name and/or avatar.

    If ``avatar_url`` is an external HTTP/HTTPS URL it is ingested (downloaded
    and re-hosted on our domain) before being stored so the avatar is always
    served from our own domain.

    Returns
    -------
    200  ``{"user": {...}}``  — updated user object
    """
    user_id: str = str(user["id"])
    updates: dict[str, Any] = {}

    if body.name is not None:
        updates["name"] = body.name.strip() or None

    if body.avatar_url is not None:
        url = body.avatar_url.strip()
        if url.startswith(("http://", "https://")):
            # External URL — ingest + rehost so we serve it ourselves.
            served = await ingest_avatar_from_url(url, "user", user_id)
            updates["avatar_url"] = served or url  # fall back to original on failure
        else:
            # Already a local path or empty string.
            updates["avatar_url"] = url or None

    if updates:
        set_clauses = ", ".join(
            f"{col} = ${i + 1}" for i, col in enumerate(updates)
        )
        values = list(updates.values())
        values.append(user_id)
        id_placeholder = f"${len(values)}"
        await execute(
            f"UPDATE users SET {set_clauses}, updated_at = now() "
            f"WHERE id = {id_placeholder}::uuid",
            *values,
        )

    # Re-fetch to return fresh data.
    row = await fetchrow(
        "SELECT id, email, name, avatar_url, email_verified, created_at FROM users "
        "WHERE id = $1::uuid",
        user_id,
    )
    if row is None:
        from app.errors import AppError  # noqa: PLC0415

        raise AppError("not_found", "User not found.", 404)

    created_at = row["created_at"]
    if hasattr(created_at, "isoformat"):
        created_at = created_at.isoformat()

    return {
        "user": {
            "id": str(row["id"]),
            "email": str(row["email"]),
            "name": row["name"],
            "avatar_url": row["avatar_url"],
            "email_verified": bool(row["email_verified"]),
            "created_at": created_at,
        }
    }
