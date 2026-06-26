"""Security tests for the scope-resolution endpoint, access-grants CRUD, and
the RLS policy cardinality cap.

Coverage
--------
GET /auth/scope
  * Embed token with a parent value hierarchy-expands into effective_policies.
  * raw `policies` vs `effective_policies` differ when hierarchy/grants present.
  * `policies` come from the verified TOKEN only — a request body is ignored.
  * A non-expired access_grant for the caller's subject shows up in
    effective_policies (token policies ∪ stored grants, per dimension).

/access-grants CRUD
  * Org-scoped + admin-gated (member/viewer → 403; owner/admin → ok).
  * Cross-org grant id → 404 (not 403).
  * Create → list round-trips.

RLS cardinality cap
  * An over-cap IN-list policy → 400 (fail-closed, AppError rls_policy_too_large).
  * An under-cap policy is unchanged (predicate built normally).

Strategy
--------
- `/auth/scope`: override the `verified_identity` dependency to inject a known
  VerifiedIdentity (first-party AND embed) — policies/org come from the token,
  never the body. Hierarchy resolver + grants store swapped to in-memory doubles.
- `/access-grants`: real first-party JWTs + InMemoryRepo role seeding; grants
  store swapped to an in-memory double (the hermetic FakeDB does not emulate the
  access_grants table).
- cap: direct unit calls into the planner (`_make_in_predicate`,
  `expand_rls_policies`) with a monkeypatched settings cap.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef")
os.environ.setdefault("JWT_ACCESS_TTL_MIN", "15")
os.environ.setdefault("GOOGLE_CLIENT_ID", "fake-gid")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "fake-gsecret")
os.environ.setdefault("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/v1/auth/google/callback")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("ENV", "test")

from app.auth.deps import verified_identity  # noqa: E402
from app.auth.jwt import mint_access_token  # noqa: E402
from app.auth.verify import VerifiedIdentity  # noqa: E402
from app.connectors import rls_hierarchy  # noqa: E402
from app.repos.memory import InMemoryRepo  # noqa: E402
from app.repos.provider import set_repo  # noqa: E402


# ---------------------------------------------------------------------------
# In-memory grants store double (FakeDB does not emulate access_grants).
# ---------------------------------------------------------------------------


class _InMemoryGrantsStore:
    """Dict-backed GrantsStore double honouring the same org-scoping contract."""

    def __init__(self) -> None:
        # id -> grant dict
        self._rows: dict[str, dict[str, Any]] = {}

    def _serialize(self, r: dict[str, Any]) -> dict[str, Any]:
        out = dict(r)
        ea = out.get("expires_at")
        out["expires_at"] = ea.isoformat() if hasattr(ea, "isoformat") else ea
        ca = out.get("created_at")
        out["created_at"] = ca.isoformat() if hasattr(ca, "isoformat") else ca
        return out

    async def list_for_subject(self, org_id, subject_type, subject_id):
        return [
            self._serialize(r)
            for r in self._rows.values()
            if r["org_id"] == org_id
            and r["subject_type"] == subject_type
            and r["subject_id"] == subject_id
        ]

    async def effective_for_subject(self, org_id, subject_type, subject_id, *, now=None):
        now = now or datetime.now(tz=timezone.utc)
        out: dict[str, list[str]] = {}
        for r in self._rows.values():
            if r["org_id"] != org_id or r["subject_type"] != subject_type or r["subject_id"] != subject_id:
                continue
            ea = r.get("expires_at")
            if ea is not None:
                if not isinstance(ea, datetime):
                    ea = datetime.fromisoformat(str(ea))
                if ea.tzinfo is None:
                    ea = ea.replace(tzinfo=timezone.utc)
                if ea <= now:
                    continue
            out.setdefault(r["dimension"], []).append(r["value"])
        return out

    async def create(self, org_id, subject_type, subject_id, dimension, value,
                     expires_at=None, created_by=None):
        # Enforce the unique tuple (idempotent refresh of expires_at).
        for r in self._rows.values():
            if (r["org_id"] == org_id and r["subject_type"] == subject_type
                    and r["subject_id"] == subject_id and r["dimension"] == dimension
                    and r["value"] == value):
                r["expires_at"] = expires_at
                return self._serialize(r)
        gid = str(uuid.uuid4())
        row = {
            "id": gid, "org_id": org_id, "subject_type": subject_type,
            "subject_id": subject_id, "dimension": dimension, "value": value,
            "expires_at": expires_at, "created_at": datetime.now(tz=timezone.utc),
        }
        self._rows[gid] = row
        return self._serialize(row)

    async def delete(self, grant_id, org_id):
        r = self._rows.get(grant_id)
        if r is None or r["org_id"] != org_id:
            return False
        del self._rows[grant_id]
        return True


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def grants_store():
    from app.access import grants_store as gs_module

    store = _InMemoryGrantsStore()
    gs_module.set_grants_store(store)  # type: ignore[arg-type]
    yield store
    gs_module.reset_for_tests()


@pytest_asyncio.fixture
async def hierarchy():
    resolver = rls_hierarchy.InMemoryHierarchyResolver()
    rls_hierarchy.set_hierarchy_resolver(resolver)
    yield resolver
    rls_hierarchy.reset_for_tests()


def _make_identity(
    *,
    kind: str = "embed",
    user_id: str,
    org: str,
    policies: dict[str, Any],
    scope: list[str] | None = None,
) -> VerifiedIdentity:
    return VerifiedIdentity(
        kind=kind,
        user_id=user_id,
        org=org,
        project=None,
        roles=[],
        policies=dict(policies),
        scope=list(scope or ["read:*"]),
        embed_origin=None,
        datastore=None,
        raw_claims={"sub": user_id, "org": org, "policies": dict(policies)},
    )


@pytest_asyncio.fixture
async def scope_client(app):
    """Client whose verified_identity is overridable per test."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver",
                           follow_redirects=False) as client:
        yield client, app


# ---------------------------------------------------------------------------
# GET /auth/scope
# ---------------------------------------------------------------------------


class TestScopeEndpoint:
    @pytest.mark.asyncio
    async def test_embed_token_hierarchy_expands(self, scope_client, hierarchy, grants_store):
        client, app = scope_client
        org = str(uuid.uuid4())
        # region=Gauteng has children JHB, PTA registered in the org hierarchy.
        hierarchy.add_sync(org, "region", "Gauteng", ["Gauteng", "JHB", "PTA"])

        ident = _make_identity(kind="embed", user_id="embed-sub-1", org=org,
                               policies={"region": "Gauteng"})
        app.dependency_overrides[verified_identity] = lambda: ident
        try:
            resp = await client.get("/api/v1/auth/scope")
        finally:
            app.dependency_overrides.pop(verified_identity, None)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["org"] == org
        assert body["policies"] == {"region": "Gauteng"}
        assert set(body["effective_policies"]["region"]) == {"Gauteng", "JHB", "PTA"}
        assert body["expanded"] is True

    @pytest.mark.asyncio
    async def test_raw_and_effective_differ_with_hierarchy(self, scope_client, hierarchy, grants_store):
        client, app = scope_client
        org = str(uuid.uuid4())
        hierarchy.add_sync(org, "region", "WC", ["WC", "CPT"])
        ident = _make_identity(kind="embed", user_id="s2", org=org,
                               policies={"region": "WC"})
        app.dependency_overrides[verified_identity] = lambda: ident
        try:
            resp = await client.get("/api/v1/auth/scope")
        finally:
            app.dependency_overrides.pop(verified_identity, None)

        body = resp.json()
        # raw is the scalar; effective is the expanded list — they DIFFER.
        assert body["policies"]["region"] == "WC"
        assert body["effective_policies"]["region"] != [body["policies"]["region"]]
        assert set(body["effective_policies"]["region"]) == {"WC", "CPT"}

    @pytest.mark.asyncio
    async def test_no_expansion_when_no_hierarchy(self, scope_client, hierarchy, grants_store):
        client, app = scope_client
        org = str(uuid.uuid4())
        ident = _make_identity(kind="embed", user_id="s3", org=org,
                               policies={"tenant_id": "acme"})
        app.dependency_overrides[verified_identity] = lambda: ident
        try:
            resp = await client.get("/api/v1/auth/scope")
        finally:
            app.dependency_overrides.pop(verified_identity, None)

        body = resp.json()
        assert body["policies"] == {"tenant_id": "acme"}
        assert body["effective_policies"] == {"tenant_id": ["acme"]}
        assert body["expanded"] is False

    @pytest.mark.asyncio
    async def test_policies_come_from_token_not_body(self, scope_client, hierarchy, grants_store):
        client, app = scope_client
        org = str(uuid.uuid4())
        ident = _make_identity(kind="embed", user_id="s4", org=org,
                               policies={"region": "WC"})
        app.dependency_overrides[verified_identity] = lambda: ident
        try:
            # Attacker tries to widen via the request body — MUST be ignored.
            resp = await client.request(
                "GET", "/api/v1/auth/scope",
                json={"policies": {"region": "EVERYTHING"}, "org": "other-org"},
            )
        finally:
            app.dependency_overrides.pop(verified_identity, None)

        body = resp.json()
        assert body["org"] == org  # from token, not body
        assert body["policies"] == {"region": "WC"}  # from token, not body
        assert body["effective_policies"]["region"] == ["WC"]

    @pytest.mark.asyncio
    async def test_access_grant_merges_into_effective(self, scope_client, hierarchy, grants_store):
        client, app = scope_client
        org = str(uuid.uuid4())
        # Subject has a token policy region=WC AND a stored grant region=GP.
        await grants_store.create(org, "embed_sub", "s5", "region", "GP")
        ident = _make_identity(kind="embed", user_id="s5", org=org,
                               policies={"region": "WC"})
        app.dependency_overrides[verified_identity] = lambda: ident
        try:
            resp = await client.get("/api/v1/auth/scope")
        finally:
            app.dependency_overrides.pop(verified_identity, None)

        body = resp.json()
        assert set(body["effective_policies"]["region"]) == {"WC", "GP"}
        assert body["expanded"] is True

    @pytest.mark.asyncio
    async def test_expired_grant_not_merged(self, scope_client, hierarchy, grants_store):
        client, app = scope_client
        org = str(uuid.uuid4())
        past = datetime.now(tz=timezone.utc) - timedelta(days=1)
        await grants_store.create(org, "embed_sub", "s6", "region", "GP", expires_at=past)
        ident = _make_identity(kind="embed", user_id="s6", org=org,
                               policies={"region": "WC"})
        app.dependency_overrides[verified_identity] = lambda: ident
        try:
            resp = await client.get("/api/v1/auth/scope")
        finally:
            app.dependency_overrides.pop(verified_identity, None)

        body = resp.json()
        assert body["effective_policies"]["region"] == ["WC"]  # expired grant ignored


# ---------------------------------------------------------------------------
# /access-grants CRUD
# ---------------------------------------------------------------------------


def _make_user(user_id: str, email: str) -> dict[str, Any]:
    return {
        "id": user_id, "email": email, "name": "U", "avatar_url": None,
        "email_verified": True, "created_at": "2024-01-01T00:00:00+00:00",
    }


def _hdr(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


@pytest_asyncio.fixture
async def crud_env(app, fake_db, grants_store):
    repo = InMemoryRepo()
    set_repo(repo)

    org_id = str(uuid.uuid4())
    other_org = str(uuid.uuid4())

    owner_id = str(uuid.uuid4())
    fake_db.users[owner_id] = _make_user(owner_id, "owner@example.com")
    repo.seed_org_member(org_id=org_id, user_id=owner_id, role="owner")

    member_id = str(uuid.uuid4())
    fake_db.users[member_id] = _make_user(member_id, "member@example.com")
    repo.seed_org_member(org_id=org_id, user_id=member_id, role="member")

    # A user in a DIFFERENT org (for cross-org 404 test).
    other_owner = str(uuid.uuid4())
    fake_db.users[other_owner] = _make_user(other_owner, "other@example.com")
    repo.seed_org_member(org_id=other_org, user_id=other_owner, role="owner")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver",
                           follow_redirects=False) as client:
        yield client, org_id, other_org, owner_id, member_id, other_owner, grants_store
    set_repo(None)


class TestAccessGrantsCrud:
    @pytest.mark.asyncio
    async def test_owner_can_create_and_list(self, crud_env):
        client, org_id, _other, owner_id, _member, _other_owner, _store = crud_env
        resp = await client.post(
            "/api/v1/access-grants",
            json={"subject_type": "user", "subject_id": "u-123",
                  "dimension": "region", "value": "GP"},
            headers=_hdr(owner_id),
        )
        assert resp.status_code == 201, resp.text
        grant = resp.json()["grant"]
        assert grant["dimension"] == "region" and grant["value"] == "GP"

        listed = await client.get(
            "/api/v1/access-grants?subject_type=user&subject_id=u-123",
            headers=_hdr(owner_id),
        )
        assert listed.status_code == 200
        grants = listed.json()["grants"]
        assert len(grants) == 1 and grants[0]["value"] == "GP"

    @pytest.mark.asyncio
    async def test_member_cannot_create(self, crud_env):
        client, org_id, _other, _owner, member_id, *_ = crud_env
        resp = await client.post(
            "/api/v1/access-grants",
            json={"subject_type": "user", "subject_id": "u-1",
                  "dimension": "region", "value": "GP"},
            headers=_hdr(member_id),
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["error"]["code"] == "forbidden"

    @pytest.mark.asyncio
    async def test_member_cannot_delete(self, crud_env):
        client, org_id, _other, owner_id, member_id, *_rest = crud_env
        store = _rest[-1]
        g = await store.create(org_id, "user", "u-2", "region", "GP")
        resp = await client.delete(f"/api/v1/access-grants/{g['id']}", headers=_hdr(member_id))
        assert resp.status_code == 403, resp.text

    @pytest.mark.asyncio
    async def test_owner_can_delete(self, crud_env):
        client, org_id, _other, owner_id, *_rest = crud_env
        store = _rest[-1]
        g = await store.create(org_id, "user", "u-3", "region", "GP")
        resp = await client.delete(f"/api/v1/access-grants/{g['id']}", headers=_hdr(owner_id))
        assert resp.status_code == 204, resp.text

    @pytest.mark.asyncio
    async def test_cross_org_delete_is_404_not_403(self, crud_env):
        client, org_id, other_org, owner_id, _member, other_owner, store = crud_env
        # Grant lives in `other_org`; our org owner must NOT see it (404, not 403).
        g = await store.create(other_org, "user", "u-x", "region", "GP")
        resp = await client.delete(f"/api/v1/access-grants/{g['id']}", headers=_hdr(owner_id))
        assert resp.status_code == 404, resp.text
        assert resp.json()["error"]["code"] == "not_found"

    @pytest.mark.asyncio
    async def test_list_is_org_scoped(self, crud_env):
        client, org_id, other_org, owner_id, _member, other_owner, store = crud_env
        await store.create(other_org, "user", "u-y", "region", "SECRET")
        listed = await client.get(
            "/api/v1/access-grants?subject_type=user&subject_id=u-y",
            headers=_hdr(owner_id),
        )
        assert listed.status_code == 200
        # Our org sees nothing for that subject — the other org's grant is invisible.
        assert listed.json()["grants"] == []

    @pytest.mark.asyncio
    async def test_grant_shows_up_in_scope(self, scope_client, hierarchy, grants_store):
        # End-to-end: a stored grant for a first-party user appears in /auth/scope.
        client, app = scope_client
        org = str(uuid.uuid4())
        await grants_store.create(org, "user", "user-99", "region", "KZN")
        ident = _make_identity(kind="access", user_id="user-99", org=org,
                               policies={"region": "WC"})
        app.dependency_overrides[verified_identity] = lambda: ident
        try:
            resp = await client.get("/api/v1/auth/scope")
        finally:
            app.dependency_overrides.pop(verified_identity, None)
        body = resp.json()
        assert set(body["effective_policies"]["region"]) == {"WC", "KZN"}


# ---------------------------------------------------------------------------
# RLS cardinality cap
# ---------------------------------------------------------------------------


class TestCardinalityCap:
    def test_under_cap_in_list_unchanged(self, monkeypatch):
        from app.connectors import planner

        monkeypatch.setattr(planner, "_max_policy_values", lambda: 10)
        node = planner._make_in_predicate("region", ["WC", "GP", "KZN"])
        sql = node.sql(dialect="postgres")
        assert "region IN" in sql.replace("  ", " ")
        assert "WC" in sql and "GP" in sql and "KZN" in sql

    def test_over_cap_in_list_fails_closed(self, monkeypatch):
        from app.connectors import planner
        from app.errors import AppError

        monkeypatch.setattr(planner, "_max_policy_values", lambda: 5)
        with pytest.raises(AppError) as exc:
            planner._make_in_predicate("region", [f"v{i}" for i in range(6)])
        assert exc.value.code == "rls_policy_too_large"
        assert exc.value.status == 400

    @pytest.mark.asyncio
    async def test_over_cap_expansion_fails_closed(self, monkeypatch, hierarchy):
        from app.connectors import planner
        from app.errors import AppError

        monkeypatch.setattr(planner, "_max_policy_values", lambda: 3)
        org = str(uuid.uuid4())
        # A scalar that expands to 5 children — exceeds the cap of 3.
        hierarchy.add_sync(org, "region", "ALL", ["a", "b", "c", "d", "e"])
        with pytest.raises(AppError) as exc:
            await planner.expand_rls_policies({"region": "ALL"}, org)
        assert exc.value.code == "rls_policy_too_large"

    @pytest.mark.asyncio
    async def test_under_cap_expansion_ok(self, monkeypatch, hierarchy):
        from app.connectors import planner

        monkeypatch.setattr(planner, "_max_policy_values", lambda: 10)
        org = str(uuid.uuid4())
        hierarchy.add_sync(org, "region", "GP", ["JHB", "PTA"])
        out = await planner.expand_rls_policies({"region": "GP"}, org)
        assert set(out["region"]) == {"JHB", "PTA"}

    def test_default_cap_is_5000(self):
        from app.config import get_settings

        assert get_settings().NUBI_RLS_MAX_POLICY_VALUES == 5000
