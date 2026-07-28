"""Connector routes must operate on the org the caller is VIEWING (X-Org-Id).

Only ``GET /connectors`` used to be header-aware. Every other route resolved the
caller's DEFAULT org, so for a user who belongs to more than one org:

  - creating a connector while viewing org B wrote it to org A, where it
    promptly vanished from the list they were looking at, and
  - get / update / delete / test all 404'd in org B because they searched org A.

In other words there was no way to attach a connector to a non-default org.
These tests pin the whole surface to the header, including the security
boundaries that must NOT loosen as a result: a non-member is refused, and a
viewer cannot write just because they happen to be a writer somewhere else.
"""

from __future__ import annotations

import base64
import os
import secrets as _secrets
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


def _auth_headers(user_id: str) -> dict[str, str]:
    from app.auth.jwt import mint_access_token

    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


@pytest.fixture(autouse=True)
def _connector_secret_key():
    """Install a deterministic AES key so the secret store can seal/open."""
    from app.security.crypto import reset_keys_for_tests

    os.environ["CONNECTOR_SECRET_KEY"] = base64.b64encode(_secrets.token_bytes(32)).decode()
    os.environ["CONNECTOR_SECRET_KEY_VERSION"] = "1"
    os.environ.pop("CONNECTOR_SECRET_KEYS", None)
    reset_keys_for_tests()
    yield
    os.environ.pop("CONNECTOR_SECRET_KEY", None)
    os.environ.pop("CONNECTOR_SECRET_KEY_VERSION", None)
    reset_keys_for_tests()


@pytest_asyncio.fixture
async def two_orgs(app, fake_db):
    """A user who is an owner of TWO orgs: `default_org` (first) and `other_org`."""
    from app.repos.memory import InMemoryRepo
    from app.repos.provider import set_repo

    user_id = str(uuid.uuid4())
    default_org = str(uuid.uuid4())
    other_org = str(uuid.uuid4())
    fake_db.users[user_id] = {
        "id": user_id,
        "email": "multi_org@example.com",
        "name": "Multi Org",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    repo = InMemoryRepo()
    # Seed order matters: the FIRST membership is the "default" org.
    repo.seed_org_member(org_id=default_org, user_id=user_id, role="owner")
    repo.seed_org_member(org_id=other_org, user_id=user_id, role="owner")
    set_repo(repo)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as ac:
        yield ac, user_id, default_org, other_org

    set_repo(None)


def _new_connector_body(name="Warehouse"):
    return {
        "name": name,
        "type": "postgres",
        "config": {"host": "db.example.com", "port": 5432, "database": "app"},
        "secret": {"password": "s3cret"},
    }


@pytest.mark.asyncio
async def test_create_lands_in_the_viewed_org(two_orgs):
    """The whole point: a connector created while viewing org B belongs to org B."""
    client, user_id, default_org, other_org = two_orgs
    headers = {**_auth_headers(user_id), "X-Org-Id": other_org}

    created = await client.post("/api/v1/connectors", json=_new_connector_body(),
                                headers=headers)
    assert created.status_code == 201, created.text
    cid = created.json()["id"]
    assert created.json()["org_id"] == other_org

    # Visible in the org it was created in...
    listed = await client.get("/api/v1/connectors", headers=headers)
    assert cid in [c["id"] for c in listed.json()]

    # ...and NOT leaked into the caller's default org.
    other = await client.get(
        "/api/v1/connectors",
        headers={**_auth_headers(user_id), "X-Org-Id": default_org},
    )
    assert cid not in [c["id"] for c in other.json()]


@pytest.mark.asyncio
async def test_get_update_delete_test_all_follow_the_header(two_orgs):
    """Every route reaches the connector in the org the caller is viewing."""
    client, user_id, _default_org, other_org = two_orgs
    headers = {**_auth_headers(user_id), "X-Org-Id": other_org}

    cid = (await client.post("/api/v1/connectors", json=_new_connector_body(),
                             headers=headers)).json()["id"]

    got = await client.get(f"/api/v1/connectors/{cid}", headers=headers)
    assert got.status_code == 200, got.text

    tested = await client.post(f"/api/v1/connectors/{cid}/test", headers=headers)
    assert tested.status_code == 200, tested.text
    assert tested.json()["ok"] is True

    updated = await client.put(f"/api/v1/connectors/{cid}",
                               json={"name": "Renamed"}, headers=headers)
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "Renamed"

    deleted = await client.delete(f"/api/v1/connectors/{cid}", headers=headers)
    assert deleted.status_code == 204, deleted.text
    assert (await client.get(f"/api/v1/connectors/{cid}",
                             headers=headers)).status_code == 404


@pytest.mark.asyncio
async def test_connector_is_not_reachable_from_another_org(two_orgs):
    """Org isolation still holds — the header widens reach, it does not bypass scoping."""
    client, user_id, default_org, other_org = two_orgs
    cid = (await client.post(
        "/api/v1/connectors", json=_new_connector_body(),
        headers={**_auth_headers(user_id), "X-Org-Id": other_org})).json()["id"]

    wrong = {**_auth_headers(user_id), "X-Org-Id": default_org}
    assert (await client.get(f"/api/v1/connectors/{cid}", headers=wrong)).status_code == 404
    assert (await client.put(f"/api/v1/connectors/{cid}", json={"name": "x"},
                             headers=wrong)).status_code == 404
    assert (await client.delete(f"/api/v1/connectors/{cid}", headers=wrong)).status_code == 404


@pytest.mark.asyncio
async def test_non_member_org_header_is_refused(two_orgs):
    """A header naming an org the caller does not belong to must 403, not create."""
    client, user_id, _default_org, _other_org = two_orgs
    stranger_org = str(uuid.uuid4())

    resp = await client.post(
        "/api/v1/connectors", json=_new_connector_body(),
        headers={**_auth_headers(user_id), "X-Org-Id": stranger_org})
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_viewer_in_the_target_org_cannot_write(app, fake_db):
    """The write guard must check the role in the TARGET org, not the default one.

    This is the trap in moving org resolution: if the guard kept using the
    default org, a user who is an owner of org A but only a viewer of org B
    could create connectors in B. It must 403.
    """
    from app.repos.memory import InMemoryRepo
    from app.repos.provider import set_repo

    user_id = str(uuid.uuid4())
    owner_org = str(uuid.uuid4())
    viewer_org = str(uuid.uuid4())
    fake_db.users[user_id] = {
        "id": user_id, "email": "mixed_roles@example.com", "name": "Mixed",
        "avatar_url": None, "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    repo = InMemoryRepo()
    repo.seed_org_member(org_id=owner_org, user_id=user_id, role="owner")
    repo.seed_org_member(org_id=viewer_org, user_id=user_id, role="viewer")
    set_repo(repo)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as ac:
        resp = await ac.post(
            "/api/v1/connectors", json=_new_connector_body(),
            headers={**_auth_headers(user_id), "X-Org-Id": viewer_org})
        assert resp.status_code == 403, resp.text

    set_repo(None)


@pytest.mark.asyncio
async def test_no_header_still_uses_the_default_org(two_orgs):
    """Back-compat: omitting X-Org-Id keeps the historical default-org behaviour."""
    client, user_id, default_org, _other_org = two_orgs

    created = await client.post("/api/v1/connectors", json=_new_connector_body(),
                                headers=_auth_headers(user_id))
    assert created.status_code == 201, created.text
    assert created.json()["org_id"] == default_org


@pytest.mark.asyncio
async def test_secret_is_stored_against_the_viewed_org(two_orgs):
    """The encrypted secret must be filed under the same org as the row.

    A mismatch here would leave the connector permanently untestable/unusable:
    the row resolves in org B but its secret was written under org A.
    """
    client, user_id, _default_org, other_org = two_orgs
    headers = {**_auth_headers(user_id), "X-Org-Id": other_org}
    cid = (await client.post("/api/v1/connectors", json=_new_connector_body(),
                             headers=headers)).json()["id"]

    # POST /{id}/test resolves BOTH layers (config + secret) in the viewed org.
    resp = await client.post(f"/api/v1/connectors/{cid}/test", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["layers"] == {"config": True, "secret": True}
