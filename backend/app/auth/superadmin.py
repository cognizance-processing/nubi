"""``require_superadmin`` — FastAPI dependency gating all /admin/* routes.

Security model
--------------
- The flag lives ONLY in ``users.is_superadmin`` and is granted exclusively by
  manual SQL (``UPDATE users SET is_superadmin = true WHERE email = '...'``)
  or the seed script.  NO API endpoint can set it — every request body in the
  backend is a closed Pydantic model with no such field, and no UPDATE
  statement outside seed/manual SQL touches the column.
- This dependency re-reads the CURRENT user row from the database on every
  request (it does NOT trust JWT claims), so a revoked superadmin loses
  access immediately, and a forged/stale token can never confer it.

The DB helper is referenced lazily via the ``app.db`` module attribute so test
fixtures that patch ``app.db.fetchrow`` are honoured.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends

from app import db
from app.auth.deps import current_user
from app.errors import AppError


async def require_superadmin(
    user: dict[str, Any] = Depends(current_user),
) -> dict[str, Any]:
    """403 unless the CURRENT DB row for the authenticated user is superadmin.

    Returns the user dict (with ``is_superadmin: True`` merged in) so route
    handlers can reuse it.
    """
    row = await db.fetchrow(
        "SELECT is_superadmin FROM users WHERE id = $1::uuid",
        str(user["id"]),
    )
    is_superadmin = bool(dict(row).get("is_superadmin")) if row is not None else False
    if not is_superadmin:
        raise AppError("forbidden", "Superadmin access required.", 403)
    return {**user, "is_superadmin": True}
