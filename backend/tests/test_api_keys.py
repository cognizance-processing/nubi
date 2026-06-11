"""Tests for long-lived API keys (files-as-code F-6).

Coverage
--------
- Mint → the raw key is returned once; authenticates a Bearer request like a
  login access token (GET /auth/me).
- List shows the key (no secret material).
- Revoke → the key is then rejected (401).
- An unknown / malformed api key → 401.
- Cross-tenant: a key minted in org A is PINNED to org A — passing X-Org-Id for
  another org the user belongs to is rejected (403); resolved org is always A.
- The minted key is stored hashed (never the raw value at rest).

Strategy mirrors tests/test_portability.py: InMemoryRepo + seeded user/org, real
JWT for the mint call, InMemoryApiKeyStore (installed by the conftest reset).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import app.routes.auth  # noqa: F401 — ensure routes registered
from app.auth.api_keys import get_api_key_store
from app.auth.jwt import mint_access_token
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo


def _make_user(user_id: str, email: str = "alice@example.com") -> dict[str, Any]:
    return {
        "id": user_id,
        "email": email,
        "name": "Alice",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


def _jwt_auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


@pytest_asyncio.fixture
async def key_client(app, fake_db):
    repo = InMemoryRepo()
    set_repo(repo)

    alice_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    fake_db.users[alice_id] = _make_user(alice_id)
    repo.seed_org_member(org_id=org_id, user_id=alice_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as ac:
        yield ac, alice_id, org_id, repo

    set_repo(None)


@pytest.mark.asyncio
async def test_mint_authenticate_list_revoke_reject(key_client):
    client, alice_id, org_id, repo = key_client

    # ── Mint ────────────────────────────────────────────────────────────────
    mint = await client.post(
        "/api/v1/auth/api-keys", json={"name": "ci-token"}, headers=_jwt_auth(alice_id)
    )
    assert mint.status_code == 201, mint.text
    payload = mint.json()
    raw = payload["key"]
    assert raw.startswith("nubi_ak_")
    key_id = payload["api_key"]["id"]
    assert payload["api_key"]["name"] == "ci-token"
    # The raw key is never echoed back in the listing-safe shape.
    assert "key" not in payload["api_key"]
    assert "token_hash" not in payload["api_key"]

    # ── Authenticate a real request with the api key (not a JWT) ─────────────
    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {raw}"})
    assert me.status_code == 200, me.text
    assert me.json()["user"]["id"] == alice_id

    # ── List ─────────────────────────────────────────────────────────────────
    listed = await client.get("/api/v1/auth/api-keys", headers=_jwt_auth(alice_id))
    assert listed.status_code == 200, listed.text
    keys = listed.json()["api_keys"]
    assert len(keys) == 1
    assert keys[0]["id"] == key_id
    assert keys[0]["last_four"] == raw[-4:]
    assert "token_hash" not in keys[0]

    # ── Revoke ───────────────────────────────────────────────────────────────
    rev = await client.delete(
        f"/api/v1/auth/api-keys/{key_id}", headers=_jwt_auth(alice_id)
    )
    assert rev.status_code == 204, rev.text

    # ── The revoked key is now rejected ───────────────────────────────────────
    me2 = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {raw}"})
    assert me2.status_code == 401


@pytest.mark.asyncio
async def test_unknown_api_key_rejected(key_client):
    client, alice_id, org_id, repo = key_client
    resp = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer nubi_ak_thisdoesnotexist000000000000000000"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_api_key_stored_hashed_not_plaintext(key_client):
    client, alice_id, org_id, repo = key_client
    mint = await client.post(
        "/api/v1/auth/api-keys", json={}, headers=_jwt_auth(alice_id)
    )
    raw = mint.json()["key"]

    # Inspect the in-memory store: the raw key must NOT be stored; only its hash.
    store = get_api_key_store()
    rows = list(store._store.values())  # type: ignore[attr-defined]
    assert len(rows) == 1
    row = rows[0]
    assert "token_hash" in row
    assert raw not in row.values()
    assert row["token_hash"] != raw

    import hashlib

    assert row["token_hash"] == hashlib.sha256(raw.encode()).hexdigest()


@pytest.mark.asyncio
async def test_api_key_pinned_to_minting_org(key_client):
    """An API key is PINNED to its minting org on every X-Org-Id-aware route.

    Alice belongs to two orgs. Her key is minted for the first (default) org.
    On a ``resolve_org_id`` route (the portability export surface), passing
    X-Org-Id for the OTHER org she belongs to must be rejected (403) — the key
    cannot be redirected to an org it was not minted for. A matching/absent
    header resolves to the minting org.
    """
    import app.routes.portability  # noqa: F401 — register /export/{kind}/{id}

    client, alice_id, org_id, repo = key_client

    # Alice also belongs to a SECOND org, with a board she owns there.
    other_org = str(uuid.uuid4())
    repo.seed_org_member(org_id=other_org, user_id=alice_id)
    other_board = await repo.create(
        resource="boards",
        org_id=other_org,
        created_by=alice_id,
        name="Other",
        config={"spec": {"version": 1, "title": "Other", "widgets": []}},
    )

    mint = await client.post(
        "/api/v1/auth/api-keys", json={}, headers=_jwt_auth(alice_id)
    )
    raw = mint.json()["key"]

    # Key minted for org_id; export of the OTHER org's board with X-Org-Id=other
    # is rejected (403) — the key is scoped to the minting org.
    denied = await client.get(
        f"/api/v1/export/dashboard/{other_board['id']}",
        headers={"Authorization": f"Bearer {raw}", "X-Org-Id": other_org},
    )
    assert denied.status_code == 403, denied.text

    # A board in the minting org IS reachable with the key (no header needed).
    my_board = await repo.create(
        resource="boards",
        org_id=org_id,
        created_by=alice_id,
        name="Mine",
        config={"spec": {"version": 1, "title": "Mine", "widgets": []}},
    )
    ok = await client.get(
        f"/api/v1/export/dashboard/{my_board['id']}",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert ok.status_code == 200, ok.text


@pytest.mark.asyncio
async def test_revoke_unknown_key_404(key_client):
    client, alice_id, org_id, repo = key_client
    resp = await client.delete(
        f"/api/v1/auth/api-keys/{uuid.uuid4()}", headers=_jwt_auth(alice_id)
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_cross_user_cannot_revoke_or_see_key(key_client, fake_db):
    client, alice_id, org_id, repo = key_client

    # Alice mints a key.
    mint = await client.post(
        "/api/v1/auth/api-keys", json={}, headers=_jwt_auth(alice_id)
    )
    key_id = mint.json()["api_key"]["id"]

    # Bob is a different user in a different org.
    bob_id = str(uuid.uuid4())
    bob_org = str(uuid.uuid4())
    fake_db.users[bob_id] = _make_user(bob_id, email="bob@example.com")
    repo.seed_org_member(org_id=bob_org, user_id=bob_id)

    # Bob cannot see Alice's key.
    listed = await client.get("/api/v1/auth/api-keys", headers=_jwt_auth(bob_id))
    assert listed.status_code == 200
    assert listed.json()["api_keys"] == []

    # Bob cannot revoke Alice's key (invisible → 404).
    rev = await client.delete(
        f"/api/v1/auth/api-keys/{key_id}", headers=_jwt_auth(bob_id)
    )
    assert rev.status_code == 404
