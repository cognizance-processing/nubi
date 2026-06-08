"""Tests for org/project lifecycle endpoints.

Coverage
--------
Org:
1.  PATCH /orgs/{id} — rename succeeds, returns updated name.
2.  PATCH /orgs/{id} — avatar_url update.
3.  PATCH /orgs/{id} — 400 when no fields given.
4.  PATCH /orgs/{id} — 404 for unknown / foreign org.
5.  GET /orgs/{id}/deletion-impact — org with projects → can_delete=false + blocker.
6.  GET /orgs/{id}/deletion-impact — org with no projects → can_delete=true.
7.  DELETE /orgs/{id} — 409 when org has projects.
8.  DELETE /orgs/{id} — 422 when confirm_name mismatches.
9.  DELETE /orgs/{id} — 204 on successful delete (no projects, correct name).
10. DELETE /orgs/{id} — 404 for unknown org.

Project:
11. PATCH /projects/{id} — rename succeeds, returns updated name.
12. PATCH /projects/{id} — 400 when name is empty.
13. GET /projects/{id}/deletion-impact — last project → can_delete=false.
14. GET /projects/{id}/deletion-impact — not last project → can_delete=true.
15. DELETE /projects/{id} — 409 when it is the last project.
16. DELETE /projects/{id} — 422 when confirm_name mismatches.
17. DELETE /projects/{id} — 204 when second project deleted with correct confirm_name.
18. DELETE /projects/{id} — 404 for unknown project.

Strategy
--------
Orgs tests patch ``app.routes.orgs.fetchrow``, ``app.routes.orgs.fetch``,
``app.routes.orgs.execute``, and ``app.repos.projects.fetchrow`` /
``app.repos.projects.execute`` to avoid a live DB.

Project tests use ``InMemoryRepo`` (via ``set_repo``) + patch
``app.repos.projects.fetchrow`` / ``app.repos.projects.fetch`` /
``app.repos.projects.execute`` for the DB-backed projects_repo helpers.
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

import app.routes.orgs  # noqa: F401 — ensure routes self-register
import app.routes.projects  # noqa: F401 — ensure routes self-register


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _auth(user_id: str) -> dict[str, str]:
    """Return an Authorization header dict for *user_id*."""
    token = mint_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


def _user(user_id: str | None = None, email: str = "test@example.com") -> dict[str, Any]:
    uid = user_id or str(uuid.uuid4())
    return {
        "id": uid,
        "email": email,
        "name": "Test User",
        "avatar_url": None,
        "email_verified": True,
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
    }


def _org(org_id: str, name: str = "Test Org") -> dict[str, Any]:
    return {
        "id": org_id,
        "name": name,
        "slug": f"test-org-{org_id[:8]}",
        "avatar_url": None,
        "created_at": datetime.now(tz=timezone.utc),
    }


def _membership_row(org_id: str, name: str = "Test Org", role: str = "owner") -> dict[str, Any]:
    """Return the shape returned by the JOIN query in orgs.py."""
    return {"id": org_id, "name": name, "role": role}


# ---------------------------------------------------------------------------
# Org fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def org_client(app, fake_db):
    """HTTP client + (user_id, org_id) for org lifecycle tests.

    Patches ``app.routes.orgs.fetchrow``, ``app.routes.orgs.fetch``, and
    ``app.routes.orgs.execute`` to serve the seeded org.

    Yields
    ------
    (AsyncClient, user_id, org_id, org_name)
    """
    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    org_name = "Acme Corp"

    fake_db.users[user_id] = _user(user_id)
    fake_db.orgs[org_id] = _org(org_id, org_name)
    fake_db.org_members[f"{org_id}:{user_id}"] = {
        "org_id": org_id,
        "user_id": user_id,
        "role": "owner",
    }

    membership_row = _membership_row(org_id, org_name)

    # Track the current name so PATCH can update it.
    state: dict[str, Any] = {"name": org_name}

    async def _fetchrow(query: str, *args: Any) -> dict[str, Any] | None:
        q = query.upper().strip()
        # Membership JOIN query (used by _get_user_org_membership).
        if "ORG_MEMBERS" in q and "JOIN" in q:
            uid = str(args[0])
            oid = str(args[1]) if len(args) > 1 else ""
            if uid == user_id and oid == org_id:
                return {**membership_row, "name": state["name"]}
            return None
        # Raw name lookup for _get_org_name (SELECT name FROM orgs).
        if "SELECT NAME FROM ORGS" in q or "SELECT NAME FROM ORGS" in q.replace(" ", ""):
            oid = str(args[0])
            if oid == org_id:
                return {"name": state["name"]}
            return None
        return None

    async def _fetch(query: str, *args: Any) -> list[dict[str, Any]]:
        q = query.upper()
        if "ORG_MEMBERS" in q and "JOIN" in q:
            uid = str(args[0])
            if uid == user_id:
                return [{**membership_row, "name": state["name"]}]
        return []

    async def _execute(query: str, *args: Any) -> str:
        q = query.upper().strip()
        if q.startswith("UPDATE ORGS"):
            # Extract new name from args if present.
            # The update call passes values then org_id last.
            # We track name changes for assertion.
            if "NAME" in q:
                state["name"] = str(args[0])
        if q.startswith("DELETE FROM ORGS"):
            state["deleted"] = True
        return "OK"

    # projects_repo.count_projects hits app.repos.projects.fetchrow.
    # Default: org has NO projects (so delete is allowed unless test overrides).
    async def _proj_fetchrow(query: str, *args: Any) -> dict[str, Any] | None:
        q = query.upper().strip()
        if "COUNT(" in q and "PROJECTS" in q:
            return {"n": state.get("project_count", 0)}
        # Slug uniqueness probe — always None (no clash).
        if "SELECT 1 FROM PROJECTS" in q:
            return None
        return None

    async def _proj_fetch(query: str, *args: Any) -> list[dict[str, Any]]:
        return []

    with (
        patch("app.routes.orgs.fetchrow", side_effect=_fetchrow),
        patch("app.routes.orgs.fetch", side_effect=_fetch),
        patch("app.routes.orgs.execute", side_effect=_execute),
        patch("app.repos.projects.fetchrow", side_effect=_proj_fetchrow),
        patch("app.repos.projects.fetch", side_effect=_proj_fetch),
        patch("app.repos.projects.execute", new=AsyncMock(return_value="OK")),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
            follow_redirects=False,
        ) as ac:
            yield ac, user_id, org_id, state


# ---------------------------------------------------------------------------
# Project fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def project_client(app, fake_db):
    """HTTP client + (user_id, org_id, project_id) for project lifecycle tests.

    Uses ``InMemoryRepo`` so the resource-count helpers work, and patches
    ``app.repos.projects.*`` for the projects_repo DB helpers.

    Yields
    ------
    (AsyncClient, user_id, org_id, project_id, project_name, repo)
    """
    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    proj_id = str(uuid.uuid4())
    proj_name = "My Project"

    fake_db.users[user_id] = _user(user_id)

    repo = InMemoryRepo()
    repo.seed_org_member(org_id, user_id)
    set_repo(repo)

    # State for in-memory project store used by patched DB helpers.
    project_row: dict[str, Any] = {
        "id": proj_id,
        "org_id": org_id,
        "name": proj_name,
        "slug": "my-project",
        "created_by": user_id,
        "git": None,
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
        "updated_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    state: dict[str, Any] = {
        "projects": {proj_id: dict(project_row)},
        "count": 1,
    }

    async def _proj_fetchrow(query: str, *args: Any) -> dict[str, Any] | None:
        q = query.upper().strip()
        # count(*) query.
        if "COUNT(" in q and "PROJECTS" in q:
            return {"n": state["count"]}
        # SELECT * FROM projects WHERE id = $1 AND org_id = $2.
        if "SELECT * FROM PROJECTS" in q and "ORDER BY" not in q:
            pid = str(args[0])
            oid = str(args[1])
            row = state["projects"].get(pid)
            if row and str(row["org_id"]) == oid:
                return dict(row)
            return None
        # SELECT * FROM projects WHERE org_id = ... LIMIT 1 (default project).
        if "SELECT * FROM PROJECTS" in q and "LIMIT 1" in q:
            oid = str(args[0])
            for row in state["projects"].values():
                if str(row["org_id"]) == oid:
                    return dict(row)
            return None
        # Slug uniqueness probe — always None (no clash).
        if "SELECT 1 FROM PROJECTS" in q:
            return None
        return None

    async def _proj_fetch(query: str, *args: Any) -> list[dict[str, Any]]:
        q = query.upper().strip()
        if "SELECT * FROM PROJECTS" in q and "ORDER BY" in q:
            oid = str(args[0])
            return [
                dict(row) for row in state["projects"].values()
                if str(row["org_id"]) == oid
            ]
        return []

    async def _proj_execute(query: str, *args: Any) -> str:
        q = query.upper().strip()
        if q.startswith("DELETE FROM PROJECTS"):
            pid = str(args[0])
            oid = str(args[1])
            row = state["projects"].get(pid)
            if row and str(row["org_id"]) == oid:
                del state["projects"][pid]
                state["count"] = len(state["projects"])
                return "DELETE 1"
            return "DELETE 0"
        if q.startswith("UPDATE PROJECTS"):
            # We handle renames via the RETURNING fetchrow mock below.
            return "UPDATE 1"
        return "OK"

    # update_project uses a fetchrow (UPDATE ... RETURNING *).
    # We need to intercept and mutate state["projects"].
    _original_proj_fetchrow = _proj_fetchrow

    async def _proj_fetchrow_with_update(query: str, *args: Any) -> dict[str, Any] | None:
        q = query.upper().strip()
        if "UPDATE PROJECTS" in q and "RETURNING" in q:
            # Find the row, apply name update, return it.
            # args: [...values..., project_id, org_id]
            pid = str(args[-2])
            oid = str(args[-1])
            row = state["projects"].get(pid)
            if row and str(row["org_id"]) == oid:
                # Extract new name from args (it's the first positional arg
                # when name is being updated).
                new_name = str(args[0])
                row["name"] = new_name
                return dict(row)
            return None
        return await _original_proj_fetchrow(query, *args)

    with (
        patch("app.repos.projects.fetchrow", side_effect=_proj_fetchrow_with_update),
        patch("app.repos.projects.fetch", side_effect=_proj_fetch),
        patch("app.repos.projects.execute", side_effect=_proj_execute),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
            follow_redirects=False,
        ) as ac:
            yield ac, user_id, org_id, proj_id, proj_name, repo, state


# ---------------------------------------------------------------------------
# Org: PATCH /orgs/{id}
# ---------------------------------------------------------------------------

class TestPatchOrg:
    @pytest.mark.asyncio
    async def test_rename_org_returns_200(self, org_client):
        ac, user_id, org_id, state = org_client
        resp = await ac.patch(
            f"/api/v1/orgs/{org_id}",
            json={"name": "New Org Name"},
            headers=_auth(user_id),
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_rename_org_returns_new_name(self, org_client):
        ac, user_id, org_id, state = org_client
        resp = await ac.patch(
            f"/api/v1/orgs/{org_id}",
            json={"name": "Renamed Corp"},
            headers=_auth(user_id),
        )
        body = resp.json()
        assert body["name"] == "Renamed Corp"

    @pytest.mark.asyncio
    async def test_patch_org_avatar_url(self, org_client):
        ac, user_id, org_id, state = org_client
        resp = await ac.patch(
            f"/api/v1/orgs/{org_id}",
            json={"avatar_url": "https://cdn.example.com/logo.png"},
            headers=_auth(user_id),
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_patch_org_no_fields_returns_400(self, org_client):
        ac, user_id, org_id, state = org_client
        resp = await ac.patch(
            f"/api/v1/orgs/{org_id}",
            json={},
            headers=_auth(user_id),
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_patch_unknown_org_returns_404(self, org_client):
        ac, user_id, _org_id, state = org_client
        fake_id = str(uuid.uuid4())
        resp = await ac.patch(
            f"/api/v1/orgs/{fake_id}",
            json={"name": "Whatever"},
            headers=_auth(user_id),
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_patch_org_requires_auth(self, org_client):
        ac, user_id, org_id, state = org_client
        resp = await ac.patch(f"/api/v1/orgs/{org_id}", json={"name": "X"})
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Org: GET /orgs/{id}/deletion-impact
# ---------------------------------------------------------------------------

class TestOrgDeletionImpact:
    @pytest.mark.asyncio
    async def test_org_with_projects_cannot_delete(self, org_client):
        ac, user_id, org_id, state = org_client
        state["project_count"] = 2

        resp = await ac.get(
            f"/api/v1/orgs/{org_id}/deletion-impact",
            headers=_auth(user_id),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["can_delete"] is False
        blockers = body["blockers"]
        assert len(blockers) >= 1
        assert any(b["type"] == "projects" for b in blockers)

    @pytest.mark.asyncio
    async def test_org_with_projects_blocker_has_count(self, org_client):
        ac, user_id, org_id, state = org_client
        state["project_count"] = 3

        resp = await ac.get(
            f"/api/v1/orgs/{org_id}/deletion-impact",
            headers=_auth(user_id),
        )
        body = resp.json()
        blocker = next(b for b in body["blockers"] if b["type"] == "projects")
        assert blocker["count"] == 3

    @pytest.mark.asyncio
    async def test_org_without_projects_can_delete(self, org_client):
        ac, user_id, org_id, state = org_client
        state["project_count"] = 0

        resp = await ac.get(
            f"/api/v1/orgs/{org_id}/deletion-impact",
            headers=_auth(user_id),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["can_delete"] is True
        assert body["blockers"] == []

    @pytest.mark.asyncio
    async def test_org_impact_includes_name(self, org_client):
        ac, user_id, org_id, state = org_client
        resp = await ac.get(
            f"/api/v1/orgs/{org_id}/deletion-impact",
            headers=_auth(user_id),
        )
        body = resp.json()
        assert body["name"] == "Acme Corp"

    @pytest.mark.asyncio
    async def test_org_impact_unknown_org_returns_404(self, org_client):
        ac, user_id, _org_id, state = org_client
        resp = await ac.get(
            f"/api/v1/orgs/{uuid.uuid4()}/deletion-impact",
            headers=_auth(user_id),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Org: DELETE /orgs/{id}
# ---------------------------------------------------------------------------

class TestDeleteOrg:
    @pytest.mark.asyncio
    async def test_delete_org_with_projects_returns_409(self, org_client):
        ac, user_id, org_id, state = org_client
        state["project_count"] = 1

        resp = await ac.request(
            "DELETE",
            f"/api/v1/orgs/{org_id}",
            json={"confirm_name": "Acme Corp"},
            headers=_auth(user_id),
        )
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_delete_org_409_message_mentions_projects(self, org_client):
        ac, user_id, org_id, state = org_client
        state["project_count"] = 2

        resp = await ac.request(
            "DELETE",
            f"/api/v1/orgs/{org_id}",
            json={"confirm_name": "Acme Corp"},
            headers=_auth(user_id),
        )
        body = resp.json()
        assert "project" in body["error"]["message"].lower()

    @pytest.mark.asyncio
    async def test_delete_org_wrong_confirm_name_returns_422(self, org_client):
        ac, user_id, org_id, state = org_client
        state["project_count"] = 0

        resp = await ac.request(
            "DELETE",
            f"/api/v1/orgs/{org_id}",
            json={"confirm_name": "Wrong Name"},
            headers=_auth(user_id),
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_delete_org_correct_confirm_name_returns_204(self, org_client):
        ac, user_id, org_id, state = org_client
        state["project_count"] = 0

        resp = await ac.request(
            "DELETE",
            f"/api/v1/orgs/{org_id}",
            json={"confirm_name": "Acme Corp"},
            headers=_auth(user_id),
        )
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_delete_unknown_org_returns_404(self, org_client):
        ac, user_id, _org_id, state = org_client
        resp = await ac.request(
            "DELETE",
            f"/api/v1/orgs/{uuid.uuid4()}",
            json={"confirm_name": "Anything"},
            headers=_auth(user_id),
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_org_requires_auth(self, org_client):
        ac, user_id, org_id, state = org_client
        resp = await ac.request(
            "DELETE",
            f"/api/v1/orgs/{org_id}",
            json={"confirm_name": "Acme Corp"},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Project: PATCH /projects/{id}
# ---------------------------------------------------------------------------

class TestPatchProject:
    @pytest.mark.asyncio
    async def test_rename_project_returns_200(self, project_client):
        ac, user_id, org_id, proj_id, proj_name, repo, state = project_client
        resp = await ac.patch(
            f"/api/v1/projects/{proj_id}",
            json={"name": "Renamed Project"},
            headers={**_auth(user_id), "X-Org-Id": org_id},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_rename_project_body_has_new_name(self, project_client):
        ac, user_id, org_id, proj_id, proj_name, repo, state = project_client
        resp = await ac.patch(
            f"/api/v1/projects/{proj_id}",
            json={"name": "New Name"},
            headers={**_auth(user_id), "X-Org-Id": org_id},
        )
        body = resp.json()
        assert body.get("name") == "New Name"

    @pytest.mark.asyncio
    async def test_patch_project_empty_name_returns_400(self, project_client):
        ac, user_id, org_id, proj_id, proj_name, repo, state = project_client
        resp = await ac.patch(
            f"/api/v1/projects/{proj_id}",
            json={"name": "   "},
            headers={**_auth(user_id), "X-Org-Id": org_id},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_patch_unknown_project_returns_404(self, project_client):
        ac, user_id, org_id, _proj_id, proj_name, repo, state = project_client
        resp = await ac.patch(
            f"/api/v1/projects/{uuid.uuid4()}",
            json={"name": "X"},
            headers={**_auth(user_id), "X-Org-Id": org_id},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Project: GET /projects/{id}/deletion-impact
# ---------------------------------------------------------------------------

class TestProjectDeletionImpact:
    @pytest.mark.asyncio
    async def test_last_project_cannot_delete(self, project_client):
        ac, user_id, org_id, proj_id, proj_name, repo, state = project_client
        # Only one project in state.
        resp = await ac.get(
            f"/api/v1/projects/{proj_id}/deletion-impact",
            headers={**_auth(user_id), "X-Org-Id": org_id},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["can_delete"] is False
        assert any(b["type"] == "last_project" for b in body["blockers"])

    @pytest.mark.asyncio
    async def test_non_last_project_can_delete(self, project_client):
        ac, user_id, org_id, proj_id, proj_name, repo, state = project_client
        # Add a second project so the first is no longer the last.
        second_id = str(uuid.uuid4())
        state["projects"][second_id] = {
            "id": second_id,
            "org_id": org_id,
            "name": "Second Project",
            "slug": "second-project",
            "created_by": user_id,
            "git": None,
            "created_at": datetime.now(tz=timezone.utc).isoformat(),
            "updated_at": datetime.now(tz=timezone.utc).isoformat(),
        }
        state["count"] = 2

        resp = await ac.get(
            f"/api/v1/projects/{proj_id}/deletion-impact",
            headers={**_auth(user_id), "X-Org-Id": org_id},
        )
        body = resp.json()
        assert body["can_delete"] is True

    @pytest.mark.asyncio
    async def test_project_impact_includes_name(self, project_client):
        ac, user_id, org_id, proj_id, proj_name, repo, state = project_client
        resp = await ac.get(
            f"/api/v1/projects/{proj_id}/deletion-impact",
            headers={**_auth(user_id), "X-Org-Id": org_id},
        )
        body = resp.json()
        assert body["name"] == proj_name

    @pytest.mark.asyncio
    async def test_project_impact_unknown_returns_404(self, project_client):
        ac, user_id, org_id, _proj_id, proj_name, repo, state = project_client
        resp = await ac.get(
            f"/api/v1/projects/{uuid.uuid4()}/deletion-impact",
            headers={**_auth(user_id), "X-Org-Id": org_id},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Project: DELETE /projects/{id}
# ---------------------------------------------------------------------------

class TestDeleteProject:
    @pytest.mark.asyncio
    async def test_delete_last_project_returns_409(self, project_client):
        ac, user_id, org_id, proj_id, proj_name, repo, state = project_client
        resp = await ac.request(
            "DELETE",
            f"/api/v1/projects/{proj_id}",
            json={"confirm_name": proj_name},
            headers={**_auth(user_id), "X-Org-Id": org_id},
        )
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_delete_project_wrong_confirm_returns_422(self, project_client):
        ac, user_id, org_id, proj_id, proj_name, repo, state = project_client
        # Add second project so it's not the last.
        second_id = str(uuid.uuid4())
        state["projects"][second_id] = {
            "id": second_id,
            "org_id": org_id,
            "name": "Other Project",
            "slug": "other-project",
            "created_by": user_id,
            "git": None,
            "created_at": datetime.now(tz=timezone.utc).isoformat(),
            "updated_at": datetime.now(tz=timezone.utc).isoformat(),
        }
        state["count"] = 2

        resp = await ac.request(
            "DELETE",
            f"/api/v1/projects/{proj_id}",
            json={"confirm_name": "wrong name"},
            headers={**_auth(user_id), "X-Org-Id": org_id},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_delete_project_correct_confirm_returns_204(self, project_client):
        ac, user_id, org_id, proj_id, proj_name, repo, state = project_client
        # Add second project so first can be deleted.
        second_id = str(uuid.uuid4())
        state["projects"][second_id] = {
            "id": second_id,
            "org_id": org_id,
            "name": "Keep Project",
            "slug": "keep-project",
            "created_by": user_id,
            "git": None,
            "created_at": datetime.now(tz=timezone.utc).isoformat(),
            "updated_at": datetime.now(tz=timezone.utc).isoformat(),
        }
        state["count"] = 2

        resp = await ac.request(
            "DELETE",
            f"/api/v1/projects/{proj_id}",
            json={"confirm_name": proj_name},
            headers={**_auth(user_id), "X-Org-Id": org_id},
        )
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_delete_unknown_project_returns_404(self, project_client):
        ac, user_id, org_id, _proj_id, proj_name, repo, state = project_client
        resp = await ac.request(
            "DELETE",
            f"/api/v1/projects/{uuid.uuid4()}",
            json={"confirm_name": "whatever"},
            headers={**_auth(user_id), "X-Org-Id": org_id},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_project_requires_auth(self, project_client):
        ac, user_id, org_id, proj_id, proj_name, repo, state = project_client
        resp = await ac.request(
            "DELETE",
            f"/api/v1/projects/{proj_id}",
            json={"confirm_name": proj_name},
        )
        assert resp.status_code == 401
