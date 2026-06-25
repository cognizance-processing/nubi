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

from app.auth.deps import current_user, verified_identity
from app.auth.roles import require_writer_default
from app.auth.verify import VerifiedIdentity
from app.connectors.ssrf import guard_url
from app.errors import AppError
from app.repos.provider import Repo, get_repo
from app.routes import api_router
from app.routes._org import get_user_org as _get_user_org

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
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> list[dict[str, Any]]:
    """Return all MCP servers registered for the caller's org (no secrets)."""
    from app.mcp.store import get_mcp_store  # noqa: PLC0415

    org_id = await _get_user_org(user["id"], repo)
    rows = await get_mcp_store().list_for_org(org_id)
    # Strip secret fields before returning.
    return [_strip_secrets(r) for r in rows]


@mcp_router.post("/servers", status_code=201, dependencies=[Depends(require_writer_default)])
async def create_mcp_server(
    body: McpServerCreate,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Register a new MCP server for the caller's org."""
    from app.mcp.store import get_mcp_store  # noqa: PLC0415

    # SSRF guard — validate the user-supplied URL before persisting.
    guard_url(body.url)

    org_id = await _get_user_org(user["id"], repo)
    row = await get_mcp_store().create(
        org_id=org_id,
        name=body.name,
        url=body.url,
        transport=body.transport,
        auth_token=body.auth_token,
        enabled=body.enabled,
        created_by=user["id"],
    )
    return _strip_secrets(row)


@mcp_router.get("/servers/{server_id}")
async def get_mcp_server(
    server_id: str,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Return a single MCP server by id (no secrets)."""
    from app.mcp.store import get_mcp_store  # noqa: PLC0415

    org_id = await _get_user_org(user["id"], repo)
    row = await get_mcp_store().get_by_id(server_id, org_id)
    if row is None:
        raise AppError("mcp_server_not_found", "MCP server not found.", 404)
    return _strip_secrets(row)


@mcp_router.put("/servers/{server_id}", dependencies=[Depends(require_writer_default)])
async def update_mcp_server(
    server_id: str,
    body: McpServerUpdate,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> dict[str, Any]:
    """Partially update an MCP server."""
    from app.mcp.store import get_mcp_store  # noqa: PLC0415

    # SSRF guard on the updated URL if provided.
    if body.url is not None:
        guard_url(body.url)

    org_id = await _get_user_org(user["id"], repo)

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
    return _strip_secrets(row)


@mcp_router.delete("/servers/{server_id}", status_code=204, dependencies=[Depends(require_writer_default)])
async def delete_mcp_server(
    server_id: str,
    user: dict[str, Any] = Depends(current_user),
    repo: Repo = Depends(get_repo),
) -> None:
    """Delete an MCP server."""
    from app.mcp.store import get_mcp_store  # noqa: PLC0415

    org_id = await _get_user_org(user["id"], repo)
    deleted = await get_mcp_store().delete(server_id, org_id)
    if not deleted:
        raise AppError("mcp_server_not_found", "MCP server not found.", 404)


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
        org_id = await _get_user_org(str(user["id"]), repo)
        claims: dict[str, Any] = {
            "kind": "access",
            "sub": str(user.get("id", "")),
            "org": org_id,
            "policies": dict(identity.policies or {}),
            "scope": ["read:*", "write:*"],
        }

        try:
            result = await asyncio.to_thread(execute_tool, tool_name, arguments, claims)
            return _jsonrpc_result(
                rpc_id,
                {
                    "content": [{"type": "text", "text": json.dumps(result)}],
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
