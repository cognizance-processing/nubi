"""Tests for host-supplied MCP tools in the streaming chat loop.

Covers the four required scenarios (all outbound HTTP mocked — no real network):

(a) tools advertised by a MOCKED MCP server are offered to the model, and a
    namespaced host tool_use is routed to ``mcp_client.call_tool`` with its
    result fed back into the loop;
(b) the SSRF guard REJECTS a private-IP / non-http MCP URL;
(c) a host ``system`` prompt is APPENDED to (not replacing) Nubi's base prompt;
(d) a request with none of the new fields behaves exactly as before.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Minimal fake `litellm` (mirrors the one in test_chat_stream.py) that also
# records the kwargs passed to `completion()` so we can assert on the merged
# tool list and the assembled system prompt.
# ---------------------------------------------------------------------------


class _Delta:
    def __init__(self, content=None):
        self.content = content
        self.tool_calls = None


class _Fn:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _ToolCall:
    def __init__(self, id, name, arguments):
        self.id = id
        self.type = "function"
        self.function = _Fn(name, arguments)


class _Msg:
    def __init__(self, content, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class _StreamChoice:
    def __init__(self, delta):
        self.delta = delta


class _Chunk:
    def __init__(self, content):
        self.choices = [_StreamChoice(_Delta(content=content))]


class _RebuiltChoice:
    def __init__(self, message, finish_reason):
        self.message = message
        self.finish_reason = finish_reason


class _Rebuilt:
    def __init__(self, message, finish_reason):
        self.choices = [_RebuiltChoice(message, finish_reason)]
        self.usage = None


class _FakeLiteLLM:
    def __init__(self, steps):
        self._steps = steps
        self._i = 0
        self.drop_params = False
        self.calls: list[dict[str, Any]] = []  # captured completion() kwargs

    def completion(self, **kwargs):
        self.calls.append(kwargs)
        return iter([_Chunk(t) for t in self._steps[self._i]["tokens"]])

    def stream_chunk_builder(self, chunks, messages=None):
        rebuilt = self._steps[self._i]["rebuilt"]
        self._i += 1
        return rebuilt

    def completion_cost(self, **kwargs):
        return 0.0


# ---------------------------------------------------------------------------
# (a) Host tools offered + host tool_use routed to call_tool
# ---------------------------------------------------------------------------


def test_host_mcp_tool_offered_and_routed(monkeypatch):
    from app.chat import mcp_client
    from app.chat.llm import stream_chat

    # A credential is present → real (LiteLLM) path.
    monkeypatch.setattr("app.chat.llm._resolve_anthropic_key", lambda: "sk-test")

    server = mcp_client.McpServerSpec(
        url="https://host.example/mcp", name="hostsrv", headers={"X-Auth": "tok"}
    )

    # Mock the MCP client — no real HTTP.
    async def fake_list_tools(srv):
        assert srv is server
        return [
            {
                "name": "echo",
                "description": "Echo back the text",
                "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
            }
        ]

    call_args: dict[str, Any] = {}

    async def fake_call_tool(srv, name, arguments):
        call_args["srv"] = srv
        call_args["name"] = name
        call_args["arguments"] = arguments
        return "42"

    monkeypatch.setattr(mcp_client, "list_tools", fake_list_tools)
    monkeypatch.setattr(mcp_client, "call_tool", fake_call_tool)

    steps = [
        {
            "tokens": ["Calling host tool… "],
            "rebuilt": _Rebuilt(
                _Msg(
                    "",
                    tool_calls=[_ToolCall("call_1", "mcp__hostsrv__echo", '{"text": "hi"}')],
                ),
                finish_reason="tool_calls",
            ),
        },
        {
            "tokens": ["Done."],
            "rebuilt": _Rebuilt(_Msg("Done."), finish_reason="stop"),
        },
    ]
    fake = _FakeLiteLLM(steps)
    monkeypatch.setitem(sys.modules, "litellm", fake)

    history = [{"role": "user", "content": "echo hi via the host tool"}]
    events: list[dict[str, Any]] = []
    for ev, _turn in stream_chat(history, "claude-opus-4-8", mcp_servers=[server]):
        events.append(ev)

    # The namespaced host tool was offered to the model.
    tool_names = [
        t["function"]["name"] for t in fake.calls[0]["tools"] if t.get("type") == "function"
    ]
    assert "mcp__hostsrv__echo" in tool_names
    # Built-in tools are still present (merged, not replaced).
    assert "propose_dashboard_spec" in tool_names

    # The host tool call was routed to mcp_client.call_tool with its args.
    assert call_args["name"] == "echo"  # un-namespaced original name
    assert call_args["arguments"] == {"text": "hi"}
    assert call_args["srv"] is server

    # The tool_use + tool_result events fired, and the host result was fed back.
    tool_use = next(e for e in events if e["type"] == "tool_use")
    assert tool_use["name"] == "mcp__hostsrv__echo"
    tool_result = next(e for e in events if e["type"] == "tool_result")
    assert tool_result["output"] == {"content": "42"}


# ---------------------------------------------------------------------------
# (b) SSRF guard rejects private-IP / non-http URLs
# ---------------------------------------------------------------------------


def test_ssrf_guard_rejects_non_http_scheme():
    from app.chat.mcp_client import McpClientError, _guard_url

    with pytest.raises(McpClientError) as exc:
        _guard_url("ftp://example.com/mcp")
    assert "scheme" in str(exc.value).lower()


def test_ssrf_guard_rejects_private_ip():
    from app.chat.mcp_client import McpClientError, _guard_url

    for url in (
        "http://127.0.0.1/mcp",
        "http://192.168.1.10/mcp",
        "http://169.254.169.254/latest/meta-data",
    ):
        with pytest.raises(McpClientError):
            _guard_url(url)


def test_resolve_pin_returns_ip_literal_and_preserves_host(monkeypatch):
    """The outbound request pins to a validated IP literal (DNS-rebind defence)
    while preserving the original hostname for Host + TLS SNI."""
    import ipaddress

    from app.chat import mcp_client

    monkeypatch.setattr(
        mcp_client, "_resolve_addresses", lambda h: [ipaddress.ip_address("93.184.216.34")]
    )
    pinned_url, host_header, sni_host = mcp_client._resolve_pin(
        "https://host.example/agent/mcp"
    )
    assert pinned_url == "https://93.184.216.34:443/agent/mcp"  # connects to the IP
    assert host_header == "host.example"  # original host preserved
    assert sni_host == "host.example"


def test_resolve_pin_blocks_rebind_to_private_ip(monkeypatch):
    """Even if a name passed an earlier guard, _resolve_pin re-validates the
    resolved address at request time and refuses a private target."""
    import ipaddress

    from app.chat import mcp_client
    from app.chat.mcp_client import McpClientError

    monkeypatch.setattr(
        mcp_client, "_resolve_addresses", lambda h: [ipaddress.ip_address("10.0.0.5")]
    )
    with pytest.raises(McpClientError):
        mcp_client._resolve_pin("https://host.example/agent/mcp")


def test_list_tools_raises_on_ssrf_block():
    from app.chat.mcp_client import McpClientError, McpServerSpec, list_tools

    server = McpServerSpec(url="http://127.0.0.1/mcp")
    with pytest.raises(McpClientError):
        asyncio.run(list_tools(server))


def test_call_tool_swallows_ssrf_block_into_error_string():
    """call_tool must never raise — an SSRF block becomes a tool error string."""
    from app.chat.mcp_client import McpServerSpec, call_tool

    server = McpServerSpec(url="http://10.0.0.5/mcp")
    result = asyncio.run(call_tool(server, "echo", {"text": "hi"}))
    assert isinstance(result, str)
    assert "MCP error" in result
    assert "ssrf_blocked" in result


def test_build_mcp_tools_skips_failing_server(monkeypatch):
    """A server that fails to list tools contributes nothing (no crash)."""
    from app.chat import llm, mcp_client

    server = mcp_client.McpServerSpec(url="http://127.0.0.1/mcp")

    async def boom(srv):
        raise mcp_client.McpClientError("ssrf_blocked")

    monkeypatch.setattr(mcp_client, "list_tools", boom)
    tools, routing = llm._build_mcp_tools([server])
    assert tools == []
    assert routing == {}


# ---------------------------------------------------------------------------
# (c) Host system prompt is APPENDED to the base prompt
# ---------------------------------------------------------------------------


def test_host_system_prompt_is_appended(monkeypatch):
    from app.chat.llm import _SYSTEM_PROMPT, stream_chat

    monkeypatch.setattr("app.chat.llm._resolve_anthropic_key", lambda: "sk-test")

    steps = [{"tokens": ["Hi."], "rebuilt": _Rebuilt(_Msg("Hi."), finish_reason="stop")}]
    fake = _FakeLiteLLM(steps)
    monkeypatch.setitem(sys.modules, "litellm", fake)

    host_ctx = "HOST_CONTEXT_XYZ: you are embedded in Acme."
    history = [{"role": "user", "content": "hello"}]
    for _ev, _turn in stream_chat(history, "claude-opus-4-8", system=host_ctx):
        pass

    messages = fake.calls[0]["messages"]
    system_msg = messages[0]
    assert system_msg["role"] == "system"
    # Base prompt preserved AND host context appended (not replacing it).
    assert system_msg["content"].startswith(_SYSTEM_PROMPT)
    assert host_ctx in system_msg["content"]


# ---------------------------------------------------------------------------
# (d) Absent new fields → unchanged behaviour
# ---------------------------------------------------------------------------


def test_request_model_defaults_backward_compatible():
    from app.routes.chat import ChatStreamRequest

    req = ChatStreamRequest(model="claude-opus-4-8", message="hi")
    assert req.system is None
    assert req.mcp_servers is None
    assert req.mcp_tools_url is None
    assert req.resolved_mcp_servers() is None


def test_mcp_tools_url_normalised_into_servers():
    from app.routes.chat import ChatStreamRequest

    req = ChatStreamRequest(
        model="claude-opus-4-8", message="hi", mcp_tools_url="https://host.example/mcp"
    )
    servers = req.resolved_mcp_servers()
    assert servers is not None and len(servers) == 1
    assert servers[0].url == "https://host.example/mcp"


def test_absent_fields_offline_stream_unchanged(monkeypatch):
    """No system / mcp_servers → identical to the pre-existing offline path."""
    monkeypatch.setattr("app.chat.llm._resolve_anthropic_key", lambda: None)

    from app.chat.llm import stream_chat

    history = [{"role": "user", "content": "build a dashboard of revenue by month"}]
    events = [ev for ev, _ in stream_chat(history, "claude-opus-4-8")]
    types = [e["type"] for e in events]
    assert "tool_use" in types
    assert "tool_result" in types
    assert "token" in types
    tool_use = next(e for e in events if e["type"] == "tool_use")
    assert tool_use["name"] == "propose_dashboard_spec"
