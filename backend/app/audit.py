"""Unified org-scoped action audit-log writer.

POPIA Compliance Contract
-------------------------
This module records METADATA ONLY.  Callers MUST NOT pass:
- Row data, query results, or any data-layer content
- SQL text with literal filter values (which can carry PII)
- Secret values, tokens, passwords, or key material
- Personal Identifiable Information (PII) such as email addresses, phone
  numbers, ID numbers, or full names embedded in the ``summary`` dict

The ``summary`` dict is intended for non-sensitive resource metadata:
resource name/slug, connector_type, count of items, status codes, etc.

Fire-and-forget
---------------
``record_audit`` is an async function that is deliberately fire-and-forget:
it catches ALL exceptions internally and logs a WARNING if the write fails.
A failed audit write MUST NOT break the mutation path it wraps.

Usage
-----
::

    from app.audit import record_audit

    await record_audit(
        org_id=org_id,
        actor_user_id=str(user["id"]),
        actor_kind="access",
        action="board.create",
        resource_type="board",
        resource_id=str(new_board["id"]),
        summary={"name": new_board["name"]},
    )

Actor kinds
-----------
- ``"access"``  — first-party bearer token (logged-in user or API key)
- ``"embed"``   — embed JWT caller (host-mode or standard embed token)
- ``"system"``  — background worker, scheduler, or internal process
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("app.audit")


async def record_audit(
    *,
    org_id: str,
    actor_user_id: str | None,
    actor_kind: str,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    summary: dict[str, Any] | None = None,
) -> None:
    """Write an audit-log row for a mutation. Never raises; never blocks the caller.

    Parameters
    ----------
    org_id:
        The organisation the action was performed within.
    actor_user_id:
        The user or API-key user performing the action. ``None`` for system
        actors (background workers, schedulers) or embed-token callers where
        there is no Nubi user id (the embed subject is recorded in ``summary``
        instead, without PII).
    actor_kind:
        ``"access"`` | ``"embed"`` | ``"system"``.
    action:
        Dot-notation action string, e.g. ``"board.create"``,
        ``"metric.update"``, ``"connector.delete"``.
    resource_type:
        Resource category, e.g. ``"board"``, ``"metric"``, ``"connector"``,
        ``"mcp_server"``, ``"secret"``.
    resource_id:
        The resource's id, if available.  May be ``None`` for bulk or
        pre-creation failure paths.
    summary:
        Non-sensitive metadata dict. MUST NOT contain PII, row data, or
        secrets.  Examples of safe keys: ``name``, ``connector_type``,
        ``action_count``, ``status``.

    Notes
    -----
    This function is fire-and-forget: any exception from the DB write is
    swallowed and logged as a WARNING.  The caller's mutation path is never
    interrupted.
    """
    try:
        from app.db import execute  # noqa: PLC0415 — lazy to avoid circular imports

        safe_summary = summary or {}
        # Serialise to JSON to detect any non-serialisable values early;
        # asyncpg wants a jsonb-compatible Python type (dict).
        try:
            json.dumps(safe_summary)
        except (TypeError, ValueError):
            safe_summary = {}

        await execute(
            """
            INSERT INTO audit_log
                (org_id, actor_user_id, actor_kind, action, resource_type, resource_id, summary)
            VALUES
                ($1, $2, $3, $4, $5, $6, $7::jsonb)
            """,
            str(org_id),
            str(actor_user_id) if actor_user_id is not None else None,
            str(actor_kind),
            str(action),
            str(resource_type),
            str(resource_id) if resource_id is not None else None,
            json.dumps(safe_summary),
        )
    except Exception as exc:  # noqa: BLE001 — audit writes must never break the caller
        logger.warning("record_audit(%s/%s) failed: %s", action, resource_type, exc)
