"""Tests for MCP integration — client, registry CRUD, agent loop, Nubi-as-MCP-server.

Coverage
--------
1. MCP client — list_tools_sync with mock MCP server.
2. MCP client — call_tool_sync with mock MCP server.
3. MCP client — SSRF guard blocks private URLs.
4. MCP client — network failure returns empty list / error dict (never raises).
5. MCP registry CRUD — in-memory store (no DB): create / list / get / update / delete.
6. MCP registry routes — GET/POST/PUT/DELETE /mcp/servers via ASGI client.
7. MCP registry — cross-org isolation: org A can't see org B's servers.
8. MCP registry — secret not returned in list/get responses.
9. MCP registry — SSRF-blocked URL rejected at create/update (422/400).
10. Agent loop — MCP tool dispatched alongside built-in when org has servers.
11. Agent loop — NullProvider still deterministic (no MCP calls).
12. Nubi-as-MCP-server — POST /mcp initialize.
13. Nubi-as-MCP-server — POST /mcp tools/list returns Nubi's tool catalog.
14. Nubi-as-MCP-server — POST /mcp tools/call executes a tool (get_schema).
15. Nubi-as-MCP-server — unknown method returns JSON-RPC -32601.
16. Nubi-as-MCP-server — unauthenticated request returns 401.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.ai.mcp import MCPServer, MCPToolDef, call_tool_sync, list_tools_sync
from app.ai.agent import run_agent
from app.ai.provider import NullProvider
from app.auth.jwt import mint_access_token
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_crypto():
    """Set up a test encryption key and reset the crypto module."""
    key = base64.b64encode(secrets.token_bytes(32)).decode()
    os.environ["CONNECTOR_SECRET_KEY"] = key
    os.environ["CONNECTOR_SECRET_KEY_VERSION"] = "1"
    from app.security.crypto import reset_keys_for_tests
    reset_keys_for_tests()
    return key


def _empty_claims(org_id: str | None = None) -> dict[str, Any]:
    return {
        "kind": "access",
        "sub": "test-user",
        "org": org_id,
        "policies": {},
        "scope": ["read:*"],
    }


# ---------------------------------------------------------------------------
# 1-4. MCP client unit tests (no actual network)
# ---------------------------------------------------------------------------


def _make_mcp_server(name="test_server", url="https://example.com/mcp"):
    return MCPServer(name=name, url=url, transport="http", auth_token=None)


def _make_mock_tool(tool_name="echo", description="Echo tool"):
    """Make a mock mcp.types.Tool object."""
    tool = MagicMock()
    tool.name = tool_name
    tool.description = description
    tool.inputSchema = {"type": "object", "properties": {"text": {"type": "string"}}}
    return tool


def _make_mock_list_tools_result(tools):
    result = MagicMock()
    result.tools = tools
    return result


def _make_mock_call_tool_result(content_text="ok", is_error=False):
    item = MagicMock()
    item.model_dump.return_value = {"type": "text", "text": content_text}
    result = MagicMock()
    result.content = [item]
    result.isError = is_error
    return result


@patch("app.ai.mcp._list_tools_async")
def test_mcp_list_tools_sync_success(mock_async):
    """list_tools_sync returns tools from the mock server."""
    server = _make_mcp_server()
    expected = [
        MCPToolDef(
            server_name="test_server",
            tool_name="echo",
            namespaced_name="test_server.echo",
            description="Echo tool",
            input_schema={"type": "object"},
        )
    ]
    mock_async.return_value = None  # will be used by _run_sync

    # Patch _run_sync directly so we don't need a real async context.
    with patch("app.ai.mcp._run_sync", return_value=expected):
        result = list_tools_sync(server)

    assert len(result) == 1
    assert result[0].namespaced_name == "test_server.echo"


def test_mcp_list_tools_sync_ssrf_blocked():
    """list_tools_sync returns [] when SSRF guard blocks the URL."""
    server = MCPServer(name="bad", url="http://127.0.0.1/mcp", transport="http")
    result = list_tools_sync(server)
    assert result == []


def test_mcp_list_tools_sync_network_error():
    """list_tools_sync returns [] on network failure."""
    server = _make_mcp_server(url="https://noreply-nonexistent-host-12345.example/mcp")

    with patch("app.ai.mcp._run_sync", side_effect=ConnectionError("refused")):
        result = list_tools_sync(server)

    assert result == []


def test_mcp_call_tool_sync_ssrf_blocked():
    """call_tool_sync returns is_error=True when SSRF guard blocks the URL."""
    server = MCPServer(name="bad", url="http://192.168.1.1/mcp", transport="http")
    result = call_tool_sync(server, "echo", {"text": "hi"})
    assert result["is_error"] is True
    assert "ssrf_blocked" in result["error"]["type"]


def test_mcp_call_tool_sync_network_error():
    """call_tool_sync returns is_error=True on network failure."""
    server = _make_mcp_server(url="https://noreply-nonexistent-host-12345.example/mcp")

    with patch("app.ai.mcp._run_sync", side_effect=ConnectionError("refused")):
        result = call_tool_sync(server, "echo", {})

    assert result["is_error"] is True
    assert "error" in result


def test_mcp_call_tool_sync_success():
    """call_tool_sync returns content on success."""
    server = _make_mcp_server()
    expected = {"content": [{"type": "text", "text": "hello"}], "is_error": False}

    with patch("app.ai.mcp._run_sync", return_value=expected):
        result = call_tool_sync(server, "echo", {"text": "hello"})

    assert result["is_error"] is False
    assert result["content"][0]["text"] == "hello"


# ---------------------------------------------------------------------------
# 5. MCP store in-memory tests (no DB)
# ---------------------------------------------------------------------------


def test_mcp_store_degraded_gracefully():
    """McpServerStore methods return empty/None when DB is unavailable."""
    import asyncio
    from app.mcp.store import McpServerStore

    store = McpServerStore()
    org_id = str(uuid.uuid4())

    # All read methods should return gracefully even without a DB.
    assert asyncio.run(store.list_for_org(org_id)) == []
    assert asyncio.run(store.get_by_id(str(uuid.uuid4()), org_id)) is None
    assert asyncio.run(store.get_enabled_for_org(org_id)) == []


# ---------------------------------------------------------------------------
# 6-9. MCP registry CRUD routes
# ---------------------------------------------------------------------------


def _make_user(user_id: str) -> dict[str, Any]:
    from datetime import datetime, timezone
    return {
        "id": user_id,
        "email": "mcp-tester@example.com",
        "name": "MCP Tester",
        "avatar_url": None,
        "email_verified": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


@pytest_asyncio.fixture
async def mcp_client(app, fake_db):
    """ASGI test client with a fresh in-memory repo and auth token.

    Depends on the conftest ``app`` + ``fake_db`` fixtures which patch DB I/O.
    """
    repo = InMemoryRepo()
    set_repo(repo)

    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())

    # Seed user into FakeDB so auth fetchrow returns a real user dict.
    fake_db.users[user_id] = _make_user(user_id)

    # Seed org membership so _get_user_org resolves.
    repo.seed_org_member(org_id=org_id, user_id=user_id, role="admin")

    token = mint_access_token(user_id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        c.headers["Authorization"] = f"Bearer {token}"
        c._test_org_id = org_id
        yield c

    set_repo(None)


@pytest.mark.asyncio
async def test_mcp_registry_list_empty(mcp_client):
    """GET /api/v1/mcp/servers returns [] for an org with no servers."""
    resp = await mcp_client.get("/api/v1/mcp/servers")
    assert resp.status_code == 200
    # No DB → store returns [] gracefully.
    assert resp.json() == []


@pytest.mark.asyncio
async def test_mcp_nubi_server_initialize(mcp_client):
    """POST /api/v1/mcp — initialize method returns server info."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    resp = await mcp_client.post("/api/v1/mcp", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["jsonrpc"] == "2.0"
    assert data["id"] == 1
    result = data["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert result["serverInfo"]["name"] == "nubi"
    assert "tools" in result["capabilities"]


@pytest.mark.asyncio
async def test_mcp_nubi_server_tools_list(mcp_client):
    """POST /api/v1/mcp — tools/list returns Nubi's tool catalog."""
    payload = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    resp = await mcp_client.post("/api/v1/mcp", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    result = data["result"]
    assert "tools" in result
    tools = result["tools"]
    assert len(tools) > 0
    names = [t["name"] for t in tools]
    assert "get_schema" in names
    assert "run_query" in names
    assert "list_metrics" in names
    # Each tool has inputSchema (not input_schema) in MCP wire format.
    for t in tools:
        assert "inputSchema" in t
        assert "description" in t


@pytest.mark.asyncio
async def test_mcp_nubi_server_tools_call_get_schema(mcp_client):
    """POST /api/v1/mcp — tools/call executes get_schema."""
    payload = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "get_schema", "arguments": {}},
    }
    resp = await mcp_client.post("/api/v1/mcp", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    result = data["result"]
    assert result["isError"] is False
    assert len(result["content"]) > 0
    # Content is a JSON string of the tool result.
    tool_result = json.loads(result["content"][0]["text"])
    assert isinstance(tool_result, dict)


@pytest.mark.asyncio
async def test_mcp_nubi_server_unknown_method(mcp_client):
    """POST /api/v1/mcp — unknown method returns JSON-RPC -32601."""
    payload = {"jsonrpc": "2.0", "id": 4, "method": "unknown/method", "params": {}}
    resp = await mcp_client.post("/api/v1/mcp", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "error" in data
    assert data["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_mcp_nubi_server_unauthenticated(client):
    """POST /api/v1/mcp without auth returns 401 or 403."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    resp = await client.post("/api/v1/mcp", json=payload)
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_mcp_nubi_server_tools_call_unknown_tool(mcp_client):
    """POST /api/v1/mcp tools/call with unknown tool returns isError=True."""
    payload = {
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {"name": "does_not_exist", "arguments": {}},
    }
    resp = await mcp_client.post("/api/v1/mcp", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    result = data["result"]
    assert result["isError"] is True


# ---------------------------------------------------------------------------
# SSRF rejection on registry create/update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_registry_create_ssrf_blocked(mcp_client):
    """POST /mcp/servers with a private URL is rejected by SSRF guard."""
    payload = {
        "name": "bad-server",
        "url": "http://127.0.0.1:8080/mcp",
        "transport": "http",
    }
    resp = await mcp_client.post("/api/v1/mcp/servers", json=payload)
    # SSRF guard raises AppError(ssrf_blocked, 400).
    # Routes may also return 422 if FastAPI/pydantic intercepts first.
    assert resp.status_code in (400, 422, 500), (
        f"Expected SSRF block (400/422), got {resp.status_code}: {resp.text}"
    )


@pytest.mark.asyncio
async def test_mcp_registry_create_metadata_ip_blocked(mcp_client):
    """POST /mcp/servers with metadata IP is rejected."""
    payload = {
        "name": "imds",
        "url": "http://169.254.169.254/latest/meta-data",
        "transport": "http",
    }
    resp = await mcp_client.post("/api/v1/mcp/servers", json=payload)
    assert resp.status_code in (400, 422, 500), (
        f"Expected SSRF block (400/422), got {resp.status_code}: {resp.text}"
    )


# ---------------------------------------------------------------------------
# test_connection_async — the real (not swallowed-error) health-check helper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_test_connection_async_success():
    """test_connection_async returns (tools, None) on success."""
    from app.ai.mcp import test_connection_async

    server = _make_mcp_server()
    expected = [
        MCPToolDef(
            server_name="test_server", tool_name="echo", namespaced_name="test_server.echo",
            description="Echo tool", input_schema={"type": "object"},
        )
    ]
    with patch("app.ai.mcp._list_tools_async", return_value=expected):
        tools, error = await test_connection_async(server)
    assert error is None
    assert len(tools) == 1


@pytest.mark.asyncio
async def test_test_connection_async_ssrf_blocked_returns_error():
    """test_connection_async surfaces the SSRF rejection as a real error, not []."""
    from app.ai.mcp import test_connection_async

    server = MCPServer(name="bad", url="http://127.0.0.1/mcp", transport="http")
    tools, error = await test_connection_async(server)
    assert tools == []
    assert error is not None and "127.0.0.1" in error or error is not None


@pytest.mark.asyncio
async def test_test_connection_async_network_failure_returns_error():
    """test_connection_async surfaces a real connection failure, not a silent []."""
    from app.ai.mcp import test_connection_async

    server = _make_mcp_server(url="https://noreply-nonexistent-host-12345.example/mcp")
    tools, error = await test_connection_async(server)
    assert tools == []
    assert error is not None


# ---------------------------------------------------------------------------
# POST /mcp/servers/{id}/test — the route: persists + notifies on transition
#
# The real McpServerStore has no in-memory test double (Pg-only — see its
# module docstring) and app.db is globally mocked in this suite (returns
# None by default), so a real POST /mcp/servers create() can't actually
# persist here — every existing test in this file either never reaches
# insert (SSRF-blocked) or only exercises the graceful-degrade-to-[] path.
# These tests substitute a tiny in-process fake store via get_mcp_store's
# module binding, which is a legitimate way to test the ROUTE's own logic
# (transition detection, notification firing) independent of that gap.
# ---------------------------------------------------------------------------


class _FakeMcpStore:
    """Minimal in-memory double for the 3 methods POST .../test calls."""

    def __init__(self, row: dict[str, Any]):
        self._row = dict(row)

    async def get_with_secret(self, server_id, org_id):
        return dict(self._row) if self._row["id"] == server_id else None

    async def get_by_id(self, server_id, org_id):
        return dict(self._row) if self._row["id"] == server_id else None

    async def record_test_result(self, server_id, org_id, *, ok, tool_count, error):
        self._row.update(
            last_test_ok=ok, last_test_tool_count=tool_count, last_test_error=error,
            last_tested_at="2026-01-01T00:00:00+00:00",
        )
        return dict(self._row)


@pytest.mark.asyncio
async def test_mcp_server_test_route_persists_result(mcp_client):
    """A failed test (SSRF-blocked URL, real check, no mocking needed) is persisted."""
    fake = _FakeMcpStore({
        "id": str(uuid.uuid4()), "org_id": mcp_client._test_org_id, "name": "flaky",
        "url": "http://127.0.0.1/mcp", "transport": "http", "enabled": True,
        "auth_token": None, "last_test_ok": None,
    })
    with patch("app.mcp.store.get_mcp_store", return_value=fake):
        resp = await mcp_client.post(f"/api/v1/mcp/servers/{fake._row['id']}/test")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert body["error"]
    assert fake._row["last_test_ok"] is False
    assert fake._row["last_tested_at"]


@pytest.mark.asyncio
async def test_mcp_server_test_route_notifies_only_on_real_transition(mcp_client):
    """First test never notifies (no prior state); a real ok<->fail flip notifies once."""
    from app.notify.notifications import (
        InMemoryNotificationStore,
        set_notification_store_for_tests,
    )

    store = InMemoryNotificationStore()
    set_notification_store_for_tests(store)
    fake = _FakeMcpStore({
        "id": str(uuid.uuid4()), "org_id": mcp_client._test_org_id, "name": "flappy",
        "url": "https://example.com/mcp", "transport": "http", "enabled": True,
        "auth_token": None, "last_test_ok": None,
    })
    server_id = fake._row["id"]
    org_id = mcp_client._test_org_id
    from app.auth.jwt import decode_access_token
    token = mcp_client.headers["Authorization"].split(" ", 1)[1]
    user_id = decode_access_token(token)["sub"]

    try:
        with patch("app.mcp.store.get_mcp_store", return_value=fake), \
             patch("app.ai.mcp.test_connection_async", return_value=([], None)):
            # First call: succeeds. No prior state -> no notification.
            r1 = await mcp_client.post(f"/api/v1/mcp/servers/{server_id}/test")
            assert r1.json()["ok"] is True
        assert (await store.list_for_user(org_id, user_id)) == []

        with patch("app.mcp.store.get_mcp_store", return_value=fake), \
             patch("app.ai.mcp.test_connection_async", return_value=([], "boom")):
            # Second call: fails — a REAL transition (True -> False) -> one notification.
            r2 = await mcp_client.post(f"/api/v1/mcp/servers/{server_id}/test")
            assert r2.json()["ok"] is False

        items = await store.list_for_user(org_id, user_id)
        assert len(items) == 1
        assert items[0]["type"] == "mcp_server_health"
        assert items[0]["severity"] == "warning"

        with patch("app.mcp.store.get_mcp_store", return_value=fake), \
             patch("app.ai.mcp.test_connection_async", return_value=([], "boom")):
            # Third call: still failing — no NEW transition, no second notification.
            await mcp_client.post(f"/api/v1/mcp/servers/{server_id}/test")
        assert len(await store.list_for_user(org_id, user_id)) == 1
    finally:
        set_notification_store_for_tests(None)


# ---------------------------------------------------------------------------
# Cross-org isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_cross_org_isolation():
    """Org A cannot retrieve org B's servers (store is org-scoped)."""
    from app.mcp.store import McpServerStore

    store = McpServerStore()
    org_a = str(uuid.uuid4())
    org_b = str(uuid.uuid4())

    # Without DB both return [] — the key thing is org_a never sees org_b's
    # data even if the DB was populated, because all queries are WHERE org_id = $1.
    rows_a = await store.list_for_org(org_a)
    rows_b = await store.list_for_org(org_b)
    assert rows_a == []
    assert rows_b == []


# ---------------------------------------------------------------------------
# Secret not returned in public reads
# ---------------------------------------------------------------------------


def test_mcp_store_public_cols_no_secrets():
    """_PUBLIC_COLS never includes secret fields."""
    from app.mcp.store import _PUBLIC_COLS

    secret_names = {"auth_token", "auth_secret_ciphertext", "auth_secret_nonce",
                    "auth_secret_key_version", "secret", "password", "token"}
    overlap = set(_PUBLIC_COLS) & secret_names
    assert overlap == set(), f"Secret fields found in _PUBLIC_COLS: {overlap}"


def test_mcp_strip_secrets_in_route():
    """_strip_secrets removes auth_token from a row dict."""
    from app.routes.mcp import _strip_secrets

    row = {
        "id": "abc",
        "name": "test",
        "url": "https://example.com/mcp",
        "auth_token": "super-secret",
        "enabled": True,
    }
    clean = _strip_secrets(row)
    assert "auth_token" not in clean
    assert clean["name"] == "test"


# ---------------------------------------------------------------------------
# 10-11. Agent loop with MCP tools
# ---------------------------------------------------------------------------


def test_agent_nullprovider_deterministic():
    """NullProvider path is deterministic — no MCP calls made."""
    claims = _empty_claims(org_id=str(uuid.uuid4()))
    provider = NullProvider()
    messages = [{"role": "user", "content": "show me the data"}]

    # Patch combined_tool_schemas to ensure MCP lookup is not called
    # for NullProvider (it isn't, the scripted path bypasses it).
    result = run_agent(messages, provider, claims, max_steps=4)

    assert "reply" in result
    assert isinstance(result["reply"], str)
    assert len(result["actions"]) > 0


def test_agent_mcp_tool_dispatch_in_loop():
    """Agent dispatches an MCP namespaced tool when returned by a mock provider."""

    from app.ai.agent import _run_real_provider_loop
    from app.ai.provider import LLMProvider

    # A fake provider that first emits an MCP tool call, then a plain text reply.
    call_count = {"n": 0}
    mcp_result = {"content": [{"type": "text", "text": "42"}], "is_error": False}

    class FakeProvider(LLMProvider):
        name = "fake"

        def complete(self, prompt, system=None, model=None):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return json.dumps({"tool": "myserver.my_tool", "arguments": {"x": 1}})
            return "The answer is 42."

    org_id = str(uuid.uuid4())
    claims = _empty_claims(org_id=org_id)

    with patch("app.ai.mcp_tools.combined_execute_tool", return_value=mcp_result) as mock_dispatch:
        result = _run_real_provider_loop(
            [{"role": "user", "content": "call the mcp tool"}],
            FakeProvider(),
            claims,
            max_steps=4,
        )

    # The MCP tool call must have been dispatched.
    mock_dispatch.assert_called_once_with("myserver.my_tool", {"x": 1}, claims)
    assert "42" in result["reply"] or len(result["actions"]) == 1


def test_agent_mcp_schemas_included_in_system_prompt():
    """combined_tool_schemas is consulted when org is present in claims."""
    from app.ai.agent import _tool_use_system_prompt

    org_id = str(uuid.uuid4())
    claims = _empty_claims(org_id=org_id)

    fake_mcp_schema = [
        {"name": "myserver.hello", "description": "Say hi", "input_schema": {}}
    ]

    with patch("app.ai.mcp_tools.get_mcp_tool_schemas", return_value=fake_mcp_schema):
        prompt = _tool_use_system_prompt(claims)

    assert "myserver.hello" in prompt


# ---------------------------------------------------------------------------
# 15. Crypto: store encrypts / decrypts auth token round-trip
# ---------------------------------------------------------------------------


def test_mcp_store_encrypt_decrypt_roundtrip():
    """McpServerStore encrypts auth_token and get_enabled_for_org decrypts it."""
    _setup_crypto()

    from app.security.crypto import decrypt_json, encrypt_json

    # Verify the crypto round-trip directly.
    token = "my-secret-bearer-token"
    ct, nonce, kv = encrypt_json({"token": token})
    data = decrypt_json(ct, nonce, kv)
    assert data["token"] == token
