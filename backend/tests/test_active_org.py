"""Tests for multi-tenant org switching via the X-Org-Id header.

These tests verify that:

1. When ``X-Org-Id`` is omitted, the user's default (first) org is used.
2. When ``X-Org-Id`` is set to an org the user is a member of, that org's
   resources are returned.
3. When ``X-Org-Id`` is set to an org the user is NOT a member of, a 403
   is returned (not a 404 — we don't leak whether the org exists).
4. The org context is enforced end-to-end: creating a resource in org A via
   ``X-Org-Id: <org-A-id>`` and then listing without the header (default org B)
   does NOT return that resource.

Strategy
--------
- Use ``InMemoryRepo`` injected via ``set_repo()`` — no live DB required.
- Seed two orgs (org A and org B) in the repo and in ``fake_db``.
- The primary user (alice) belongs to BOTH orgs.
- Requests without ``X-Org-Id`` resolve to alice's *default* org (the first
  one seeded, which is org A by the InMemoryRepo's ``get_org_for_user``).
- A second user (bob) belongs only to org B — used to test the 403 case.
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(user_id: str | None = None, email: str = "alice@example.com") -> dict[str, Any]:
    return {
        "id": user_id or str(uuid.uuid4()),
        "email": email,
        "name": email.split("@")[0].capitalize(),
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


def _auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


def _org_header(org_id: str) -> dict[str, str]:
    return {"X-Org-Id": org_id}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def two_org_setup(app, fake_db):
    """Yield (client, alice_id, org_a_id, org_b_id, repo).

    Alice belongs to BOTH org A and org B.
    Bob belongs to org B ONLY (used to test the 403 path).
    Org A is alice's *default* org (seeded first so InMemoryRepo picks it).
    """
    repo = InMemoryRepo()
    set_repo(repo)

    alice_id = str(uuid.uuid4())
    org_a_id = str(uuid.uuid4())
    org_b_id = str(uuid.uuid4())
    bob_id = str(uuid.uuid4())

    # Seed users in FakeDB so current_user dependency resolves them.
    fake_db.users[alice_id] = _make_user(alice_id, "alice@example.com")
    fake_db.users[bob_id] = _make_user(bob_id, "bob@example.com")

    # Alice is a member of BOTH orgs; seed org_a first so it becomes default.
    repo.seed_org_member(org_id=org_a_id, user_id=alice_id)
    repo.seed_org_member(org_id=org_b_id, user_id=alice_id)

    # Bob is a member of org B only.
    repo.seed_org_member(org_id=org_b_id, user_id=bob_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as client:
        yield client, alice_id, org_a_id, org_b_id, bob_id, repo

    set_repo(None)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestOrgSwitching:
    """X-Org-Id header switches the resource context to the named org."""

    @pytest.mark.asyncio
    async def test_no_header_uses_default_org(self, two_org_setup):
        """Without X-Org-Id the user's default (first) org is used."""
        client, alice_id, org_a_id, org_b_id, bob_id, repo = two_org_setup

        # Create a board in org A (no header → default org A).
        resp = await client.post(
            "/api/v1/boards",
            json={"name": "Org-A Board"},
            headers=_auth(alice_id),
        )
        assert resp.status_code == 201, resp.text
        board = resp.json()
        assert board["org_id"] == org_a_id, (
            f"Expected org_id={org_a_id} (default), got {board['org_id']}"
        )

    @pytest.mark.asyncio
    async def test_header_switches_to_org_b(self, two_org_setup):
        """X-Org-Id: <org_b_id> switches alice's context to org B."""
        client, alice_id, org_a_id, org_b_id, bob_id, repo = two_org_setup

        resp = await client.post(
            "/api/v1/boards",
            json={"name": "Org-B Board"},
            headers={**_auth(alice_id), **_org_header(org_b_id)},
        )
        assert resp.status_code == 201, resp.text
        board = resp.json()
        assert board["org_id"] == org_b_id, (
            f"Expected org_id={org_b_id}, got {board['org_id']}"
        )

    @pytest.mark.asyncio
    async def test_resources_are_scoped_to_header_org(self, two_org_setup):
        """Resources created in org B are not visible when listing org A (no header)."""
        client, alice_id, org_a_id, org_b_id, bob_id, repo = two_org_setup

        # Create in org B explicitly.
        create_b = await client.post(
            "/api/v1/boards",
            json={"name": "Only In B"},
            headers={**_auth(alice_id), **_org_header(org_b_id)},
        )
        assert create_b.status_code == 201

        # List with no header → default org A.
        list_a = await client.get("/api/v1/boards", headers=_auth(alice_id))
        assert list_a.status_code == 200
        names_a = [b["name"] for b in list_a.json()]
        assert "Only In B" not in names_a, (
            "Org B resource leaked into org A listing"
        )

        # List with X-Org-Id: org_b → must include the board.
        list_b = await client.get(
            "/api/v1/boards",
            headers={**_auth(alice_id), **_org_header(org_b_id)},
        )
        assert list_b.status_code == 200
        names_b = [b["name"] for b in list_b.json()]
        assert "Only In B" in names_b, (
            "Org B resource not visible when X-Org-Id: org_b is set"
        )

    @pytest.mark.asyncio
    async def test_non_member_org_returns_403(self, two_org_setup):
        """X-Org-Id set to an org the user is NOT a member of → 403."""
        client, alice_id, org_a_id, org_b_id, bob_id, repo = two_org_setup

        non_member_org = str(uuid.uuid4())

        resp = await client.get(
            "/api/v1/boards",
            headers={**_auth(alice_id), "X-Org-Id": non_member_org},
        )
        assert resp.status_code == 403, (
            f"Expected 403 for non-member org, got {resp.status_code}: {resp.text}"
        )

    @pytest.mark.asyncio
    async def test_bob_cannot_access_org_a_via_header(self, two_org_setup):
        """Bob (member of org B only) gets 403 when requesting org A via header."""
        client, alice_id, org_a_id, org_b_id, bob_id, repo = two_org_setup

        resp = await client.get(
            "/api/v1/boards",
            headers={**_auth(bob_id), "X-Org-Id": org_a_id},
        )
        assert resp.status_code == 403, (
            f"Bob must not access org A. Got {resp.status_code}: {resp.text}"
        )

    @pytest.mark.asyncio
    async def test_bob_can_access_org_b_via_header(self, two_org_setup):
        """Bob (member of org B) can use X-Org-Id: <org_b_id> successfully."""
        client, alice_id, org_a_id, org_b_id, bob_id, repo = two_org_setup

        resp = await client.get(
            "/api/v1/boards",
            headers={**_auth(bob_id), "X-Org-Id": org_b_id},
        )
        assert resp.status_code == 200, (
            f"Bob should be able to access org B. Got {resp.status_code}: {resp.text}"
        )

    @pytest.mark.asyncio
    async def test_create_in_correct_org_via_header(self, two_org_setup):
        """End-to-end: alice creates in org B, gets it back from org B list."""
        client, alice_id, org_a_id, org_b_id, bob_id, repo = two_org_setup

        # Create in org B.
        create = await client.post(
            "/api/v1/boards",
            json={"name": "B-Created"},
            headers={**_auth(alice_id), **_org_header(org_b_id)},
        )
        assert create.status_code == 201
        board_id = create.json()["id"]

        # Get by ID with org B header → 200.
        get_b = await client.get(
            f"/api/v1/boards/{board_id}",
            headers={**_auth(alice_id), **_org_header(org_b_id)},
        )
        assert get_b.status_code == 200
        assert get_b.json()["id"] == board_id

        # Get by ID with no header (default org A) → 404 (cross-org, no leak).
        get_a = await client.get(
            f"/api/v1/boards/{board_id}",
            headers=_auth(alice_id),
        )
        assert get_a.status_code == 404, (
            "Cross-org GET must return 404 even for alice (wrong default org)"
        )

    @pytest.mark.asyncio
    async def test_no_header_falls_back_to_first_org(self, two_org_setup):
        """Without X-Org-Id, the backend always falls back to the default org."""
        client, alice_id, org_a_id, org_b_id, bob_id, repo = two_org_setup

        # Default: create in org A.
        create_a = await client.post(
            "/api/v1/boards",
            json={"name": "Default Org Board"},
            headers=_auth(alice_id),
        )
        assert create_a.status_code == 201
        assert create_a.json()["org_id"] == org_a_id
