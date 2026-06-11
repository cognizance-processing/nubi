"""Tests for the whole-project bundle export/import (files-as-code F-3 / F-4).

Strategy mirrors tests/test_portability.py:
- InMemoryRepo injected via set_repo() — no live DB.
- InMemoryFlowStore for flows.
- Seed org membership on the repo; seed the user in FakeDB; mint a real JWT.
- Project belongs-to-org is checked via app.repos.projects.* — patched here to
  resolve the seeded project for the fake-DB suite.

Coverage
--------
- F-3 bundle export shape (apiVersion/kind/metadata/resources) covering all kinds.
- Connector envelopes carry NO secret material.
- Cross-org / unknown project export → 404 (tenant isolation).
- F-4 bulk import: per-resource results, created vs updated, partial failure.
- F-4 connector import never writes the connector secret store.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import app.routes.portability  # noqa: F401 — ensure routes registered
import app.routes.projects_bundle  # noqa: F401
from app.auth.jwt import mint_access_token
from app.flows.store import InMemoryFlowStore, set_flow_store
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
async def bundle_client(app, fake_db, monkeypatch):
    repo = InMemoryRepo()
    set_repo(repo)
    flow_store = InMemoryFlowStore()
    set_flow_store(flow_store)

    alice_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    fake_db.users[alice_id] = _make_user(alice_id)
    repo.seed_org_member(org_id=org_id, user_id=alice_id)

    # Patch the projects repo so the seeded project resolves to this org and any
    # other id is treated as a foreign/unknown project (404).
    import app.repos.projects as projects_repo
    import app.routes.projects_bundle as bundle_mod

    async def _get_project(o: str, p: str):
        if str(o) == org_id and str(p) == project_id:
            return {"id": project_id, "name": "My Project", "org_id": org_id}
        return None

    monkeypatch.setattr(bundle_mod.projects_repo, "get_project", _get_project)
    monkeypatch.setattr(projects_repo, "get_project", _get_project)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as ac:
        yield ac, alice_id, org_id, project_id, repo, flow_store

    set_repo(None)
    set_flow_store(InMemoryFlowStore())


_DASHBOARD_SPEC = {
    "version": 1,
    "title": "Sales",
    "layout": {"cols": 12, "row_height": 60},
    "variables": [],
    "widgets": [],
}


async def _seed_resources(repo, flow_store, org_id, project_id, alice_id):
    board = await repo.create(
        resource="boards",
        org_id=org_id,
        created_by=alice_id,
        name="Sales",
        config={"spec": _DASHBOARD_SPEC},
        project_id=project_id,
    )
    query = await repo.create(
        resource="queries",
        org_id=org_id,
        created_by=alice_id,
        name="All rows",
        config={"name": "All rows", "sql": "SELECT 1", "params": [], "datastore_id": None},
        project_id=project_id,
    )
    # Connector with a SECRET key in config (must be scrubbed on export).
    connector = await repo.create(
        resource="datastores",
        org_id=org_id,
        created_by=alice_id,
        name="Prod PG",
        config={
            "connector_type": "postgres",
            "host": "db.internal",
            "port": 5432,
            "database": "analytics",
            "user": "readonly",
            "password": "s3cr3t-should-never-export",
        },
        project_id=project_id,
    )
    flow = await flow_store.create_flow(
        org_id, alice_id, "Nightly", {"name": "Nightly", "cells": []}, project_id=project_id
    )
    return board, query, connector, flow


# ── F-3 export ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bundle_export_shape_and_kinds(bundle_client):
    client, alice_id, org_id, project_id, repo, flow_store = bundle_client
    await _seed_resources(repo, flow_store, org_id, project_id, alice_id)

    resp = await client.get(
        f"/api/v1/projects/{project_id}/export", headers=_auth(alice_id)
    )
    assert resp.status_code == 200, resp.text
    bundle = resp.json()

    assert bundle["apiVersion"] == "nubi/v1"
    assert bundle["kind"] == "project"
    assert bundle["metadata"]["id"] == project_id
    assert bundle["metadata"]["name"] == "My Project"

    kinds = sorted(e["kind"] for e in bundle["resources"])
    assert kinds == ["connector", "dashboard", "flow", "query"]

    # Every envelope is a proper portability envelope.
    for env in bundle["resources"]:
        assert env["apiVersion"] == "nubi/v1"
        assert "metadata" in env and "spec" in env


@pytest.mark.asyncio
async def test_bundle_export_connector_has_no_secrets(bundle_client):
    client, alice_id, org_id, project_id, repo, flow_store = bundle_client
    await _seed_resources(repo, flow_store, org_id, project_id, alice_id)

    resp = await client.get(
        f"/api/v1/projects/{project_id}/export", headers=_auth(alice_id)
    )
    assert resp.status_code == 200
    body = resp.text
    assert "s3cr3t-should-never-export" not in body
    assert "password" not in body

    connector = next(
        e for e in resp.json()["resources"] if e["kind"] == "connector"
    )
    assert connector["spec"]["connector_type"] == "postgres"
    assert "password" not in connector["spec"]


@pytest.mark.asyncio
async def test_bundle_export_unknown_project_404(bundle_client):
    client, alice_id, org_id, project_id, repo, flow_store = bundle_client
    resp = await client.get(
        f"/api/v1/projects/{uuid.uuid4()}/export", headers=_auth(alice_id)
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_bundle_export_requires_auth(bundle_client):
    client, alice_id, org_id, project_id, repo, flow_store = bundle_client
    resp = await client.get(f"/api/v1/projects/{project_id}/export")
    assert resp.status_code == 401


# ── F-4 import ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bundle_import_creates_and_updates(bundle_client):
    client, alice_id, org_id, project_id, repo, flow_store = bundle_client
    board, query, connector, flow = await _seed_resources(
        repo, flow_store, org_id, project_id, alice_id
    )

    # Export, then re-import: existing ids → all "updated".
    exp = await client.get(
        f"/api/v1/projects/{project_id}/export", headers=_auth(alice_id)
    )
    bundle = exp.json()
    imp = await client.post(
        f"/api/v1/projects/{project_id}/import",
        json=bundle,
        headers=_auth(alice_id),
    )
    assert imp.status_code == 200, imp.text
    out = imp.json()
    assert out["total"] == 4
    assert out["updated"] == 4
    assert out["created"] == 0
    assert out["failed"] == 0
    for r in out["results"]:
        assert r["action"] == "updated"
        assert "error" not in r

    # A bare list of NEW envelopes (no id) → all "created".
    new_envs = [
        {
            "kind": "dashboard",
            "apiVersion": "nubi/v1",
            "metadata": {"name": "Fresh"},
            "spec": _DASHBOARD_SPEC,
        },
        {
            "kind": "query",
            "apiVersion": "nubi/v1",
            "metadata": {"name": "Fresh q"},
            "spec": {"name": "Fresh q", "sql": "SELECT 2", "params": [], "datastore_id": None},
        },
    ]
    imp2 = await client.post(
        f"/api/v1/projects/{project_id}/import",
        json=new_envs,
        headers=_auth(alice_id),
    )
    assert imp2.status_code == 200, imp2.text
    out2 = imp2.json()
    assert out2["created"] == 2
    assert out2["updated"] == 0

    # Created resources land in the target project.
    boards = await repo.list("boards", org_id, project_id)
    assert any(b["name"] == "Fresh" for b in boards)


@pytest.mark.asyncio
async def test_bundle_import_partial_failure(bundle_client):
    client, alice_id, org_id, project_id, repo, flow_store = bundle_client

    envs = [
        {
            "kind": "query",
            "apiVersion": "nubi/v1",
            "metadata": {"name": "Good"},
            "spec": {"name": "Good", "sql": "SELECT 1", "params": [], "datastore_id": None},
        },
        {
            # Invalid: query spec with empty sql → validation failure.
            "kind": "query",
            "apiVersion": "nubi/v1",
            "metadata": {"name": "Bad"},
            "spec": {"name": "Bad", "sql": "", "params": [], "datastore_id": None},
        },
        {
            "kind": "dashboard",
            "apiVersion": "nubi/v1",
            "metadata": {"name": "Also good"},
            "spec": _DASHBOARD_SPEC,
        },
    ]
    imp = await client.post(
        f"/api/v1/projects/{project_id}/import", json=envs, headers=_auth(alice_id)
    )
    assert imp.status_code == 200, imp.text
    out = imp.json()
    assert out["total"] == 3
    assert out["created"] == 2
    assert out["failed"] == 1

    bad = next(r for r in out["results"] if r["name"] == "Bad")
    assert bad["action"] == "skipped"
    assert "error" in bad and bad["error"]

    # The good ones still applied despite the bad one.
    queries = await repo.list("queries", org_id, project_id)
    assert any(q["name"] == "Good" for q in queries)


@pytest.mark.asyncio
async def test_bundle_import_connector_never_writes_secret_store(bundle_client):
    client, alice_id, org_id, project_id, repo, flow_store = bundle_client

    from app.connectors import secret_store as ss

    calls: list[Any] = []

    class _SpyStore:
        async def put(self, *a, **k):
            calls.append(("put", a, k))

        async def get(self, *a, **k):
            calls.append(("get", a, k))
            return None

    original = ss.get_secret_store
    ss.get_secret_store = lambda: _SpyStore()  # type: ignore[assignment]
    try:
        env = {
            "kind": "connector",
            "apiVersion": "nubi/v1",
            "metadata": {"name": "New PG"},
            "spec": {
                "connector_type": "postgres",
                "host": "h",
                "database": "d",
            },
        }
        imp = await client.post(
            f"/api/v1/projects/{project_id}/import",
            json=[env],
            headers=_auth(alice_id),
        )
        assert imp.status_code == 200, imp.text
        assert imp.json()["created"] == 1
    finally:
        ss.get_secret_store = original  # type: ignore[assignment]

    # Importing a connector must never touch the connector secret store.
    assert calls == []


@pytest.mark.asyncio
async def test_bundle_import_requires_auth(bundle_client):
    client, alice_id, org_id, project_id, repo, flow_store = bundle_client
    resp = await client.post(f"/api/v1/projects/{project_id}/import", json=[])
    assert resp.status_code == 401
