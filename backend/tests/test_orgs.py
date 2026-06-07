"""Tests for multi-org support — GET /orgs, GET /orgs/{id}, POST /orgs.

Coverage
--------
1.  GET /orgs without auth → 401.
2.  GET /orgs with auth → 200, returns {orgs: [{id, name, role}]}.
3.  GET /orgs always includes the user's personal org.
4.  GET /orgs — second user cannot see the first user's org (org-scoping).
5.  GET /orgs/{id} — member can fetch their own org.
6.  GET /orgs/{id} — non-member gets 404 (no cross-org info leak).
7.  GET /orgs/{id} — without auth → 401.
8.  POST /orgs — creates a new org and returns {id, name, role=owner}.
9.  POST /orgs — without auth → 401.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token

# Import the orgs router module so it self-registers on api_router at import
# time.  main.py will add the explicit import line once the orchestrator wires
# the new router; until then this import ensures the tests see the routes.
import app.routes.orgs  # noqa: F401, E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auth_headers(user_id: str) -> dict[str, str]:
    token = mint_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


def _make_user(user_id: str, email: str = "user@example.com") -> dict[str, Any]:
    return {
        "id": user_id,
        "email": email,
        "name": "Test User",
        "avatar_url": None,
        "email_verified": True,
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
    }


def _make_org(org_id: str, name: str = "My Workspace") -> dict[str, Any]:
    return {
        "id": org_id,
        "name": name,
        "slug": f"my-workspace-{org_id[:8]}",
        "created_at": datetime.now(tz=timezone.utc),
    }


def _make_membership(org_id: str, user_id: str, role: str = "owner") -> dict[str, Any]:
    """Return an asyncpg-like row dict for org_members JOIN orgs query."""
    return {
        "id": org_id,
        "name": "My Workspace",
        "role": role,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def orgs_client(app, fake_db):
    """HTTP client with a pre-seeded user + org membership.

    Seeds
    -----
    - user_id  → user in fake_db.users
    - org_id   → org in fake_db.orgs
    - membership → fake_db.org_members

    The ``app.routes.orgs.fetch`` and ``app.routes.orgs.fetchrow`` are
    patched to return the seeded data (since the conftest fake_fetch returns
    [] by default and doesn't know about the JOIN query).
    """
    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())

    # Seed in FakeDB so auth resolves the user.
    fake_db.users[user_id] = _make_user(user_id)
    fake_db.orgs[org_id] = _make_org(org_id)
    fake_db.org_members[f"{org_id}:{user_id}"] = {
        "org_id": org_id,
        "user_id": user_id,
        "role": "owner",
    }

    # Build the row shape returned by the JOIN query in orgs.py.
    membership_row = {"id": org_id, "name": "My Workspace", "role": "owner"}

    async def _fake_orgs_fetch(query: str, *args: Any) -> list[dict[str, Any]]:
        """Return memberships for GET /orgs."""
        q = query.upper()
        if "ORG_MEMBERS" in q and "JOIN" in q:
            queried_user = str(args[0])
            if queried_user == user_id:
                return [membership_row]
        return []

    async def _fake_orgs_fetchrow(query: str, *args: Any) -> dict[str, Any] | None:
        """Return a single membership for GET /orgs/{id}."""
        q = query.upper()
        if "ORG_MEMBERS" in q and "JOIN" in q:
            queried_user = str(args[0])
            queried_org = str(args[1])
            if queried_user == user_id and queried_org == org_id:
                return membership_row
        # Fall back to the existing fake for user resolution etc.
        # (This path is not reached for orgs queries, but keeps auth working.)
        return None

    with (
        patch("app.routes.orgs.fetch", side_effect=_fake_orgs_fetch),
        patch("app.routes.orgs.fetchrow", side_effect=_fake_orgs_fetchrow),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
            follow_redirects=False,
        ) as ac:
            yield ac, user_id, org_id


@pytest_asyncio.fixture
async def two_user_client(app, fake_db):
    """Two users each with their own org; neither should see the other's org."""
    user_a = str(uuid.uuid4())
    org_a = str(uuid.uuid4())
    user_b = str(uuid.uuid4())
    org_b = str(uuid.uuid4())

    fake_db.users[user_a] = _make_user(user_a, email="a@example.com")
    fake_db.users[user_b] = _make_user(user_b, email="b@example.com")
    fake_db.orgs[org_a] = _make_org(org_a, "Alice's Workspace")
    fake_db.orgs[org_b] = _make_org(org_b, "Bob's Workspace")
    fake_db.org_members[f"{org_a}:{user_a}"] = {"org_id": org_a, "user_id": user_a, "role": "owner"}
    fake_db.org_members[f"{org_b}:{user_b}"] = {"org_id": org_b, "user_id": user_b, "role": "owner"}

    row_a = {"id": org_a, "name": "Alice's Workspace", "role": "owner"}
    row_b = {"id": org_b, "name": "Bob's Workspace", "role": "owner"}

    async def _fake_fetch(query: str, *args: Any) -> list[dict[str, Any]]:
        q = query.upper()
        if "ORG_MEMBERS" in q and "JOIN" in q:
            uid = str(args[0])
            if uid == user_a:
                return [row_a]
            if uid == user_b:
                return [row_b]
        return []

    async def _fake_fetchrow(query: str, *args: Any) -> dict[str, Any] | None:
        q = query.upper()
        if "ORG_MEMBERS" in q and "JOIN" in q:
            uid = str(args[0])
            oid = str(args[1])
            if uid == user_a and oid == org_a:
                return row_a
            if uid == user_b and oid == org_b:
                return row_b
        return None

    with (
        patch("app.routes.orgs.fetch", side_effect=_fake_fetch),
        patch("app.routes.orgs.fetchrow", side_effect=_fake_fetchrow),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
            follow_redirects=False,
        ) as ac:
            yield ac, user_a, org_a, user_b, org_b


# ---------------------------------------------------------------------------
# 1. Auth required
# ---------------------------------------------------------------------------


class TestOrgsAuth:
    @pytest.mark.asyncio
    async def test_list_orgs_requires_auth(self, orgs_client):
        ac, _, _ = orgs_client
        resp = await ac.get("/api/v1/orgs")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_get_org_requires_auth(self, orgs_client):
        ac, _, org_id = orgs_client
        resp = await ac.get(f"/api/v1/orgs/{org_id}")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_create_org_requires_auth(self, orgs_client):
        ac, _, _ = orgs_client
        resp = await ac.post("/api/v1/orgs", json={"name": "New Org"})
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 2. GET /orgs — list memberships
# ---------------------------------------------------------------------------


class TestListOrgs:
    @pytest.mark.asyncio
    async def test_list_orgs_returns_200(self, orgs_client):
        ac, user_id, _ = orgs_client
        resp = await ac.get("/api/v1/orgs", headers=_auth_headers(user_id))
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_list_orgs_returns_orgs_key(self, orgs_client):
        ac, user_id, _ = orgs_client
        resp = await ac.get("/api/v1/orgs", headers=_auth_headers(user_id))
        body = resp.json()
        assert "orgs" in body
        assert isinstance(body["orgs"], list)

    @pytest.mark.asyncio
    async def test_list_orgs_contains_personal_org(self, orgs_client):
        """The user's personal org must appear in the list."""
        ac, user_id, org_id = orgs_client
        resp = await ac.get("/api/v1/orgs", headers=_auth_headers(user_id))
        body = resp.json()
        ids = [o["id"] for o in body["orgs"]]
        assert org_id in ids, f"Expected {org_id!r} in {ids}"

    @pytest.mark.asyncio
    async def test_list_orgs_entry_has_required_fields(self, orgs_client):
        ac, user_id, _ = orgs_client
        resp = await ac.get("/api/v1/orgs", headers=_auth_headers(user_id))
        body = resp.json()
        for org in body["orgs"]:
            assert "id" in org, f"Missing 'id': {org}"
            assert "name" in org, f"Missing 'name': {org}"
            assert "role" in org, f"Missing 'role': {org}"

    @pytest.mark.asyncio
    async def test_list_orgs_role_is_string(self, orgs_client):
        ac, user_id, _ = orgs_client
        resp = await ac.get("/api/v1/orgs", headers=_auth_headers(user_id))
        body = resp.json()
        for org in body["orgs"]:
            assert isinstance(org["role"], str)

    @pytest.mark.asyncio
    async def test_list_orgs_owner_role(self, orgs_client):
        """Creator of a personal org should have role 'owner'."""
        ac, user_id, _ = orgs_client
        resp = await ac.get("/api/v1/orgs", headers=_auth_headers(user_id))
        body = resp.json()
        roles = [o["role"] for o in body["orgs"]]
        assert "owner" in roles, f"Expected 'owner' role, got {roles}"


# ---------------------------------------------------------------------------
# 3. Org-scoping: second user cannot see first user's org
# ---------------------------------------------------------------------------


class TestOrgScoping:
    @pytest.mark.asyncio
    async def test_user_b_cannot_see_user_a_org_in_list(self, two_user_client):
        ac, user_a, org_a, user_b, org_b = two_user_client
        resp = await ac.get("/api/v1/orgs", headers=_auth_headers(user_b))
        body = resp.json()
        ids = [o["id"] for o in body["orgs"]]
        assert org_a not in ids, f"User B should not see User A's org; ids={ids}"

    @pytest.mark.asyncio
    async def test_user_a_cannot_see_user_b_org_in_list(self, two_user_client):
        ac, user_a, org_a, user_b, org_b = two_user_client
        resp = await ac.get("/api/v1/orgs", headers=_auth_headers(user_a))
        body = resp.json()
        ids = [o["id"] for o in body["orgs"]]
        assert org_b not in ids, f"User A should not see User B's org; ids={ids}"

    @pytest.mark.asyncio
    async def test_user_b_get_org_a_returns_404(self, two_user_client):
        """GET /orgs/{org_a} as user_b must return 404, not 403."""
        ac, user_a, org_a, user_b, org_b = two_user_client
        resp = await ac.get(f"/api/v1/orgs/{org_a}", headers=_auth_headers(user_b))
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 4. GET /orgs/{id}
# ---------------------------------------------------------------------------


class TestGetOrg:
    @pytest.mark.asyncio
    async def test_get_own_org_returns_200(self, orgs_client):
        ac, user_id, org_id = orgs_client
        resp = await ac.get(f"/api/v1/orgs/{org_id}", headers=_auth_headers(user_id))
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_get_own_org_returns_correct_id(self, orgs_client):
        ac, user_id, org_id = orgs_client
        resp = await ac.get(f"/api/v1/orgs/{org_id}", headers=_auth_headers(user_id))
        body = resp.json()
        assert body["id"] == org_id

    @pytest.mark.asyncio
    async def test_get_own_org_has_name_and_role(self, orgs_client):
        ac, user_id, org_id = orgs_client
        resp = await ac.get(f"/api/v1/orgs/{org_id}", headers=_auth_headers(user_id))
        body = resp.json()
        assert "name" in body
        assert "role" in body

    @pytest.mark.asyncio
    async def test_get_nonexistent_org_returns_404(self, orgs_client):
        ac, user_id, _ = orgs_client
        fake_org_id = str(uuid.uuid4())
        resp = await ac.get(f"/api/v1/orgs/{fake_org_id}", headers=_auth_headers(user_id))
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 5. POST /orgs — create a new org
# ---------------------------------------------------------------------------


class TestCreateOrg:
    @pytest.mark.asyncio
    async def test_create_org_returns_201(self, orgs_client):
        ac, user_id, _ = orgs_client
        with patch("app.routes.orgs.execute", new=AsyncMock(return_value="INSERT 0 1")):
            resp = await ac.post(
                "/api/v1/orgs",
                json={"name": "New Team Org"},
                headers=_auth_headers(user_id),
            )
        assert resp.status_code == 201

    @pytest.mark.asyncio
    async def test_create_org_returns_name_and_role(self, orgs_client):
        ac, user_id, _ = orgs_client
        with patch("app.routes.orgs.execute", new=AsyncMock(return_value="INSERT 0 1")):
            resp = await ac.post(
                "/api/v1/orgs",
                json={"name": "New Team Org"},
                headers=_auth_headers(user_id),
            )
        body = resp.json()
        assert body["name"] == "New Team Org"
        assert body["role"] == "owner"
        assert "id" in body

    @pytest.mark.asyncio
    async def test_create_org_missing_name_returns_422(self, orgs_client):
        ac, user_id, _ = orgs_client
        resp = await ac.post(
            "/api/v1/orgs",
            json={},
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 422
