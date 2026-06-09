"""Bridges CRUD and heartbeat endpoints — M22-A.

A *bridge* is a lightweight agent (running on-prem or in a VPC) that proxies
database connections from the Nubi backend when ``network_mode != 'direct'``.
Bridge agents call ``POST /bridges/{id}/heartbeat`` periodically to signal
liveness.  The query route consults the bridge status before attempting to
connect through it.

Endpoints
---------
POST   /bridges                  — create a new bridge record.
GET    /bridges                  — list all bridges for the caller's org.
GET    /bridges/{id}             — fetch a single bridge (404 if wrong org).
DELETE /bridges/{id}             — delete a bridge (204; wrong org → 404).
POST   /bridges/{id}/heartbeat   — update status='online' + last_seen_at.

Authentication
--------------
All endpoints require a valid first-party Bearer token (``current_user``
dependency).  Every operation is org-scoped: a user can only see and manage
bridges that belong to their org.

Storage
-------
Bridges are stored in an in-module in-memory store (``_BridgeStore``) for the
current milestone.  When the migration lands and the ``bridges`` table is live,
this store should be replaced with a thin wrapper over the DB.  The store
interface (``create``, ``list``, ``get``, ``delete``, ``heartbeat``) is
deliberately simple so the swap is mechanical.

The router self-registers on the shared ``api_router`` at import time (bottom of
this file), so ``main.py`` only needs::

    import app.routes.bridges  # noqa: F401
"""

from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from app.auth.deps import current_user
from app.auth.roles import require_writer_default
from app.bridges.broker import get_broker
from app.errors import AppError
from app.repos.provider import Repo, get_repo
from app.routes import api_router

# ---------------------------------------------------------------------------
# In-module bridge store (replaces the DB until the migration ships)
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class _BridgeStore:
    """Dict-backed in-process store for bridge rows.

    Thread-safety: single async-event-loop assumption (same as asyncpg helpers).
    """

    def __init__(self) -> None:
        # {bridge_id: bridge_dict}
        self._rows: dict[str, dict[str, Any]] = {}

    def reset(self) -> None:
        """Clear all stored bridges.  Called by tests between runs."""
        self._rows.clear()

    async def create(
        self,
        org_id: str,
        created_by: str,
        name: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        bridge_id = str(uuid.uuid4())
        now = _now_iso()
        row: dict[str, Any] = {
            "id": bridge_id,
            "org_id": str(org_id),
            "created_by": str(created_by),
            "name": name,
            "status": "offline",
            "last_seen_at": None,
            "config": deepcopy(config),
            "created_at": now,
            "updated_at": now,
        }
        self._rows[bridge_id] = row
        return deepcopy(row)

    async def list(self, org_id: str) -> list[dict[str, Any]]:
        rows = [
            deepcopy(r)
            for r in self._rows.values()
            if str(r["org_id"]) == str(org_id)
        ]
        rows.sort(key=lambda r: r["created_at"])
        return rows

    async def get(self, org_id: str, bridge_id: str) -> dict[str, Any] | None:
        row = self._rows.get(str(bridge_id))
        if row is None or str(row["org_id"]) != str(org_id):
            return None
        return deepcopy(row)

    async def delete(self, org_id: str, bridge_id: str) -> bool:
        row = self._rows.get(str(bridge_id))
        if row is None or str(row["org_id"]) != str(org_id):
            return False
        del self._rows[str(bridge_id)]
        return True

    async def heartbeat(self, org_id: str, bridge_id: str) -> dict[str, Any] | None:
        row = self._rows.get(str(bridge_id))
        if row is None or str(row["org_id"]) != str(org_id):
            return None
        row["status"] = "online"
        row["last_seen_at"] = _now_iso()
        row["updated_at"] = _now_iso()
        return deepcopy(row)


# Module-level singleton (reset by tests via reset_bridge_store()).
_bridge_store = _BridgeStore()


def get_bridge_store() -> _BridgeStore:
    """Return the active bridge store singleton."""
    return _bridge_store


def reset_bridge_store() -> None:
    """Reset the bridge store — test helper, mirrors connector registry pattern."""
    _bridge_store.reset()


# ---------------------------------------------------------------------------
# Internal helper used by query.py to pre-fetch a bridge row
# ---------------------------------------------------------------------------


async def _get_bridge(org_id: str, bridge_id: str, _repo: Repo | None = None) -> dict[str, Any] | None:
    """Return the bridge row for *bridge_id* scoped to *org_id*, or None."""
    return await _bridge_store.get(org_id, str(bridge_id))


# ---------------------------------------------------------------------------
# Org resolution helper
# ---------------------------------------------------------------------------


async def _get_user_org(user_id: str, repo: Repo) -> str:
    """Return the org_id for the user's first membership (mirrors resources.py)."""
    if hasattr(repo, "get_org_for_user"):
        org_id = repo.get_org_for_user(user_id)
        if org_id:
            return org_id
        raise AppError("org_not_found", "User has no org membership.", 404)

    from app.db import fetchrow as _fetchrow  # noqa: PLC0415

    row = await _fetchrow(
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
# Pydantic schemas
# ---------------------------------------------------------------------------


class BridgeIn(BaseModel):
    """Request body for POST /bridges."""

    name: str
    config: dict[str, Any] = {}


class BridgeOut(BaseModel):
    """Response shape for bridge rows."""

    id: str
    org_id: str
    created_by: str
    name: str
    status: str
    last_seen_at: str | None
    config: dict[str, Any]
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/bridges", tags=["bridges"])


# ── POST /bridges — create ─────────────────────────────────────────────────


@router.post("", status_code=201, response_model=BridgeOut, dependencies=[Depends(require_writer_default)])
async def create_bridge(
    body: BridgeIn,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Create a new bridge record for the caller's org.

    Parameters
    ----------
    body:
        ``BridgeIn`` with ``name`` and optional ``config``.

    Returns
    -------
    BridgeOut
        The newly created bridge row (status='offline', last_seen_at=None).
    """
    org_id = await _get_user_org(str(user["id"]), repo)
    return await get_bridge_store().create(
        org_id=org_id,
        created_by=str(user["id"]),
        name=body.name,
        config=body.config,
    )


# ── GET /bridges — list ────────────────────────────────────────────────────


@router.get("", response_model=list[BridgeOut])
async def list_bridges(
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> list[dict[str, Any]]:
    """List all bridges belonging to the caller's org."""
    org_id = await _get_user_org(str(user["id"]), repo)
    return await get_bridge_store().list(org_id)


# ── GET /bridges/{id} — get ────────────────────────────────────────────────


@router.get("/{bridge_id}", response_model=BridgeOut)
async def get_bridge(
    bridge_id: str,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Fetch a single bridge by ID (org-scoped).

    Returns 404 if the bridge doesn't exist or belongs to a different org.
    """
    org_id = await _get_user_org(str(user["id"]), repo)
    row = await get_bridge_store().get(org_id, bridge_id)
    if row is None:
        raise AppError("bridge_not_found", f"Bridge {bridge_id!r} not found.", 404)
    return row


# ── DELETE /bridges/{id} — delete ─────────────────────────────────────────


@router.delete("/{bridge_id}", status_code=204, dependencies=[Depends(require_writer_default)])
async def delete_bridge(
    bridge_id: str,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> Response:
    """Delete a bridge (org-scoped); returns 204 on success, 404 if not found."""
    org_id = await _get_user_org(str(user["id"]), repo)
    deleted = await get_bridge_store().delete(org_id, bridge_id)
    if not deleted:
        raise AppError("bridge_not_found", f"Bridge {bridge_id!r} not found.", 404)
    return Response(status_code=204)


# ── POST /bridges/{id}/heartbeat ──────────────────────────────────────────


@router.post("/{bridge_id}/heartbeat", response_model=BridgeOut)
async def bridge_heartbeat(
    bridge_id: str,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Accept a heartbeat from a bridge agent.

    Updates ``status='online'`` and ``last_seen_at`` to the current UTC time.
    The bridge agent should call this endpoint on a regular interval (e.g.
    every 30 seconds) to signal liveness.

    Returns 404 if the bridge doesn't exist or belongs to a different org.
    """
    org_id = await _get_user_org(str(user["id"]), repo)
    row = await get_bridge_store().heartbeat(org_id, bridge_id)
    if row is None:
        raise AppError("bridge_not_found", f"Bridge {bridge_id!r} not found.", 404)
    return row


# ── WS /bridges/{id}/connect — bridge agent tunnel ────────────────────────


@router.websocket("/{bridge_id}/connect")
async def bridge_connect(
    bridge_id: str,
    websocket: WebSocket,
) -> None:
    """WebSocket endpoint for bridge agents.

    Authentication
    --------------
    The bridge agent must supply its secret token in **one** of:

    * ``X-Bridge-Token: <token>`` request header
    * ``?token=<token>`` query parameter

    The token is validated against ``bridge.config["token"]``.  If the bridge
    row does not exist or the token does not match, the WebSocket is closed with
    code 4401 before the connection is accepted.

    Protocol
    --------
    Once the handshake is accepted, the broker registers the WebSocket and
    takes over the connection.  The broker will send OPEN frames and receive
    DATA/READY/CLOSE frames using the binary frame protocol defined in
    ``app.bridges.protocol``.

    The endpoint marks the bridge as ``status='online'`` while connected and
    leaves the status unchanged on disconnect (the heartbeat endpoint handles
    the offline transition via TTL/monitoring in production; for now the status
    remains 'online' — a follow-up can add explicit offline-on-disconnect).
    """
    # --- Token extraction ---------------------------------------------------
    token_header = websocket.headers.get("x-bridge-token", "")
    token_query = websocket.query_params.get("token", "")
    supplied_token = token_header or token_query

    if not supplied_token:
        await websocket.close(code=4401)
        return

    # --- Bridge row lookup (no org-scoping needed here; token IS the secret) --
    # We scan all orgs since the agent only knows its bridge_id and token.
    # In production a real DB query would look this up by id directly.
    row: dict[str, Any] | None = _bridge_store._rows.get(bridge_id)
    if row is None:
        await websocket.close(code=4404)
        return

    expected_token: str = str(row.get("config", {}).get("token") or "")
    if not expected_token or supplied_token != expected_token:
        await websocket.close(code=4401)
        return

    # --- Accept the connection -----------------------------------------------
    await websocket.accept()

    # Mark the bridge online.
    row["status"] = "online"
    row["last_seen_at"] = _now_iso()
    row["updated_at"] = _now_iso()

    broker = get_broker()
    await broker.register(bridge_id, websocket)

    try:
        # Keep the connection alive by waiting for messages.
        # The broker's reader loop runs in the background; here we just wait
        # for the client to disconnect.
        while True:
            try:
                await websocket.receive_bytes()
            except WebSocketDisconnect:
                break
            except Exception:
                break
    finally:
        await broker.unregister(bridge_id)


# ---------------------------------------------------------------------------
# Register on the shared api_router (self-registration pattern)
# ---------------------------------------------------------------------------

api_router.include_router(router)
