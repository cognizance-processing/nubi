"""Tests for metric spec version history + revert.

Coverage
--------
1. create→update→update produces 3 versions in the store
2. GET /metrics/{id}/versions lists them newest-first
3. GET /metrics/{id}/versions/{v} returns the full spec for that version
4. POST /metrics/{id}/revert/{v} restores the older spec + creates a new version
5. Cross-org access → 404 (versions list + single version + revert)
6. Revert without author:metric scope → 403
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.metrics.versions import (
    InMemoryMetricVersionStore,
    set_metric_version_store,
)
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(user_id: str | None = None) -> dict[str, Any]:
    uid = user_id or str(uuid.uuid4())
    return {
        "id": uid,
        "email": f"user-{uid[:8]}@example.com",
        "name": "Test User",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


def _auth_headers(user_id: str) -> dict[str, str]:
    """Full-session token — carries author:metric by default."""
    token = mint_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


def _read_only_headers(user_id: str) -> dict[str, str]:
    """Explicitly scoped read-only token — must be rejected by author:metric gate."""
    from app.auth.jwt import mint_access_token as _mint  # noqa: PLC0415

    # extra_claims scope overrides _FIRST_PARTY_SCOPES so author:metric is absent.
    token = _mint(user_id=user_id, extra_claims={"scope": "read:query"})
    return {"Authorization": f"Bearer {token}"}


# Minimal valid metric body
_METRIC_BODY_V1 = {
    "name": "test_revenue",
    "measure": {"name": "revenue", "agg": "sum", "expr": "amount"},
    "base_table": "orders",
}

_METRIC_BODY_V2 = {
    "name": "test_revenue",
    "measure": {"name": "revenue", "agg": "sum", "expr": "amount"},
    "base_table": "orders",
    "description": "Updated in v2",
}

_METRIC_BODY_V3 = {
    "name": "test_revenue",
    "measure": {"name": "revenue", "agg": "sum", "expr": "amount"},
    "base_table": "orders",
    "description": "Updated in v3",
    "owner": "data-team",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def app_and_store(app, fake_db):
    """Yield (app, version_store, repo, fake_db) with a fresh in-memory store."""
    vs = InMemoryMetricVersionStore()
    set_metric_version_store(vs)
    repo = InMemoryRepo()
    set_repo(repo)
    yield app, vs, repo, fake_db


@pytest_asyncio.fixture
async def client_with_user(app_and_store):
    """Yield (client, user, org_id, vs, repo, fake_db)."""
    app, vs, repo, fake_db = app_and_store
    user = _make_user()
    org_id = str(uuid.uuid4())

    # Seed user in FakeDB.
    fake_db.users[user["id"]] = {
        "id": user["id"],
        "email": user["email"],
        "name": user["name"],
        "avatar_url": user["avatar_url"],
        "email_verified": user["email_verified"],
        "created_at": user["created_at"],
    }
    # Seed org membership so _caller_org / repo.get_org_for_user works.
    repo.seed_org_member(org_id=org_id, user_id=user["id"], role="owner")

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as ac:
        yield ac, user, org_id, vs, repo, fake_db


# ---------------------------------------------------------------------------
# 1. Version capture: create → update → update → 3 versions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_update_update_produces_three_versions(client_with_user):
    ac, user, org_id, vs, repo, fake_db = client_with_user
    headers = _auth_headers(user["id"])

    # CREATE
    r = await ac.post("/api/v1/metrics", json=_METRIC_BODY_V1, headers=headers)
    assert r.status_code == 201, r.text
    metric_id = r.json()["id"]

    # UPDATE v2
    r2 = await ac.put(f"/api/v1/metrics/{metric_id}", json=_METRIC_BODY_V2, headers=headers)
    assert r2.status_code == 200, r2.text

    # UPDATE v3
    r3 = await ac.put(f"/api/v1/metrics/{metric_id}", json=_METRIC_BODY_V3, headers=headers)
    assert r3.status_code == 200, r3.text

    versions = await vs.list_metric_versions(metric_id)
    assert len(versions) == 3, f"Expected 3 versions, got {len(versions)}"
    nums = [v["version"] for v in versions]
    assert nums == [1, 2, 3]


# ---------------------------------------------------------------------------
# 2. GET /metrics/{id}/versions — list, newest first
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_metric_versions_newest_first(client_with_user, monkeypatch):
    ac, user, org_id, vs, repo, fake_db = client_with_user
    headers = _auth_headers(user["id"])

    # Bypass DB-backed org-ownership check so in-memory-only tests work.
    async def _always_owned(metric_id, org_id):
        return True
    monkeypatch.setattr("app.metrics.registry.metric_belongs_to_org", _always_owned)

    r = await ac.post("/api/v1/metrics", json=_METRIC_BODY_V1, headers=headers)
    assert r.status_code == 201
    metric_id = r.json()["id"]

    r2 = await ac.put(f"/api/v1/metrics/{metric_id}", json=_METRIC_BODY_V2, headers=headers)
    assert r2.status_code == 200

    rv = await ac.get(f"/api/v1/metrics/{metric_id}/versions", headers=headers)
    assert rv.status_code == 200, rv.text
    body = rv.json()
    assert body["metric_id"] == metric_id
    versions = body["versions"]
    assert len(versions) == 2
    # Newest first
    assert versions[0]["version"] > versions[1]["version"]
    # Each entry has the expected keys
    for v in versions:
        assert "id" in v
        assert "version" in v
        assert "created_at" in v


# ---------------------------------------------------------------------------
# 3. GET /metrics/{id}/versions/{v} — full spec at that version
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_specific_metric_version(client_with_user, monkeypatch):
    ac, user, org_id, vs, repo, fake_db = client_with_user
    headers = _auth_headers(user["id"])

    async def _always_owned(metric_id, org_id):
        return True
    monkeypatch.setattr("app.metrics.registry.metric_belongs_to_org", _always_owned)

    r = await ac.post("/api/v1/metrics", json=_METRIC_BODY_V1, headers=headers)
    assert r.status_code == 201
    metric_id = r.json()["id"]

    r2 = await ac.put(f"/api/v1/metrics/{metric_id}", json=_METRIC_BODY_V2, headers=headers)
    assert r2.status_code == 200

    rv = await ac.get(f"/api/v1/metrics/{metric_id}/versions/1", headers=headers)
    assert rv.status_code == 200, rv.text
    body = rv.json()
    assert body["version"] == 1
    assert body["metric_id"] == metric_id
    assert "spec" in body
    # v1 had no description
    assert body["spec"].get("description", "") == ""

    rv2 = await ac.get(f"/api/v1/metrics/{metric_id}/versions/2", headers=headers)
    assert rv2.status_code == 200
    body2 = rv2.json()
    assert body2["version"] == 2
    assert body2["spec"].get("description") == "Updated in v2"


# ---------------------------------------------------------------------------
# 4. POST /metrics/{id}/revert/{v} — restores older spec + new version
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revert_restores_older_spec_and_creates_new_version(client_with_user, monkeypatch):
    ac, user, org_id, vs, repo, fake_db = client_with_user
    headers = _auth_headers(user["id"])

    async def _always_owned(metric_id, org_id):
        return True
    monkeypatch.setattr("app.metrics.registry.metric_belongs_to_org", _always_owned)

    # Create v1 (no description)
    r = await ac.post("/api/v1/metrics", json=_METRIC_BODY_V1, headers=headers)
    assert r.status_code == 201
    metric_id = r.json()["id"]

    # Update to v2 (with description)
    r2 = await ac.put(f"/api/v1/metrics/{metric_id}", json=_METRIC_BODY_V2, headers=headers)
    assert r2.status_code == 200

    # Revert to v1
    rv = await ac.post(f"/api/v1/metrics/{metric_id}/revert/1", headers=headers)
    assert rv.status_code == 200, rv.text
    body = rv.json()
    # The returned metric should have v1's spec (no description)
    assert body.get("description", "") == ""

    # Now there should be 3 versions (v1 original, v2 update, v3 = revert)
    versions = await vs.list_metric_versions(metric_id)
    assert len(versions) == 3, f"Expected 3 versions after revert, got {len(versions)}"

    # The live metric (from GET) should reflect the reverted spec
    rg = await ac.get(f"/api/v1/metrics/{metric_id}", headers=headers)
    assert rg.status_code == 200
    live = rg.json()
    assert live.get("description", "") == ""


# ---------------------------------------------------------------------------
# 5. Cross-org access → 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_org_versions_returns_404(client_with_user):
    ac, user, org_id, vs, repo, fake_db = client_with_user
    headers = _auth_headers(user["id"])

    # Create metric as user in org_id
    r = await ac.post("/api/v1/metrics", json=_METRIC_BODY_V1, headers=headers)
    assert r.status_code == 201
    metric_id = r.json()["id"]

    # Create a second user in a DIFFERENT org
    other_user = _make_user()
    other_org_id = str(uuid.uuid4())
    fake_db.users[other_user["id"]] = {
        "id": other_user["id"],
        "email": other_user["email"],
        "name": other_user["name"],
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    repo.seed_org_member(org_id=other_org_id, user_id=other_user["id"], role="owner")

    other_headers = _auth_headers(other_user["id"])

    # versions list — cross-org should 404
    rv = await ac.get(f"/api/v1/metrics/{metric_id}/versions", headers=other_headers)
    assert rv.status_code == 404, f"Expected 404 from cross-org versions list, got {rv.status_code}"

    # single version — cross-org should 404
    rv2 = await ac.get(f"/api/v1/metrics/{metric_id}/versions/1", headers=other_headers)
    assert rv2.status_code == 404, f"Expected 404 from cross-org version get, got {rv2.status_code}"

    # revert — cross-org should 404 (or 403 before 404; either is acceptable)
    rv3 = await ac.post(f"/api/v1/metrics/{metric_id}/revert/1", headers=other_headers)
    assert rv3.status_code in (403, 404), f"Expected 403/404 from cross-org revert, got {rv3.status_code}"


# ---------------------------------------------------------------------------
# 6. Revert without author:metric → 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revert_without_author_metric_scope_returns_403(client_with_user):
    ac, user, org_id, vs, repo, fake_db = client_with_user
    headers = _auth_headers(user["id"])

    r = await ac.post("/api/v1/metrics", json=_METRIC_BODY_V1, headers=headers)
    assert r.status_code == 201
    metric_id = r.json()["id"]

    # Use a read-only token
    read_headers = _read_only_headers(user["id"])
    rv = await ac.post(f"/api/v1/metrics/{metric_id}/revert/1", headers=read_headers)
    assert rv.status_code == 403, f"Expected 403, got {rv.status_code}: {rv.text}"


# ---------------------------------------------------------------------------
# Unit tests for InMemoryMetricVersionStore (no HTTP, no app)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_in_memory_store_monotonic_versions():
    store = InMemoryMetricVersionStore()
    mid = "my_metric"
    org = str(uuid.uuid4())

    v1 = await store.add_metric_version(mid, org, {"id": mid, "name": "m1"}, "user-1")
    v2 = await store.add_metric_version(mid, org, {"id": mid, "name": "m2"}, "user-1")
    v3 = await store.add_metric_version(mid, org, {"id": mid, "name": "m3"}, "user-1")

    assert v1["version"] == 1
    assert v2["version"] == 2
    assert v3["version"] == 3

    all_v = await store.list_metric_versions(mid)
    assert [v["version"] for v in all_v] == [1, 2, 3]

    got = await store.get_metric_version(mid, 2)
    assert got is not None
    assert got["spec"]["name"] == "m2"

    missing = await store.get_metric_version(mid, 99)
    assert missing is None


@pytest.mark.asyncio
async def test_in_memory_store_independent_metrics():
    """Versions are scoped per metric_id — different metrics do not share counters."""
    store = InMemoryMetricVersionStore()
    org = str(uuid.uuid4())

    await store.add_metric_version("m1", org, {"id": "m1"})
    await store.add_metric_version("m1", org, {"id": "m1"})
    await store.add_metric_version("m2", org, {"id": "m2"})

    m1_versions = await store.list_metric_versions("m1")
    m2_versions = await store.list_metric_versions("m2")

    assert [v["version"] for v in m1_versions] == [1, 2]
    assert [v["version"] for v in m2_versions] == [1]
