"""MCP server registry CRUD + Nubi-as-MCP-server endpoint.

Endpoints
---------
GET    /mcp/servers              -> list servers for caller's org (no secrets)
POST   /mcp/servers              -> register a new MCP server
GET    /mcp/servers/{server_id}  -> get one server (no secret)
PUT    /mcp/servers/{server_id}  -> update a server
DELETE /mcp/servers/{server_id}  -> delete a server

POST   /mcp                      -> Nubi-as-MCP-server endpoint (JSON-RPC)
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.audit import record_audit as _record_audit
from app.auth.deps import current_user, verified_identity
from app.auth.roles import require_writer
from app.auth.verify import VerifiedIdentity
from app.connectors.ssrf import guard_url
from app.errors import AppError
from app.repos.provider import Repo, get_repo
from app.routes import api_router
from app.routes._org import resolve_org_id as _resolve_org_id

logger = logging.getLogger("nubi.mcp")

mcp_router = APIRouter(prefix="/mcp", tags=["mcp"])


# ---------------------------------------------------------------------------
# Pydantic request models
# ---------------------------------------------------------------------------


class McpServerCreate(BaseModel):
    name: str
    url: str
    transport: str = "http"
    auth_token: str | None = None  # plaintext, will be encrypted at rest
    enabled: bool = True


class McpServerUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    transport: str | None = None
    auth_token: str | None = None
    enabled: bool | None = None


# ---------------------------------------------------------------------------
# CRUD routes — /mcp/servers
# ---------------------------------------------------------------------------


@mcp_router.get("/servers")
async def list_mcp_servers(
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> list[dict[str, Any]]:
    """Return all MCP servers registered for the caller's org (no secrets)."""
    from app.mcp.store import get_mcp_store  # noqa: PLC0415

    org_id = await _resolve_org_id(user["id"], repo, request)
    rows = await get_mcp_store().list_for_org(org_id)
    # Strip secret fields before returning.
    return [_strip_secrets(r) for r in rows]


@mcp_router.post("/servers", status_code=201, dependencies=[Depends(require_writer)])
async def create_mcp_server(
    body: McpServerCreate,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Register a new MCP server for the caller's org."""
    from app.mcp.store import get_mcp_store  # noqa: PLC0415

    # SSRF guard — validate the user-supplied URL before persisting.
    guard_url(body.url)

    org_id = await _resolve_org_id(user["id"], repo, request)
    row = await get_mcp_store().create(
        org_id=org_id,
        name=body.name,
        url=body.url,
        transport=body.transport,
        auth_token=body.auth_token,
        enabled=body.enabled,
        created_by=user["id"],
    )
    # Audit — fire-and-forget; POPIA-safe (no auth_token in summary).
    await _record_audit(
        org_id=org_id,
        actor_user_id=str(user["id"]),
        actor_kind="access",
        action="mcp_server.create",
        resource_type="mcp_server",
        resource_id=str(row.get("id") or ""),
        summary={"name": body.name, "transport": body.transport},
    )
    return _strip_secrets(row)


@mcp_router.get("/servers/{server_id}")
async def get_mcp_server(
    server_id: str,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Return a single MCP server by id (no secrets)."""
    from app.mcp.store import get_mcp_store  # noqa: PLC0415

    org_id = await _resolve_org_id(user["id"], repo, request)
    row = await get_mcp_store().get_by_id(server_id, org_id)
    if row is None:
        raise AppError("mcp_server_not_found", "MCP server not found.", 404)
    return _strip_secrets(row)


@mcp_router.put("/servers/{server_id}", dependencies=[Depends(require_writer)])
async def update_mcp_server(
    server_id: str,
    body: McpServerUpdate,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Partially update an MCP server."""
    from app.mcp.store import get_mcp_store  # noqa: PLC0415

    # SSRF guard on the updated URL if provided.
    if body.url is not None:
        guard_url(body.url)

    org_id = await _resolve_org_id(user["id"], repo, request)

    update_kwargs: dict[str, Any] = {}
    if body.name is not None:
        update_kwargs["name"] = body.name
    if body.url is not None:
        update_kwargs["url"] = body.url
    if body.transport is not None:
        update_kwargs["transport"] = body.transport
    if body.enabled is not None:
        update_kwargs["enabled"] = body.enabled
    # auth_token is nullable — use model_fields_set for sentinel detection.
    if "auth_token" in body.model_fields_set:
        update_kwargs["auth_token"] = body.auth_token

    row = await get_mcp_store().update(server_id, org_id, **update_kwargs)
    if row is None:
        raise AppError("mcp_server_not_found", "MCP server not found.", 404)
    # Audit — fire-and-forget; POPIA-safe (no auth_token in summary).
    await _record_audit(
        org_id=org_id,
        actor_user_id=str(user["id"]),
        actor_kind="access",
        action="mcp_server.update",
        resource_type="mcp_server",
        resource_id=server_id,
        summary={"name": str(row.get("name") or "")},
    )
    return _strip_secrets(row)


@mcp_router.post("/servers/{server_id}/test")
async def test_mcp_server(
    server_id: str,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Actually connect to a registered MCP server and report what happened.

    Unlike the connector "test" (structural only), this is a REAL check —
    it runs the same MCP handshake + ``tools/list`` call the agent's tool
    loop uses to discover this server's tools (``app.ai.mcp``), not a bespoke
    HTTP ping. Persists the result (``last_tested_at``/``last_test_ok``/
    ``last_test_tool_count``/``last_test_error``) so ``GET /mcp/servers``
    shows it at rest, and fires a notification on a real ok<->fail
    transition — same shape as the bridge and connector status stories.

    Returns
    -------
    dict
        ``{ok: bool, tool_count: int | None, error: str | None,
           server_id: str}``
    """
    from app.ai.mcp import MCPServer, test_connection_async  # noqa: PLC0415
    from app.mcp.store import get_mcp_store  # noqa: PLC0415

    org_id = await _resolve_org_id(user["id"], repo, request)
    store = get_mcp_store()
    row = await store.get_with_secret(server_id, org_id)
    if row is None:
        raise AppError("mcp_server_not_found", "MCP server not found.", 404)

    server = MCPServer(
        name=row["name"], url=row["url"], transport=row.get("transport") or "http",
        auth_token=row.get("auth_token"),
    )
    tools, error = await test_connection_async(server)
    ok = error is None
    tool_count = len(tools) if ok else None

    # Read the PRE-test row (public, has last_test_ok) for transition detection.
    before = await store.get_by_id(server_id, org_id)
    updated = await store.record_test_result(server_id, org_id, ok=ok, tool_count=tool_count, error=error)

    was_ok = before.get("last_test_ok") if before else None
    if was_ok is not None and was_ok != ok:
        try:
            from app.notify.notifications import get_notification_store  # noqa: PLC0415

            name = row.get("name") or "MCP server"
            if ok:
                severity, title, body = (
                    "success", f"{name} is reachable again",
                    f"Reconnected — {tool_count} tool(s) available.",
                )
            else:
                severity, title, body = (
                    "warning", f"{name} failed its connection test",
                    error or "The last connection test failed.",
                )
            await get_notification_store().create(
                str(org_id), type="mcp_server_health", severity=severity, title=title, body=body,
                link="/settings/mcp", metadata={"server_id": server_id},
            )
        except Exception:  # noqa: BLE001
            logger.warning("mcp: health-change notification failed for %s", server_id)

    return {
        "ok": ok,
        "tool_count": tool_count,
        "error": error,
        "server_id": server_id,
        "server": _strip_secrets(updated) if updated else None,
    }


@mcp_router.delete("/servers/{server_id}", status_code=204, dependencies=[Depends(require_writer)])
async def delete_mcp_server(
    server_id: str,
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> None:
    """Delete an MCP server."""
    from app.mcp.store import get_mcp_store  # noqa: PLC0415

    org_id = await _resolve_org_id(user["id"], repo, request)
    deleted = await get_mcp_store().delete(server_id, org_id)
    if not deleted:
        raise AppError("mcp_server_not_found", "MCP server not found.", 404)
    # Audit — fire-and-forget; POPIA-safe.
    await _record_audit(
        org_id=org_id,
        actor_user_id=str(user["id"]),
        actor_kind="access",
        action="mcp_server.delete",
        resource_type="mcp_server",
        resource_id=server_id,
        summary={},
    )


# ---------------------------------------------------------------------------
# Helper — strip secret fields from DB rows before returning to callers
# ---------------------------------------------------------------------------


def _strip_secrets(row: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *row* with secret fields removed."""
    secret_fields = {"auth_token", "secret", "token"}
    return {k: v for k, v in row.items() if k not in secret_fields}


# ---------------------------------------------------------------------------
# Nubi-as-MCP-server endpoint — POST /mcp
# ---------------------------------------------------------------------------
#
# Exposes Nubi's own tool registry to external MCP clients via JSON-RPC 2.0.
# Auth is a first-party Bearer token (same as /ai/chat).
# Tool calls are dispatched via execute_tool, offloaded to a thread.


@mcp_router.post("")
async def mcp_endpoint(
    request: Request,
    user: dict[str, Any] = Depends(current_user),
    identity: VerifiedIdentity = Depends(verified_identity),
    repo: Repo = Depends(get_repo),
) -> JSONResponse:
    """JSON-RPC 2.0 endpoint exposing Nubi's tools to external MCP clients.

    Handles:
    - ``initialize``  — server handshake
    - ``tools/list``  — enumerate Nubi's tool catalog
    - ``tools/call``  — execute a tool via ``execute_tool``

    Auth: Bearer JWT (same ``current_user`` + ``verified_identity`` deps as
    ``/ai/chat``).  Tool calls run in a thread via ``asyncio.to_thread``.
    """
    from app.ai.tools import execute_tool, tool_schemas  # noqa: PLC0415

    # ── Parse the JSON-RPC request body ──────────────────────────────────────
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _jsonrpc_error(None, -32700, "Parse error")

    rpc_id = body.get("id")
    method = body.get("method", "")
    params = body.get("params") or {}

    # ── initialize ────────────────────────────────────────────────────────────
    if method == "initialize":
        return _jsonrpc_result(
            rpc_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "nubi", "version": "1.0.0"},
            },
        )

    # ── tools/list ────────────────────────────────────────────────────────────
    if method == "tools/list":
        schemas = tool_schemas()
        # Rename input_schema → inputSchema for MCP wire format.
        tools = [
            {
                "name": t["name"],
                "description": t["description"],
                "inputSchema": t["input_schema"],
            }
            for t in schemas
        ]
        return _jsonrpc_result(rpc_id, {"tools": tools})

    # ── tools/call ────────────────────────────────────────────────────────────
    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments: dict[str, Any] = params.get("arguments") or {}

        # Build first-party claims — mirrors ai_chat exactly.
        # SECURITY: pass the identity's ACTUAL scope, never hard-code write:*
        # which would escalate a read-only or embed token to writer level.
        org_id = await _resolve_org_id(str(user["id"]), repo, request)
        claims: dict[str, Any] = {
            "kind": identity.kind,
            "sub": str(user.get("id", "")),
            "org": org_id,
            "policies": dict(identity.policies or {}),
            "scope": list(identity.scope or []),
        }

        try:
            result = await asyncio.to_thread(execute_tool, tool_name, arguments, claims)
            return _jsonrpc_result(
                rpc_id,
                {
                    "content": [{"type": "text", "text": _encode_tool_result(result)}],
                    "isError": False,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("mcp tools/call %r failed: %s", tool_name, exc)
            return _jsonrpc_result(
                rpc_id,
                {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
            )

    # ── Unknown method ────────────────────────────────────────────────────────
    return _jsonrpc_error(rpc_id, -32601, "Method not found")


# ---------------------------------------------------------------------------
# Response size guard
# ---------------------------------------------------------------------------
#
# An MCP client consumes a tool result as text in a context window, so an
# oversized payload is a denial-of-service against the caller rather than
# against us.  Individual tools cap their own output (see run_query's `limit`),
# but this is the backstop that holds for EVERY tool, including ones added
# later that forget to.
#
# Truncating serialised JSON mid-string would hand the client invalid JSON, so
# instead we shrink the payload structurally: drop the biggest list-valued key
# down to a sample and say so in-band, and only fall back to hard text
# truncation when there is no list to shrink.

_MAX_TOOL_RESULT_BYTES = 100_000


def _encode_tool_result(result: Any) -> str:
    """Serialise *result* to JSON, shrinking it if it exceeds the size guard."""
    text = json.dumps(result)
    if len(text) <= _MAX_TOOL_RESULT_BYTES:
        return text

    if isinstance(result, dict):
        # Find the list-valued key contributing the most bytes and sample it.
        list_keys = [k for k, v in result.items() if isinstance(v, list) and v]
        if list_keys:
            biggest = max(list_keys, key=lambda k: len(json.dumps(result[k])))
            items = result[biggest]
            # Binary-search the largest prefix that fits.
            lo, hi = 0, len(items)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                trial = dict(result)
                trial[biggest] = items[:mid]
                if len(json.dumps(trial)) <= _MAX_TOOL_RESULT_BYTES:
                    lo = mid
                else:
                    hi = mid - 1
            shrunk = dict(result)
            shrunk[biggest] = items[:lo]
            shrunk["_truncated"] = {
                "key": biggest,
                "returned": lo,
                "total": len(items),
                "reason": (
                    f"Result exceeded the {_MAX_TOOL_RESULT_BYTES}-byte MCP "
                    f"response guard. Narrow the request (filter, aggregate, "
                    f"or pass a smaller limit) to see the rest."
                ),
            }
            return json.dumps(shrunk)

    # No list to shrink — hard-truncate the text and stay valid JSON by
    # wrapping it in an envelope.
    return json.dumps(
        {
            "_truncated": {
                "reason": (
                    f"Result exceeded the {_MAX_TOOL_RESULT_BYTES}-byte MCP "
                    f"response guard and had no list field to sample."
                ),
                "total_bytes": len(text),
            },
            "preview": text[:_MAX_TOOL_RESULT_BYTES],
        }
    )


# ---------------------------------------------------------------------------
# JSON-RPC helpers
# ---------------------------------------------------------------------------


def _jsonrpc_result(rpc_id: Any, result: Any) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "result": result})


def _jsonrpc_error(rpc_id: Any, code: int, message: str) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}
    )


# ---------------------------------------------------------------------------
# Self-register on api_router
# ---------------------------------------------------------------------------

api_router.include_router(mcp_router)
