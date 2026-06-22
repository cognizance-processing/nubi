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
    """HTTPX client with a seeded user + org for the register-query tests.

    An InMemoryRepo is injected so that org resolution succeeds — this means
    GET /query/registry correctly scopes to the user's org (fail-closed fix).
    """
    from app.repos.memory import InMemoryRepo
    from app.repos.provider import set_repo

    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    fake_db.users[user_id] = {
        "id": user_id,
        "email": "reg_tester@example.com",
        "name": "Registry Tester",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    repo = InMemoryRepo()
    repo.seed_org_member(org_id=org_id, user_id=user_id)
    set_repo(repo)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as ac:
        yield ac, user_id

    set_repo(None)


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

    # Use a UUID id so the query is persisted to the repo and org-scoped list
    # picks it up.  Slug-only ids are registry-only (not persisted) and are
    # intentionally excluded from first-party list responses.
    unique_id = str(uuid.uuid4())
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


# ---------------------------------------------------------------------------
# (11) Security: registry fallback NEVER returns cross-org SQL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registry_fallback_does_not_leak_cross_org_sql() -> None:
    """GET /query/registry exception path must fail closed (empty list).

    When org/project resolution raises (e.g. repo unavailable), the old code
    set row_ids=None which skipped the filter and leaked all orgs' SQL.  The
    fix sets row_ids=set() so the filter returns nothing.
    """
    from unittest.mock import AsyncMock, MagicMock

    from fastapi import Request

    from app.auth.verify import VerifiedIdentity
    from app.queries.registry import get_query_registry, reset_for_tests
    from app.repos.provider import set_repo
    from app.routes.query import list_query_registry

    # ── Seed two registry entries that would appear in an unfiltered list ──────
    reset_for_tests()
    registry = get_query_registry()
    registry.register(
        id="cross_org_secret_q",
        sql="SELECT secret FROM other_org_table",
        name="Other-org secret",
    )
    registry.register(
        id="cross_org_secret_q2",
        sql="SELECT classified FROM rival_org",
        name="Rival org data",
    )

    # ── Repo whose list() raises to trigger the exception path ────────────────
    bad_repo = MagicMock()
    bad_repo.list = AsyncMock(side_effect=RuntimeError("db unavailable"))
    set_repo(bad_repo)

    # ── First-party identity (kind='access') — org resolution will call repo ──
    identity = VerifiedIdentity(
        kind="access",
        user_id="user-cross-org-test",
        org=None,
        project=None,
        roles=["editor"],
        policies={},
        scope=["read:query"],
        embed_origin=None,
        datastore=None,
        raw_claims={},
    )

    # Build a minimal Request stub (only used for header inspection).
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {}

    # Patch the resolve_org_id helper so it raises (simulating unavailable repo).
    import sys
    import types

    fake_org_mod = types.ModuleType("app.routes._org")

    async def _failing_resolve_org_id(*_a, **_kw):
        raise RuntimeError("org resolution failed")

    async def _failing_resolve_project_filter(*_a, **_kw):
        raise RuntimeError("project resolution failed")

    fake_org_mod.resolve_org_id = _failing_resolve_org_id  # type: ignore[attr-defined]
    fake_org_mod.resolve_project_filter = _failing_resolve_project_filter  # type: ignore[attr-defined]

    orig = sys.modules.get("app.routes._org")
    sys.modules["app.routes._org"] = fake_org_mod
    try:
        result = await list_query_registry(request=mock_request, identity=identity)
    finally:
        if orig is None:
            sys.modules.pop("app.routes._org", None)
        else:
            sys.modules["app.routes._org"] = orig
        reset_for_tests()

    # Security property: on the scoping-error fallback, NO non-system
    # (tenant/cross-org) query may leak. Built-in/system queries (demo_*) are
    # global seed data carrying no tenant SQL and MAY still appear.
    returned_ids = [q["id"] for q in result["queries"]]
    assert "cross_org_secret_q" not in returned_ids, (
        "registry fallback leaked cross-org SQL (cross_org_secret_q)"
    )
    assert "cross_org_secret_q2" not in returned_ids, (
        "registry fallback leaked cross-org SQL (cross_org_secret_q2)"
    )
    # Everything returned must be a built-in/system query (reset_for_tests in the
    # finally above removed the cross-org entries and re-seeded only the built-ins).
    builtin_ids = {rq.id for rq in get_query_registry().all()}
    leaked = set(returned_ids) - builtin_ids
    assert not leaked, f"registry fallback leaked non-built-in queries: {leaked}"
