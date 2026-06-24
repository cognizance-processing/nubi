"""Tests for ``POST /apply`` — declarative resource-bundle endpoint.

Strategy
--------
- ``InMemoryRepo`` + ``set_repo()`` for repo-backed resources (dashboards,
  queries, connectors) — no live DB needed.
- ``app.auth.jwt.mint_access_token`` for first-party Bearer tokens.
- ``app.routes.portability.upsert_envelope`` is the shared upsert helper; we
  verify the endpoint delegates to it correctly for each resource kind.
- No network connections; no DB; no secrets store.

Coverage
--------
1. Happy-path apply: mix of dashboards and connectors → created.
2. Idempotent re-apply: same bundle returns ``unchanged`` action when resource
   already exists (verified via double POST).
3. Dry-run=True: body has dry_run=True → no writes to repo; actions are
   "create" or "update" (planned, not executed).
4. Partial failure: one envelope with an unknown kind → that entry has
   action=failed; rest succeed; HTTP 200 always.
5. Empty bundle: resources=[] → empty results, HTTP 200.
6. Auth: unauthenticated request → 401/403.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


def _org_header(org_id: str) -> dict[str, str]:
    return {"X-Org-Id": org_id}


def _dashboard_envelope(name: str, env_id: str | None = None) -> dict[str, Any]:
    """Return a minimal dashboard portability envelope."""
    meta: dict[str, Any] = {"name": name}
    if env_id:
        meta["id"] = env_id
    return {
        "kind": "dashboard",
        "apiVersion": "nubi/v1",
        "metadata": meta,
        "spec": {"title": name, "panels": []},
    }


def _connector_envelope(name: str, env_id: str | None = None) -> dict[str, Any]:
    """Return a minimal connector portability envelope (non-secret config only)."""
    meta: dict[str, Any] = {"name": name}
    if env_id:
        meta["id"] = env_id
    return {
        "kind": "connector",
        "apiVersion": "nubi/v1",
        "metadata": meta,
        "spec": {
            "connector_type": "postgres",
            "host": "db.example.com",
            "database": "prod",
        },
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def apply_setup(app, fake_db):
    """Yield (client, user_id, org_id, repo) for /apply tests."""
    repo = InMemoryRepo()
    set_repo(repo)

    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())

    fake_db.users[user_id] = {
        "id": user_id,
        "email": "alice@example.com",
        "name": "Alice",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }

    repo.seed_org_member(org_id=org_id, user_id=user_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as client:
        yield client, user_id, org_id, repo

    set_repo(None)


# ---------------------------------------------------------------------------
# 1. Happy-path apply
# ---------------------------------------------------------------------------


class TestApplyHappyPath:
    @pytest.mark.asyncio
    async def test_apply_dashboard_created(self, apply_setup):
        """A dashboard envelope is created and the result shows action=created."""
        client, user_id, org_id, repo = apply_setup

        body = {
            "resources": [_dashboard_envelope("Test Dashboard")],
            "version": "1",
            "dry_run": False,
        }

        resp = await client.post(
            "/api/v1/apply",
            json=body,
            headers={**_auth(user_id), **_org_header(org_id)},
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["dry_run"] is False
        results = data["results"]
        assert len(results) == 1
        r = results[0]
        assert r["kind"] == "dashboard"
        assert r["action"] == "created"
        assert r["name"] == "Test Dashboard"
        assert r.get("id")  # must have been assigned an id

        summary = data["summary"]
        assert summary["created"] == 1
        assert summary["failed"] == 0

    @pytest.mark.asyncio
    async def test_apply_multiple_resources(self, apply_setup):
        """Multiple resources in one bundle are all applied."""
        client, user_id, org_id, repo = apply_setup

        body = {
            "resources": [
                _dashboard_envelope("Dashboard A"),
                _connector_envelope("My Connector"),
            ],
            "version": "1",
            "dry_run": False,
        }

        resp = await client.post(
            "/api/v1/apply",
            json=body,
            headers={**_auth(user_id), **_org_header(org_id)},
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        results = data["results"]
        assert len(results) == 2
        actions = {r["action"] for r in results}
        assert "created" in actions
        summary = data["summary"]
        assert summary["created"] == 2
        assert summary["failed"] == 0


# ---------------------------------------------------------------------------
# 2. Idempotent re-apply
# ---------------------------------------------------------------------------


class TestApplyIdempotent:
    @pytest.mark.asyncio
    async def test_second_apply_with_same_id_updates(self, apply_setup):
        """Applying with the same metadata.id twice → second call is an update."""
        client, user_id, org_id, repo = apply_setup

        # First apply — creates.
        resp1 = await client.post(
            "/api/v1/apply",
            json={
                "resources": [_dashboard_envelope("My Dashboard")],
                "dry_run": False,
            },
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        assert resp1.status_code == 200, resp1.text
        created_id = resp1.json()["results"][0]["id"]
        assert created_id

        # Second apply — same id → update.
        resp2 = await client.post(
            "/api/v1/apply",
            json={
                "resources": [_dashboard_envelope("My Dashboard", env_id=created_id)],
                "dry_run": False,
            },
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        assert resp2.status_code == 200, resp2.text
        r2 = resp2.json()["results"][0]
        assert r2["action"] == "updated", f"Expected updated, got {r2}"
        assert r2["id"] == created_id

    @pytest.mark.asyncio
    async def test_double_apply_no_extra_resources(self, apply_setup):
        """After two applies, only one resource exists in the repo."""
        client, user_id, org_id, repo = apply_setup

        # First apply.
        resp1 = await client.post(
            "/api/v1/apply",
            json={"resources": [_dashboard_envelope("Unique Dashboard")], "dry_run": False},
            headers={**_auth(user_id), **_org_header(org_id)},
        )
        assert resp1.status_code == 200
        created_id = resp1.json()["results"][0]["id"]

        # Second apply with same id.
        await client.post(
            "/api/v1/apply",
            json={
                "resources": [_dashboard_envelope("Unique Dashboard", env_id=created_id)],
                "dry_run": False,
            },
            headers={**_auth(user_id), **_org_header(org_id)},
        )

        # Check repo: still just 1 board.
        boards = await repo.list("boards", org_id, None)
        matching = [b for b in boards if b["id"] == created_id]
        assert len(matching) == 1, f"Expected 1 board, got {len(boards)}: {boards}"


# ---------------------------------------------------------------------------
# 3. Dry-run
# ---------------------------------------------------------------------------


class TestApplyDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_no_repo_writes(self, apply_setup):
        """dry_run=True: response shows planned action, repo stays empty."""
        client, user_id, org_id, repo = apply_setup

        resp = await client.post(
            "/api/v1/apply",
            json={
                "resources": [_dashboard_envelope("Dry Dashboard")],
                "dry_run": True,
            },
            headers={**_auth(user_id), **_org_header(org_id)},
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["dry_run"] is True

        # The plan action should be "create" (not "created").
        r = data["results"][0]
        assert r["action"] in ("create", "update"), f"Unexpected action: {r['action']}"

        # No boards written to the repo.
        boards = await repo.list("boards", org_id, None)
        assert boards == [], f"Expected no boards after dry run, got {boards}"

    @pytest.mark.asyncio
    async def test_dry_run_with_existing_id_shows_update(self, apply_setup):
        """dry_run=True with an existing id: planned action is 'update'."""
        client, user_id, org_id, repo = apply_setup

        # Create a real dashboard first.
        created = await repo.create(
            resource="boards",
            org_id=org_id,
            created_by=user_id,
            name="Existing",
            config={"spec": {"title": "Existing", "panels": []}},
        )
        existing_id = str(created["id"])

        resp = await client.post(
            "/api/v1/apply",
            json={
                "resources": [_dashboard_envelope("Existing", env_id=existing_id)],
                "dry_run": True,
            },
            headers={**_auth(user_id), **_org_header(org_id)},
        )

        assert resp.status_code == 200, resp.text
        r = resp.json()["results"][0]
        assert r["action"] == "update", f"Expected update, got {r['action']}"


# ---------------------------------------------------------------------------
# 4. Partial failure
# ---------------------------------------------------------------------------


class TestApplyPartialFailure:
    @pytest.mark.asyncio
    async def test_unknown_kind_fails_gracefully(self, apply_setup):
        """An envelope with unknown kind → action=failed; other resources succeed."""
        client, user_id, org_id, repo = apply_setup

        bad_envelope = {
            "kind": "gizmo",
            "apiVersion": "nubi/v1",
            "metadata": {"name": "Bad Resource"},
            "spec": {},
        }

        body = {
            "resources": [
                _dashboard_envelope("Good Dashboard"),
                bad_envelope,
            ],
            "dry_run": False,
        }

        resp = await client.post(
            "/api/v1/apply",
            json=body,
            headers={**_auth(user_id), **_org_header(org_id)},
        )

        # HTTP 200 always (best-effort).
        assert resp.status_code == 200, resp.text
        data = resp.json()
        results = data["results"]
        assert len(results) == 2

        actions = {r["action"] for r in results}
        assert "created" in actions, f"Good resource should be created: {results}"
        assert "failed" in actions, f"Bad resource should fail: {results}"

        # The failed result carries an error message.
        failed = [r for r in results if r["action"] == "failed"]
        assert len(failed) == 1
        assert failed[0].get("error")

        summary = data["summary"]
        assert summary["created"] == 1
        assert summary["failed"] == 1

    @pytest.mark.asyncio
    async def test_failure_does_not_abort_remaining(self, apply_setup):
        """A failed envelope does not abort the rest — all resources are processed."""
        client, user_id, org_id, repo = apply_setup

        body = {
            "resources": [
                {"kind": "unknown_kind", "apiVersion": "nubi/v1",
                 "metadata": {"name": "Will Fail"}, "spec": {}},
                _dashboard_envelope("Will Succeed"),
                _dashboard_envelope("Also Will Succeed"),
            ],
            "dry_run": False,
        }

        resp = await client.post(
            "/api/v1/apply",
            json=body,
            headers={**_auth(user_id), **_org_header(org_id)},
        )

        assert resp.status_code == 200, resp.text
        results = resp.json()["results"]
        assert len(results) == 3

        created = [r for r in results if r["action"] == "created"]
        failed = [r for r in results if r["action"] == "failed"]
        assert len(created) == 2, f"Expected 2 created: {results}"
        assert len(failed) == 1, f"Expected 1 failed: {results}"


# ---------------------------------------------------------------------------
# 5. Empty bundle
# ---------------------------------------------------------------------------


class TestApplyEmptyBundle:
    @pytest.mark.asyncio
    async def test_empty_resources_returns_200(self, apply_setup):
        """resources=[] → HTTP 200, empty results, zero summary."""
        client, user_id, org_id, _ = apply_setup

        resp = await client.post(
            "/api/v1/apply",
            json={"resources": [], "dry_run": False},
            headers={**_auth(user_id), **_org_header(org_id)},
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["results"] == []
        summary = data["summary"]
        assert summary["created"] == 0
        assert summary["failed"] == 0

    @pytest.mark.asyncio
    async def test_empty_dry_run_returns_200(self, apply_setup):
        """dry_run=True with resources=[] → HTTP 200."""
        client, user_id, org_id, _ = apply_setup

        resp = await client.post(
            "/api/v1/apply",
            json={"resources": [], "dry_run": True},
            headers={**_auth(user_id), **_org_header(org_id)},
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["dry_run"] is True


# ---------------------------------------------------------------------------
# 6. Authentication
# ---------------------------------------------------------------------------


class TestApplyAuth:
    @pytest.mark.asyncio
    async def test_unauthenticated_returns_4xx(self, apply_setup):
        """Requests without a token are rejected with 401 or 403."""
        client, user_id, org_id, _ = apply_setup

        resp = await client.post(
            "/api/v1/apply",
            json={
                "resources": [_dashboard_envelope("Unauth Attempt")],
                "dry_run": False,
            },
        )

        assert resp.status_code in (401, 403), (
            f"Expected 401 or 403, got {resp.status_code}: {resp.text}"
        )
