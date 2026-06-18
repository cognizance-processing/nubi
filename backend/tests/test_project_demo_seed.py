"""D4 — New-project demo seeding tests.

POST /projects with seed_demo=true must create a project AND immediately seed
it with the full demo starter template (connector + queries + dashboards, all
tagged sample=true).  POST /projects without seed_demo (or seed_demo=false)
must leave the project empty — unchanged baseline behaviour.

Coverage
--------
1.  POST /projects {seed_demo: false}  — 201, project row, NO seed key.
2.  POST /projects (no seed_demo field) — 201, project row, NO seed key.
3.  POST /projects {seed_demo: true}   — 201, project contains "seed" key.
4.  seed_demo=true creates >= 1 datastore, >= 1 query, >= 1 board in the repo.
5.  All seeded resources are tagged config.sample=true and belong to the new project.
6.  Seeding is scoped: blank project sees no sample resources.
7.  seed_demo=true response "seed" contains datastore_id + board_ids.
8.  Blank-project path unchanged: repo is empty after seed_demo=false.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import app.routes.projects  # noqa: F401 — ensure routes self-register
from app.auth.jwt import mint_access_token
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo

# Env vars that flip seed_sample_bundle into S3 / editable-lakehouse mode —
# cleared so these tests deterministically exercise the offline parquet-view path.
_S3_ENV_VARS = ("S3_ACCESS_KEY", "AWS_ACCESS_KEY_ID")
_LAKE_DIR_ENV_VARS = ("NUBI_MANAGED_LAKE_DIR", "NUBI_LOCAL_LAKE_DIR", "NUBI_DEMO_LAKE_DIR")


def _auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


def _user(user_id: str) -> dict[str, Any]:
    return {
        "id": user_id,
        "email": f"user-{user_id[:8]}@example.com",
        "name": "Test User",
        "avatar_url": None,
        "email_verified": True,
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
    }


def _proj_row(proj_id: str, org_id: str, user_id: str, name: str = "My Project") -> dict[str, Any]:
    return {
        "id": proj_id,
        "org_id": org_id,
        "name": name,
        "slug": name.lower().replace(" ", "-"),
        "created_by": user_id,
        "git": None,
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
        "updated_at": datetime.now(tz=timezone.utc).isoformat(),
    }


@pytest_asyncio.fixture
async def seed_client(app, fake_db, monkeypatch):
    """Async HTTP client wired to InMemoryRepo; patches project-creation helpers.

    Yields (client, user_id, org_id, repo, state).
    """
    for var in (*_S3_ENV_VARS, *_LAKE_DIR_ENV_VARS):
        monkeypatch.delenv(var, raising=False)

    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())

    fake_db.users[user_id] = _user(user_id)

    repo = InMemoryRepo()
    repo.seed_org_member(org_id, user_id)
    set_repo(repo)

    # Track which project ids have been "created" by the patched projects_repo.
    created_projects: dict[str, dict[str, Any]] = {}

    def _make_proj(proj_id: str, name: str) -> dict[str, Any]:
        row = _proj_row(proj_id, org_id, user_id, name)
        created_projects[proj_id] = row
        return row

    async def _proj_fetchrow(query: str, *args: Any) -> dict[str, Any] | None:
        q = query.upper().strip()
        if "COUNT(" in q and "PROJECTS" in q:
            return {"n": len(created_projects)}
        # SELECT 1 FROM projects (slug uniqueness probe) — always None (no clash).
        if "SELECT 1 FROM PROJECTS" in q:
            return None
        if "SELECT * FROM PROJECTS" in q:
            # get_project(org_id, project_id)
            if len(args) >= 2:
                pid = str(args[0])
                oid = str(args[1])
                row = created_projects.get(pid)
                if row and str(row["org_id"]) == oid:
                    return dict(row)
            # get_default_project — LIMIT 1
            elif len(args) == 1:
                oid = str(args[0])
                for row in created_projects.values():
                    if str(row["org_id"]) == oid:
                        return dict(row)
        return None

    async def _proj_fetch(query: str, *args: Any) -> list[dict[str, Any]]:
        return []

    async def _proj_fetchrow_insert(query: str, *args: Any) -> dict[str, Any] | None:
        """Handle INSERT ... RETURNING * for create_project."""
        q = query.upper().strip()
        if q.startswith("INSERT INTO PROJECTS") and "RETURNING" in q:
            # args: pid, org_id, name, slug, created_by, git_json
            pid = str(args[0])
            name = str(args[2])
            return _make_proj(pid, name)
        return await _proj_fetchrow(query, *args)

    with (
        patch("app.repos.projects.fetchrow", side_effect=_proj_fetchrow_insert),
        patch("app.repos.projects.fetch", side_effect=_proj_fetch),
        patch("app.repos.projects.execute", return_value="OK"),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
            follow_redirects=False,
        ) as client:
            yield client, user_id, org_id, repo

    set_repo(None)


# ---------------------------------------------------------------------------
# 1 & 2 — Blank-project creation unchanged
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_project_without_seed_demo_returns_201(seed_client):
    client, user_id, org_id, repo = seed_client
    resp = await client.post(
        "/api/v1/projects",
        json={"name": "Empty Project", "seed_demo": False},
        headers={**_auth(user_id), "X-Org-Id": org_id},
    )
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_create_project_without_seed_demo_has_no_seed_key(seed_client):
    client, user_id, org_id, repo = seed_client
    resp = await client.post(
        "/api/v1/projects",
        json={"name": "Empty Project"},
        headers={**_auth(user_id), "X-Org-Id": org_id},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "seed" not in body


@pytest.mark.asyncio
async def test_create_blank_project_repo_stays_empty(seed_client):
    client, user_id, org_id, repo = seed_client
    resp = await client.post(
        "/api/v1/projects",
        json={"name": "Blank"},
        headers={**_auth(user_id), "X-Org-Id": org_id},
    )
    assert resp.status_code == 201
    # No datastores/queries/boards created.
    assert await repo.list("datastores", org_id) == []
    assert await repo.list("queries", org_id) == []
    assert await repo.list("boards", org_id) == []


# ---------------------------------------------------------------------------
# 3 & 4 — seed_demo=true creates resources
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_project_with_seed_demo_returns_201(seed_client):
    client, user_id, org_id, repo = seed_client
    resp = await client.post(
        "/api/v1/projects",
        json={"name": "Demo Project", "seed_demo": True},
        headers={**_auth(user_id), "X-Org-Id": org_id},
    )
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_create_project_with_seed_demo_has_seed_key(seed_client):
    client, user_id, org_id, repo = seed_client
    resp = await client.post(
        "/api/v1/projects",
        json={"name": "Demo Project", "seed_demo": True},
        headers={**_auth(user_id), "X-Org-Id": org_id},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "seed" in body, f"Expected 'seed' key in response; got: {list(body.keys())}"


@pytest.mark.asyncio
async def test_create_project_with_seed_demo_creates_connector(seed_client):
    client, user_id, org_id, repo = seed_client
    resp = await client.post(
        "/api/v1/projects",
        json={"name": "Demo Project", "seed_demo": True},
        headers={**_auth(user_id), "X-Org-Id": org_id},
    )
    assert resp.status_code == 201
    datastores = await repo.list("datastores", org_id)
    assert len(datastores) >= 1, "seed_demo=true must create at least one datastore"


@pytest.mark.asyncio
async def test_create_project_with_seed_demo_creates_queries(seed_client):
    client, user_id, org_id, repo = seed_client
    resp = await client.post(
        "/api/v1/projects",
        json={"name": "Demo Project", "seed_demo": True},
        headers={**_auth(user_id), "X-Org-Id": org_id},
    )
    assert resp.status_code == 201
    queries = await repo.list("queries", org_id)
    assert len(queries) >= 1, "seed_demo=true must create at least one query"


@pytest.mark.asyncio
async def test_create_project_with_seed_demo_creates_boards(seed_client):
    client, user_id, org_id, repo = seed_client
    resp = await client.post(
        "/api/v1/projects",
        json={"name": "Demo Project", "seed_demo": True},
        headers={**_auth(user_id), "X-Org-Id": org_id},
    )
    assert resp.status_code == 201
    boards = await repo.list("boards", org_id)
    assert len(boards) >= 1, "seed_demo=true must create at least one board"


# ---------------------------------------------------------------------------
# 5 — All seeded resources tagged sample=true and scoped to the new project
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_seeded_resources_are_tagged_sample(seed_client):
    client, user_id, org_id, repo = seed_client
    resp = await client.post(
        "/api/v1/projects",
        json={"name": "Demo Project", "seed_demo": True},
        headers={**_auth(user_id), "X-Org-Id": org_id},
    )
    assert resp.status_code == 201
    project_id = resp.json()["id"]

    for table in ("datastores", "queries", "boards"):
        rows = await repo.list(table, org_id)
        for row in rows:
            assert row["config"].get("sample") is True, (
                f"{table} row {row['id']} missing config.sample=true"
            )
            assert str(row["project_id"]) == str(project_id), (
                f"{table} row {row['id']} belongs to wrong project"
            )


# ---------------------------------------------------------------------------
# 6 — Isolation: blank project does not inherit another project's resources
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_blank_project_sees_no_sample_resources_after_seeded_project(seed_client):
    client, user_id, org_id, repo = seed_client

    # First: create a seeded project.
    r1 = await client.post(
        "/api/v1/projects",
        json={"name": "Seeded", "seed_demo": True},
        headers={**_auth(user_id), "X-Org-Id": org_id},
    )
    assert r1.status_code == 201
    seeded_project_id = r1.json()["id"]

    # Second: create a blank project in the same org.
    r2 = await client.post(
        "/api/v1/projects",
        json={"name": "Blank"},
        headers={**_auth(user_id), "X-Org-Id": org_id},
    )
    assert r2.status_code == 201
    blank_project_id = r2.json()["id"]

    # Blank project should have no resources.
    for table in ("datastores", "queries", "boards"):
        blank_rows = await repo.list(table, org_id, blank_project_id)
        assert blank_rows == [], (
            f"Blank project unexpectedly has {table}: {blank_rows}"
        )

    # Seeded project retains its resources.
    seeded_ds = await repo.list("datastores", org_id, seeded_project_id)
    assert len(seeded_ds) >= 1


# ---------------------------------------------------------------------------
# 7 — seed key contains datastore_id + board_ids
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_seed_key_contains_datastore_id_and_board_ids(seed_client):
    client, user_id, org_id, repo = seed_client
    resp = await client.post(
        "/api/v1/projects",
        json={"name": "Demo Project", "seed_demo": True},
        headers={**_auth(user_id), "X-Org-Id": org_id},
    )
    assert resp.status_code == 201
    seed = resp.json().get("seed", {})

    assert "skipped" not in seed, f"Demo seeding was skipped: {seed}"
    assert "datastore_id" in seed, f"seed missing datastore_id: {seed}"
    assert "board_ids" in seed, f"seed missing board_ids: {seed}"
    assert isinstance(seed["board_ids"], list)
    assert len(seed["board_ids"]) >= 1
