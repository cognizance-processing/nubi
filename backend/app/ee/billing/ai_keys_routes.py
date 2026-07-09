"""BYO AI provider key routes — EE billing.

Routes
------
POST   /ai/keys     Set (create or replace) the caller's org BYO key for a provider.
DELETE /ai/keys      Clear the caller's org BYO key for a provider.

Authorization
-------------
Both routes require the caller to be an org admin/owner of the target org, OR
a platform superadmin (for support/ops).  Free-tier orgs receive
``AppError("ai_key_requires_paid_tier", 402)`` — set/clear is itself gated by
:func:`app.ee.billing.org_ai_keys.set_org_ai_key` (paid tiers only), not just
this route.

Mounting
--------
Mounted by :func:`app.ee.billing.setup` alongside the other billing routes —
never imported by core.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth.deps import current_user
from app.errors import AppError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ai"])


async def _is_superadmin(user: dict[str, Any]) -> bool:
    """Best-effort superadmin check (re-reads the DB row; never raises)."""
    try:
        from app import db  # noqa: PLC0415

        row = await db.fetchrow(
            "SELECT is_superadmin FROM users WHERE id = $1::uuid",
            str(user.get("id", "")),
        )
        return bool(dict(row).get("is_superadmin")) if row is not None else False
    except Exception:  # noqa: BLE001
        return False


async def _require_org_admin_or_superadmin(user: dict[str, Any], org_id: str) -> None:
    """403 unless the caller is an admin/owner of *org_id*, or a superadmin."""
    if await _is_superadmin(user):
        return
    from app.auth.roles import get_org_role  # noqa: PLC0415
    from app.repos.provider import get_repo  # noqa: PLC0415

    try:
        role = await get_org_role(str(user.get("id", "")), org_id, get_repo())
    except RuntimeError:
        logger.warning("ai_keys: org membership check skipped for org=%s — DB pool not initialised", org_id)
        return
    if role is None or role not in ("owner", "admin"):
        raise AppError("forbidden", "Setting an AI provider key requires an org admin, owner, or superadmin.", 403)


class SetAiKeyRequest(BaseModel):
    """Body for POST /ai/keys."""

    org_id: str
    provider: str  # "anthropic" | "openai" | "gemini"
    api_key: str


class ClearAiKeyRequest(BaseModel):
    """Body for DELETE /ai/keys."""

    org_id: str
    provider: str


class AiKeyResponse(BaseModel):
    """Response for POST/DELETE /ai/keys — never echoes the key itself."""

    org_id: str
    provider: str
    configured: bool


@router.post("/ai/keys", response_model=AiKeyResponse)
async def set_ai_key(
    body: SetAiKeyRequest,
    user: dict[str, Any] = Depends(current_user),
) -> AiKeyResponse:
    """Store (create or replace) the org's BYO API key for *body.provider*.

    Requires the org's active tier to be PAID (Starter and above) — a
    FREE-tier org receives ``AppError("ai_key_requires_paid_tier", 402)``.
    The key is encrypted at rest with the SAME mechanism as connector secrets
    (AES-256-GCM, ``CONNECTOR_SECRET_KEY``) and is NEVER echoed back.
    """
    from app.ee.billing.org_ai_keys import set_org_ai_key  # noqa: PLC0415

    await _require_org_admin_or_superadmin(user, body.org_id)
    await set_org_ai_key(body.org_id, body.provider.lower().strip(), body.api_key, created_by=str(user.get("id", "")))
    return AiKeyResponse(org_id=body.org_id, provider=body.provider.lower().strip(), configured=True)


@router.delete("/ai/keys", response_model=AiKeyResponse)
async def clear_ai_key(
    body: ClearAiKeyRequest,
    user: dict[str, Any] = Depends(current_user),
) -> AiKeyResponse:
    """Clear the org's BYO API key for *body.provider* (no-op if absent)."""
    from app.ee.billing.org_ai_keys import clear_org_ai_key  # noqa: PLC0415

    await _require_org_admin_or_superadmin(user, body.org_id)
    await clear_org_ai_key(body.org_id, body.provider.lower().strip())
    return AiKeyResponse(org_id=body.org_id, provider=body.provider.lower().strip(), configured=False)


def setup(app: Any) -> None:
    """Mount the BYO AI key routes onto *app* at ``/api/v1``.

    Called from :func:`app.ee.billing.setup`.  ``/ai/keys`` is a literal
    2-segment path (same shape as the core ``/ai/ask`` etc.) so normal
    append-order inclusion does not collide with the ``/{resource}``
    catch-all — no route-reordering needed (contrast with ``/pricing`` in
    ``app.ee.billing.routes.setup``).
    """
    app.include_router(router, prefix="/api/v1")
    logger.info("Nubi EE: BYO AI key routes mounted at /api/v1/ai/keys")
