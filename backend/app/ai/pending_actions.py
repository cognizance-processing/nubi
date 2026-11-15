"""Destructive agent actions wait for the person.

An assistant steered by content it has read (a query result, a document, a
third-party tool's output) must not be able to delete or overwrite a board on
its own say-so.  When the in-app agent asks for such a tool, nothing runs:
the request is recorded here as a *pending action* and shown to the person,
who confirms it — as themselves, with their own role — or dismisses it.

A pending action is single-use, expires after a few minutes, and can only be
confirmed by the user it was proposed to, in the organisation it was proposed
for.  It is stored in the shared cache backend so it works across workers.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from app.errors import AppError

#: Tools the in-app agent can only *propose*.
CONFIRM_REQUIRED: frozenset[str] = frozenset({"delete_dashboard", "update_dashboard"})



def needs_confirmation(name: str, arguments: dict[str, Any] | None) -> bool:
    """True for a call that would change or remove a saved dashboard.

    ``edit_dashboard`` counts only when it targets a saved board; editing a
    spec the caller holds in hand changes nothing until it is saved.
    """
    if name in CONFIRM_REQUIRED:
        return True
    return name == "edit_dashboard" and bool((arguments or {}).get("board_id"))


_TTL_SECONDS = 600
_PREFIX = "pending-action:"


def _cache() -> Any:
    from app.connectors.cache import get_cache  # noqa: PLC0415

    return get_cache()


def _summary(kind: str, arguments: dict[str, Any]) -> str:
    board = arguments.get("board_id") or arguments.get("dashboard_id") or "a dashboard"
    if kind == "delete_dashboard":
        return f"Delete dashboard {board}"
    if kind == "edit_dashboard":
        return f"Edit dashboard {board}"
    return f"Replace the contents of dashboard {board}"


def propose(kind: str, arguments: dict[str, Any], claims: dict[str, Any]) -> dict[str, Any]:
    """Record a destructive action for the person to confirm; run nothing."""
    action_id = uuid.uuid4().hex
    record = {
        "kind": kind,
        "arguments": {k: v for k, v in (arguments or {}).items() if k != "confirm"},
        "org": str(claims.get("org") or ""),
        "user": str(claims.get("sub") or ""),
        "expires": time.time() + _TTL_SECONDS,
    }
    key = _PREFIX + action_id
    _cache().put(key, json.dumps(record, default=str).encode("utf-8"), tags=[key])
    return {
        "pending_confirmation": True,
        "action_id": action_id,
        "kind": kind,
        "summary": _summary(kind, record["arguments"]),
        "message": (
            "Nothing has been changed. This action needs the user's confirmation "
            "in the app; tell them what you proposed and why."
        ),
    }


def _take(action_id: str, user_id: str, org_id: str) -> dict[str, Any]:
    key = _PREFIX + str(action_id)
    raw = _cache().get(key)
    missing = AppError("not_found", "That action is no longer available.", 404)
    if raw is None:
        raise missing
    try:
        record = json.loads(raw)
    except (ValueError, TypeError):
        raise missing from None
    if record.get("user") != str(user_id) or record.get("org") != str(org_id):
        # Same answer as "does not exist": no hint that it belongs to someone else.
        raise missing
    _cache().invalidate(key)  # single-use, whatever happens next
    if float(record.get("expires") or 0) < time.time():
        raise missing
    return record


def confirm(action_id: str, *, user_id: str, org_id: str, claims: dict[str, Any]) -> dict[str, Any]:
    """Run a pending action as the confirming person (their role, their scope)."""
    record = _take(action_id, user_id, org_id)
    from app.ai import agent  # noqa: PLC0415 — the same executor the agent uses

    arguments = dict(record["arguments"])
    if record["kind"] == "delete_dashboard":
        arguments["confirm"] = True
    return agent.execute_tool(record["kind"], arguments, claims)


def dismiss(action_id: str, *, user_id: str, org_id: str) -> None:
    """Drop a pending action without running it."""
    _take(action_id, user_id, org_id)
