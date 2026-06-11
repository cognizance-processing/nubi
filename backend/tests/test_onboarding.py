"""Tests for the Supabase-style onboarding backend (single-project demo model).

Coverage
--------
POST /projects/sample/restore  (the way to add demo data to the ACTIVE project):
1.  Seeds the demo bundle INTO the active/default project (sample-tagged rows
    scoped to that project) → 200, no separate "Demo" project is created.
2.  Idempotent — a second call reuses existing rows (no duplicates).
3.  Honours X-Project-Id — seeds into the requested project.

POST /projects/sample/remove:
4.  Bulk-removes the sample-tagged rows from the active project.
5.  No-op (all-zero / null) when nothing is seeded.

POST /auth/register:
6.  demo_project=true → exactly ONE project, with the demo bundle seeded into it.
7.  Without demo_project → exactly ONE EMPTY project (no sample-tagged resources).

Google OAuth new-user onboarding:
8.  New OAuth user gets NO auto-created org/project (bare user only).
9.  Org-less user: GET /orgs → {"orgs": []} (no 500), GET /auth/me works,
    then POST /orgs (first org, no prior membership) → 201, then
    POST /projects → 201.

Strategy
--------
Follows the existing route-test style (httpx.AsyncClient against the conftest
``app`` + FakeDB) with:
- ``InMemoryRepo`` (set via ``set_repo``) for org membership/roles + seeded
  sample-bundle resources.
- A small stateful fake for the ``projects`` table patched onto
  ``app.repos.projects.fetchrow / fetch / execute`` (the conftest FakeDB does
  not model projects).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo

import app.routes.auth  # noqa: F401 — ensure routes self-register
import app.routes.orgs  # noqa: F401
import app.routes.projects  # noqa: F401


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


def _user(user_id: str, email: str = "owner@example.com") -> dict[str, Any]:
    return {
        "id": user_id,
        "email": email,
        "name": "Test User",
        "password_hash": None,
        "avatar_url": None,
        "email_verified": True,
        "created_at": datetime.now(tz=timezone.utc),
        "updated_at": datetime.now(tz=timezone.utc),
    }


class ProjectsState:
    """Stateful in-memory stand-in for the ``projects`` table."""

    def __init__(self) -> None:
        self.projects: dict[str, dict[str, Any]] = {}
        self._seq = 0

    def add(
        self,
        org_id: str,
        name: str,
        slug: str,
        created_by: str | None,
        project_id: str | None = None,
        git: Any = None,
    ) -> dict[str, Any]:
        self._seq += 1
        pid = project_id or str(uuid.uuid4())
        row = {
            "id": pid,
            "org_id": str(org_id),
            "name": name,
            "slug": slug,
            "created_by": str(created_by) if created_by else None,
            "git": git,
            "created_at": f"2026-01-01T00:00:00.{self._seq:06d}+00:00",
            "updated_at": f"2026-01-01T00:00:00.{self._seq:06d}+00:00",
        }
        self.projects[pid] = row
        return row

    def for_org(self, org_id: str) -> list[dict[str, Any]]:
        rows = [r for r in self.projects.values() if r["org_id"] == str(org_id)]
        rows.sort(key=lambda r: r["created_at"])
        return rows


def _projects_patches(state: ProjectsState):
    """Return the patch objects wiring app.repos.projects.* to *state*."""

    async def _fetchrow(query: str, *args: Any) -> dict[str, Any] | None:
        q = " ".join(query.upper().split())
        if q.startswith("INSERT INTO PROJECTS"):
            pid, org_id, name, slug, created_by, _git = args
            return dict(state.add(str(org_id), str(name), str(slug), created_by, project_id=str(pid)))
        if "SELECT 1 FROM PROJECTS WHERE ID" in q:
            # project_belongs_to_org(project_id, org_id)
            pid, org_id = str(args[0]), str(args[1])
            row = state.projects.get(pid)
            return {"exists": 1} if row and row["org_id"] == org_id else None
        if "SELECT 1 FROM PROJECTS" in q:
            org_id, slug = str(args[0]), str(args[1])
            for r in state.projects.values():
                if r["org_id"] == org_id and r["slug"] == slug:
                    return {"exists": 1}
            return None
        if "COUNT(" in q and "PROJECTS" in q:
            return {"n": len(state.for_org(str(args[0])))}
        if "SELECT * FROM PROJECTS WHERE ID" in q:
            pid, org_id = str(args[0]), str(args[1])
            row = state.projects.get(pid)
            if row and row["org_id"] == org_id:
                return dict(row)
            return None
        if "SELECT * FROM PROJECTS" in q and "LIMIT 1" in q:
            rows = state.for_org(str(args[0]))
            return dict(rows[0]) if rows else None
        return None

    async def _fetch(query: str, *args: Any) -> list[dict[str, Any]]:
        q = " ".join(query.upper().split())
        if "SELECT * FROM PROJECTS" in q and "ORDER BY" in q:
            return [dict(r) for r in state.for_org(str(args[0]))]
        return []

    async def _execute(query: str, *args: Any) -> str:
        q = " ".join(query.upper().split())
        if q.startswith("DELETE FROM PROJECTS"):
            pid, org_id = str(args[0]), str(args[1])
            row = state.projects.get(pid)
            if row and row["org_id"] == org_id:
                del state.projects[pid]
                return "DELETE 1"
            return "DELETE 0"
        return "OK"

    return [
        patch("app.repos.projects.fetchrow", side_effect=_fetchrow),
        patch("app.repos.projects.fetch", side_effect=_fetch),
        patch("app.repos.projects.execute", side_effect=_execute),
    ]


async def _sample_resource_counts(repo: InMemoryRepo, org_id: str) -> dict[str, int]:
    return {
        kind: len(await repo.list(kind, org_id))
        for kind in ("datastores", "queries", "boards")
    }


# ---------------------------------------------------------------------------
# Fixture: client + owner user + org with a single default project
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def onboard(app, fake_db):
    """Yield (client, user_id, org_id, repo, state) for sample-bundle tests."""
    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    fake_db.users[user_id] = _user(user_id)

    repo = InMemoryRepo()
    repo.seed_org_member(org_id, user_id, role="owner")
    set_repo(repo)

    state = ProjectsState()
    state.add(org_id, "Default", "default", user_id)

    patches = _projects_patches(state)
    for p in patches:
        p.start()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
            follow_redirects=False,
        ) as ac:
            yield ac, user_id, org_id, repo, state
    finally:
        for p in patches:
            p.stop()


# ---------------------------------------------------------------------------
# POST /projects/sample/restore — seed demo INTO the active/default project
# ---------------------------------------------------------------------------

class TestSampleRestoreSeedsActiveProject:
    @pytest.mark.asyncio
    async def test_seeds_default_project_no_separate_demo_project(self, onboard):
        """Restore seeds the default project; it never creates a "Demo" project."""
        ac, user_id, org_id, repo, state = onboard
        default_id = state.for_org(org_id)[0]["id"]

        resp = await ac.post("/api/v1/projects/sample/restore", headers=_auth(user_id))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["project_id"] == default_id

        # No second project was created — still exactly the one default project.
        assert len(state.for_org(org_id)) == 1
        assert not any(p["slug"] == "demo" for p in state.for_org(org_id))

        # The demo bundle landed in the default project, sample-tagged.
        for kind in ("datastores", "queries", "boards"):
            rows = await repo.list(kind, org_id)
            assert rows, f"no {kind} seeded"
            for row in rows:
                assert row["config"].get("sample") is True
                assert str(row["project_id"]) == str(default_id)

    @pytest.mark.asyncio
    async def test_restore_is_idempotent(self, onboard):
        ac, user_id, org_id, repo, state = onboard
        first = await ac.post("/api/v1/projects/sample/restore", headers=_auth(user_id))
        assert first.status_code == 200
        counts = await _sample_resource_counts(repo, org_id)

        second = await ac.post("/api/v1/projects/sample/restore", headers=_auth(user_id))
        assert second.status_code == 200
        assert await _sample_resource_counts(repo, org_id) == counts
        # No extra project created on the second call either.
        assert len(state.for_org(org_id)) == 1

    @pytest.mark.asyncio
    async def test_restore_honours_x_project_id(self, onboard):
        """When X-Project-Id targets a second project, the bundle seeds there."""
        ac, user_id, org_id, repo, state = onboard
        second = state.add(org_id, "Analytics", "analytics", user_id)

        resp = await ac.post(
            "/api/v1/projects/sample/restore",
            headers={**_auth(user_id), "X-Project-Id": second["id"]},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["project_id"] == second["id"]

        for kind in ("datastores", "queries", "boards"):
            for row in await repo.list(kind, org_id):
                assert str(row["project_id"]) == str(second["id"])


# ---------------------------------------------------------------------------
# POST /projects/sample/remove — remove demo from the active project
# ---------------------------------------------------------------------------

class TestSampleRemove:
    @pytest.mark.asyncio
    async def test_remove_clears_active_project(self, onboard):
        ac, user_id, org_id, repo, state = onboard
        default_id = state.for_org(org_id)[0]["id"]
        await ac.post("/api/v1/projects/sample/restore", headers=_auth(user_id))
        assert await repo.list("datastores", org_id)

        resp = await ac.post("/api/v1/projects/sample/remove", headers=_auth(user_id))
        assert resp.status_code == 200, resp.text
        assert resp.json()["project_id"] == default_id
        assert await repo.list("datastores", org_id) == []
        assert await repo.list("queries", org_id) == []
        assert await repo.list("boards", org_id) == []

    @pytest.mark.asyncio
    async def test_remove_without_bundle_is_noop(self, onboard):
        ac, user_id, org_id, _repo, state = onboard
        default_id = state.for_org(org_id)[0]["id"]
        resp = await ac.post("/api/v1/projects/sample/remove", headers=_auth(user_id))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["project_id"] == default_id
        # All-zero counts — nothing was seeded.
        assert all(v == 0 for v in body["removed"].values())


# ---------------------------------------------------------------------------
# POST /auth/register with/without demo_project (single-project model)
# ---------------------------------------------------------------------------

class TestRegisterDemoData:
    @pytest.mark.asyncio
    async def test_register_with_demo_seeds_single_project(self, client, fake_db):
        repo = InMemoryRepo()
        set_repo(repo)
        state = ProjectsState()
        patches = _projects_patches(state)
        for p in patches:
            p.start()
        try:
            resp = await client.post(
                "/api/v1/auth/register",
                json={
                    "email": "demo-signup@example.com",
                    "password": "longenough123",
                    "name": "Demo Signup",
                    "org_name": "Demo Org",
                    "project_name": "Main",
                    "demo_project": True,
                },
            )
        finally:
            for p in patches:
                p.stop()

        assert resp.status_code == 201, resp.text
        projects = list(state.projects.values())
        # Exactly ONE project — no separate "Demo" project.
        assert len(projects) == 1
        assert projects[0]["name"] == "Main"

        org_id = projects[0]["org_id"]
        # The demo bundle was seeded INTO that single project.
        datastores = await repo.list("datastores", org_id)
        assert datastores, "demo bundle was not seeded"
        for kind in ("datastores", "queries", "boards"):
            for row in await repo.list(kind, org_id):
                assert row["config"].get("sample") is True
                assert str(row["project_id"]) == str(projects[0]["id"])

    @pytest.mark.asyncio
    async def test_register_without_demo_creates_one_empty_project(self, client, fake_db):
        repo = InMemoryRepo()
        set_repo(repo)
        state = ProjectsState()
        patches = _projects_patches(state)
        for p in patches:
            p.start()
        try:
            resp = await client.post(
                "/api/v1/auth/register",
                json={
                    "email": "plain-signup@example.com",
                    "password": "longenough123",
                    "name": "Plain Signup",
                    "org_name": "Plain Org",
                    "project_name": "Main",
                },
            )
        finally:
            for p in patches:
                p.stop()

        assert resp.status_code == 201, resp.text
        projects = list(state.projects.values())
        assert len(projects) == 1
        assert projects[0]["name"] == "Main"

        # The single project starts EMPTY — no sample-tagged resources at all.
        org_id = projects[0]["org_id"]
        assert await repo.list("datastores", org_id) == []
        assert await repo.list("queries", org_id) == []
        assert await repo.list("boards", org_id) == []


# ---------------------------------------------------------------------------
# Google OAuth new-user path — bare user, frontend-driven org creation
# ---------------------------------------------------------------------------

def _orgs_route_patches(fake_db):
    """Patch app.routes.orgs DB helpers to serve org data from the FakeDB.

    (orgs.py binds ``fetch``/``execute`` at import time, so the conftest's
    ``app.db`` patches do not reach it — same approach as test_orgs.py.)
    """

    async def _fetch(query: str, *args: Any) -> list[dict[str, Any]]:
        q = query.upper()
        if "FROM ORG_MEMBERS" in q and "JOIN ORGS" in q:
            uid = str(args[0])
            out = []
            for m in fake_db.org_members.values():
                if str(m["user_id"]) == uid:
                    org = fake_db.orgs.get(m["org_id"], {})
                    out.append({"id": m["org_id"], "name": org.get("name", "?"), "role": m["role"]})
            return out
        return []

    return [
        patch("app.routes.orgs.fetch", side_effect=_fetch),
        patch("app.routes.orgs.execute", side_effect=fake_db.fake_execute),
        patch("app.routes.orgs.fetchrow", side_effect=fake_db.fake_fetchrow),
    ]


class TestOAuthOrglessOnboarding:
    @pytest.mark.asyncio
    async def test_oauth_new_user_gets_no_org_or_project(self, client, fake_db):
        repo = InMemoryRepo()
        set_repo(repo)
        state = ProjectsState()
        fake_profile = {
            "provider_account_id": "google-sub-orgless-1",
            "email": "fresh@example.com",
            "email_verified": True,
            "name": "Fresh",
            "picture": None,
        }
        state_val = "test-state-orgless"

        patches = _projects_patches(state)
        for p in patches:
            p.start()
        try:
            with patch("app.routes.auth.exchange_code", new=AsyncMock(return_value=fake_profile)):
                resp = await client.get(
                    "/api/v1/auth/google/callback",
                    params={"code": "fake-code", "state": state_val},
                    cookies={
                        "nubi_oauth_state": state_val,
                        "nubi_oauth_verifier": "verifier-xyz",
                    },
                )
        finally:
            for p in patches:
                p.stop()

        assert resp.status_code == 302, resp.text
        user = fake_db._user_by_email("fresh@example.com")
        assert user is not None, "OAuth user was not created"

        # Bare user only: no org, no membership, no project.
        assert fake_db.orgs == {}
        assert fake_db.org_members == {}
        assert state.projects == {}

    @pytest.mark.asyncio
    async def test_orgless_user_can_onboard_via_orgs_then_projects(self, client, fake_db):
        """Org-less user: GET /orgs == [], /auth/me OK, POST /orgs then POST /projects."""
        repo = InMemoryRepo()
        set_repo(repo)
        state = ProjectsState()
        user_id = str(uuid.uuid4())
        fake_db.users[user_id] = _user(user_id, "onboardee@example.com")

        patches = _projects_patches(state) + _orgs_route_patches(fake_db)
        for p in patches:
            p.start()
        try:
            # 1. /auth/me works without any org.
            me = await client.get("/api/v1/auth/me", headers=_auth(user_id))
            assert me.status_code == 200, me.text

            # 2. GET /orgs returns a clean empty list (no 500).
            orgs = await client.get("/api/v1/orgs", headers=_auth(user_id))
            assert orgs.status_code == 200, orgs.text
            assert orgs.json() == {"orgs": []}

            # 3. POST /orgs — first org for a user with ZERO memberships.
            created = await client.post(
                "/api/v1/orgs", json={"name": "First Org"}, headers=_auth(user_id)
            )
            assert created.status_code == 201, created.text
            org_id = created.json()["id"]
            assert created.json()["role"] == "owner"
            assert f"{org_id}:{user_id}" in fake_db.org_members
            # The org's Default project was created EMPTY.
            assert [p["name"] for p in state.for_org(org_id)] == ["Default"]
            assert await repo.list("datastores", org_id) == []

            # 4. POST /projects works for the new org (mirror membership into
            #    the repo double used for org resolution, as tests do).
            repo.seed_org_member(org_id, user_id, role="owner")
            proj = await client.post(
                "/api/v1/projects", json={"name": "Analytics"}, headers=_auth(user_id)
            )
            assert proj.status_code == 201, proj.text
            assert proj.json()["name"] == "Analytics"
            assert len(state.for_org(org_id)) == 2
            # Still empty — POST /projects no longer auto-seeds the sample bundle.
            assert await repo.list("datastores", org_id) == []
        finally:
            for p in patches:
                p.stop()
