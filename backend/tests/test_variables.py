"""Tests for the variables store (app/vars/store.py) + route (routes/variables.py).

Strategy mirrors tests/test_portability.py:
- InMemoryRepo injected via set_repo() — no live DB.
- InMemoryVarStore injected via set_var_store() — no live DB.
- Seed org membership on the repo; seed the user in FakeDB; mint a real JWT.

Coverage
--------
Store (InMemory, direct):
- upsert / get / list / delete round-trip.
- project-scoped vs org-global isolation (same key, no collision).
- upsert updates value + updated_by in place (no duplicate row).

Route:
- PUT then GET happy path; list; DELETE → 204 then 404.
- cross-org GET → 404 (no leak).
- writer-role gate: viewer gets 403 on PUT and DELETE.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Ensure the variables router is registered on api_router for the test app.
import app.routes.variables  # noqa: F401
from app.auth.jwt import mint_access_token
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo
from app.vars.store import InMemoryVarStore, set_var_store


# ── Store-level tests (no HTTP) ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_store_upsert_get_list_delete_roundtrip():
    store = InMemoryVarStore()
    org = str(uuid.uuid4())

    created = await store.set_var(org, "api_url", "https://a", updated_by="u1")
    assert created["key"] == "api_url"
    assert created["value"] == "https://a"
    assert created["project_id"] is None
    assert created["updated_by"] == "u1"

    got = await store.get_var(org, "api_url")
    assert got is not None and got["value"] == "https://a"

    rows = await store.list_vars(org)
    assert [r["key"] for r in rows] == ["api_url"]

    assert await store.delete_var(org, "api_url") is True
    assert await store.get_var(org, "api_url") is None
    assert await store.delete_var(org, "api_url") is False


@pytest.mark.asyncio
async def test_store_upsert_updates_in_place():
    store = InMemoryVarStore()
    org = str(uuid.uuid4())

    first = await store.set_var(org, "k", {"a": 1}, updated_by="u1")
    second = await store.set_var(org, "k", {"a": 2}, updated_by="u2")

    assert first["id"] == second["id"]  # same row, not a duplicate
    assert second["value"] == {"a": 2}
    assert second["updated_by"] == "u2"

    rows = await store.list_vars(org)
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_store_project_scope_vs_global_isolation():
    store = InMemoryVarStore()
    org = str(uuid.uuid4())
    proj = str(uuid.uuid4())

    await store.set_var(org, "shared", "global-val")
    await store.set_var(org, "shared", "project-val", project_id=proj)

    g = await store.get_var(org, "shared")
    p = await store.get_var(org, "shared", project_id=proj)
    assert g is not None and g["value"] == "global-val"
    assert p is not None and p["value"] == "project-val"
    assert g["id"] != p["id"]

    # Listing is scope-specific.
    assert [r["value"] for r in await store.list_vars(org)] == ["global-val"]
    assert [r["value"] for r in await store.list_vars(org, project_id=proj)] == [
        "project-val"
    ]

    # Deleting one scope leaves the other intact.
    assert await store.delete_var(org, "shared", project_id=proj) is True
    assert await store.get_var(org, "shared") is not None
    assert await store.get_var(org, "shared", project_id=proj) is None


@pytest.mark.asyncio
async def test_store_value_is_arbitrary_json():
    store = InMemoryVarStore()
    org = str(uuid.uuid4())

    for key, val in [
        ("scalar", 42),
        ("list", [1, 2, 3]),
        ("dict", {"nested": {"x": True}}),
        ("null", None),
        ("str", "hello"),
    ]:
        out = await store.set_var(org, key, val)
        assert out["value"] == val


@pytest.mark.asyncio
async def test_store_cross_org_isolation():
    store = InMemoryVarStore()
    org_a = str(uuid.uuid4())
    org_b = str(uuid.uuid4())

    await store.set_var(org_a, "secret", "a-only")
    assert await store.get_var(org_b, "secret") is None
    assert await store.list_vars(org_b) == []


# ── Route fixtures ──────────────────────────────────────────────────────────


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


@pytest_asyncio.fixture
async def var_client(app, fake_db):
    repo = InMemoryRepo()
    set_repo(repo)
    store = InMemoryVarStore()
    set_var_store(store)

    alice_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    fake_db.users[alice_id] = _make_user(alice_id)
    repo.seed_org_member(org_id=org_id, user_id=alice_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as ac:
        yield ac, alice_id, org_id, repo, store

    set_repo(None)
    set_var_store(None)


# ── Route happy paths ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_route_put_get_list_delete(var_client):
    client, alice_id, org_id, repo, store = var_client
    hdr = {**_auth(alice_id), "X-Org-Id": org_id}

    # PUT (upsert).
    resp = await client.put(
        "/api/v1/variables/region", json={"value": "eu-west"}, headers=hdr
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["key"] == "region"
    assert body["value"] == "eu-west"
    assert body["org_id"] == org_id
    assert body["updated_by"] == alice_id

    # GET single.
    got = await client.get("/api/v1/variables/region", headers=hdr)
    assert got.status_code == 200
    assert got.json()["value"] == "eu-west"

    # LIST.
    lst = await client.get("/api/v1/variables", headers=hdr)
    assert lst.status_code == 200
    assert [r["key"] for r in lst.json()] == ["region"]

    # PUT again updates value (no duplicate).
    resp2 = await client.put(
        "/api/v1/variables/region", json={"value": "us-east"}, headers=hdr
    )
    assert resp2.status_code == 200
    assert resp2.json()["value"] == "us-east"
    lst2 = await client.get("/api/v1/variables", headers=hdr)
    assert len(lst2.json()) == 1

    # DELETE → 204, then 404.
    d = await client.delete("/api/v1/variables/region", headers=hdr)
    assert d.status_code == 204
    g404 = await client.get("/api/v1/variables/region", headers=hdr)
    assert g404.status_code == 404
    d404 = await client.delete("/api/v1/variables/region", headers=hdr)
    assert d404.status_code == 404


@pytest.mark.asyncio
async def test_route_get_missing_returns_404(var_client):
    client, alice_id, org_id, repo, store = var_client
    hdr = {**_auth(alice_id), "X-Org-Id": org_id}
    resp = await client.get("/api/v1/variables/nope", headers=hdr)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_route_value_arbitrary_json(var_client):
    client, alice_id, org_id, repo, store = var_client
    hdr = {**_auth(alice_id), "X-Org-Id": org_id}
    payload = {"value": {"nested": [1, 2, {"x": True}], "flag": None}}
    resp = await client.put("/api/v1/variables/cfg", json=payload, headers=hdr)
    assert resp.status_code == 200
    assert resp.json()["value"] == payload["value"]
    got = await client.get("/api/v1/variables/cfg", headers=hdr)
    assert got.json()["value"] == payload["value"]


# ── Cross-org isolation ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_route_cross_org_get_returns_404(var_client):
    client, alice_id, org_id, repo, store = var_client

    # Seed a variable in a DIFFERENT org directly in the store.
    other_org = str(uuid.uuid4())
    await store.set_var(other_org, "secret", "hidden")

    hdr = {**_auth(alice_id), "X-Org-Id": org_id}
    resp = await client.get("/api/v1/variables/secret", headers=hdr)
    assert resp.status_code == 404
    # And it does not leak into the caller's list.
    lst = await client.get("/api/v1/variables", headers=hdr)
    assert lst.json() == []


# ── Writer-role gate ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_route_viewer_cannot_put_or_delete(var_client, fake_db):
    client, alice_id, org_id, repo, store = var_client

    # A viewer in their own org.
    viewer_id = str(uuid.uuid4())
    viewer_org = str(uuid.uuid4())
    fake_db.users[viewer_id] = _make_user(viewer_id, email="viewer@example.com")
    repo.seed_org_member(org_id=viewer_org, user_id=viewer_id, role="viewer")
    hdr = {**_auth(viewer_id), "X-Org-Id": viewer_org}

    put = await client.put(
        "/api/v1/variables/k", json={"value": 1}, headers=hdr
    )
    assert put.status_code == 403

    delete = await client.delete("/api/v1/variables/k", headers=hdr)
    assert delete.status_code == 403

    # GET is allowed for viewers (reader role) — 404 because nothing exists.
    got = await client.get("/api/v1/variables/k", headers=hdr)
    assert got.status_code == 404
