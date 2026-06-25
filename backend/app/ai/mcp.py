"""Async MCP (Model Context Protocol) client over Streamable-HTTP transport.

Uses the official ``mcp`` package's ``streamablehttp_client`` + ``ClientSession``.

The public interface is **fully synchronous** (the agent loop is sync); async
internals are bridged via the same ``_run_sync`` helper used in tools.py.

Public API
----------
MCPServer       — dataclass describing an MCP server (name, url, transport, auth_token)
MCPToolDef      — dataclass describing one tool exposed by a server
MCPClientError  — exception class for MCP client errors

list_tools_sync(server, *, timeout=10.0) -> list[MCPToolDef]
    Enumerate the tools advertised by *server*.  Never raises; returns [] on error.

call_tool_sync(server, tool_name, arguments, *, timeout=30.0) -> dict
    Invoke a tool on *server*.  Returns
    ``{"content": [...], "is_error": False}`` on success,
    ``{"error": {...}, "is_error": True}`` on failure.  Never raises.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Awaitable, TypeVar

from app.connectors.ssrf import guard_url

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


# ---------------------------------------------------------------------------
# Sync→async bridge (same pattern as tools.py)
# ---------------------------------------------------------------------------


def _run_sync(coro: Awaitable[_T]) -> _T:
    """Run *coro* to completion from synchronous code and return its result."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        import concurrent.futures  # noqa: PLC0415

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)  # type: ignore[arg-type]
            return future.result()

    return asyncio.run(coro)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass
class MCPServer:
    """Configuration for a single external MCP server."""

    name: str
    url: str
    transport: str = "http"
    auth_token: str | None = None  # plaintext (caller decrypts before passing)


@dataclass
class MCPToolDef:
    """A single tool as advertised by an MCP server."""

    server_name: str
    tool_name: str
    namespaced_name: str          # f"{server_name}.{tool_name}"
    description: str
    input_schema: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class MCPClientError(Exception):
    """Raised internally for MCP protocol / transport errors.

    Never escapes the public boundary (list_tools_sync / call_tool_sync catch
    it and return a safe fallback).
    """


# ---------------------------------------------------------------------------
# Async internals
# ---------------------------------------------------------------------------


def _build_headers(auth_token: str | None) -> dict[str, str]:
    if auth_token:
        return {"Authorization": f"Bearer {auth_token}"}
    return {}


async def _list_tools_async(server: MCPServer, timeout: float) -> list[MCPToolDef]:
    """Async implementation of list_tools_sync."""
    from mcp.client.streamable_http import streamablehttp_client  # noqa: PLC0415
    from mcp import ClientSession  # noqa: PLC0415

    headers = _build_headers(server.auth_token)

    async with streamablehttp_client(
        server.url,
        headers=headers,
        timeout=timeout,
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()

    tools: list[MCPToolDef] = []
    for tool in result.tools:
        namespaced = f"{server.name}.{tool.name}"
        schema: dict = {}
        if hasattr(tool, "inputSchema") and tool.inputSchema is not None:
            raw = tool.inputSchema
            schema = raw if isinstance(raw, dict) else (raw.model_dump() if hasattr(raw, "model_dump") else dict(raw))
        tools.append(
            MCPToolDef(
                server_name=server.name,
                tool_name=tool.name,
                namespaced_name=namespaced,
                description=tool.description or "",
                input_schema=schema,
            )
        )
    return tools


async def _call_tool_async(
    server: MCPServer,
    tool_name: str,
    arguments: dict,
    timeout: float,
) -> dict:
    """Async implementation of call_tool_sync."""
    from mcp.client.streamable_http import streamablehttp_client  # noqa: PLC0415
    from mcp import ClientSession  # noqa: PLC0415

    headers = _build_headers(server.auth_token)

    async with streamablehttp_client(
        server.url,
        headers=headers,
        timeout=timeout,
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)

    content = []
    if hasattr(result, "content") and result.content is not None:
        for item in result.content:
            if hasattr(item, "model_dump"):
                content.append(item.model_dump())
            elif hasattr(item, "__dict__"):
                content.append(dict(item.__dict__))
            else:
                content.append(item)

    is_error = bool(getattr(result, "isError", False))
    return {"content": content, "is_error": is_error}


# ---------------------------------------------------------------------------
# Public synchronous API
# ---------------------------------------------------------------------------


def list_tools_sync(server: MCPServer, *, timeout: float = 10.0) -> list[MCPToolDef]:
    """Enumerate the tools exposed by *server*.

    Parameters
    ----------
    server:
        MCP server configuration (URL, optional auth token).
    timeout:
        Network timeout in seconds (default 10 s).

    Returns
    -------
    list[MCPToolDef]
        Possibly empty.  On any error (SSRF block, network failure, protocol
        error, unexpected exception) logs a warning and returns ``[]``.
    """
    try:
        guard_url(server.url)
    except Exception as exc:  # noqa: BLE001
        logger.warning("mcp.list_tools_sync: SSRF guard rejected %r: %s", server.url, exc)
        return []

    try:
        return _run_sync(_list_tools_async(server, timeout))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "mcp.list_tools_sync: failed to list tools from %r (%s): %s",
            server.name,
            server.url,
            exc,
        )
        return []


def call_tool_sync(
    server: MCPServer,
    tool_name: str,
    arguments: dict,
    *,
    timeout: float = 30.0,
) -> dict:
    """Invoke *tool_name* on *server* with *arguments*.

    Parameters
    ----------
    server:
        MCP server configuration.
    tool_name:
        The tool name as reported by the server (un-namespaced).
    arguments:
        JSON-serialisable arguments dict.
    timeout:
        Network timeout in seconds (default 30 s).

    Returns
    -------
    dict
        ``{"content": [...], "is_error": False}`` on success.
        ``{"error": {"message": str, "type": str}, "is_error": True}`` on failure.
        Never raises.
    """
    try:
        guard_url(server.url)
    except Exception as exc:  # noqa: BLE001
        logger.warning("mcp.call_tool_sync: SSRF guard rejected %r: %s", server.url, exc)
        return {"error": {"message": str(exc), "type": "ssrf_blocked"}, "is_error": True}

    try:
        return _run_sync(_call_tool_async(server, tool_name, arguments, timeout))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "mcp.call_tool_sync: error calling %r on %r (%s): %s",
            tool_name,
            server.name,
            server.url,
            exc,
        )
        return {
            "error": {"message": str(exc), "type": type(exc).__name__},
            "is_error": True,
        }
