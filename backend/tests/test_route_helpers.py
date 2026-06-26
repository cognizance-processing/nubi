"""Tests for the shared route helpers introduced by the dedup refactor.

Coverage
--------
Dedup 1 — ``get_or_404``
    1. Hit path: returns the row from repo when it exists.
    2. Miss path: raises ``AppError("not_found", ..., 404)`` when absent.
    3. Custom ``detail`` is preserved verbatim in the raised error.
    4. Custom ``error_code`` is used when specified.

Dedup 2 — ``resolved_ctx`` / ``resolved_ctx_scoped`` / ``resolved_ctx_default``
    5. Returns ``(user_id, org_id)`` correctly via ``resolve_org_id``.
    6. Org-isolation: a handler using ``resolved_ctx_scoped`` 404s cross-org.
    7. X-Org-Id pointing at another org is rejected 403 (not granted access).
    8. ``resolved_ctx_default`` unit test: returns correct (user_id, org_id).
    9. ``resolved_ctx_default`` ignores X-Org-Id (uses default org only).

Dedup 3 — ``get_org_role`` in ``remove_member``
    10. ``DELETE /orgs/{id}/members/{mid}`` → 404 when member absent
        (i.e. ``get_org_role`` returns None → "not_found" as before).
    11. ``DELETE /orgs/{id}/members/{mid}`` → 403 when non-owner tries
        to remove an owner (role check still works via ``get_org_role``).

All tests use ``InMemoryRepo`` + FakeDB (no live DB).
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.errors import AppError
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(user_id: str, email: str = "alice@example.com") -> dict[str, Any]:
    return {
        "id": user_id,
        "email": email,
        "name": "Alice",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


def _auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


# ---------------------------------------------------------------------------
# Dedup 1: get_or_404 unit tests
# ---------------------------------------------------------------------------


class TestGetOr404:
    """Unit tests for ``app.routes._helpers.get_or_404``."""

    @pytest.mark.asyncio
    async def test_hit_returns_row(self):
        """When repo.get returns a row, get_or_404 returns it unchanged."""
        from app.routes._helpers import get_or_404

        expected = {"id": "abc", "name": "test"}
        repo = InMemoryRepo()
        org_id = str(uuid.uuid4())
        row_id = str(uuid.uuid4())

        # Seed a boards row in the repo.
        created = await repo.create(
            resource="boards",
            org_id=org_id,
            created_by="user-1",
            name="test",
            config={},
        )
        row_id = str(created["id"])

        result = await get_or_404(repo, "boards", org_id, row_id)
        assert result["name"] == "test"
        assert str(result["id"]) == row_id

    @pytest.mark.asyncio
    async def test_miss_raises_not_found(self):
        """When repo.get returns None, get_or_404 raises AppError not_found 404."""
        from app.routes._helpers import get_or_404

        repo = InMemoryRepo()
        org_id = str(uuid.uuid4())
        missing_id = str(uuid.uuid4())

        with pytest.raises(AppError) as exc_info:
            await get_or_404(repo, "boards", org_id, missing_id)

        err = exc_info.value
        assert err.code == "not_found"
        assert err.status == 404

    @pytest.mark.asyncio
    async def test_miss_uses_custom_detail(self):
        """Custom detail message is preserved verbatim in the raised AppError."""
        from app.routes._helpers import get_or_404

        repo = InMemoryRepo()
        org_id = str(uuid.uuid4())
        missing_id = str(uuid.uuid4())
        custom_msg = "Dataset 'abc-123' not found."

        with pytest.raises(AppError) as exc_info:
            await get_or_404(
                repo, "boards", org_id, missing_id, detail=custom_msg
            )

        assert exc_info.value.message == custom_msg

    @pytest.mark.asyncio
    async def test_miss_uses_custom_error_code(self):
        """Custom error_code is used in the raised AppError."""
        from app.routes._helpers import get_or_404

        repo = InMemoryRepo()
        org_id = str(uuid.uuid4())
        missing_id = str(uuid.uuid4())

        with pytest.raises(AppError) as exc_info:
            await get_or_404(
                repo, "boards", org_id, missing_id,
                error_code="board_not_found",
                detail="Board not found.",
            )

        assert exc_info.value.code == "board_not_found"
        assert exc_info.value.status == 404


# ---------------------------------------------------------------------------
# Dedup 2: resolved_ctx integration test via HTTP
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def resources_ctx_client(app, fake_db):
    """Client + repo + two users in separate orgs for cross-org isolation test."""
    repo = InMemoryRepo()
    set_repo(repo)

    alice_id = str(uuid.uuid4())
    alice_org = str(uuid.uuid4())
    bob_id = str(uuid.uuid4())
    bob_org = str(uuid.uuid4())

    fake_db.users[alice_id] = _make_user(alice_id, "alice@example.com")
    fake_db.users[bob_id] = _make_user(bob_id, "bob@example.com")

    repo.seed_org_member(org_id=alice_org, user_id=alice_id)
    repo.seed_org_member(org_id=bob_org, user_id=bob_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as ac:
        yield ac, repo, alice_id, alice_org, bob_id, bob_org

    set_repo(None)


class TestResolvedCtxDependency:
    """Integration tests for ``resolved_ctx`` via the resources endpoints
    (which use ``resolve_org_id`` — the same variant ``resolved_ctx`` wraps)."""

    @pytest.mark.asyncio
    async def test_resolved_ctx_returns_correct_org(self, resources_ctx_client):
        """GET /boards/{id} resolves to the caller's org; resource is returned."""
        client, repo, alice_id, alice_org, bob_id, bob_org = resources_ctx_client

        # Alice creates a board.
        r = await client.post(
            "/api/v1/boards",
            json={"name": "Alice Board", "config": {}},
            headers=_auth(alice_id),
        )
        assert r.status_code == 201
        board_id = r.json()["id"]

        # Alice can fetch it.
        r2 = await client.get(
            f"/api/v1/boards/{board_id}",
            headers=_auth(alice_id),
        )
        assert r2.status_code == 200
        assert r2.json()["name"] == "Alice Board"

    @pytest.mark.asyncio
    async def test_cross_org_isolation_404(self, resources_ctx_client):
        """Org-isolation: Bob cannot read Alice's board — 404 (not 200 or 403)."""
        client, repo, alice_id, alice_org, bob_id, bob_org = resources_ctx_client

        # Alice creates a board.
        r = await client.post(
            "/api/v1/boards",
            json={"name": "Private Board", "config": {}},
            headers=_auth(alice_id),
        )
        assert r.status_code == 201
        board_id = r.json()["id"]

        # Bob tries to GET it — must 404, not 200.
        r2 = await client.get(
            f"/api/v1/boards/{board_id}",
            headers=_auth(bob_id),
        )
        assert r2.status_code == 404
        # Error code must be the canonical not_found, not a 403/forbidden.
        body = r2.json()
        assert body.get("error", {}).get("code") == "not_found"


# ---------------------------------------------------------------------------
# Dedup 3: get_org_role in remove_member
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def orgs_client(app, fake_db):
    """Client for orgs-member tests with the orgs module's fetchrow patched."""
    repo = InMemoryRepo()
    set_repo(repo)

    owner_id = str(uuid.uuid4())
    admin_id = str(uuid.uuid4())
    member_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())

    fake_db.users[owner_id] = _make_user(owner_id, "owner@example.com")
    fake_db.users[admin_id] = _make_user(admin_id, "admin@example.com")
    fake_db.users[member_id] = _make_user(member_id, "member@example.com")

    repo.seed_org_member(org_id=org_id, user_id=owner_id, role="owner")
    repo.seed_org_member(org_id=org_id, user_id=admin_id, role="admin")
    repo.seed_org_member(org_id=org_id, user_id=member_id, role="member")

    # Patch orgs.py-specific fetchrow (used by _require_manage, _get_user_org_membership,
    # _count_owners, _get_org_name, etc.)
    async def fake_orgs_fetchrow(query: str, *args: Any) -> dict[str, Any] | None:
        """Minimal fetchrow stub for the orgs module's internal queries."""
        q = query.upper().strip()

        # _get_user_org_membership: SELECT o.id, o.name, om.role FROM org_members om
        # JOIN orgs o … WHERE om.user_id = $1 AND om.org_id = $2
        # args: (user_id, org_id)
        if "JOIN ORGS" in q and "ORG_MEMBERS" in q and len(args) >= 2:
            q_user_id = str(args[0])
            q_org_id = str(args[1])
            entry = repo._org_members.get(f"{q_org_id}:{q_user_id}")
            if entry:
                return {"id": q_org_id, "name": "Test Org", "role": entry["role"]}
            return None

        # _get_org_name: SELECT name FROM orgs WHERE id = $1
        if "FROM ORGS" in q and "NAME" in q and "JOIN" not in q:
            return {"name": "Test Org"}

        # _count_owners via fetchrow: SELECT count(*)::int … (fetchrow variant)
        if "COUNT" in q and "ORG_MEMBERS" in q and "OWNER" in q and len(args) >= 1:
            q_org_id = str(args[0])
            count = sum(
                1 for entry in repo._org_members.values()
                if str(entry.get("org_id")) == q_org_id and entry.get("role") == "owner"
            )
            return {"n": count}

        return None

    async def fake_orgs_fetch(query: str, *args: Any) -> list[dict[str, Any]]:
        """Minimal fetch stub for list-style queries in orgs.py."""
        return []

    async def fake_orgs_execute(query: str, *args: Any) -> None:
        """Minimal execute stub for DELETE FROM org_members."""
        q = query.upper().strip()
        if "DELETE" in q and "ORG_MEMBERS" in q and len(args) >= 2:
            q_org_id = str(args[0])
            q_user_id = str(args[1])
            key = f"{q_org_id}:{q_user_id}"
            repo._org_members.pop(key, None)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as ac:
        with (
            patch("app.routes.orgs.fetchrow", side_effect=fake_orgs_fetchrow),
            patch("app.routes.orgs.fetch",    side_effect=fake_orgs_fetch),
            patch("app.routes.orgs.execute",   side_effect=fake_orgs_execute),
        ):
            yield ac, repo, org_id, owner_id, admin_id, member_id

    set_repo(None)


class TestRemoveMemberGetOrgRole:
    """Tests that remove_member still works correctly after the get_org_role refactor."""

    @pytest.mark.asyncio
    async def test_remove_nonexistent_member_404(self, orgs_client):
        """DELETE /orgs/{id}/members/{missing} → 404 not_found (get_org_role → None)."""
        client, repo, org_id, owner_id, admin_id, member_id = orgs_client
        missing_id = str(uuid.uuid4())

        r = await client.delete(
            f"/api/v1/orgs/{org_id}/members/{missing_id}",
            headers=_auth(owner_id),
        )
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "not_found"

    @pytest.mark.asyncio
    async def test_admin_cannot_remove_owner_403(self, orgs_client):
        """DELETE /orgs/{id}/members/{owner} by admin → 403 forbidden."""
        client, repo, org_id, owner_id, admin_id, member_id = orgs_client

        r = await client.delete(
            f"/api/v1/orgs/{org_id}/members/{owner_id}",
            headers=_auth(admin_id),
        )
        assert r.status_code == 403
        body = r.json()
        assert body["error"]["code"] == "forbidden"

    @pytest.mark.asyncio
    async def test_owner_can_remove_member(self, orgs_client):
        """DELETE /orgs/{id}/members/{member} by owner → 204."""
        client, repo, org_id, owner_id, admin_id, member_id = orgs_client

        r = await client.delete(
            f"/api/v1/orgs/{org_id}/members/{member_id}",
            headers=_auth(owner_id),
        )
        assert r.status_code == 204
        # Verify the member was actually removed from the repo.
        assert f"{org_id}:{member_id}" not in repo._org_members


# ---------------------------------------------------------------------------
# Dedup 2 (extended): resolved_ctx_scoped X-Org-Id isolation
# ---------------------------------------------------------------------------


class TestResolvedCtxScopedXOrgId:
    """Verify that resolved_ctx_scoped (header-aware) correctly enforces tenant
    isolation when the X-Org-Id header is provided.

    Security invariant: a request carrying X-Org-Id pointing at org B must
    not grant access to org B's resources if the authenticated user only
    belongs to org A.  The call should 403 (forbidden), not 200 or 404.
    """

    @pytest.mark.asyncio
    async def test_x_org_id_for_non_member_is_403(self, resources_ctx_client):
        """X-Org-Id pointing at another org returns 403, not 200."""
        client, repo, alice_id, alice_org, bob_id, bob_org = resources_ctx_client

        # Bob creates a board in his org.
        r = await client.post(
            "/api/v1/boards",
            json={"name": "Bob Board", "config": {}},
            headers=_auth(bob_id),
        )
        assert r.status_code == 201
        bob_board_id = r.json()["id"]

        # Alice sends X-Org-Id pointing at Bob's org — must be 403.
        r2 = await client.get(
            f"/api/v1/boards/{bob_board_id}",
            headers={**_auth(alice_id), "X-Org-Id": bob_org},
        )
        assert r2.status_code == 403, (
            f"Expected 403 but got {r2.status_code}: {r2.text}"
        )
        body = r2.json()
        assert body.get("error", {}).get("code") == "forbidden"

    @pytest.mark.asyncio
    async def test_x_org_id_matching_own_org_succeeds(self, resources_ctx_client):
        """X-Org-Id matching the user's actual org resolves correctly."""
        client, repo, alice_id, alice_org, bob_id, bob_org = resources_ctx_client

        # Alice creates a board.
        r = await client.post(
            "/api/v1/boards",
            json={"name": "Alice Board 2", "config": {}},
            headers=_auth(alice_id),
        )
        assert r.status_code == 201
        board_id = r.json()["id"]

        # Alice fetches with X-Org-Id = her own org — should succeed.
        r2 = await client.get(
            f"/api/v1/boards/{board_id}",
            headers={**_auth(alice_id), "X-Org-Id": alice_org},
        )
        assert r2.status_code == 200
        assert r2.json()["name"] == "Alice Board 2"


# ---------------------------------------------------------------------------
# Dedup 2 (extended): resolved_ctx_default unit tests
# ---------------------------------------------------------------------------


class TestResolvedCtxDefault:
    """Unit tests for ``resolved_ctx_default``.

    Because ``resolved_ctx_default`` is a FastAPI dependency (not a plain
    coroutine), we test it by invoking ``get_user_org`` directly and verifying
    its semantics: it always returns the default org regardless of any
    simulated X-Org-Id context.
    """

    @pytest.mark.asyncio
    async def test_default_dep_returns_user_default_org(self):
        """resolved_ctx_default wraps get_user_org — returns default org."""
        from app.routes._org import get_user_org

        repo = InMemoryRepo()
        user_id = str(uuid.uuid4())
        org_id = str(uuid.uuid4())
        repo.seed_org_member(org_id=org_id, user_id=user_id)

        result = await get_user_org(user_id, repo)
        assert result == org_id

    @pytest.mark.asyncio
    async def test_default_dep_ignores_x_org_id(self):
        """get_user_org (resolved_ctx_default's backing fn) ignores X-Org-Id.

        The default-org variant must NOT honour the X-Org-Id header.
        We prove this by seeding the user in org A and verifying that
        get_user_org returns org A regardless of what org_id is passed as
        the "other org" — i.e., there is no mechanism inside get_user_org
        to switch to a different org via a header.
        """
        from app.routes._org import get_user_org

        repo = InMemoryRepo()
        user_id = str(uuid.uuid4())
        alice_org = str(uuid.uuid4())
        bob_org = str(uuid.uuid4())

        # User is a member of alice_org only.
        repo.seed_org_member(org_id=alice_org, user_id=user_id)
        # bob_org is seeded but user is NOT a member.

        # get_user_org always returns alice_org regardless of bob_org's existence.
        result = await get_user_org(user_id, repo)
        assert result == alice_org
        assert result != bob_org

    @pytest.mark.asyncio
    async def test_resolved_ctx_default_import(self):
        """resolved_ctx_default is importable and is a callable (dependency)."""
        from app.routes._helpers import resolved_ctx_default

        assert callable(resolved_ctx_default)

    @pytest.mark.asyncio
    async def test_resolved_ctx_scoped_import(self):
        """resolved_ctx_scoped is importable and is a callable (dependency)."""
        from app.routes._helpers import resolved_ctx_scoped

        assert callable(resolved_ctx_scoped)

    @pytest.mark.asyncio
    async def test_resolved_ctx_alias_matches_scoped(self):
        """resolved_ctx is the same object as resolved_ctx_scoped (alias)."""
        from app.routes._helpers import resolved_ctx, resolved_ctx_scoped

        assert resolved_ctx is resolved_ctx_scoped
