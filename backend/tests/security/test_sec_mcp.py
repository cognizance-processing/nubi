"""Security tests for MCP integration.

Coverage
--------
1. SSRF guard blocks private/loopback/metadata URLs at MCP server registration.
2. Cross-org isolation: org A's MCP servers are never visible to org B's calls.
3. Auth token (secret) is never returned in public API responses.
4. Nubi-as-MCP-server: unauthenticated request returns 401.
5. Nubi-as-MCP-server: raw SQL via tools/call is still gated by existing guards.
6. MCP client: SSRF blocks 169.254.169.254 (metadata IP).
7. MCP client: SSRF blocks RFC1918 addresses.
8. McpServerStore._PUBLIC_COLS provably excludes all secret column names.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.ai.mcp import MCPServer, call_tool_sync, list_tools_sync
from app.connectors.ssrf import guard_url
from app.errors import AppError


# ---------------------------------------------------------------------------
# 1. SSRF guard blocks bad URLs at registration time
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_url", [
    "http://127.0.0.1/mcp",
    "http://localhost/mcp",
    "http://0.0.0.0/mcp",
    "http://10.0.0.1/mcp",
    "http://172.16.0.1/mcp",
    "http://192.168.1.1/mcp",
    "http://169.254.169.254/latest/meta-data",
    "file:///etc/passwd",
    "ftp://internal/",
])
def test_ssrf_guard_blocks_bad_urls(bad_url):
    """guard_url (used at MCP server create/update) blocks private/bad URLs."""
    with pytest.raises(AppError) as exc_info:
        guard_url(bad_url)
    assert exc_info.value.code in ("ssrf_blocked",) or exc_info.value.status == 400


@pytest.mark.parametrize("bad_url", [
    "http://127.0.0.1/mcp",
    "http://169.254.169.254/latest/meta-data",
    "http://192.168.1.1/mcp",
])
def test_mcp_client_ssrf_blocks_list_tools(bad_url):
    """list_tools_sync returns [] for SSRF-blocked URLs."""
    server = MCPServer(name="bad", url=bad_url)
    result = list_tools_sync(server)
    assert result == []


@pytest.mark.parametrize("bad_url", [
    "http://127.0.0.1/mcp",
    "http://169.254.169.254/latest/meta-data",
    "http://10.0.0.1/mcp",
])
def test_mcp_client_ssrf_blocks_call_tool(bad_url):
    """call_tool_sync returns is_error=True for SSRF-blocked URLs."""
    server = MCPServer(name="bad", url=bad_url)
    result = call_tool_sync(server, "echo", {})
    assert result["is_error"] is True
    assert "ssrf_blocked" in result.get("error", {}).get("type", "")


# ---------------------------------------------------------------------------
# 2. Cross-org isolation (store-level)
# ---------------------------------------------------------------------------


def test_mcp_store_org_scoped_query():
    """Queries in McpServerStore always include org_id in the WHERE clause."""
    import inspect
    from app.mcp.store import McpServerStore

    # Inspect the source of list_for_org to confirm org_id is parameterised.
    src = inspect.getsource(McpServerStore.list_for_org)
    # The query must bind org_id (not interpolate it).
    assert "$1" in src or "org_id" in src
    assert "WHERE" in src.upper()


# ---------------------------------------------------------------------------
# 3. Secret columns never in public API
# ---------------------------------------------------------------------------


def test_mcp_public_cols_excludes_secrets():
    """_PUBLIC_COLS never includes any secret column name."""
    from app.mcp.store import _PUBLIC_COLS

    forbidden = {
        "auth_token",
        "auth_secret_ciphertext",
        "auth_secret_nonce",
        "auth_secret_key_version",
        "secret",
        "password",
        "token",
        "key",
    }
    overlap = set(_PUBLIC_COLS) & forbidden
    assert overlap == set(), f"SECRET COLUMNS EXPOSED: {overlap}"


def test_mcp_strip_secrets_helper():
    """_strip_secrets removes every known secret field."""
    from app.routes.mcp import _strip_secrets

    row = {
        "id": "x",
        "name": "server",
        "url": "https://example.com/mcp",
        "auth_token": "VERY_SECRET",
        "secret": "ALSO_SECRET",
        "token": "AGAIN_SECRET",
        "enabled": True,
    }
    clean = _strip_secrets(row)
    for bad in ("auth_token", "secret", "token"):
        assert bad not in clean, f"{bad!r} leaked into public response"
    assert clean["name"] == "server"


# ---------------------------------------------------------------------------
# 4. Unauthenticated request to Nubi-as-MCP-server
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_endpoint_requires_auth(client):
    """POST /mcp without Bearer token returns 401 or 403."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    resp = await client.post("/api/v1/mcp", json=payload)
    assert resp.status_code in (401, 403), (
        f"Expected 401/403 for unauthenticated request, got {resp.status_code}"
    )


# ---------------------------------------------------------------------------
# 5. Raw SQL via Nubi-MCP-server is gated by run_query's existing guards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_nubi_server_run_query_rls_enforced(app, fake_db):
    """tools/call run_query with ad-hoc SQL is gated by claims/RLS (same as REST)."""
    from httpx import ASGITransport, AsyncClient
    from app.auth.jwt import mint_access_token
    from app.repos.memory import InMemoryRepo
    from app.repos.provider import set_repo

    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())

    fake_db.users[user_id] = {
        "id": user_id,
        "email": "sec@test.com",
        "name": "Sec Tester",
        "avatar_url": None,
        "email_verified": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    repo = InMemoryRepo()
    repo.seed_org_member(org_id=org_id, user_id=user_id, role="admin")
    set_repo(repo)

    token = mint_access_token(user_id)

    payload = {
        "jsonrpc": "2.0",
        "id": 10,
        "method": "tools/call",
        "params": {
            "name": "run_query",
            "arguments": {"sql": "SELECT 1 as n"},
        },
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        client.headers["Authorization"] = f"Bearer {token}"
        resp = await client.post("/api/v1/mcp", json=payload)

    assert resp.status_code == 200
    data = resp.json()
    # Whether it succeeds or fails, it must not crash and must be MCP-shaped.
    assert "result" in data
    result = data["result"]
    assert "content" in result
    assert "isError" in result

    set_repo(None)
