"""OAuth for remote MCP clients: the browser-connector flow, end to end.

Coverage
--------
1. Discovery: an unauthenticated MCP call gets a 401 challenge pointing at the
   protected-resource metadata, which points at the authorization server.
2. The full flow — register, authorize, consent, token — yields a token that
   works on the MCP endpoint exactly like an API key (same user, same org).
3. Audience: the token is refused on every other REST route, but works on the
   in-process REST call MCP's api_request makes.
4. PKCE, redirect-URI and client binding; single-use codes (a replay revokes
   the grant); refresh rotation (reuse revokes the grant).
5. Revocation and removal from the org cut the connection off.
6. Consent can't be approved by an API key, or for an org the user isn't in.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import uuid
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.auth.oauth import InMemoryOAuthStore, set_oauth_store_for_tests
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo

REDIRECT = "https://claude.ai/api/mcp/auth_callback"


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


@pytest_asyncio.fixture
async def env(app, fake_db, monkeypatch):
    # Imported here, not at module level: importing route modules at collection
    # time binds their DB helpers before the fake_db patches exist, which leaks
    # real-DB calls into unrelated tests that run later.
    import app.routes.oauth as oauth_routes

    repo = InMemoryRepo()
    set_repo(repo)
    store = InMemoryOAuthStore()
    set_oauth_store_for_tests(store)

    user_id, org_id, other_org = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    fake_db.users[user_id] = {
        "id": user_id, "email": f"u-{user_id[:8]}@example.com", "name": "U",
        "avatar_url": None, "email_verified": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    repo.seed_org_member(org_id=org_id, user_id=user_id, role="admin")
    store.members.add((user_id, org_id))

    async def fake_list(uid):
        return [{"id": org_id, "name": "Acme", "role": "admin"}]

    async def fake_membership(uid, oid):
        return {"id": oid, "name": "Acme", "role": "admin"} if (uid, oid) in store.members else None

    async def no_audit(**_kw):
        return None

    monkeypatch.setattr(oauth_routes, "_list_user_orgs", fake_list)
    monkeypatch.setattr(oauth_routes, "_get_user_org_membership", fake_membership)
    monkeypatch.setattr(oauth_routes, "record_audit", no_audit)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        c.user_id, c.org_id, c.other_org, c.store = user_id, org_id, other_org, store
        c.session = {"Authorization": f"Bearer {mint_access_token(user_id)}"}
        yield c
    set_oauth_store_for_tests(None)
    set_repo(None)


async def _register(c, **extra) -> dict:
    resp = await c.post("/api/v1/oauth/register", json={
        "client_name": "Claude", "redirect_uris": [REDIRECT], **extra,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _authorize(c, client_id: str, challenge: str, **overrides):
    params = {
        "response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT,
        "code_challenge": challenge, "code_challenge_method": "S256",
        "state": "xyz", "scope": "mcp", "resource": "http://testserver/api/v1/mcp",
        **overrides,
    }
    return await c.get("/api/v1/oauth/authorize", params=params, follow_redirects=False)


async def _consent(c, location: str, org_id: str | None = None, approve=True, headers=None):
    request_id = parse_qs(urlsplit(location).query)["request"][0]
    return await c.post(
        f"/api/v1/oauth/requests/{request_id}/decision",
        json={"approve": approve, "org_id": org_id or c.org_id},
        headers=headers or c.session,
    )


async def _connect(c) -> tuple[dict, dict]:
    """Run the whole flow; return (client registration, token response)."""
    client = await _register(c)
    verifier, challenge = _pkce()
    resp = await _authorize(c, client["client_id"], challenge)
    assert resp.status_code == 302, resp.text
    decision = await _consent(c, resp.headers["location"])
    assert decision.status_code == 200, decision.text
    q = parse_qs(urlsplit(decision.json()["redirect_to"]).query)
    assert q["state"] == ["xyz"]
    tok = await c.post("/api/v1/oauth/token", data={
        "grant_type": "authorization_code", "code": q["code"][0], "redirect_uri": REDIRECT,
        "code_verifier": verifier, "client_id": client["client_id"],
    })
    assert tok.status_code == 200, tok.text
    assert tok.headers["cache-control"] == "no-store"
    return client, tok.json()


async def _mcp(c, token: str, method="tools/list", params=None, path="/api/v1/mcp"):
    return await c.post(path, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
                        headers={"Authorization": f"Bearer {token}"})


# ── 1. Discovery ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unauthenticated_mcp_call_gets_discovery_challenge(env):
    resp = await env.post("/api/v1/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert resp.status_code == 401
    challenge = resp.headers["www-authenticate"]
    assert 'resource_metadata="http://testserver/.well-known/oauth-protected-resource/api/v1/mcp"' in challenge

    prm = (await env.get("/.well-known/oauth-protected-resource/api/v1/mcp")).json()
    assert prm["resource"] == "http://testserver/api/v1/mcp"
    issuer = prm["authorization_servers"][0]
    asm = (await env.get("/.well-known/oauth-authorization-server")).json()
    assert asm["issuer"] == issuer
    assert asm["code_challenge_methods_supported"] == ["S256"]
    assert asm["registration_endpoint"].endswith("/api/v1/oauth/register")


@pytest.mark.asyncio
async def test_bad_token_challenge_says_invalid_token(env):
    resp = await _mcp(env, "nubi_oat_not-a-real-token")
    assert resp.status_code == 401
    assert 'error="invalid_token"' in resp.headers["www-authenticate"]


# ── 2. Full flow → same powers as an API key ────────────────────────────────


@pytest.mark.asyncio
async def test_full_flow_token_works_on_mcp(env):
    _, tok = await _connect(env)
    assert tok["token_type"] == "Bearer" and tok["access_token"].startswith("nubi_oat_")
    resp = await _mcp(env, tok["access_token"])
    assert resp.status_code == 200
    names = [t["name"] for t in resp.json()["result"]["tools"]]
    assert "run_query" in names and "api_request" in names


@pytest.mark.asyncio
async def test_token_is_pinned_to_the_approved_org(env):
    _, tok = await _connect(env)
    resp = await env.post(
        "/api/v1/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
              "params": {"name": "list_dashboards", "arguments": {}}},
        headers={"Authorization": f"Bearer {tok['access_token']}", "X-Org-Id": env.other_org},
    )
    assert resp.status_code == 403


# ── 3. Audience ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", [
    ("GET", "/api/v1/orgs"), ("POST", "/api/v1/auth/api-keys"), ("GET", "/api/v1/mcp/servers"),
    ("GET", "/api/v1/oauth/connections"),
])
async def test_token_refused_off_the_mcp_endpoint(env, method, path):
    _, tok = await _connect(env)
    resp = await env.request(method, path, headers={"Authorization": f"Bearer {tok['access_token']}"},
                             json={"name": "x"} if method == "POST" else None)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_token_works_through_api_request(env):
    _, tok = await _connect(env)
    resp = await _mcp(env, tok["access_token"], "tools/call",
                      {"name": "api_request", "arguments": {"method": "GET", "path": "/oauth-probe"}})
    body = json.loads(resp.json()["result"]["content"][0]["text"])
    # Authenticated: the route lookup ran (404) rather than auth failing (401).
    assert body["status"] == 404, body


@pytest.mark.asyncio
async def test_api_request_cannot_reach_oauth_routes(env):
    _, tok = await _connect(env)
    resp = await _mcp(env, tok["access_token"], "tools/call",
                      {"name": "api_request", "arguments": {"method": "GET", "path": "/oauth/connections"}})
    result = resp.json()["result"]
    assert result["isError"] and "not reachable over MCP" in result["content"][0]["text"]


# ── 4. Binding, PKCE, single use, rotation ──────────────────────────────────


@pytest.mark.asyncio
async def test_authorize_never_redirects_to_unregistered_uri(env):
    client = await _register(env)
    _, challenge = _pkce()
    resp = await _authorize(env, client["client_id"], challenge, redirect_uri="https://evil.example/cb")
    assert resp.status_code == 400 and "location" not in resp.headers


@pytest.mark.asyncio
async def test_authorize_requires_pkce(env):
    client = await _register(env)
    resp = await _authorize(env, client["client_id"], "", code_challenge_method="plain")
    assert resp.status_code == 302
    assert parse_qs(urlsplit(resp.headers["location"]).query)["error"] == ["invalid_request"]


@pytest.mark.asyncio
async def test_authorize_rejects_foreign_resource(env):
    client = await _register(env)
    _, challenge = _pkce()
    resp = await _authorize(env, client["client_id"], challenge, resource="https://other.example/mcp")
    assert parse_qs(urlsplit(resp.headers["location"]).query)["error"] == ["invalid_target"]


@pytest.mark.asyncio
async def test_register_rejects_plain_http_redirect(env):
    resp = await env.post("/api/v1/oauth/register", json={"redirect_uris": ["http://example.com/cb"]})
    assert resp.status_code == 400 and resp.json()["error"] == "invalid_redirect_uri"


async def _code(env, client):
    verifier, challenge = _pkce()
    resp = await _authorize(env, client["client_id"], challenge)
    decision = await _consent(env, resp.headers["location"])
    return verifier, parse_qs(urlsplit(decision.json()["redirect_to"]).query)["code"][0]


@pytest.mark.asyncio
async def test_wrong_verifier_fails(env):
    client = await _register(env)
    _, code = await _code(env, client)
    resp = await env.post("/api/v1/oauth/token", data={
        "grant_type": "authorization_code", "code": code, "code_verifier": _pkce()[0],
        "client_id": client["client_id"],
    })
    assert resp.status_code == 400 and resp.json()["error"] == "invalid_grant"


@pytest.mark.asyncio
async def test_code_replay_revokes_the_grant(env):
    client = await _register(env)
    verifier, code = await _code(env, client)
    form = {"grant_type": "authorization_code", "code": code, "code_verifier": verifier,
            "client_id": client["client_id"]}
    first = await env.post("/api/v1/oauth/token", data=form)
    assert first.status_code == 200
    replay = await env.post("/api/v1/oauth/token", data=form)
    assert replay.status_code == 400
    assert (await _mcp(env, first.json()["access_token"])).status_code == 401


@pytest.mark.asyncio
async def test_code_bound_to_its_client(env):
    client, other = await _register(env), await _register(env)
    verifier, code = await _code(env, client)
    resp = await env.post("/api/v1/oauth/token", data={
        "grant_type": "authorization_code", "code": code, "code_verifier": verifier,
        "client_id": other["client_id"],
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_refresh_rotates_and_reuse_revokes(env):
    client, tok = await _connect(env)
    form = {"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
            "client_id": client["client_id"]}
    rotated = await env.post("/api/v1/oauth/token", data=form)
    assert rotated.status_code == 200
    new = rotated.json()
    assert new["refresh_token"] != tok["refresh_token"]
    assert (await _mcp(env, new["access_token"])).status_code == 200

    reuse = await env.post("/api/v1/oauth/token", data=form)
    assert reuse.status_code == 400
    # Reuse of a rotated token means it was copied: the whole connection dies.
    assert (await _mcp(env, new["access_token"])).status_code == 401


@pytest.mark.asyncio
async def test_confidential_client_needs_its_secret(env):
    client = await _register(env, token_endpoint_auth_method="client_secret_basic")
    assert client["client_secret"]
    verifier, code = await _code(env, client)
    form = {"grant_type": "authorization_code", "code": code, "code_verifier": verifier}
    bad = base64.b64encode(f"{client['client_id']}:wrong".encode()).decode()
    assert (await env.post("/api/v1/oauth/token", data=form,
                           headers={"Authorization": f"Basic {bad}"})).status_code == 401
    good = base64.b64encode(f"{client['client_id']}:{client['client_secret']}".encode()).decode()
    assert (await env.post("/api/v1/oauth/token", data=form,
                           headers={"Authorization": f"Basic {good}"})).status_code == 200


# ── 5. Revocation ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_disconnect_from_settings_kills_the_token(env):
    _, tok = await _connect(env)
    listing = await env.get("/api/v1/oauth/connections", headers={**env.session, "X-Org-Id": env.org_id})
    [conn] = listing.json()["connections"]
    assert conn["client_name"] == "Claude" and conn["redirect_host"] == "claude.ai"
    resp = await env.delete(f"/api/v1/oauth/connections/{conn['id']}",
                            headers={**env.session, "X-Org-Id": env.org_id})
    assert resp.status_code == 204
    assert (await _mcp(env, tok["access_token"])).status_code == 401


@pytest.mark.asyncio
async def test_client_revocation_endpoint(env):
    client, tok = await _connect(env)
    resp = await env.post("/api/v1/oauth/revoke", data={
        "token": tok["refresh_token"], "client_id": client["client_id"]})
    assert resp.status_code == 200
    assert (await _mcp(env, tok["access_token"])).status_code == 401


@pytest.mark.asyncio
async def test_removed_member_loses_access(env):
    _, tok = await _connect(env)
    env.store.members.discard((env.user_id, env.org_id))
    assert (await _mcp(env, tok["access_token"])).status_code == 401


# ── 6. Consent guards ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_consent_requires_membership_of_chosen_org(env):
    client = await _register(env)
    _, challenge = _pkce()
    resp = await _authorize(env, client["client_id"], challenge)
    decision = await _consent(env, resp.headers["location"], org_id=env.other_org)
    assert decision.status_code == 403


@pytest.mark.asyncio
async def test_consent_refuses_api_key_callers(env):
    from app.auth.api_keys import InMemoryApiKeyStore, set_api_key_store_for_tests

    keys = InMemoryApiKeyStore()
    set_api_key_store_for_tests(keys)
    try:
        raw, _ = await keys.create(env.user_id, env.org_id, "k")
        client = await _register(env)
        _, challenge = _pkce()
        resp = await _authorize(env, client["client_id"], challenge)
        decision = await _consent(env, resp.headers["location"],
                                  headers={"Authorization": f"Bearer {raw}"})
        assert decision.status_code == 403
    finally:
        set_api_key_store_for_tests(None)


@pytest.mark.asyncio
async def test_consent_details_flag_known_clients(env):
    client = await _register(env)
    _, challenge = _pkce()
    resp = await _authorize(env, client["client_id"], challenge)
    request_id = parse_qs(urlsplit(resp.headers["location"]).query)["request"][0]
    details = (await env.get(f"/api/v1/oauth/requests/{request_id}", headers=env.session)).json()
    assert details["verified_as"] == "Claude" and details["redirect_host"] == "claude.ai"
    assert details["orgs"][0]["id"] == env.org_id


@pytest.mark.asyncio
async def test_probe_path_needs_auth_when_called_directly(env):
    """Guards the api_request test above: the probe path is auth-gated."""
    _, tok = await _connect(env)
    resp = await env.get("/api/v1/oauth-probe", headers={"Authorization": f"Bearer {tok['access_token']}"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_consent_details_mark_loopback_clients_as_local(env):
    resp = await env.post("/api/v1/oauth/register", json={
        "client_name": "Claude Code (nubi)", "redirect_uris": ["http://localhost:53682/callback"]})
    client = resp.json()
    _, challenge = _pkce()
    auth = await _authorize(env, client["client_id"], challenge,
                            redirect_uri="http://localhost:61000/callback")  # any loopback port
    request_id = parse_qs(urlsplit(auth.headers["location"]).query)["request"][0]
    details = (await env.get(f"/api/v1/oauth/requests/{request_id}", headers=env.session)).json()
    assert details["local_app"] is True and details["verified_as"] is None
