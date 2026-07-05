"""HTTP MCP client — Nubi as an MCP *client* of the embedding application.

The streaming chat loop (``app.chat.llm``) can be handed a list of
:class:`McpServerSpec` describing MCP servers hosted by the embedding
application.  Nubi discovers the tools each server advertises and, when the
model chooses one, calls it — feeding the result back into the agentic loop
alongside Nubi's own built-in tools.

This is Nubi acting as an MCP **client** making OUTBOUND HTTP calls to a
HOST-SUPPLIED URL.  Do NOT confuse it with ``app/routes/mcp.py`` where Nubi
*exposes* its own tools as an MCP server.

Wire protocol
-------------
Standard MCP JSON-RPC 2.0 over a single HTTP ``POST`` to the server URL:

* ``tools/list`` → ``{"jsonrpc":"2.0","id":1,"method":"tools/list"}`` and the
  response ``result.tools`` is a list of ``{name, description, inputSchema}``.
* ``tools/call`` → ``method:"tools/call"`` with
  ``params:{name, arguments}``; the response ``result.content`` blocks are
  flattened to a single string.

Security (SSRF)
---------------
Nubi fetches a URL the *host* controls, so every guard in
``_guard_url`` is mandatory:

* scheme must be ``http`` / ``https`` (anything else is rejected);
* the host is resolved and EVERY resolved address is checked with
  :mod:`ipaddress`; private / loopback / link-local / reserved / cloud-metadata
  targets are rejected UNLESS ``NUBI_MCP_ALLOW_PRIVATE_HOSTS`` is set (and
  link-local + cloud-metadata are blocked even then);
* the outbound request uses a hard ~10 s timeout, caps the response body at
  ~1 MB, and does NOT follow redirects (a 3xx to a private IP cannot bypass the
  check);
* host-supplied ``headers`` are passed verbatim (that is how the host
  authenticates its own callback).

Neither :func:`list_tools` nor :func:`call_tool` is allowed to crash the chat
stream: :func:`call_tool` turns every failure into an error string fed back to
the model, and the loop wrapping :func:`list_tools` swallows its errors.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel

from app.connectors.ssrf import _is_forbidden, _resolve_addresses

# Hard outbound timeout (seconds) for a single MCP JSON-RPC call.
_TIMEOUT_S = 10.0
# Maximum response body we will read from a host MCP server (~1 MB).
_MAX_BODY_BYTES = 1_000_000


class McpServerSpec(BaseModel):
    """A single host-supplied MCP server the chat loop may call.

    ``url``     — the MCP endpoint (http/https) Nubi POSTs JSON-RPC to.
    ``headers`` — optional headers sent verbatim (host authenticates its
                  own callback here, e.g. ``{"Authorization": "Bearer …"}``).
    ``name``    — optional friendly label used when namespacing the tools.
    """

    url: str
    headers: dict[str, str] | None = None
    name: str | None = None


class McpClientError(Exception):
    """Raised for SSRF blocks and MCP transport/protocol failures.

    Callers at the chat-loop boundary catch this so an MCP failure can never
    crash the stream.
    """


def _allow_private_hosts() -> bool:
    """Return True if the private-host SSRF escape hatch is enabled in config."""
    try:
        from app.config import get_settings  # noqa: PLC0415

        return bool(get_settings().NUBI_MCP_ALLOW_PRIVATE_HOSTS)
    except Exception:  # noqa: BLE001 — never let config lookup break the guard
        return False


def _guard_url(url: str) -> None:
    """Validate *url* is safe to fetch, else raise :class:`McpClientError`.

    Rejects non-http(s) schemes, host-less URLs, hosts that do not resolve, and
    any host that resolves to a forbidden address (private / loopback /
    link-local / reserved / cloud-metadata) unless
    ``NUBI_MCP_ALLOW_PRIVATE_HOSTS`` relaxes the private ranges.
    """
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise McpClientError(
            f"ssrf_blocked: URL scheme {scheme or '(none)'!r} is not allowed; "
            "only http(s) MCP URLs may be called."
        )

    host = parts.hostname
    if not host:
        raise McpClientError("ssrf_blocked: MCP URL has no host component.")

    allow_private = _allow_private_hosts()
    addrs = _resolve_addresses(host)
    if not addrs:
        # Fail closed: no address to validate means we cannot prove the target
        # is safe, so we refuse rather than let the HTTP client re-resolve it.
        raise McpClientError(
            f"ssrf_blocked: MCP host {host!r} does not resolve to any address."
        )

    for ip in addrs:
        if _is_forbidden(ip, allow_private=allow_private):
            raise McpClientError(
                f"ssrf_blocked: refusing to call MCP host {host!r}: it resolves "
                f"to a blocked address ({ip}). Internal, loopback, link-local, "
                "and cloud-metadata targets are not permitted."
            )


async def _http_post_json(
    url: str, headers: dict[str, str], payload: dict[str, Any]
) -> dict[str, Any]:
    """POST *payload* as JSON to *url* and return the parsed JSON-RPC envelope.

    Enforces the outbound safety limits: a hard timeout, a capped response body,
    and NO redirect following.  Raises :class:`McpClientError` on a non-2xx
    status, an oversized body, or a malformed JSON response.
    """
    import httpx  # noqa: PLC0415

    async with httpx.AsyncClient(timeout=_TIMEOUT_S, follow_redirects=False) as client:
        async with client.stream(
            "POST", url, json=payload, headers=headers or {}
        ) as resp:
            if not (200 <= resp.status_code < 300):
                raise McpClientError(
                    f"MCP server returned HTTP {resp.status_code}."
                )
            total = 0
            chunks: list[bytes] = []
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if total > _MAX_BODY_BYTES:
                    raise McpClientError(
                        "MCP response body exceeded the 1 MB limit."
                    )
                chunks.append(chunk)

    raw = b"".join(chunks)
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise McpClientError(f"malformed JSON-RPC response: {exc}") from exc
    if not isinstance(data, dict):
        raise McpClientError("JSON-RPC response was not a JSON object.")
    return data


def _unwrap_result(data: dict[str, Any]) -> dict[str, Any]:
    """Return the JSON-RPC ``result`` object, raising on a JSON-RPC error."""
    err = data.get("error")
    if err:
        msg = err.get("message") if isinstance(err, dict) else str(err)
        code = err.get("code") if isinstance(err, dict) else None
        raise McpClientError(f"JSON-RPC error {code}: {msg}")
    result = data.get("result")
    if not isinstance(result, dict):
        raise McpClientError("JSON-RPC response missing a result object.")
    return result


def _content_to_str(content: Any) -> str:
    """Flatten an MCP ``result.content`` payload to a single string."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                # Standard MCP text blocks: {"type":"text","text":"…"}.
                text = item.get("text")
                parts.append(text if isinstance(text, str) else json.dumps(item))
            else:
                parts.append(str(item))
        return "\n".join(p for p in parts if p)
    return json.dumps(content)


async def list_tools(server: McpServerSpec) -> list[dict]:
    """Return the tools advertised by *server* as ``{name, description, inputSchema}``.

    Raises :class:`McpClientError` on an SSRF block or any transport/protocol
    failure — the caller (the chat loop) is responsible for catching this so a
    bad server merely contributes no tools rather than crashing the turn.
    """
    _guard_url(server.url)
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    data = await _http_post_json(server.url, server.headers or {}, payload)
    result = _unwrap_result(data)

    tools: list[dict] = []
    for t in result.get("tools") or []:
        if not isinstance(t, dict) or not t.get("name"):
            continue
        tools.append(
            {
                "name": str(t["name"]),
                "description": str(t.get("description") or ""),
                "inputSchema": t.get("inputSchema")
                if isinstance(t.get("inputSchema"), dict)
                else {"type": "object"},
            }
        )
    return tools


async def call_tool(
    server: McpServerSpec, name: str, arguments: dict[str, Any]
) -> str:
    """Call *name* on *server* with *arguments*; return the result as a string.

    NEVER raises: every failure (SSRF block, network error, non-2xx, JSON-RPC
    error, oversized/malformed body) is caught and returned as an ``[MCP error …]``
    string so it can be fed back to the model as the tool result without
    crashing the chat stream.
    """
    try:
        _guard_url(server.url)
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        }
        data = await _http_post_json(server.url, server.headers or {}, payload)
        result = _unwrap_result(data)
        # A tool-level error is signalled via result.isError (content carries msg).
        text = _content_to_str(result.get("content"))
        if result.get("isError"):
            return f"[MCP tool error] {text or 'the host tool reported an error.'}"
        return text
    except McpClientError as exc:
        return f"[MCP error] {exc}"
    except Exception as exc:  # noqa: BLE001 — a host tool must never crash the stream
        return f"[MCP error] {type(exc).__name__}: {exc}"


__all__ = ["McpServerSpec", "McpClientError", "list_tools", "call_tool"]
