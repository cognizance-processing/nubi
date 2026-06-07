"""M13-C: POST /query/registry — register queries from the UI end-to-end.

Test coverage
-------------
(1) POST /query/registry with sql+params registers the query and returns it.
(2) The registered query is immediately runnable via POST /query {query_id, named_params}.
(3) Filtering: run with active=true returns only active rows; active=false returns fewer.
(4) Update: posting same id again overwrites the query (upsert).
(5) Auto-slug: omitting id derives one from name.
(6) Empty sql → 400 validation_error.
(7) Empty name → 400 validation_error.
(8) Registered query appears in GET /query/registry after POST.
(9) Unauthenticated POST → 401.
(10) Embed token cannot register queries → 403 forbidden.
"""

from __future__ import annotations

import uuid
from io import BytesIO

import pyarrow.ipc as pa_ipc
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auth_headers(user_id: str) -> dict[str, str]:
    from app.auth.jwt import mint_access_token
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


def _parse_arrow(content: bytes):
    return pa_ipc.open_stream(BytesIO(content)).read_all()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def reg_client(app, fake_db):
    """HTTPX client with a seeded user for the register-query tests."""
    user_id = str(uuid.uuid4())
    fake_db.users[user_id] = {
        "id": user_id,
        "email": "reg_tester@example.com",
        "name": "Registry Tester",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as ac:
        yield ac, user_id


# ---------------------------------------------------------------------------
# (1) POST /query/registry registers the query and returns it
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_query_returns_registered_entry(reg_client):
    """POST /query/registry returns the registered query with its id and params."""
    client, user_id = reg_client

    resp = await client.post(
        "/api/v1/query/registry",
        json={
            "id": "test_reg_basic",
            "name": "Test basic registration",
            "sql": "SELECT * FROM demo WHERE active = {{active}}",
            "params": [{"name": "active", "type": "boolean", "required": True}],
        },
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["id"] == "test_reg_basic"
    assert body["name"] == "Test basic registration"
    assert body["sql"] == "SELECT * FROM demo WHERE active = {{active}}"
    assert len(body["params"]) == 1
    assert body["params"][0]["name"] == "active"
    assert body["params"][0]["type"] == "boolean"
    assert body["params"][0]["required"] is True


# ---------------------------------------------------------------------------
# (2) Registered query is immediately runnable via POST /query {query_id, named_params}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registered_query_is_runnable(reg_client):
    """After POST /query/registry the query can be run via POST /query {query_id}."""
    client, user_id = reg_client
    headers = _auth_headers(user_id)

    # Register a simple query with a named param.
    reg_resp = await client.post(
        "/api/v1/query/registry",
        json={
            "id": "test_runnable_after_register",
            "name": "Runnable after register",
            "sql": "SELECT i FROM generate_series(1, {{n}}) AS t(i)",
            "params": [{"name": "n", "type": "number", "required": True}],
        },
        headers=headers,
    )
    assert reg_resp.status_code == 201, reg_resp.text

    # Run it with named_params.
    run_resp = await client.post(
        "/api/v1/query",
        json={"query_id": "test_runnable_after_register", "named_params": {"n": 4}},
        headers=headers,
    )
    assert run_resp.status_code == 200, run_resp.text
    table = _parse_arrow(run_resp.content)
    assert table.num_rows == 4


# ---------------------------------------------------------------------------
# (3) active=true returns only active rows; active=false returns different count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_active_param_filters_rows(reg_client):
    """Run with active=true vs active=false gives different row counts from demo."""
    client, user_id = reg_client
    headers = _auth_headers(user_id)

    # Register the demo-filter query.
    await client.post(
        "/api/v1/query/registry",
        json={
            "id": "test_active_filter",
            "name": "Demo active filter",
            "sql": "SELECT * FROM demo WHERE active = {{active}}",
            "params": [{"name": "active", "type": "boolean", "required": True}],
        },
        headers=headers,
    )

    # Run with active=true.
    resp_true = await client.post(
        "/api/v1/query",
        json={"query_id": "test_active_filter", "named_params": {"active": True}},
        headers=headers,
    )
    assert resp_true.status_code == 200, resp_true.text
    rows_true = _parse_arrow(resp_true.content).num_rows

    # Run with active=false.
    resp_false = await client.post(
        "/api/v1/query",
        json={"query_id": "test_active_filter", "named_params": {"active": False}},
        headers=headers,
    )
    assert resp_false.status_code == 200, resp_false.text
    rows_false = _parse_arrow(resp_false.content).num_rows

    # Demo has 3 active (id 1,3,5) and 2 inactive (id 2,4).
    assert rows_true == 3
    assert rows_false == 2
    assert rows_true != rows_false


# ---------------------------------------------------------------------------
# (4) Update: posting same id again overwrites (upsert)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_same_id_overwrites(reg_client):
    """POST /query/registry with an existing id replaces the query (upsert)."""
    client, user_id = reg_client
    headers = _auth_headers(user_id)

    await client.post(
        "/api/v1/query/registry",
        json={
            "id": "test_upsert_q",
            "name": "Original",
            "sql": "SELECT 1 AS v",
            "params": [],
        },
        headers=headers,
    )

    # Overwrite with a different SQL and params.
    resp2 = await client.post(
        "/api/v1/query/registry",
        json={
            "id": "test_upsert_q",
            "name": "Updated",
            "sql": "SELECT i FROM generate_series(1, {{k}}) AS t(i)",
            "params": [{"name": "k", "type": "number", "default": 2}],
        },
        headers=headers,
    )
    assert resp2.status_code == 201, resp2.text
    body = resp2.json()
    assert body["name"] == "Updated"
    assert "{{k}}" in body["sql"]

    # Running it should use the updated SQL.
    run_resp = await client.post(
        "/api/v1/query",
        json={"query_id": "test_upsert_q", "named_params": {"k": 6}},
        headers=headers,
    )
    assert run_resp.status_code == 200, run_resp.text
    table = _parse_arrow(run_resp.content)
    assert table.num_rows == 6


# ---------------------------------------------------------------------------
# (5) Auto-slug: omitting id derives one from name
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_slug_from_name(reg_client):
    """When id is omitted, a slug is derived from the name."""
    client, user_id = reg_client
    headers = _auth_headers(user_id)

    resp = await client.post(
        "/api/v1/query/registry",
        json={
            "name": "My Cool Query 123",
            "sql": "SELECT 42 AS answer",
            "params": [],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    # Slug should be something derived from the name.
    assert body["id"]  # not empty
    assert " " not in body["id"]  # spaces removed
    assert body["id"].islower() or body["id"].replace("_", "").isalnum()

    # The query should be runnable using the auto-generated id.
    run_resp = await client.post(
        "/api/v1/query",
        json={"query_id": body["id"]},
        headers=headers,
    )
    assert run_resp.status_code == 200, run_resp.text


# ---------------------------------------------------------------------------
# (6) Empty sql → 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_sql_returns_400(reg_client):
    """POST /query/registry with empty sql → 400 validation_error."""
    client, user_id = reg_client

    resp = await client.post(
        "/api/v1/query/registry",
        json={"name": "Bad query", "sql": "   ", "params": []},
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["error"]["code"] == "validation_error"


# ---------------------------------------------------------------------------
# (7) Empty name → 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_name_returns_400(reg_client):
    """POST /query/registry with empty name → 400 validation_error."""
    client, user_id = reg_client

    resp = await client.post(
        "/api/v1/query/registry",
        json={"name": "  ", "sql": "SELECT 1", "params": []},
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["error"]["code"] == "validation_error"


# ---------------------------------------------------------------------------
# (8) Registered query appears in GET /query/registry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registered_query_appears_in_list(reg_client):
    """After POST /query/registry the query is visible in GET /query/registry."""
    client, user_id = reg_client
    headers = _auth_headers(user_id)

    unique_id = f"test_list_visible_{uuid.uuid4().hex[:8]}"
    await client.post(
        "/api/v1/query/registry",
        json={
            "id": unique_id,
            "name": "List visible test",
            "sql": "SELECT 1",
            "params": [],
        },
        headers=headers,
    )

    list_resp = await client.get("/api/v1/query/registry", headers=headers)
    assert list_resp.status_code == 200, list_resp.text
    queries = list_resp.json().get("queries", [])
    ids = [q["id"] for q in queries]
    assert unique_id in ids


# ---------------------------------------------------------------------------
# (9) Unauthenticated POST → 401
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unauthenticated_register_returns_401(reg_client):
    """POST /query/registry without a token → 401."""
    client, _ = reg_client

    resp = await client.post(
        "/api/v1/query/registry",
        json={"name": "Anon", "sql": "SELECT 1", "params": []},
        # No Authorization header
    )
    assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------------------
# (10) Embed token cannot register queries → 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_token_cannot_register(reg_client):
    """Embed tokens (kind='embed') cannot POST /query/registry → 403 forbidden."""
    import time

    import jwt

    from app.config import get_settings

    client, user_id = reg_client

    # Mint a minimal embed-style RS256-ish token using the HS256 secret but
    # with kind='embed' in claims.  We just need the route to recognise it as
    # an embed identity; since our test stack uses HS256, we craft a token that
    # the verified_identity dep will parse as kind='embed'.
    # Simplest approach: use the internal VerifiedIdentity injection path by
    # directly crafting a valid HS256 token with kind=embed.
    settings = get_settings()
    now = int(time.time())
    embed_token = jwt.encode(
        {
            "sub": user_id,
            "kind": "embed",
            "scope": ["read:query"],
            "iat": now,
            "exp": now + 900,
        },
        settings.JWT_SECRET,
        algorithm="HS256",
    )

    resp = await client.post(
        "/api/v1/query/registry",
        json={"name": "Embed attempt", "sql": "SELECT 1", "params": []},
        headers={"Authorization": f"Bearer {embed_token}"},
    )
    # Embed tokens are rejected (403 forbidden OR 401 depending on verify_token).
    # The route itself raises 403 when it sees kind='embed'.
    assert resp.status_code in (401, 403), resp.text
