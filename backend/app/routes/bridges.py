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

import asyncio
import secrets
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

#: How often the live tunnel re-validates its bridge token so a mid-session
#: revoke / lapsed rotation grace drops the tunnel promptly (§7).
_BRIDGE_TOKEN_REVALIDATE_SECONDS = 30.0

from fastapi import APIRouter, Depends, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from app.auth.bridge_tokens import get_bridge_token_store
from app.auth.deps import current_user
from app.auth.roles import get_org_role, require_writer_default
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


# ───────────────────────────────────────────────────────────────────────────
# Bridge-token management (§7) — mint / list / rotate / revoke
# ───────────────────────────────────────────────────────────────────────────
#
# A bridge token is the credential the agent presents on every tunnel
# handshake/heartbeat (hashed at rest, bound to (org, bridge)). These routes are
# owner/admin-only and org-scoped — minting a bridge credential is a privileged,
# audit-worthy action, stricter than the writer guard on bridge CRUD.


async def _require_owner_or_admin(user_id: str, org_id: str, repo: Repo) -> None:
    """Raise 403 unless the caller is an owner/admin of *org_id*."""
    role = await get_org_role(user_id, org_id, repo)
    if role not in ("owner", "admin"):
        raise AppError(
            "forbidden",
            "Only org owners and admins can manage bridge tokens.",
            403,
        )


class BridgeTokenIn(BaseModel):
    """Request body for POST /bridges/{id}/tokens."""

    name: str = "bridge token"


@router.post("/{bridge_id}/tokens", status_code=201)
async def mint_bridge_token(
    bridge_id: str,
    body: BridgeTokenIn,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Mint a new bridge token for *bridge_id* (owner/admin, org-scoped).

    The raw ``nubi_br_…`` token is returned EXACTLY ONCE here and never again —
    only its SHA-256 hash is stored. Hand it to the agent
    (``nubi bridge start --token …``).
    """
    org_id = await _get_user_org(str(user["id"]), repo)
    await _require_owner_or_admin(str(user["id"]), org_id, repo)
    bridge = await get_bridge_store().get(org_id, bridge_id)
    if bridge is None:
        raise AppError("bridge_not_found", f"Bridge {bridge_id!r} not found.", 404)

    from app.auth.bridge_tokens import _public_row  # noqa: PLC0415

    raw, row = await get_bridge_token_store().mint(org_id, bridge_id, body.name)
    return {"token": raw, "bridge_token": _public_row(row)}


@router.get("/{bridge_id}/tokens")
async def list_bridge_tokens(
    bridge_id: str,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """List a bridge's tokens (no token material; owner/admin, org-scoped)."""
    org_id = await _get_user_org(str(user["id"]), repo)
    await _require_owner_or_admin(str(user["id"]), org_id, repo)
    bridge = await get_bridge_store().get(org_id, bridge_id)
    if bridge is None:
        raise AppError("bridge_not_found", f"Bridge {bridge_id!r} not found.", 404)
    tokens = await get_bridge_token_store().list_for_bridge(org_id, bridge_id)
    return {"bridge_tokens": tokens}


@router.post("/{bridge_id}/tokens/{token_id}/rotate", status_code=201)
async def rotate_bridge_token(
    bridge_id: str,
    token_id: str,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Rotate a bridge token: mint a replacement, grace-window the old one (§7).

    During the grace window BOTH tokens validate, so a running agent can swap
    its token without a tunnel drop. The new raw token is returned once.
    """
    org_id = await _get_user_org(str(user["id"]), repo)
    await _require_owner_or_admin(str(user["id"]), org_id, repo)
    bridge = await get_bridge_store().get(org_id, bridge_id)
    if bridge is None:
        raise AppError("bridge_not_found", f"Bridge {bridge_id!r} not found.", 404)

    from app.auth.bridge_tokens import _public_row  # noqa: PLC0415

    result = await get_bridge_token_store().rotate(token_id, org_id, bridge_id)
    if result is None:
        raise AppError("bridge_token_not_found", f"Token {token_id!r} not found.", 404)
    raw, row = result
    return {"token": raw, "bridge_token": _public_row(row)}


@router.delete("/{bridge_id}/tokens/{token_id}", status_code=204)
async def revoke_bridge_token(
    bridge_id: str,
    token_id: str,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> Response:
    """Revoke a bridge token and drop the live tunnel (§7).

    Revocation sets ``revoked_at`` so every subsequent handshake/heartbeat
    fails, AND immediately drops the live WebSocket tunnel via the broker — the
    bridge goes ``offline`` and connectors pinned to it fail fast with
    ``bridge_not_connected`` rather than hanging.
    """
    org_id = await _get_user_org(str(user["id"]), repo)
    await _require_owner_or_admin(str(user["id"]), org_id, repo)
    bridge = await get_bridge_store().get(org_id, bridge_id)
    if bridge is None:
        raise AppError("bridge_not_found", f"Bridge {bridge_id!r} not found.", 404)

    revoked = await get_bridge_token_store().revoke(token_id, org_id, bridge_id)
    if not revoked:
        raise AppError("bridge_token_not_found", f"Token {token_id!r} not found.", 404)

    # Drop the live tunnel so the now-untrusted agent cannot keep tunnelling.
    await get_broker().drop(bridge_id)
    # Reflect the offline transition on the bridge row.
    row = _bridge_store._rows.get(bridge_id)
    if row is not None:
        row["status"] = "offline"
        row["updated_at"] = _now_iso()
    return Response(status_code=204)


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
    # The agent only knows its bridge_id and token. The token authenticates the
    # CONTROL CHANNEL ONLY (§7): it lets the agent open the tunnel and claim its
    # bridge's tasks — by itself it reads no secrets and no storage.
    row: dict[str, Any] | None = _bridge_store._rows.get(bridge_id)
    if row is None:
        await websocket.close(code=4404)
        return

    # Hashed, rotatable bridge token (§7): validate against the bridge_tokens
    # store, which binds the token to (org_id, bridge_id). A revoked token (or
    # one past its rotation grace window) fails here, so the handshake is
    # rejected before accept(). ADDITIVE: fall back to the legacy plaintext
    # ``config["token"]`` so existing bridges keep working unchanged.
    token_store = get_bridge_token_store()
    authed = False
    binding = await token_store.validate(supplied_token)
    if binding is not None:
        token_org_id, token_bridge_id = binding
        if token_bridge_id == bridge_id and token_org_id == str(row.get("org_id")):
            authed = True
    if not authed:
        # Legacy plaintext-token fallback (ADDITIVE) — only honoured while the
        # bridge has NOT yet adopted any hashed v2 token. Once a v2 token is
        # minted, this downgrade path is CLOSED so a revoked/rotated bridge can
        # never be kept alive through the un-revocable plaintext config token
        # (§7: "the legacy plaintext-token fallback isn't a downgrade hole").
        # Compared with secrets.compare_digest to avoid a timing side channel.
        if not await token_store.has_any_for_bridge(str(row.get("org_id")), bridge_id):
            legacy_token: str = str(row.get("config", {}).get("token") or "")
            if legacy_token and secrets.compare_digest(supplied_token, legacy_token):
                authed = True
    if not authed:
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

    # Server-side re-validation: the token is checked on connect, but a token can
    # be revoked (or its rotation grace window can lapse) mid-tunnel. The explicit
    # broker.drop() on the revoke route only fires in the worker that holds the
    # live socket; to make revocation robust across workers / direct-DB revokes we
    # also re-validate the token on an interval here and drop the tunnel ourselves
    # the moment it stops validating (§7: "broker rejects next handshake/heartbeat
    # and drops the live tunnel"). The legacy plaintext path is intentionally NOT
    # re-validated (no revocation semantics) — but the moment a v2 token is minted
    # for the bridge the connect-time check already closed that path.
    revalidate_every_s = _BRIDGE_TOKEN_REVALIDATE_SECONDS
    using_v2_token = binding is not None

    async def _revalidate_loop() -> None:
        if not using_v2_token:
            return
        while True:
            await asyncio.sleep(revalidate_every_s)
            still = await token_store.validate(supplied_token)
            ok = (
                still is not None
                and still[1] == bridge_id
                and still[0] == str(row.get("org_id"))
            )
            if not ok:
                await broker.drop(bridge_id)
                row["status"] = "offline"
                row["updated_at"] = _now_iso()
                try:
                    await websocket.close(code=4401)
                except Exception:  # noqa: BLE001
                    pass
                return

    revalidator = asyncio.ensure_future(_revalidate_loop())
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
        revalidator.cancel()
        try:
            await revalidator
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        await broker.unregister(bridge_id)


# ---------------------------------------------------------------------------
# Register on the shared api_router (self-registration pattern)
# ---------------------------------------------------------------------------

api_router.include_router(router)
