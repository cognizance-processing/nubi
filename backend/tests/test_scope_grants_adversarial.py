"""Adversarial tests for scope resolution and access grants.

Coverage
--------
_is_expired:
1. Exactly at boundary (expires_at == now) → expired.
2. 1 microsecond ago → expired.
3. 1 microsecond in future → NOT expired.
4. Naive datetime (no tzinfo) → treated as UTC.
5. String ISO datetime (past) → expired.
6. String ISO datetime (future) → not expired.
7. None → not expired.
8. Unparseable string → not expired (fail-open for unparseable).

resolve_scope:
9. Empty policies {}.
10. Dict-value (range band) stays in policies but not in effective_policies values.
11. No org_id → raw policies returned (fail-closed).
12. Same dimension in token policies + grants → union, dedup.
13. expand_rls_policies throws → fall back to baseline (NARROWER).
14. grants store throws → grants silently ignored (effective keeps expansion).
15. List policy with 0 items → effective_policies key has [].
16. Scalar policy value → normalised to [str(value)].

GrantsStore.effective_for_subject:
17. Filters expired but keeps future grants.
18. Returns {} when all grants are expired.

Access grants route:
19. List with invalid subject_type → 400.
20. Create with empty subject_id → 422.
21. Create with whitespace-only value → 422.
22. Delete non-existent grant → 404.
23. Viewer role trying to create grant → 403.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, patch
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.access.grants_store import (
    GrantsStore,
    _is_expired,
    get_grants_store,
    reset_for_tests,
    set_grants_store,
)
from app.auth.jwt import mint_access_token
from app.auth.verify import VerifiedIdentity
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW_UTC = datetime(2026, 6, 26, 12, 0, 0, tzinfo=timezone.utc)


def _identity(
    user_id: str = "user-1",
    org: str = "org-1",
    policies: dict | None = None,
    scope: list | None = None,
    kind: str = "access",
) -> VerifiedIdentity:
    return VerifiedIdentity(
        kind=kind,
        user_id=user_id,
        org=org,
        project=None,
        roles=[],
        policies=policies or {},
        scope=scope or [],
        embed_origin=None,
        datastore=None,
        raw_claims={},
    )


def _make_user(user_id: str) -> dict[str, Any]:
    return {
        "id": user_id,
        "email": f"u-{user_id[:6]}@example.com",
        "name": "Test",
        "avatar_url": None,
        "email_verified": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# 1–8. _is_expired boundary tests
# ---------------------------------------------------------------------------


class TestIsExpired:
    def test_none_is_not_expired(self):
        assert _is_expired(None) is False

    def test_exactly_at_boundary_is_expired(self):
        """expires_at == now → expired (boundary is inclusive via <=)."""
        assert _is_expired(NOW_UTC, now=NOW_UTC) is True

    def test_one_microsecond_ago_is_expired(self):
        past = NOW_UTC - timedelta(microseconds=1)
        assert _is_expired(past, now=NOW_UTC) is True

    def test_one_microsecond_future_is_not_expired(self):
        future = NOW_UTC + timedelta(microseconds=1)
        assert _is_expired(future, now=NOW_UTC) is False

    def test_naive_datetime_treated_as_utc(self):
        """Naive datetime (no tzinfo) should be treated as UTC."""
        naive_past = datetime(2020, 1, 1, 0, 0, 0)  # no tzinfo
        assert _is_expired(naive_past, now=NOW_UTC) is True

    def test_naive_future_datetime_not_expired(self):
        naive_future = datetime(2099, 1, 1, 0, 0, 0)  # no tzinfo
        assert _is_expired(naive_future, now=NOW_UTC) is False

    def test_string_iso_past_is_expired(self):
        past_str = "2020-01-01T00:00:00+00:00"
        assert _is_expired(past_str, now=NOW_UTC) is True

    def test_string_iso_future_is_not_expired(self):
        future_str = "2099-01-01T00:00:00+00:00"
        assert _is_expired(future_str, now=NOW_UTC) is False

    def test_unparseable_string_not_expired(self):
        """Unparseable string → ValueError caught → returns False (not expired)."""
        assert _is_expired("not-a-date", now=NOW_UTC) is False

    def test_string_iso_without_timezone(self):
        """ISO string without tz → naive → treated as UTC."""
        past_naive_str = "2020-06-01T10:00:00"
        assert _is_expired(past_naive_str, now=NOW_UTC) is True

    def test_far_future_not_expired(self):
        far_future = NOW_UTC + timedelta(days=365 * 100)
        assert _is_expired(far_future, now=NOW_UTC) is False

    def test_uses_real_now_when_not_supplied(self):
        """When now=None, _is_expired uses datetime.now(tz=utc)."""
        past = datetime(2020, 1, 1, tzinfo=timezone.utc)
        assert _is_expired(past) is True  # no 'now' arg — uses real now


# ---------------------------------------------------------------------------
# 9–16. resolve_scope tests
# ---------------------------------------------------------------------------


class TestResolveScope:
    @pytest.mark.asyncio
    async def test_empty_policies(self):
        """resolve_scope with empty policies returns empty effective_policies."""
        from app.access.scope import resolve_scope

        identity = _identity(policies={})

        with patch("app.connectors.planner.expand_rls_policies", new=AsyncMock(return_value={})):
            store_mock = AsyncMock()
            store_mock.effective_for_subject = AsyncMock(return_value={})
            set_grants_store(store_mock)
            result = await resolve_scope(identity)

        assert result["policies"] == {}
        assert result["effective_policies"] == {}
        reset_for_tests()

    @pytest.mark.asyncio
    async def test_dict_value_policy_not_in_effective_values(self):
        """Dict (range band) policy stays in policies but NOT as values in effective_policies."""
        from app.access.scope import resolve_scope

        policies = {"region": {"gte": "A", "lte": "Z"}}
        identity = _identity(policies=policies)

        with patch("app.connectors.planner.expand_rls_policies", new=AsyncMock(return_value={})):
            store_mock = AsyncMock()
            store_mock.effective_for_subject = AsyncMock(return_value={})
            set_grants_store(store_mock)
            result = await resolve_scope(identity)

        # policies carries the range
        assert result["policies"]["region"] == {"gte": "A", "lte": "Z"}
        # effective_policies has [] for dict values (not value-enumerable)
        assert result["effective_policies"]["region"] == []
        reset_for_tests()

    @pytest.mark.asyncio
    async def test_no_org_returns_raw_policies(self):
        """No org_id → fails closed, returns baseline (raw policies normalised)."""
        from app.access.scope import resolve_scope

        identity = _identity(org="", policies={"country": ["ZA", "NG"]})
        result = await resolve_scope(identity)

        assert result["org"] == ""
        assert result["expanded"] is False
        assert result["effective_policies"]["country"] == ["ZA", "NG"]
        # Should NOT have called expand or grants
        assert result["policies"]["country"] == ["ZA", "NG"]

    @pytest.mark.asyncio
    async def test_grants_merge_same_dimension_union_dedup(self):
        """Same dimension in token policies + grants → union, deduplicated."""
        from app.access.scope import resolve_scope

        policies = {"country": ["ZA"]}
        identity = _identity(policies=policies)

        with patch("app.connectors.planner.expand_rls_policies", new=AsyncMock(return_value={})):
            store_mock = AsyncMock()
            # Grant adds NG and ZA (ZA is a dupe)
            store_mock.effective_for_subject = AsyncMock(return_value={"country": ["ZA", "NG"]})
            set_grants_store(store_mock)
            result = await resolve_scope(identity)

        effective = result["effective_policies"]["country"]
        # ZA should appear exactly once; NG should be present
        assert effective.count("ZA") == 1
        assert "NG" in effective
        assert result["expanded"] is True
        reset_for_tests()

    @pytest.mark.asyncio
    async def test_expand_rls_throws_falls_back_to_baseline(self):
        """expand_rls_policies exception → effective = baseline (narrower, never widened)."""
        from app.access.scope import resolve_scope

        policies = {"country": ["ZA"]}
        identity = _identity(policies=policies)

        with patch(
            "app.connectors.planner.expand_rls_policies",
            new=AsyncMock(side_effect=RuntimeError("DB down")),
        ):
            store_mock = AsyncMock()
            store_mock.effective_for_subject = AsyncMock(return_value={})
            set_grants_store(store_mock)
            result = await resolve_scope(identity)

        # On expand failure, effective should equal baseline (not widened)
        assert result["effective_policies"]["country"] == ["ZA"]
        assert result["expanded"] is False
        reset_for_tests()

    @pytest.mark.asyncio
    async def test_grants_store_throws_still_returns_expansion(self):
        """Grants store exception → grants silently ignored; expanded value from hierarchy kept."""
        from app.access.scope import resolve_scope

        policies = {"country": ["ZA"]}
        identity = _identity(policies=policies)

        with patch(
            "app.connectors.planner.expand_rls_policies",
            new=AsyncMock(return_value={"country": ["ZA", "NG"]}),
        ):
            store_mock = AsyncMock()
            store_mock.effective_for_subject = AsyncMock(side_effect=RuntimeError("grants db down"))
            set_grants_store(store_mock)
            result = await resolve_scope(identity)

        # Grants threw, but expansion already succeeded — effective has expansion
        effective = result["effective_policies"]["country"]
        assert "NG" in effective
        reset_for_tests()

    @pytest.mark.asyncio
    async def test_list_policy_with_zero_items(self):
        """Empty list policy [] → effective_policies key has []."""
        from app.access.scope import resolve_scope

        policies = {"country": []}
        identity = _identity(policies=policies)

        with patch("app.connectors.planner.expand_rls_policies", new=AsyncMock(return_value={})):
            store_mock = AsyncMock()
            store_mock.effective_for_subject = AsyncMock(return_value={})
            set_grants_store(store_mock)
            result = await resolve_scope(identity)

        assert result["effective_policies"]["country"] == []
        reset_for_tests()

    @pytest.mark.asyncio
    async def test_scalar_policy_value_normalised(self):
        """Scalar policy (e.g. 'ZA') → effective_policies normalised to ['ZA']."""
        from app.access.scope import resolve_scope

        policies = {"country": "ZA"}
        identity = _identity(policies=policies)

        with patch("app.connectors.planner.expand_rls_policies", new=AsyncMock(return_value={})):
            store_mock = AsyncMock()
            store_mock.effective_for_subject = AsyncMock(return_value={})
            set_grants_store(store_mock)
            result = await resolve_scope(identity)

        assert result["effective_policies"]["country"] == ["ZA"]
        reset_for_tests()

    @pytest.mark.asyncio
    async def test_expand_returns_same_values_no_expansion_flag(self):
        """If expansion returns same values as baseline, expanded stays False."""
        from app.access.scope import resolve_scope

        policies = {"country": ["ZA"]}
        identity = _identity(policies=policies)

        with patch(
            "app.connectors.planner.expand_rls_policies",
            new=AsyncMock(return_value={"country": ["ZA"]}),
        ):
            store_mock = AsyncMock()
            store_mock.effective_for_subject = AsyncMock(return_value={})
            set_grants_store(store_mock)
            result = await resolve_scope(identity)

        assert result["expanded"] is False
        reset_for_tests()


# ---------------------------------------------------------------------------
# 17–18. GrantsStore.effective_for_subject
# ---------------------------------------------------------------------------


class InMemoryGrantsStore(GrantsStore):
    """Simple in-memory implementation for testing the effective_for_subject logic."""

    def __init__(self, grants: list[dict[str, Any]]) -> None:
        self._grants = grants

    async def list_for_subject(
        self,
        org_id: str,
        subject_type: str,
        subject_id: str,
    ) -> list[dict[str, Any]]:
        return [
            g for g in self._grants
            if g["org_id"] == org_id
            and g["subject_type"] == subject_type
            and g["subject_id"] == subject_id
        ]


class TestEffectiveForSubject:
    @pytest.mark.asyncio
    async def test_filters_expired_keeps_future(self):
        """expired grant is filtered, future grant is kept."""
        past = (NOW_UTC - timedelta(hours=1)).isoformat()
        future = (NOW_UTC + timedelta(hours=1)).isoformat()

        grants = [
            {
                "id": "g1",
                "org_id": "org-1",
                "subject_type": "user",
                "subject_id": "u-1",
                "dimension": "country",
                "value": "ZA",
                "expires_at": past,  # EXPIRED
                "created_at": None,
            },
            {
                "id": "g2",
                "org_id": "org-1",
                "subject_type": "user",
                "subject_id": "u-1",
                "dimension": "country",
                "value": "NG",
                "expires_at": future,  # ACTIVE
                "created_at": None,
            },
        ]
        store = InMemoryGrantsStore(grants)
        result = await store.effective_for_subject("org-1", "user", "u-1", now=NOW_UTC)

        assert "ZA" not in result.get("country", [])
        assert "NG" in result.get("country", [])

    @pytest.mark.asyncio
    async def test_all_expired_returns_empty(self):
        """When all grants are expired, returns {}."""
        past = (NOW_UTC - timedelta(days=1)).isoformat()
        grants = [
            {
                "id": "g1",
                "org_id": "org-1",
                "subject_type": "user",
                "subject_id": "u-1",
                "dimension": "country",
                "value": "ZA",
                "expires_at": past,
                "created_at": None,
            }
        ]
        store = InMemoryGrantsStore(grants)
        result = await store.effective_for_subject("org-1", "user", "u-1", now=NOW_UTC)
        assert result == {}

    @pytest.mark.asyncio
    async def test_no_expiry_grant_always_included(self):
        """Grant with expires_at=None is never filtered."""
        grants = [
            {
                "id": "g1",
                "org_id": "org-1",
                "subject_type": "user",
                "subject_id": "u-1",
                "dimension": "country",
                "value": "ZA",
                "expires_at": None,
                "created_at": None,
            }
        ]
        store = InMemoryGrantsStore(grants)
        result = await store.effective_for_subject("org-1", "user", "u-1", now=NOW_UTC)
        assert result == {"country": ["ZA"]}


# ---------------------------------------------------------------------------
# 19–23. Access grants route adversarial
# ---------------------------------------------------------------------------


def _make_auth_headers(user_id: str, role: str = "owner") -> dict[str, str]:
    token = mint_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def grants_client(app, fake_db):
    """ASGI client with admin user for grants tests."""
    repo = InMemoryRepo()
    set_repo(repo)

    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    fake_db.users[user_id] = _make_user(user_id)
    repo.seed_org_member(org_id=org_id, user_id=user_id, role="owner")
    token = mint_access_token(user_id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        c.headers["Authorization"] = f"Bearer {token}"
        c._org_id = org_id
        c._user_id = user_id
        yield c

    set_repo(None)
    reset_for_tests()


@pytest_asyncio.fixture
async def viewer_grants_client(app, fake_db):
    """ASGI client with viewer role (not admin/owner) for 403 tests."""
    repo = InMemoryRepo()
    set_repo(repo)

    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    fake_db.users[user_id] = _make_user(user_id)
    repo.seed_org_member(org_id=org_id, user_id=user_id, role="member")
    token = mint_access_token(user_id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        c.headers["Authorization"] = f"Bearer {token}"
        c._org_id = org_id
        yield c

    set_repo(None)
    reset_for_tests()


class TestAccessGrantsRoute:
    @pytest.mark.asyncio
    async def test_list_invalid_subject_type_returns_400(self, grants_client):
        """GET /access-grants with invalid subject_type → 400."""
        resp = await grants_client.get(
            "/api/v1/access-grants",
            params={"subject_type": "invalid_type", "subject_id": "u-1"},
        )
        assert resp.status_code == 400
        data = resp.json()
        # AppError serialises as {"error": {"code": ..., "message": ...}}
        error = data.get("error") or data
        assert error.get("code") == "invalid_subject_type"

    @pytest.mark.asyncio
    async def test_create_empty_subject_id_returns_422(self, grants_client):
        """POST /access-grants with subject_id='' → 422 (pydantic validation)."""
        resp = await grants_client.post(
            "/api/v1/access-grants",
            json={
                "subject_type": "user",
                "subject_id": "",
                "dimension": "country",
                "value": "ZA",
            },
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_whitespace_only_value_returns_422(self, grants_client):
        """POST /access-grants with value='   ' (whitespace only) → 422."""
        resp = await grants_client.post(
            "/api/v1/access-grants",
            json={
                "subject_type": "user",
                "subject_id": "u-1",
                "dimension": "country",
                "value": "   ",
            },
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_whitespace_only_subject_id_returns_422(self, grants_client):
        """POST /access-grants with subject_id='  ' → 422."""
        resp = await grants_client.post(
            "/api/v1/access-grants",
            json={
                "subject_type": "user",
                "subject_id": "   ",
                "dimension": "country",
                "value": "ZA",
            },
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_delete_nonexistent_grant_returns_404(self, grants_client):
        """DELETE /access-grants/{id} for non-existent grant → 404."""
        fake_id = str(uuid.uuid4())

        # Patch grants store so delete returns False (not found)
        from app.access.grants_store import GrantsStore

        class _FakeStore(GrantsStore):
            async def list_for_subject(self, *args, **kwargs):
                return []

            async def delete(self, grant_id, org_id):
                return False  # not found

        set_grants_store(_FakeStore())

        resp = await grants_client.delete(f"/api/v1/access-grants/{fake_id}")
        assert resp.status_code == 404

        reset_for_tests()

    @pytest.mark.asyncio
    async def test_viewer_cannot_create_grant_returns_403(self, viewer_grants_client):
        """Member (non-approver) cannot create a grant → 403."""
        resp = await viewer_grants_client.post(
            "/api/v1/access-grants",
            json={
                "subject_type": "user",
                "subject_id": "u-other",
                "dimension": "country",
                "value": "ZA",
            },
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_viewer_cannot_delete_grant_returns_403(self, viewer_grants_client):
        """Member (non-approver) cannot delete a grant → 403."""
        resp = await viewer_grants_client.delete(f"/api/v1/access-grants/{uuid.uuid4()}")
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_list_valid_user_subject_type(self, grants_client):
        """GET /access-grants with valid subject_type=user → 200."""
        # Patch store to return empty list (no DB)
        from app.access.grants_store import GrantsStore

        class _EmptyStore(GrantsStore):
            async def list_for_subject(self, *args, **kwargs):
                return []

        set_grants_store(_EmptyStore())

        resp = await grants_client.get(
            "/api/v1/access-grants",
            params={"subject_type": "user", "subject_id": "u-1"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "grants" in data
        assert isinstance(data["grants"], list)

        reset_for_tests()

    @pytest.mark.asyncio
    async def test_list_valid_embed_sub_subject_type(self, grants_client):
        """GET /access-grants with subject_type=embed_sub → 200."""
        from app.access.grants_store import GrantsStore

        class _EmptyStore(GrantsStore):
            async def list_for_subject(self, *args, **kwargs):
                return []

        set_grants_store(_EmptyStore())

        resp = await grants_client.get(
            "/api/v1/access-grants",
            params={"subject_type": "embed_sub", "subject_id": "embed-user-1"},
        )
        assert resp.status_code == 200
        reset_for_tests()
