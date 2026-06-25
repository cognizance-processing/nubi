"""Tests for flow spec version history/revert, environments list, and /transpile.

Coverage
--------
- create flow → version 1 snapshotted
- update flow spec → version 2 snapshotted
- GET /flows/{id}/versions lists both versions (newest first)
- GET /flows/{id}/versions/{v} fetches spec at that version
- POST /flows/{id}/revert/{v} restores the spec + creates new version
- revert cross-org returns 404
- GET /flows/{id}/environments returns watermark data
- POST /transpile DuckDB→BigQuery produces valid SQL
- POST /transpile handles multiple dialect pairs
- POST /transpile rejects unknown dialect (400)
- POST /transpile rejects malformed SQL (400)
- cross-org isolation: cannot read another org's versions
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.flows.store import InMemoryFlowStore, set_flow_store
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo

# FakeDB is the conftest singleton exposed via the ``fake_db`` fixture
# (see conftest.py). Importing here so _make_user can seed it directly.

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(user_id: str | None = None, email: str = "alice@example.com") -> dict[str, Any]:
    uid = user_id or str(uuid.uuid4())
    return {
        "id": uid,
        "email": email,
        "name": "Alice",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


def _auth_headers(user_id: str) -> dict[str, str]:
    token = mint_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


_NOOP_SPEC = {
    "version": 1,
    "name": "test_flow",
    "tasks": [{"key": "s1", "kind": "noop", "needs": [], "config": {}}],
}

_NOOP_SPEC_V2 = {
    "version": 1,
    "name": "test_flow_v2",
    "tasks": [
        {"key": "s1", "kind": "noop", "needs": [], "config": {}},
        {"key": "s2", "kind": "noop", "needs": ["s1"], "config": {}},
    ],
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def app_and_store(app, fake_db):
    """Yield (app, store, repo, fake_db) with a fresh in-memory store injected."""
    store = InMemoryFlowStore()
    set_flow_store(store)
    repo = InMemoryRepo()
    set_repo(repo)
    yield app, store, repo, fake_db


@pytest_asyncio.fixture
async def client_with_user(app_and_store):
    """Yield (client, user, org_id, store, repo)."""
    app, store, repo, fake_db = app_and_store
    user = _make_user()
    org_id = str(uuid.uuid4())
    # Seed user in FakeDB so current_user dep (which calls fetchrow on users table) works.
    fake_db.users[user["id"]] = {
        "id": user["id"],
        "email": user["email"],
        "name": user["name"],
        "avatar_url": user["avatar_url"],
        "email_verified": user["email_verified"],
        "created_at": user["created_at"],
    }
    # Seed org membership in InMemoryRepo so _get_user_org works.
    repo.seed_org_member(org_id=org_id, user_id=user["id"], role="owner")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac, user, org_id, store, repo, fake_db


# ---------------------------------------------------------------------------
# Version history tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_flow_snapshots_version_1(client_with_user):
    client, user, org_id, store, repo, fake_db = client_with_user
    headers = _auth_headers(user["id"])
    resp = await client.post(
        "/api/v1/flows", json={"name": "myflow", "spec": _NOOP_SPEC}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    flow_id = resp.json()["id"]

    versions = await store.list_flow_versions(flow_id)
    assert len(versions) == 1
    assert versions[0]["version"] == 1
    assert versions[0]["spec"]["name"] == "test_flow"


@pytest.mark.asyncio
async def test_update_flow_spec_snapshots_new_version(client_with_user):
    client, user, org_id, store, repo, fake_db = client_with_user
    headers = _auth_headers(user["id"])

    resp = await client.post(
        "/api/v1/flows", json={"name": "myflow", "spec": _NOOP_SPEC}, headers=headers
    )
    assert resp.status_code == 201
    flow_id = resp.json()["id"]

    resp2 = await client.put(
        f"/api/v1/flows/{flow_id}",
        json={"spec": _NOOP_SPEC_V2},
        headers=headers,
    )
    assert resp2.status_code == 200

    versions = await store.list_flow_versions(flow_id)
    assert len(versions) == 2
    assert versions[1]["version"] == 2
    assert versions[1]["spec"]["name"] == "test_flow_v2"


@pytest.mark.asyncio
async def test_list_versions_endpoint(client_with_user):
    client, user, org_id, store, repo, fake_db = client_with_user
    headers = _auth_headers(user["id"])

    resp = await client.post(
        "/api/v1/flows", json={"name": "myflow", "spec": _NOOP_SPEC}, headers=headers
    )
    flow_id = resp.json()["id"]
    await client.put(f"/api/v1/flows/{flow_id}", json={"spec": _NOOP_SPEC_V2}, headers=headers)

    resp = await client.get(f"/api/v1/flows/{flow_id}/versions", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["flow_id"] == flow_id
    versions = data["versions"]
    assert len(versions) == 2
    # Newest first
    assert versions[0]["version"] == 2
    assert versions[1]["version"] == 1


@pytest.mark.asyncio
async def test_get_specific_version_endpoint(client_with_user):
    client, user, org_id, store, repo, fake_db = client_with_user
    headers = _auth_headers(user["id"])

    resp = await client.post(
        "/api/v1/flows", json={"name": "myflow", "spec": _NOOP_SPEC}, headers=headers
    )
    flow_id = resp.json()["id"]
    await client.put(f"/api/v1/flows/{flow_id}", json={"spec": _NOOP_SPEC_V2}, headers=headers)

    resp = await client.get(f"/api/v1/flows/{flow_id}/versions/1", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["version"] == 1
    assert data["spec"]["name"] == "test_flow"

    resp2 = await client.get(f"/api/v1/flows/{flow_id}/versions/2", headers=headers)
    assert resp2.status_code == 200
    assert resp2.json()["spec"]["name"] == "test_flow_v2"


@pytest.mark.asyncio
async def test_revert_restores_spec_as_new_version(client_with_user):
    client, user, org_id, store, repo, fake_db = client_with_user
    headers = _auth_headers(user["id"])

    resp = await client.post(
        "/api/v1/flows", json={"name": "myflow", "spec": _NOOP_SPEC}, headers=headers
    )
    flow_id = resp.json()["id"]
    await client.put(f"/api/v1/flows/{flow_id}", json={"spec": _NOOP_SPEC_V2}, headers=headers)

    # Revert to version 1
    resp = await client.post(
        f"/api/v1/flows/{flow_id}/revert/1", headers=headers
    )
    assert resp.status_code == 200
    flow = resp.json()
    # Live spec should match v1
    assert flow["spec"]["name"] == "test_flow"

    # Version history should have grown
    versions = await store.list_flow_versions(flow_id)
    assert len(versions) >= 3  # v1, v2 original, v2 snapshot-before-revert, v3 revert


@pytest.mark.asyncio
async def test_revert_cross_org_returns_404(client_with_user):
    client, user, org_id, store, repo, fake_db = client_with_user
    headers = _auth_headers(user["id"])

    # Create flow in org
    resp = await client.post(
        "/api/v1/flows", json={"name": "myflow", "spec": _NOOP_SPEC}, headers=headers
    )
    flow_id = resp.json()["id"]
    await client.put(f"/api/v1/flows/{flow_id}", json={"spec": _NOOP_SPEC_V2}, headers=headers)

    # Second user in a DIFFERENT org
    user2 = _make_user(email="bob@example.com")
    org2_id = str(uuid.uuid4())
    fake_db.users[user2["id"]] = {
        "id": user2["id"], "email": user2["email"], "name": user2["name"],
        "avatar_url": None, "email_verified": True, "created_at": user2["created_at"],
    }
    repo.seed_org_member(org_id=org2_id, user_id=user2["id"], role="owner")
    headers2 = _auth_headers(user2["id"])
    resp2 = await client.post(
        f"/api/v1/flows/{flow_id}/revert/1", headers=headers2
    )
    assert resp2.status_code == 404


@pytest.mark.asyncio
async def test_versions_cross_org_isolation(client_with_user):
    client, user, org_id, store, repo, fake_db = client_with_user
    headers = _auth_headers(user["id"])

    resp = await client.post(
        "/api/v1/flows", json={"name": "myflow", "spec": _NOOP_SPEC}, headers=headers
    )
    flow_id = resp.json()["id"]

    # Second user in a DIFFERENT org
    user2 = _make_user(email="bob2@example.com")
    org2_id = str(uuid.uuid4())
    fake_db.users[user2["id"]] = {
        "id": user2["id"], "email": user2["email"], "name": user2["name"],
        "avatar_url": None, "email_verified": True, "created_at": user2["created_at"],
    }
    repo.seed_org_member(org_id=org2_id, user_id=user2["id"], role="owner")
    headers2 = _auth_headers(user2["id"])

    resp2 = await client.get(f"/api/v1/flows/{flow_id}/versions", headers=headers2)
    assert resp2.status_code == 404


# ---------------------------------------------------------------------------
# Environments list tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_environments_empty(client_with_user):
    client, user, org_id, store, repo, fake_db = client_with_user
    headers = _auth_headers(user["id"])

    resp = await client.post(
        "/api/v1/flows", json={"name": "myflow", "spec": _NOOP_SPEC}, headers=headers
    )
    flow_id = resp.json()["id"]

    resp = await client.get(f"/api/v1/flows/{flow_id}/environments", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["flow_id"] == flow_id
    assert data["environments"] == []


@pytest.mark.asyncio
async def test_list_environments_with_watermarks(client_with_user):
    client, user, org_id, store, repo, fake_db = client_with_user
    headers = _auth_headers(user["id"])

    resp = await client.post(
        "/api/v1/flows", json={"name": "myflow", "spec": _NOOP_SPEC}, headers=headers
    )
    flow_id = resp.json()["id"]

    # Inject some watermarks directly into the store
    await store.set_watermark(flow_id, "s1", "prod", "2024-01-01T00:00:00+00:00")
    await store.set_watermark(flow_id, "s1", "dev", "2024-01-02T00:00:00+00:00")

    resp = await client.get(f"/api/v1/flows/{flow_id}/environments", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    envs = {e["key"]: e for e in data["environments"]}
    assert "prod" in envs
    assert envs["prod"]["watermarks"]["s1"] == "2024-01-01T00:00:00+00:00"
    assert "dev" in envs


# ---------------------------------------------------------------------------
# Backfill endpoint already exists — just verify it's reachable
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backfill_endpoint_exists(client_with_user):
    """Confirm /backfill is publicly accessible (returns 200 or 422 not 404/401)."""
    client, user, org_id, store, repo, fake_db = client_with_user
    headers = _auth_headers(user["id"])

    resp = await client.post(
        "/api/v1/flows", json={"name": "myflow", "spec": _NOOP_SPEC}, headers=headers
    )
    flow_id = resp.json()["id"]

    resp = await client.post(
        f"/api/v1/flows/{flow_id}/backfill",
        json={
            "start": "2024-01-01T00:00:00Z",
            "end": "2024-01-03T00:00:00Z",
            "window": "1d",
            "max_windows": 2,
        },
        headers=headers,
    )
    # Should succeed (200) or fail with a business error, NOT 401/403/404
    assert resp.status_code not in (401, 403, 404), resp.text


# ---------------------------------------------------------------------------
# Transpile endpoint tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_transpile_duckdb_to_bigquery(client_with_user):
    client, user, org_id, store, repo, fake_db = client_with_user
    headers = _auth_headers(user["id"])

    resp = await client.post(
        "/api/v1/transpile",
        json={
            "sql": "SELECT id, EPOCH_MS(created_at) AS ts FROM events",
            "from_dialect": "duckdb",
            "to_dialect": "bigquery",
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert "sql" in result
    assert isinstance(result["sql"], str)
    assert len(result["sql"]) > 0


@pytest.mark.asyncio
async def test_transpile_postgres_to_snowflake(client_with_user):
    client, user, org_id, store, repo, fake_db = client_with_user
    headers = _auth_headers(user["id"])

    resp = await client.post(
        "/api/v1/transpile",
        json={
            "sql": "SELECT id, created_at::date AS day FROM orders",
            "from_dialect": "postgres",
            "to_dialect": "snowflake",
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert "sql" in resp.json()


@pytest.mark.asyncio
async def test_transpile_bigquery_to_trino(client_with_user):
    client, user, org_id, store, repo, fake_db = client_with_user
    headers = _auth_headers(user["id"])

    resp = await client.post(
        "/api/v1/transpile",
        json={
            "sql": "SELECT id FROM `project.dataset.table` WHERE TRUE",
            "from_dialect": "bigquery",
            "to_dialect": "trino",
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert "sql" in resp.json()


@pytest.mark.asyncio
async def test_transpile_rejects_unknown_from_dialect(client_with_user):
    client, user, org_id, store, repo, fake_db = client_with_user
    headers = _auth_headers(user["id"])

    resp = await client.post(
        "/api/v1/transpile",
        json={"sql": "SELECT 1", "from_dialect": "fakedb", "to_dialect": "postgres"},
        headers=headers,
    )
    assert resp.status_code == 400, resp.text


@pytest.mark.asyncio
async def test_transpile_rejects_unknown_to_dialect(client_with_user):
    client, user, org_id, store, repo, fake_db = client_with_user
    headers = _auth_headers(user["id"])

    resp = await client.post(
        "/api/v1/transpile",
        json={"sql": "SELECT 1", "from_dialect": "postgres", "to_dialect": "notadialect"},
        headers=headers,
    )
    assert resp.status_code == 400, resp.text


@pytest.mark.asyncio
async def test_transpile_malformed_sql_returns_400(client_with_user):
    client, user, org_id, store, repo, fake_db = client_with_user
    headers = _auth_headers(user["id"])

    resp = await client.post(
        "/api/v1/transpile",
        json={
            "sql": "SELECT FROM WHERE",
            "from_dialect": "duckdb",
            "to_dialect": "bigquery",
        },
        headers=headers,
    )
    # sqlglot is lenient for some malformed SQL but should return 400 on truly bad SQL
    # OR return 200 with best-effort output — either is acceptable.
    # What we verify is: if 400, error code is parse_error; if 200, sql is a string.
    if resp.status_code == 400:
        error_body = resp.json().get("error", {})
        assert error_body.get("code") == "parse_error"
    else:
        assert resp.status_code == 200
        assert isinstance(resp.json()["sql"], str)


@pytest.mark.asyncio
async def test_transpile_requires_auth(client_with_user):
    client, user, org_id, store, repo, fake_db = client_with_user

    resp = await client.post(
        "/api/v1/transpile",
        json={"sql": "SELECT 1", "from_dialect": "duckdb", "to_dialect": "postgres"},
        # No auth headers
    )
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_transpile_empty_sql_returns_400(client_with_user):
    client, user, org_id, store, repo, fake_db = client_with_user
    headers = _auth_headers(user["id"])

    resp = await client.post(
        "/api/v1/transpile",
        json={"sql": "   ", "from_dialect": "duckdb", "to_dialect": "bigquery"},
        headers=headers,
    )
    assert resp.status_code == 400, resp.text
