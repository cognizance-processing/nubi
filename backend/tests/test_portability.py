"""Tests for the portable export/import spec format (app/portability.py + route).

Strategy mirrors tests/test_resources.py:
- InMemoryRepo injected via set_repo() — no live DB.
- Seed org membership on the repo; seed the user in FakeDB; mint a real JWT.

Coverage
--------
- Dashboard export → import round-trip (export then import = update no-op).
- Query export → import round-trip.
- Import without metadata.id creates a new resource.
- Cross-org export → 404.
- Unknown kind → 404.
- YAML and JSON formats both round-trip.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
import yaml
from httpx import ASGITransport, AsyncClient

# Ensure the portability router is registered on api_router for the test app,
# regardless of whether main.py imports it yet (it is being wired separately).
import app.routes.portability  # noqa: F401
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


def _auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


@pytest_asyncio.fixture
async def port_client(app, fake_db):
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


# ── Dashboard round-trip ────────────────────────────────────────────────────

_DASHBOARD_SPEC = {
    "version": 1,
    "title": "Sales Overview",
    "layout": {"cols": 12, "row_height": 60},
    "variables": [],
    "widgets": [
        {
            "id": "w1",
            "type": "kpi",
            "query_id": "demo_all",
            "encoding": {"value": "value"},
            "props": {"label": "Total"},
            "pos": {"x": 1, "y": 1, "w": 3, "h": 2},
        }
    ],
}


@pytest.mark.asyncio
async def test_dashboard_export_import_roundtrip(port_client):
    client, alice_id, org_id, repo = port_client

    # Seed a board whose config nests the spec under 'spec'.
    created = await repo.create(
        resource="boards",
        org_id=org_id,
        created_by=alice_id,
        name="Sales Overview",
        config={"spec": _DASHBOARD_SPEC},
    )
    board_id = created["id"]

    # Export (YAML default).
    resp = await client.get(
        f"/api/v1/export/dashboard/{board_id}", headers=_auth(alice_id)
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/yaml")
    assert "attachment; filename=" in resp.headers["content-disposition"]

    doc = resp.text
    env = yaml.safe_load(doc)
    assert env["kind"] == "dashboard"
    assert env["apiVersion"] == "nubi/v1"
    assert env["metadata"]["id"] == board_id
    assert env["metadata"]["name"] == "Sales Overview"
    assert env["spec"]["title"] == "Sales Overview"

    # Re-import the exported document — id present → update in place (no-op).
    imp = await client.post(
        "/api/v1/import",
        content=doc,
        headers={**_auth(alice_id), "Content-Type": "application/yaml"},
    )
    assert imp.status_code == 200, imp.text
    body = imp.json()
    assert body["id"] == board_id  # same row updated, NOT a new one
    assert body["config"]["spec"]["title"] == "Sales Overview"

    # Still exactly one board in the org.
    boards = await repo.list("boards", org_id)
    assert len(boards) == 1


@pytest.mark.asyncio
async def test_dashboard_import_without_id_creates_new(port_client):
    client, alice_id, org_id, repo = port_client

    env = {
        "kind": "dashboard",
        "apiVersion": "nubi/v1",
        "metadata": {"name": "Fresh Board"},
        "spec": _DASHBOARD_SPEC,
    }
    imp = await client.post(
        "/api/v1/import",
        content=yaml.safe_dump(env),
        headers={**_auth(alice_id), "Content-Type": "application/yaml"},
    )
    assert imp.status_code == 200, imp.text
    body = imp.json()
    assert body["name"] == "Fresh Board"
    assert body["org_id"] == org_id
    assert body["config"]["spec"]["title"] == "Sales Overview"


# ── Query round-trip ────────────────────────────────────────────────────────

_QUERY_SPEC = {
    "name": "Active rows",
    "sql": "SELECT * FROM demo WHERE active = true",
    "params": [
        {"name": "region", "type": "text", "default": None, "required": False}
    ],
    "datastore_id": None,
}


@pytest.mark.asyncio
async def test_query_export_import_roundtrip_json(port_client):
    client, alice_id, org_id, repo = port_client

    created = await repo.create(
        resource="queries",
        org_id=org_id,
        created_by=alice_id,
        name="Active rows",
        config=_QUERY_SPEC,
    )
    qid = created["id"]

    # Export as JSON.
    resp = await client.get(
        f"/api/v1/export/query/{qid}?format=json", headers=_auth(alice_id)
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/json")

    env = resp.json()
    assert env["kind"] == "query"
    assert env["metadata"]["id"] == qid
    assert env["spec"]["sql"] == "SELECT * FROM demo WHERE active = true"
    assert env["spec"]["params"][0]["name"] == "region"

    # Re-import as JSON body → update in place.
    imp = await client.post(
        "/api/v1/import",
        json=env,
        headers=_auth(alice_id),
    )
    assert imp.status_code == 200, imp.text
    body = imp.json()
    assert body["id"] == qid
    assert body["config"]["sql"] == "SELECT * FROM demo WHERE active = true"

    queries = await repo.list("queries", org_id)
    assert len(queries) == 1


# ── Error cases ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_export_cross_org_returns_404(port_client):
    client, alice_id, org_id, repo = port_client

    other_org = str(uuid.uuid4())
    row = await repo.create(
        resource="boards",
        org_id=other_org,
        created_by=str(uuid.uuid4()),
        name="Secret",
        config={"spec": _DASHBOARD_SPEC},
    )
    resp = await client.get(
        f"/api/v1/export/dashboard/{row['id']}", headers=_auth(alice_id)
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_export_unknown_kind_returns_404(port_client):
    client, alice_id, org_id, repo = port_client
    resp = await client.get(
        f"/api/v1/export/connector/{uuid.uuid4()}", headers=_auth(alice_id)
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_import_invalid_query_spec_returns_400(port_client):
    client, alice_id, org_id, repo = port_client
    env = {
        "kind": "query",
        "apiVersion": "nubi/v1",
        "metadata": {"name": "Bad"},
        "spec": {"sql": "", "params": []},  # empty sql → validation failure
    }
    imp = await client.post(
        "/api/v1/import",
        content=yaml.safe_dump(env),
        headers={**_auth(alice_id), "Content-Type": "application/yaml"},
    )
    assert imp.status_code == 400, imp.text
