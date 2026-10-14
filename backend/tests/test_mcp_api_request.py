"""MCP `api_request`: the REST API as the calling user, with credential routes fenced off.

Coverage
--------
1. tools/list advertises api_request.
2. A GET runs as the caller (their identity, their org) and returns JSON.
3. Deny-list: auth/api-keys, secrets, admin, bridge tokens, embed-token minting,
   and MCP itself are refused for every method.
4. Membership/access changes are refused for writes but readable.
5. Path tricks ('..', '%2e', '?', backslash, non-leading slash) are refused.
6. A caller-supplied X-Org-Id in the tool arguments cannot change the org.
7. Unknown methods are refused.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo


@pytest_asyncio.fixture
async def client(app, fake_db):
    repo = InMemoryRepo()
    set_repo(repo)
    user_id, org_id = str(uuid.uuid4()), str(uuid.uuid4())
    fake_db.users[user_id] = {
        "id": user_id, "email": f"u-{user_id[:8]}@example.com", "name": "U",
        "avatar_url": None, "email_verified": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    repo.seed_org_member(org_id=org_id, user_id=user_id, role="admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        c.headers["Authorization"] = f"Bearer {mint_access_token(user_id)}"
        c.headers["X-Org-Id"] = org_id
        c._user_id, c._org_id = user_id, org_id
        yield c
    set_repo(None)


async def _call(c, args):
    resp = await c.post("/api/v1/mcp", json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "api_request", "arguments": args},
    })
    assert resp.status_code == 200
    res = resp.json()["result"]
    return res["isError"], res["content"][0]["text"]


@pytest.mark.asyncio
async def test_tool_is_listed(client):
    resp = await client.post("/api/v1/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = [t["name"] for t in resp.json()["result"]["tools"]]
    assert "api_request" in names


@pytest.mark.asyncio
async def test_get_runs_as_the_caller(client):
    is_err, text = await _call(client, {"method": "GET", "path": "/orgs"})
    body = json.loads(text)
    assert not is_err, text
    assert body["status"] == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", [
    ("POST", "/auth/api-keys"), ("GET", "/auth/me"), ("GET", "/secrets"),
    ("GET", "/admin/orgs"), ("POST", "/bridges/b1/tokens"), ("POST", "/bridges/b1/tokens/t/rotate"),
    ("POST", "/embed/embed-token"), ("GET", "/mcp/servers"), ("POST", "/mcp"),
    ("GET", "/security/jwt-issuers"), ("GET", "/api/v1/auth/me"),
])
async def test_deny_list(client, method, path):
    is_err, text = await _call(client, {"method": method, "path": path})
    assert is_err and "not reachable over MCP" in text


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/orgs/x/members/m1", "/orgs/x/invites", "/orgs/invites/tok/accept", "/access-grants"])
async def test_membership_writes_refused(client, path):
    is_err, text = await _call(client, {"method": "POST", "path": path})
    assert is_err and "not reachable over MCP" in text


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [
    "/boards/../auth/me", "/%2e%2e/auth/me", "/boards?x=1", "/boards\\..\\auth", "boards", "/./auth/me",
])
async def test_path_tricks_refused(client, path):
    is_err, _ = await _call(client, {"method": "GET", "path": path})
    assert is_err


@pytest.mark.asyncio
async def test_org_cannot_be_overridden(client, monkeypatch):
    seen = {}
    import app.routes.mcp as m

    real = m._api_request

    async def spy(request, identity, user, org_id, arguments):
        seen["org"] = org_id
        return await real(request, identity, user, org_id, arguments)

    monkeypatch.setattr(m, "_api_request", spy)
    await _call(client, {"method": "GET", "path": "/boards", "params": {"X-Org-Id": str(uuid.uuid4())},
                         "headers": {"X-Org-Id": str(uuid.uuid4())}})
    assert seen["org"] == client._org_id


@pytest.mark.asyncio
async def test_unknown_method_refused(client):
    is_err, text = await _call(client, {"method": "TRACE", "path": "/boards"})
    assert is_err and "method must be one of" in text
