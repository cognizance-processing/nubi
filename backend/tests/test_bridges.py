"""M22-A: Bridges CRUD + heartbeat + org-scoping + auth tests.

What this suite verifies
------------------------
(1) POST /bridges → 201 with the created row (status='offline', last_seen_at=None).
(2) GET /bridges lists all bridges for the caller's org.
(3) GET /bridges/{id} → 200 with the correct row.
(4) DELETE /bridges/{id} → 204; subsequent GET → 404.
(5) POST /bridges/{id}/heartbeat → updates status='online' + last_seen_at.
(6) Org-scoping: org A cannot see org B's bridges (GET list and GET by id).
(7) Auth required: unauthenticated requests → 401 on all endpoints.
(8) GET unknown bridge id → 404.

Test strategy
-------------
- Import ``app.routes.bridges`` at module level to trigger self-registration
  on ``api_router`` BEFORE the conftest ``app`` fixture creates the FastAPI app.
- Use InMemoryRepo injected via set_repo() for org resolution.
- Reset the bridge store between tests via reset_bridge_store() (autouse fixture).
- First-party JWTs via mint_access_token.
- The conftest fake_db fixture seeds the user for auth resolution.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo

# Trigger self-registration on api_router so the /bridges routes are mounted
# when the conftest app fixture creates the FastAPI app.
import app.routes.bridges  # noqa: F401, E402
from app.routes.bridges import reset_bridge_store


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(user_id: str | None = None, email: str = "alice@example.com") -> dict[str, Any]:
    return {
        "id": user_id or str(uuid.uuid4()),
        "email": email,
        "name": "Alice",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


def _auth_headers(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_bridges():
    """Reset the bridge store before and after every test."""
    reset_bridge_store()
    yield
    reset_bridge_store()


@pytest_asyncio.fixture
async def bridges_app(app):
    """Yield the FastAPI app with InMemoryRepo injected."""
    repo = InMemoryRepo()
    set_repo(repo)
    yield app, repo
    set_repo(None)


@pytest_asyncio.fixture
async def bridges_client(bridges_app, fake_db):
    """HTTPX client with InMemoryRepo, pre-seeded user + org."""
    app, repo = bridges_app

    alice_id = str(uuid.uuid4())
    alice_org_id = str(uuid.uuid4())
    fake_db.users[alice_id] = _make_user(user_id=alice_id)
    repo.seed_org_member(org_id=alice_org_id, user_id=alice_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as ac:
        yield ac, alice_id, alice_org_id, repo


# ---------------------------------------------------------------------------
# (1) POST /bridges → 201 with the created row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_bridge_returns_201(bridges_client):
    """POST /bridges → 201 with status='offline' and last_seen_at=None."""
    client, user_id, org_id, repo = bridges_client

    resp = await client.post(
        "/api/v1/bridges",
        json={"name": "My Bridge", "config": {"region": "us-east-1"}},
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert body["name"] == "My Bridge"
    assert body["config"] == {"region": "us-east-1"}
    assert body["status"] == "offline"
    assert body["last_seen_at"] is None
    assert body["org_id"] == org_id
    assert body["created_by"] == user_id
    assert "id" in body
    assert "created_at" in body
    assert "updated_at" in body


# ---------------------------------------------------------------------------
# (2) GET /bridges — list shows created bridges for the org
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_bridges_shows_created(bridges_client):
    """After POST, GET /bridges includes the new bridge."""
    client, user_id, org_id, repo = bridges_client

    create_resp = await client.post(
        "/api/v1/bridges",
        json={"name": "List Test Bridge"},
        headers=_auth_headers(user_id),
    )
    assert create_resp.status_code == 201

    list_resp = await client.get(
        "/api/v1/bridges",
        headers=_auth_headers(user_id),
    )
    assert list_resp.status_code == 200
    bridges = list_resp.json()
    assert isinstance(bridges, list)
    assert any(b["name"] == "List Test Bridge" for b in bridges), (
        f"Created bridge not found in list: {bridges}"
    )


# ---------------------------------------------------------------------------
# (3) GET /bridges/{id} → 200 with correct row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_bridge_returns_200(bridges_client):
    """GET /bridges/{id} → 200 with the correct row."""
    client, user_id, org_id, repo = bridges_client

    create_resp = await client.post(
        "/api/v1/bridges",
        json={"name": "Gettable Bridge"},
        headers=_auth_headers(user_id),
    )
    bridge_id = create_resp.json()["id"]

    get_resp = await client.get(
        f"/api/v1/bridges/{bridge_id}",
        headers=_auth_headers(user_id),
    )
    assert get_resp.status_code == 200, get_resp.text
    assert get_resp.json()["id"] == bridge_id
    assert get_resp.json()["name"] == "Gettable Bridge"


# ---------------------------------------------------------------------------
# (4) DELETE /bridges/{id} → 204; subsequent GET → 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_bridge_returns_204_then_get_404(bridges_client):
    """DELETE /bridges/{id} → 204; GET after delete → 404."""
    client, user_id, org_id, repo = bridges_client

    create_resp = await client.post(
        "/api/v1/bridges",
        json={"name": "Delete Me"},
        headers=_auth_headers(user_id),
    )
    bridge_id = create_resp.json()["id"]

    del_resp = await client.delete(
        f"/api/v1/bridges/{bridge_id}",
        headers=_auth_headers(user_id),
    )
    assert del_resp.status_code == 204

    get_resp = await client.get(
        f"/api/v1/bridges/{bridge_id}",
        headers=_auth_headers(user_id),
    )
    assert get_resp.status_code == 404
    assert get_resp.json()["error"]["code"] == "bridge_not_found"


# ---------------------------------------------------------------------------
# (5) POST /bridges/{id}/heartbeat — status + last_seen_at updated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_heartbeat_updates_status_and_last_seen(bridges_client):
    """POST /bridges/{id}/heartbeat → status='online', last_seen_at is set."""
    client, user_id, org_id, repo = bridges_client

    create_resp = await client.post(
        "/api/v1/bridges",
        json={"name": "Heartbeat Bridge"},
        headers=_auth_headers(user_id),
    )
    bridge = create_resp.json()
    bridge_id = bridge["id"]

    assert bridge["status"] == "offline"
    assert bridge["last_seen_at"] is None

    hb_resp = await client.post(
        f"/api/v1/bridges/{bridge_id}/heartbeat",
        headers=_auth_headers(user_id),
    )
    assert hb_resp.status_code == 200, hb_resp.text
    updated = hb_resp.json()

    assert updated["status"] == "online", f"Expected 'online', got {updated['status']!r}"
    assert updated["last_seen_at"] is not None, "last_seen_at should be set after heartbeat"
    assert updated["id"] == bridge_id


# ---------------------------------------------------------------------------
# (6) Org-scoping: org A cannot see org B's bridges
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_org_scoping_list_isolation(bridges_app, fake_db):
    """Org A's bridges are not visible in org B's list."""
    app, repo = bridges_app

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
        # Alice creates a bridge.
        create_resp = await client.post(
            "/api/v1/bridges",
            json={"name": "Alice's Bridge"},
            headers=_auth_headers(alice_id),
        )
        assert create_resp.status_code == 201
        bridge_id = create_resp.json()["id"]

        # Bob's list should be empty.
        bob_list = await client.get(
            "/api/v1/bridges",
            headers=_auth_headers(bob_id),
        )
        assert bob_list.status_code == 200
        bob_bridges = bob_list.json()
        assert all(b["id"] != bridge_id for b in bob_bridges), (
            "Org B should not see Org A's bridges in the list"
        )


@pytest.mark.asyncio
async def test_org_scoping_get_isolation(bridges_app, fake_db):
    """Org B cannot GET a bridge that belongs to org A (returns 404, no leak)."""
    app, repo = bridges_app

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
            "/api/v1/bridges",
            json={"name": "Alice's Secret Bridge"},
            headers=_auth_headers(alice_id),
        )
        assert create_resp.status_code == 201
        bridge_id = create_resp.json()["id"]

        # Bob tries to GET Alice's bridge → must get 404, not bridge data.
        get_resp = await client.get(
            f"/api/v1/bridges/{bridge_id}",
            headers=_auth_headers(bob_id),
        )
        assert get_resp.status_code == 404, (
            f"Org B GET must return 404 (no information leak), got {get_resp.status_code}"
        )
        assert get_resp.json()["error"]["code"] == "bridge_not_found"


@pytest.mark.asyncio
async def test_org_scoping_delete_isolation(bridges_app, fake_db):
    """Org B cannot DELETE a bridge that belongs to org A (returns 404)."""
    app, repo = bridges_app

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
            "/api/v1/bridges",
            json={"name": "Alice's Protected Bridge"},
            headers=_auth_headers(alice_id),
        )
        bridge_id = create_resp.json()["id"]

        del_resp = await client.delete(
            f"/api/v1/bridges/{bridge_id}",
            headers=_auth_headers(bob_id),
        )
        assert del_resp.status_code == 404, (
            "Org B DELETE must return 404 (no cross-org delete)"
        )

        # Alice's bridge must still exist.
        get_resp = await client.get(
            f"/api/v1/bridges/{bridge_id}",
            headers=_auth_headers(alice_id),
        )
        assert get_resp.status_code == 200


# ---------------------------------------------------------------------------
# (7) Auth required — unauthenticated requests → 401
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_bridges_no_auth_returns_401(bridges_client):
    """GET /bridges without Authorization → 401."""
    client, *_ = bridges_client
    resp = await client.get("/api/v1/bridges")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_create_bridge_no_auth_returns_401(bridges_client):
    """POST /bridges without Authorization → 401."""
    client, *_ = bridges_client
    resp = await client.post("/api/v1/bridges", json={"name": "x"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_bridge_no_auth_returns_401(bridges_client):
    """GET /bridges/{id} without Authorization → 401."""
    client, *_ = bridges_client
    resp = await client.get(f"/api/v1/bridges/{uuid.uuid4()}")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_delete_bridge_no_auth_returns_401(bridges_client):
    """DELETE /bridges/{id} without Authorization → 401."""
    client, *_ = bridges_client
    resp = await client.delete(f"/api/v1/bridges/{uuid.uuid4()}")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_heartbeat_no_auth_returns_401(bridges_client):
    """POST /bridges/{id}/heartbeat without Authorization → 401."""
    client, *_ = bridges_client
    resp = await client.post(f"/api/v1/bridges/{uuid.uuid4()}/heartbeat")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# (8) GET /bridges/{unknown_id} → 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_unknown_bridge_returns_404(bridges_client):
    """GET /bridges/{random_uuid} → 404 when no such bridge exists."""
    client, user_id, *_ = bridges_client
    resp = await client.get(
        f"/api/v1/bridges/{uuid.uuid4()}",
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "bridge_not_found"


@pytest.mark.asyncio
async def test_heartbeat_unknown_bridge_returns_404(bridges_client):
    """POST /bridges/{unknown}/heartbeat → 404."""
    client, user_id, *_ = bridges_client
    resp = await client.post(
        f"/api/v1/bridges/{uuid.uuid4()}/heartbeat",
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "bridge_not_found"
