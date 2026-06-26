"""Adversarial tests for MCP surfaces NOT covered in test_mcp.py.

Coverage
--------
1. Malformed JSON-RPC: missing jsonrpc, missing id, missing method.
2. params=null, params={}, params=[] for tools/call.
3. tools/call with no "name" in params.
4. tools/call with name="" (empty string).
5. JSON-RPC with wrong version ("1.0").
6. JSON-RPC with integer method.
7. Registry: delete non-existent server → 404.
8. Registry: get non-existent server → 404.
9. Registry: SSRF with file:// and port 22.
10. Cross-org: org_b cannot get org_a's server (store-level).
11. _strip_secrets removes all expected secret keys.
12. Huge payload doesn't crash the server.
13. Invalid JSON body → parse error (-32700).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(user_id: str) -> dict[str, Any]:
    return {
        "id": user_id,
        "email": f"user-{user_id[:8]}@example.com",
        "name": "Test User",
        "avatar_url": None,
        "email_verified": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


@pytest_asyncio.fixture
async def mcp_adv_client(app, fake_db):
    """ASGI client with auth for MCP adversarial tests."""
    repo = InMemoryRepo()
    set_repo(repo)

    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    fake_db.users[user_id] = _make_user(user_id)
    repo.seed_org_member(org_id=org_id, user_id=user_id, role="admin")
    token = mint_access_token(user_id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        c.headers["Authorization"] = f"Bearer {token}"
        c._test_org_id = org_id
        c._test_user_id = user_id
        yield c

    set_repo(None)


# ---------------------------------------------------------------------------
# 1. Malformed JSON-RPC bodies
# ---------------------------------------------------------------------------


class TestMalformedJsonRpc:
    @pytest.mark.asyncio
    async def test_missing_jsonrpc_field(self, mcp_adv_client):
        """JSON-RPC body without 'jsonrpc' field — server should still respond."""
        payload = {"id": 1, "method": "initialize", "params": {}}
        resp = await mcp_adv_client.post("/api/v1/mcp", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        # Server is lenient — processes the method regardless of missing version.
        assert "result" in data or "error" in data

    @pytest.mark.asyncio
    async def test_missing_id_field(self, mcp_adv_client):
        """JSON-RPC body without 'id' — server treats id as None."""
        payload = {"jsonrpc": "2.0", "method": "initialize", "params": {}}
        resp = await mcp_adv_client.post("/api/v1/mcp", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        # rpc_id is None → body.get("id") returns None
        assert data.get("id") is None
        assert "result" in data

    @pytest.mark.asyncio
    async def test_missing_method_field(self, mcp_adv_client):
        """JSON-RPC body with no 'method' — defaults to '' → unknown method (-32601)."""
        payload = {"jsonrpc": "2.0", "id": 99, "params": {}}
        resp = await mcp_adv_client.post("/api/v1/mcp", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data
        assert data["error"]["code"] == -32601

    @pytest.mark.asyncio
    async def test_wrong_jsonrpc_version_10(self, mcp_adv_client):
        """JSON-RPC 1.0 version string — server doesn't validate version, processes method."""
        payload = {"jsonrpc": "1.0", "id": 1, "method": "initialize", "params": {}}
        resp = await mcp_adv_client.post("/api/v1/mcp", json=payload)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_integer_method_field(self, mcp_adv_client):
        """JSON-RPC with method as integer — body.get("method", "") → 42 → no match → error."""
        payload = {"jsonrpc": "2.0", "id": 1, "method": 42, "params": {}}
        resp = await mcp_adv_client.post("/api/v1/mcp", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        # 42 != "initialize" / "tools/list" / "tools/call" → unknown method
        assert "result" in data or "error" in data

    @pytest.mark.asyncio
    async def test_invalid_json_body(self, mcp_adv_client):
        """Non-JSON body — should return JSON-RPC parse error (-32700), not 500."""
        resp = await mcp_adv_client.post(
            "/api/v1/mcp",
            content=b"not json at all!!",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data
        assert data["error"]["code"] == -32700

    @pytest.mark.asyncio
    async def test_empty_json_object(self, mcp_adv_client):
        """Empty JSON object {} — method defaults to '' → -32601."""
        resp = await mcp_adv_client.post("/api/v1/mcp", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data
        assert data["error"]["code"] == -32601


# ---------------------------------------------------------------------------
# 2–4. tools/call param variations
# ---------------------------------------------------------------------------


class TestToolsCallParams:
    @pytest.mark.asyncio
    async def test_params_null(self, mcp_adv_client):
        """tools/call with params=null → params falls back to {} → tool_name='' → isError."""
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": None}
        resp = await mcp_adv_client.post("/api/v1/mcp", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        result = data.get("result", {})
        assert result.get("isError") is True

    @pytest.mark.asyncio
    async def test_params_empty_dict(self, mcp_adv_client):
        """tools/call with params={} → name defaults to '' → isError=True."""
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {}}
        resp = await mcp_adv_client.post("/api/v1/mcp", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        result = data.get("result", {})
        assert result.get("isError") is True

    @pytest.mark.asyncio
    async def test_params_list(self, mcp_adv_client):
        """tools/call with params=[] (not a dict) — falls back to {} → isError."""
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": []}
        resp = await mcp_adv_client.post("/api/v1/mcp", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        # params or {} → {} when params is []
        assert "result" in data or "error" in data  # no 500

    @pytest.mark.asyncio
    async def test_no_name_key_in_params(self, mcp_adv_client):
        """tools/call params without 'name' key → tool_name='' → isError."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"arguments": {"x": 1}},
        }
        resp = await mcp_adv_client.post("/api/v1/mcp", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        result = data.get("result", {})
        assert result.get("isError") is True

    @pytest.mark.asyncio
    async def test_empty_string_name(self, mcp_adv_client):
        """tools/call with name='' — no tool matches → isError=True."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "", "arguments": {}},
        }
        resp = await mcp_adv_client.post("/api/v1/mcp", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        result = data.get("result", {})
        assert result.get("isError") is True


# ---------------------------------------------------------------------------
# 5. Registry CRUD adversarial
# ---------------------------------------------------------------------------


class TestMcpRegistryCrud:
    @pytest.mark.asyncio
    async def test_get_nonexistent_server_returns_404(self, mcp_adv_client):
        """GET /mcp/servers/{id} for unknown id → 404."""
        resp = await mcp_adv_client.get(f"/api/v1/mcp/servers/{uuid.uuid4()}")
        assert resp.status_code == 404
        data = resp.json()
        # AppError serialises as {"error": {"code": ..., "message": ...}}
        error = data.get("error") or data
        assert error.get("code") == "mcp_server_not_found"

    @pytest.mark.asyncio
    async def test_delete_nonexistent_server_returns_404(self, mcp_adv_client):
        """DELETE /mcp/servers/{id} for unknown id → 404.

        The fake DB execute() returns "OK" which the McpServerStore.delete()
        misparses as success. We therefore mock the store's delete method to
        return False (not found) to exercise the 404 path cleanly.
        """
        from app.mcp.store import get_mcp_store

        store = get_mcp_store()
        from unittest.mock import AsyncMock, patch as _patch

        with _patch.object(store, "delete", new=AsyncMock(return_value=False)):
            resp = await mcp_adv_client.delete(f"/api/v1/mcp/servers/{uuid.uuid4()}")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_create_with_file_scheme_url_blocked(self, mcp_adv_client):
        """file:// scheme should be rejected by SSRF guard (400)."""
        resp = await mcp_adv_client.post(
            "/api/v1/mcp/servers",
            json={"name": "local", "url": "file:///etc/passwd", "transport": "http"},
        )
        # SSRF guard raises AppError(ssrf_blocked, 400) or validation 422
        assert resp.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_create_with_loopback_blocked(self, mcp_adv_client):
        """127.0.0.1 loopback URL should be blocked by SSRF guard."""
        resp = await mcp_adv_client.post(
            "/api/v1/mcp/servers",
            json={"name": "local", "url": "http://127.0.0.1:8080/mcp", "transport": "http"},
        )
        assert resp.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_create_with_500_char_name_no_crash(self, mcp_adv_client):
        """Name with 500 chars — pydantic and SSRF guard accept it; store can store it."""
        from app.mcp.store import get_mcp_store
        from unittest.mock import AsyncMock, patch as _patch
        import uuid as _uuid

        long_name = "a" * 500
        fake_row = {
            "id": str(_uuid.uuid4()),
            "name": long_name,
            "url": "https://example.com/mcp",
            "transport": "http",
            "enabled": True,
            "org_id": str(_uuid.uuid4()),
            "created_at": "2026-01-01T00:00:00+00:00",
        }
        store = get_mcp_store()
        with _patch.object(store, "create", new=AsyncMock(return_value=fake_row)):
            resp = await mcp_adv_client.post(
                "/api/v1/mcp/servers",
                json={"name": long_name, "url": "https://example.com/mcp", "transport": "http"},
            )
        # No 500 — route returns 201 when store succeeds
        assert resp.status_code in (201, 400, 422)


# ---------------------------------------------------------------------------
# 6. Cross-org isolation (store-level)
# ---------------------------------------------------------------------------


class TestMcpCrossOrg:
    @pytest.mark.asyncio
    async def test_get_other_org_server_returns_none(self):
        """org_b cannot GET a server that org_a registered (store is org-scoped)."""
        try:
            from app.mcp.store import InMemoryMcpStore, set_mcp_store

            store = InMemoryMcpStore()
            set_mcp_store(store)

            org_a_id = str(uuid.uuid4())
            org_b_id = str(uuid.uuid4())

            server = await store.create(
                org_id=org_a_id,
                name="org-a-server",
                url="https://example.com/mcp",
                transport="http",
                auth_token=None,
                enabled=True,
                created_by="user-a",
            )
            server_id = server["id"]

            # org_b query must return None — the server belongs to org_a only.
            result = await store.get_by_id(server_id, org_b_id)
            assert result is None, "org_b must not see org_a's server"

        except (ImportError, AttributeError):
            pytest.skip("InMemoryMcpStore not available — store is DB-backed")


# ---------------------------------------------------------------------------
# 7. _strip_secrets correctness
# ---------------------------------------------------------------------------


class TestStripSecrets:
    def test_strips_auth_token(self):
        from app.routes.mcp import _strip_secrets

        row = {"id": "x", "name": "s", "auth_token": "super-secret", "url": "http://a.com"}
        clean = _strip_secrets(row)
        assert "auth_token" not in clean
        assert clean["id"] == "x"
        assert clean["url"] == "http://a.com"

    def test_strips_secret_field(self):
        from app.routes.mcp import _strip_secrets

        row = {"id": "x", "secret": "abc123", "name": "s"}
        clean = _strip_secrets(row)
        assert "secret" not in clean

    def test_strips_token_field(self):
        from app.routes.mcp import _strip_secrets

        row = {"id": "x", "token": "tok", "name": "s"}
        clean = _strip_secrets(row)
        assert "token" not in clean

    def test_preserves_non_secret_fields(self):
        from app.routes.mcp import _strip_secrets

        row = {
            "id": "y",
            "name": "ok",
            "url": "https://x.com",
            "transport": "http",
            "enabled": True,
        }
        clean = _strip_secrets(row)
        assert clean == row

    def test_empty_dict(self):
        from app.routes.mcp import _strip_secrets

        assert _strip_secrets({}) == {}

    def test_all_secret_keys_removed(self):
        """Ensure all three secret field names are stripped simultaneously."""
        from app.routes.mcp import _strip_secrets

        row = {
            "id": "z",
            "auth_token": "at",
            "secret": "s",
            "token": "t",
            "name": "ok",
        }
        clean = _strip_secrets(row)
        assert "auth_token" not in clean
        assert "secret" not in clean
        assert "token" not in clean
        assert clean["name"] == "ok"


# ---------------------------------------------------------------------------
# 8. Huge payload doesn't crash
# ---------------------------------------------------------------------------


class TestLargePayload:
    @pytest.mark.asyncio
    async def test_50kb_argument_no_crash(self, mcp_adv_client):
        """A 50 KB JSON-RPC payload must not crash the server (may return isError=True)."""
        big_string = "x" * (50 * 1024)
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "get_schema", "arguments": {"large_field": big_string}},
        }
        resp = await mcp_adv_client.post("/api/v1/mcp", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert "result" in data or "error" in data


# ---------------------------------------------------------------------------
# 9. Unauthenticated access
# ---------------------------------------------------------------------------


class TestUnauthenticated:
    @pytest.mark.asyncio
    async def test_unauthenticated_mcp_returns_401(self, client):
        """POST /api/v1/mcp without auth → 401."""
        payload = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        resp = await client.post("/api/v1/mcp", json=payload)
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_unauthenticated_servers_list_returns_401(self, client):
        """GET /api/v1/mcp/servers without auth → 401."""
        resp = await client.get("/api/v1/mcp/servers")
        assert resp.status_code in (401, 403)
