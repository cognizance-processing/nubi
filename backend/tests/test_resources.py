"""Tests for the generic resource CRUD endpoints (M6-A).

Strategy
--------
- Use ``InMemoryRepo`` injected via ``set_repo()`` — no live DB required.
- Seed org memberships on the repo directly (``repo.seed_org_member()``).
- Obtain real JWTs by patching ``app.auth.deps.fetchrow`` to return a
  pre-seeded user dict (the same pattern the conftest auth fake uses, but
  extended to also stub the user for the resources auth check).
- The conftest ``app`` fixture patches ``app.db.*`` globally, so
  ``app.auth.deps.fetchrow`` (which resolves the user from the DB) is already
  patched — we just need to seed the user in ``fake_db`` and generate a JWT.

Coverage
--------
For the 'boards' resource:

1.  create → 201
2.  list shows the created board
3.  get → 200
4.  update → reflects change
5.  delete → 204, then get → 404
6.  GET unknown id → 404
7.  cross-org: second user in a different org cannot GET/PUT/DELETE → 404
8.  no token → 401
9.  unknown resource name → 404
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(user_id: str | None = None, email: str = "alice@example.com") -> dict[str, Any]:
    """Return a minimal user dict matching the shape from ``current_user``."""
    return {
        "id": user_id or str(uuid.uuid4()),
        "email": email,
        "name": "Alice",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


def _auth_headers(user_id: str) -> dict[str, str]:
    """Return an Authorization header with a valid JWT for *user_id*."""
    token = mint_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def resources_app(app):
    """Yield the FastAPI app with InMemoryRepo injected.

    This fixture wraps the conftest ``app`` fixture (which patches all DB
    helpers with the in-memory fake) and additionally injects an
    ``InMemoryRepo`` so that resource routes use the in-memory implementation.
    """
    repo = InMemoryRepo()
    set_repo(repo)
    yield app, repo
    # Reset after the test so subsequent tests start clean.
    set_repo(None)


@pytest_asyncio.fixture
async def resources_client(resources_app, fake_db):
    """Async HTTPX client with InMemoryRepo, pre-seeded user + org."""
    app, repo = resources_app

    # Create the primary user.
    alice_id = str(uuid.uuid4())
    alice_org_id = str(uuid.uuid4())
    alice = _make_user(user_id=alice_id, email="alice@example.com")

    # Seed user in FakeDB so current_user dependency can resolve it.
    fake_db.users[alice_id] = alice
    # Seed org membership in the InMemoryRepo.
    repo.seed_org_member(org_id=alice_org_id, user_id=alice_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as ac:
        yield ac, alice_id, alice_org_id, repo


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBoardsCrud:
    """Happy-path CRUD tests for the 'boards' resource."""

    @pytest.mark.asyncio
    async def test_create_board_returns_201(self, resources_client):
        """POST /boards → 201 with the created row."""
        client, alice_id, org_id, repo = resources_client

        resp = await client.post(
            "/api/v1/boards",
            json={"name": "My Board", "config": {"theme": "dark"}},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == "My Board"
        assert body["config"] == {"theme": "dark"}
        assert "id" in body
        assert body["org_id"] == org_id
        assert body["created_by"] == alice_id

    @pytest.mark.asyncio
    async def test_list_shows_created_board(self, resources_client):
        """After POST, GET /boards must include the new row."""
        client, alice_id, org_id, repo = resources_client

        create_resp = await client.post(
            "/api/v1/boards",
            json={"name": "Visible Board"},
            headers=_auth_headers(alice_id),
        )
        assert create_resp.status_code == 201

        list_resp = await client.get(
            "/api/v1/boards",
            headers=_auth_headers(alice_id),
        )
        assert list_resp.status_code == 200
        boards = list_resp.json()
        assert isinstance(boards, list)
        names = [b["name"] for b in boards]
        assert "Visible Board" in names

    @pytest.mark.asyncio
    async def test_get_board_returns_200(self, resources_client):
        """GET /boards/{id} → 200 with the correct row."""
        client, alice_id, org_id, repo = resources_client

        create_resp = await client.post(
            "/api/v1/boards",
            json={"name": "Gettable Board"},
            headers=_auth_headers(alice_id),
        )
        board_id = create_resp.json()["id"]

        get_resp = await client.get(
            f"/api/v1/boards/{board_id}",
            headers=_auth_headers(alice_id),
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["id"] == board_id
        assert get_resp.json()["name"] == "Gettable Board"

    @pytest.mark.asyncio
    async def test_update_board_reflects_change(self, resources_client):
        """PUT /boards/{id} → 200, updated fields reflected."""
        client, alice_id, org_id, repo = resources_client

        create_resp = await client.post(
            "/api/v1/boards",
            json={"name": "Old Name", "config": {"v": 1}},
            headers=_auth_headers(alice_id),
        )
        board_id = create_resp.json()["id"]

        update_resp = await client.put(
            f"/api/v1/boards/{board_id}",
            json={"name": "New Name", "config": {"v": 2}},
            headers=_auth_headers(alice_id),
        )
        assert update_resp.status_code == 200
        updated = update_resp.json()
        assert updated["name"] == "New Name"
        assert updated["config"] == {"v": 2}

    @pytest.mark.asyncio
    async def test_delete_returns_204_then_get_returns_404(self, resources_client):
        """DELETE /boards/{id} → 204; subsequent GET → 404."""
        client, alice_id, org_id, repo = resources_client

        create_resp = await client.post(
            "/api/v1/boards",
            json={"name": "Delete Me"},
            headers=_auth_headers(alice_id),
        )
        board_id = create_resp.json()["id"]

        del_resp = await client.delete(
            f"/api/v1/boards/{board_id}",
            headers=_auth_headers(alice_id),
        )
        assert del_resp.status_code == 204

        get_resp = await client.get(
            f"/api/v1/boards/{board_id}",
            headers=_auth_headers(alice_id),
        )
        assert get_resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_unknown_id_returns_404(self, resources_client):
        """GET /boards/{random_uuid} → 404 when no such row exists."""
        client, alice_id, org_id, repo = resources_client

        resp = await client.get(
            f"/api/v1/boards/{uuid.uuid4()}",
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 404


class TestCrossOrgIsolation:
    """Cross-org access must always return 404 — no information leak."""

    @pytest.mark.asyncio
    async def test_cross_org_get_returns_404(self, resources_app, fake_db):
        """User in org B cannot GET a board owned by org A."""
        app, repo = resources_app

        # Alice in org A
        alice_id = str(uuid.uuid4())
        alice_org = str(uuid.uuid4())
        fake_db.users[alice_id] = _make_user(alice_id, "alice@example.com")
        repo.seed_org_member(org_id=alice_org, user_id=alice_id)

        # Bob in org B
        bob_id = str(uuid.uuid4())
        bob_org = str(uuid.uuid4())
        fake_db.users[bob_id] = _make_user(bob_id, "bob@example.com")
        repo.seed_org_member(org_id=bob_org, user_id=bob_id)

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
            follow_redirects=False,
        ) as client:
            # Alice creates a board in org A.
            create_resp = await client.post(
                "/api/v1/boards",
                json={"name": "Alice's Secret"},
                headers=_auth_headers(alice_id),
            )
            assert create_resp.status_code == 201
            board_id = create_resp.json()["id"]

            # Bob tries to GET it — must get 404, not the board data.
            get_resp = await client.get(
                f"/api/v1/boards/{board_id}",
                headers=_auth_headers(bob_id),
            )
            assert get_resp.status_code == 404, (
                "Cross-org GET must return 404 (no leak)"
            )

    @pytest.mark.asyncio
    async def test_cross_org_put_returns_404(self, resources_app, fake_db):
        """User in org B cannot PUT a board owned by org A."""
        app, repo = resources_app

        alice_id = str(uuid.uuid4())
        alice_org = str(uuid.uuid4())
        fake_db.users[alice_id] = _make_user(alice_id, "alice@example.com")
        repo.seed_org_member(org_id=alice_org, user_id=alice_id)

        bob_id = str(uuid.uuid4())
        bob_org = str(uuid.uuid4())
        fake_db.users[bob_id] = _make_user(bob_id, "bob@example.com")
        repo.seed_org_member(org_id=bob_org, user_id=bob_id)

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
            follow_redirects=False,
        ) as client:
            create_resp = await client.post(
                "/api/v1/boards",
                json={"name": "Alice's Board"},
                headers=_auth_headers(alice_id),
            )
            board_id = create_resp.json()["id"]

            put_resp = await client.put(
                f"/api/v1/boards/{board_id}",
                json={"name": "Hijacked"},
                headers=_auth_headers(bob_id),
            )
            assert put_resp.status_code == 404, (
                "Cross-org PUT must return 404 (no leak)"
            )

            # Verify original is unchanged.
            get_resp = await client.get(
                f"/api/v1/boards/{board_id}",
                headers=_auth_headers(alice_id),
            )
            assert get_resp.json()["name"] == "Alice's Board"

    @pytest.mark.asyncio
    async def test_cross_org_delete_returns_404(self, resources_app, fake_db):
        """User in org B cannot DELETE a board owned by org A."""
        app, repo = resources_app

        alice_id = str(uuid.uuid4())
        alice_org = str(uuid.uuid4())
        fake_db.users[alice_id] = _make_user(alice_id, "alice@example.com")
        repo.seed_org_member(org_id=alice_org, user_id=alice_id)

        bob_id = str(uuid.uuid4())
        bob_org = str(uuid.uuid4())
        fake_db.users[bob_id] = _make_user(bob_id, "bob@example.com")
        repo.seed_org_member(org_id=bob_org, user_id=bob_id)

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
            follow_redirects=False,
        ) as client:
            create_resp = await client.post(
                "/api/v1/boards",
                json={"name": "Alice's Board"},
                headers=_auth_headers(alice_id),
            )
            board_id = create_resp.json()["id"]

            del_resp = await client.delete(
                f"/api/v1/boards/{board_id}",
                headers=_auth_headers(bob_id),
            )
            assert del_resp.status_code == 404, (
                "Cross-org DELETE must return 404 (no leak)"
            )

            # Alice's board must still exist.
            get_resp = await client.get(
                f"/api/v1/boards/{board_id}",
                headers=_auth_headers(alice_id),
            )
            assert get_resp.status_code == 200


class TestAuthGuard:
    """Unauthenticated requests must be rejected with 401."""

    @pytest.mark.asyncio
    async def test_no_token_list_returns_401(self, resources_client):
        """GET /boards without Authorization → 401."""
        client, *_ = resources_client
        resp = await client.get("/api/v1/boards")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_no_token_create_returns_401(self, resources_client):
        """POST /boards without Authorization → 401."""
        client, *_ = resources_client
        resp = await client.post("/api/v1/boards", json={"name": "x"})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_no_token_get_returns_401(self, resources_client):
        """GET /boards/{id} without Authorization → 401."""
        client, *_ = resources_client
        resp = await client.get(f"/api/v1/boards/{uuid.uuid4()}")
        assert resp.status_code == 401


class TestUnknownResource:
    """Unknown resource names in the URL must return 404."""

    @pytest.mark.asyncio
    async def test_unknown_resource_list_returns_404(self, resources_client):
        """GET /gadgets → 404 (not in the allowlist)."""
        client, alice_id, org_id, repo = resources_client
        resp = await client.get(
            "/api/v1/gadgets",
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_unknown_resource_post_returns_404(self, resources_client):
        """POST /gadgets → 404 (not in the allowlist)."""
        client, alice_id, org_id, repo = resources_client
        resp = await client.post(
            "/api/v1/gadgets",
            json={"name": "x"},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 404


class TestAllResources:
    """Smoke-test each resource name in the allowlist."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("resource", ["boards", "datastores", "queries", "widgets"])
    async def test_create_and_get_all_resources(
        self, resource: str, resources_client
    ):
        """Each allowlisted resource should support create + get."""
        client, alice_id, org_id, repo = resources_client

        create_resp = await client.post(
            f"/api/v1/{resource}",
            json={"name": f"Test {resource}"},
            headers=_auth_headers(alice_id),
        )
        assert create_resp.status_code == 201, create_resp.text
        row_id = create_resp.json()["id"]

        get_resp = await client.get(
            f"/api/v1/{resource}/{row_id}",
            headers=_auth_headers(alice_id),
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["id"] == row_id
