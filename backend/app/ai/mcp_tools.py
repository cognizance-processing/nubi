"""MCP-aware tool discovery and dispatch for the agent loop.

This module bridges between the per-org MCP server registry and the agent's
tool-calling loop, enabling the agent to discover and invoke BOTH Nubi's
built-in tools AND external MCP server tools (namespaced as
``serverName.toolName``).

Public API
----------
get_mcp_tool_schemas(org_id) -> list[dict]
    Return tool descriptors for all external MCP tools available to *org_id*.
    Each entry follows the same shape as ``tool_schemas()``::

        {"name": "serverName.toolName", "description": "...", "input_schema": {...}}

    Returns [] if the org has no enabled MCP servers, the DB is unavailable,
    or all servers fail to respond.  Never raises.

combined_tool_schemas(claims) -> list[dict]
    Return Nubi's built-in tools + all MCP tools for the org in *claims*.

combined_execute_tool(name, arguments, claims) -> dict
    Execute *name* — routing to the built-in registry if the name contains no
    dot prefix, or to the matching MCP server if it does (``server.tool``).

_server_map_for_org(org_id) -> dict[str, MCPServer]
    Build {server_name: MCPServer} from the org's enabled servers.
    Internal — exposed for testing.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.ai.mcp import MCPServer, MCPToolDef, call_tool_sync, list_tools_sync
from app.ai.tools import execute_tool as _builtin_execute_tool
from app.ai.tools import tool_schemas as _builtin_tool_schemas

logger = logging.getLogger("nubi.ai.mcp_tools")

# ---------------------------------------------------------------------------
# Sync→async bridge (same pattern as tools.py and mcp.py)
# ---------------------------------------------------------------------------


def _run_sync(coro):
    """Run *coro* to completion from synchronous code."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        import concurrent.futures  # noqa: PLC0415

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()

    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _get_enabled_servers_async(org_id: str) -> list[dict]:
    """Fetch the org's enabled MCP servers with decrypted auth_token."""
    try:
        from app.mcp.store import get_mcp_store  # noqa: PLC0415

        return await get_mcp_store().get_enabled_for_org(org_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("mcp_tools: could not fetch servers for org %s: %s", org_id, exc)
        return []


def _server_map_for_org(org_id: str) -> dict[str, MCPServer]:
    """Return {server_name: MCPServer} for *org_id*'s enabled MCP servers.

    Exposed for testing; internal use by this module only.
    """
    rows = _run_sync(_get_enabled_servers_async(org_id))
    server_map: dict[str, MCPServer] = {}
    for row in rows:
        name = row.get("name", "")
        url = row.get("url", "")
        transport = row.get("transport", "http")
        auth_token = row.get("auth_token")  # already decrypted
        if name and url:
            server_map[name] = MCPServer(
                name=name,
                url=url,
                transport=transport,
                auth_token=auth_token,
            )
    return server_map


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_mcp_tool_schemas(org_id: str) -> list[dict[str, Any]]:
    """Return tool schema descriptors for all external MCP tools for *org_id*.

    Parameters
    ----------
    org_id:
        The caller's org id.  Only servers belonging to this org are consulted.

    Returns
    -------
    list[dict]
        Each entry: ``{"name": "serverName.toolName", "description": "...",
        "input_schema": {...}}``.  Empty list on any error.
    """
    if not org_id:
        return []

    try:
        server_map = _server_map_for_org(org_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("mcp_tools.get_mcp_tool_schemas: server map failed: %s", exc)
        return []

    schemas: list[dict[str, Any]] = []
    for server in server_map.values():
        tools: list[MCPToolDef] = list_tools_sync(server)
        for t in tools:
            schemas.append(
                {
                    "name": t.namespaced_name,
                    "description": t.description,
                    "input_schema": t.input_schema,
                }
            )
    return schemas


def combined_tool_schemas(claims: dict[str, Any]) -> list[dict[str, Any]]:
    """Return Nubi built-in tools + external MCP tools for the caller's org.

    Parameters
    ----------
    claims:
        Agent claims dict; ``claims["org"]`` is the org id for MCP lookup.

    Returns
    -------
    list[dict]
        Built-in tools first, then MCP tools (namespaced).
    """
    builtin = _builtin_tool_schemas()
    org_id: str = claims.get("org") or ""
    if not org_id:
        return builtin
    mcp_schemas = get_mcp_tool_schemas(org_id)
    return builtin + mcp_schemas


def combined_execute_tool(
    name: str,
    arguments: dict[str, Any],
    claims: dict[str, Any],
) -> dict[str, Any]:
    """Execute *name* — built-in or MCP — and return the result dict.

    Routing
    -------
    - Names WITHOUT a dot are dispatched to Nubi's built-in tool registry
      (``execute_tool`` from ``app.ai.tools``).
    - Names WITH a dot (``serverName.toolName``) are dispatched to the
      matching enabled MCP server for the caller's org.

    Parameters
    ----------
    name:
        Tool name.  Namespaced MCP tools use ``"serverName.toolName"``.
    arguments:
        Tool arguments dict.
    claims:
        Caller's auth claims (RLS enforced; ``claims["org"]`` for MCP routing).

    Returns
    -------
    dict
        JSON-serialisable result.

    Raises
    ------
    AppError("tool_not_found", 404)
        When neither a built-in nor a matching MCP server tool is found.
    """
    # ── Built-in tool path ────────────────────────────────────────────────────
    if "." not in name:
        return _builtin_execute_tool(name, arguments, claims)

    # ── MCP tool path ─────────────────────────────────────────────────────────
    server_name, tool_name = name.split(".", 1)
    org_id: str = claims.get("org") or ""

    if not org_id:
        from app.errors import AppError  # noqa: PLC0415

        raise AppError(
            "tool_not_found",
            f"MCP tool {name!r} cannot be dispatched: no org in claims.",
            404,
        )

    try:
        server_map = _server_map_for_org(org_id)
    except Exception as exc:  # noqa: BLE001
        from app.errors import AppError  # noqa: PLC0415

        raise AppError("tool_not_found", f"MCP server lookup failed: {exc}", 404) from exc

    server = server_map.get(server_name)
    if server is None:
        from app.errors import AppError  # noqa: PLC0415

        raise AppError(
            "tool_not_found",
            f"No enabled MCP server named {server_name!r} for this org.",
            404,
        )

    result = call_tool_sync(server, tool_name, arguments)
    # Normalise: surface is_error as a tool error so the agent sees it.
    if result.get("is_error"):
        err = result.get("error") or {}
        return {
            "error": {
                "code": "mcp_tool_error",
                "message": err.get("message", str(result)),
            }
        }
    return result
