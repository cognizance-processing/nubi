"""Credentials stop working when their holder leaves, and stay inside their org."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import Request

from tests.security.conftest_helpers import as_user, make_tenant_app

ORG_A = str(uuid.uuid4())
ORG_B = str(uuid.uuid4())


@pytest.fixture
def stores():
    from app.auth.api_keys import InMemoryApiKeyStore, set_api_key_store_for_tests
    from app.repos.memory import InMemoryRepo
    from app.repos.provider import set_repo

    keys = InMemoryApiKeyStore()
    set_api_key_store_for_tests(keys)
    repo = InMemoryRepo()
    set_repo(repo)
    yield keys, repo
    set_api_key_store_for_tests(None)
    set_repo(None)


@pytest.mark.asyncio
async def test_api_key_stops_resolving_once_its_user_leaves_the_org(stores):
    from app.auth.deps import _resolve_opaque_credential

    keys, repo = stores
    user = str(uuid.uuid4())
    repo.seed_org_member(ORG_A, user, "member")
    raw, _row = await keys.create(user, ORG_A, "cli")

    row = await _resolve_opaque_credential(raw, None)
    assert row and row["org_id"] == ORG_A

    repo._org_members.clear()  # the user is removed from the org
    assert await _resolve_opaque_credential(raw, None) is None


@pytest.mark.asyncio
async def test_removing_a_member_revokes_their_keys(stores):
    keys, _repo = stores
    user, other = str(uuid.uuid4()), str(uuid.uuid4())
    raw_a, _ = await keys.create(user, ORG_A, "a")
    raw_b, _ = await keys.create(user, ORG_B, "b")
    raw_o, _ = await keys.create(other, ORG_A, "o")

    assert await keys.revoke_all_for_user(user, ORG_A) == 1
    assert await keys.resolve(raw_a) is None
    assert await keys.resolve(raw_b) is not None  # other org untouched
    assert await keys.resolve(raw_o) is not None  # other user untouched


@pytest.mark.asyncio
async def test_member_removal_route_revokes_credentials(stores):
    from app.routes import orgs

    keys, repo = stores
    manager, leaver = str(uuid.uuid4()), str(uuid.uuid4())
    repo.seed_org_member(ORG_A, manager, "owner")
    repo.seed_org_member(ORG_A, leaver, "member")
    raw, _ = await keys.create(leaver, ORG_A, "cli")
    grants = AsyncMock()
    grants.list_grants = AsyncMock(return_value=[{"id": "g1"}])
    grants.revoke_grant = AsyncMock(return_value=True)

    with (
        patch.object(orgs, "_require_manage", new=AsyncMock(return_value={"role": "owner"})),
        patch.object(orgs, "execute", new=AsyncMock(return_value="DELETE 1")),
        patch("app.auth.oauth.get_oauth_store", return_value=grants),
    ):
        await orgs.remove_member(ORG_A, leaver, user={"id": manager}, repo=repo)

    assert await keys.resolve(raw) is None
    grants.revoke_grant.assert_awaited_once_with("g1", leaver, ORG_A)


def test_non_members_are_not_writers():
    from app.auth.roles import _forbid_viewer
    from app.errors import AppError

    for role in (None, "viewer"):
        with pytest.raises(AppError) as exc:
            _forbid_viewer(role)
        assert exc.value.status == 403
    for role in ("member", "admin", "owner"):
        _forbid_viewer(role)


def test_pg_key_lookup_requires_current_membership():
    import inspect

    from app.auth.api_keys import PgApiKeyStore

    sql = inspect.getsource(PgApiKeyStore.resolve)
    assert "org_members" in sql


# ---------------------------------------------------------------------------
# A credential pinned to one org cannot act on another through /orgs/*
# ---------------------------------------------------------------------------


@pytest.fixture
def pinned_client():
    from fastapi.testclient import TestClient

    from app.auth.deps import current_user
    from app.repos.provider import set_repo
    from app.routes._org import api_key_org_pin

    app, _repo, users = make_tenant_app({"k": (ORG_A, "owner")}, modules=("app.routes.orgs",))

    async def _user(request: Request):
        api_key_org_pin.set(ORG_A)  # what an API key / OAuth token does
        return {"id": users["k"], "email": "k@example.com", "_api_key_org_id": ORG_A}

    app.dependency_overrides[current_user] = _user
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    api_key_org_pin.set(None)
    set_repo(None)


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", f"/api/v1/orgs/{ORG_B}"),
        ("PATCH", f"/api/v1/orgs/{ORG_B}"),
        ("DELETE", f"/api/v1/orgs/{ORG_B}"),
        ("GET", f"/api/v1/orgs/{ORG_B}/members"),
        ("POST", f"/api/v1/orgs/{ORG_B}/invites"),
        ("GET", f"/api/v1/orgs/{ORG_B}/deletion-impact"),
        ("POST", "/api/v1/orgs"),
        ("POST", "/api/v1/orgs/invites/sometoken/accept"),
    ],
)
def test_pinned_credential_cannot_reach_other_orgs(pinned_client, method, path):
    resp = pinned_client.request(method, path, json={"name": "x", "email": "a@b.co", "role": "member", "confirm_name": "x"})
    assert resp.status_code == 403, resp.text


def test_pinned_credential_can_still_read_its_own_org(pinned_client):
    from app.routes import orgs

    with patch.object(
        orgs, "_get_user_org_membership", new=AsyncMock(return_value={"id": ORG_A, "name": "A", "role": "owner"})
    ):
        assert pinned_client.get(f"/api/v1/orgs/{ORG_A}").status_code == 200
    rows = [{"id": ORG_A, "name": "A", "role": "owner"}, {"id": ORG_B, "name": "B", "role": "owner"}]
    with patch.object(orgs, "_list_user_orgs", new=AsyncMock(return_value=rows)):
        listed = pinned_client.get("/api/v1/orgs").json()["orgs"]
    assert [o["id"] for o in listed] == [ORG_A]
