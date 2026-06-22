"""Authz tests for flows routes — viewer gets 403 on mutating/compute endpoints.

Coverage
--------
1. POST /flows/preview  — viewer → 403; writer → 200.
2. POST /flows/run-cell — viewer → 403; writer → 200.
3. POST /flows          — viewer → 403; writer → 201.
4. PUT  /flows/{id}     — viewer → 403; writer → 200.
5. DELETE /flows/{id}   — viewer → 403; writer → 204.
6. POST /flows/{id}/run — viewer → 403; writer → 200.
7. POST /flows/notebooks — viewer → 403; writer → 201.
8. RLS re-snapshot: save_notebook update path re-snapshots owner policies
   when the existing notebook flow is scheduled/enabled.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.flows.store import InMemoryFlowStore, set_flow_store
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo
from app.routes.flows import OWNER_POLICIES_KEY


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(user_id: str | None = None, email: str = "user@example.com") -> dict[str, Any]:
    uid = user_id or str(uuid.uuid4())
    return {
        "id": uid,
        "email": email,
        "name": "User",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


def _auth_headers(user_id: str) -> dict[str, str]:
    token = mint_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


# Minimal noop flow spec (always succeeds, no external deps).
_NOOP_SPEC = {
    "version": 1,
    "name": "noop_flow",
    "params": [],
    "tasks": [
        {"key": "step1", "kind": "noop", "needs": [], "config": {}},
    ],
}

# Minimal notebook spec for save_notebook.
_NOTEBOOK_SPEC = {
    "version": 1,
    "name": "nb_flow",
    "notebook_id": "",
    "view": "notebook",
    "params": [],
    "tasks": [
        {
            "key": "cell_q",
            "kind": "query",
            "needs": [],
            "config": {"sql": "SELECT 1 AS n"},
            "cell_type": "sql",
            "execution_mode": "preview",
        }
    ],
    "execution_mode": "preview",
    "runtime_config": {},
    "source": "notebook",
}

# A single-cell inline spec for /preview and /run-cell.
_CELL_SPEC = {
    "version": 1,
    "name": "cell_flow",
    "params": [],
    "tasks": [
        {
            "key": "cell_demo",
            "kind": "query",
            "needs": [],
            "config": {"sql": "SELECT 1 AS n"},
            "cell_type": "sql",
            "execution_mode": "preview",
        }
    ],
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def authz_app(app):
    """App with fresh InMemoryFlowStore + InMemoryRepo injected."""
    store = InMemoryFlowStore()
    set_flow_store(store)

    repo = InMemoryRepo()
    set_repo(repo)

    yield app, store, repo

    set_flow_store(None)
    set_repo(None)


@pytest_asyncio.fixture
async def authz_env(authz_app, fake_db):
    """Yields (client, writer_id, viewer_id, org_id, store, repo).

    writer  = "member" role (may mutate)
    viewer  = "viewer" role (read-only)
    """
    app, store, repo = authz_app

    org_id = str(uuid.uuid4())

    writer_id = str(uuid.uuid4())
    fake_db.users[writer_id] = _make_user(writer_id, "writer@example.com")
    repo.seed_org_member(org_id=org_id, user_id=writer_id, role="member")

    viewer_id = str(uuid.uuid4())
    fake_db.users[viewer_id] = _make_user(viewer_id, "viewer@example.com")
    repo.seed_org_member(org_id=org_id, user_id=viewer_id, role="viewer")

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as client:
        yield client, writer_id, viewer_id, org_id, store, repo


# ---------------------------------------------------------------------------
# 1. POST /flows/preview — viewer 403; writer 200
# ---------------------------------------------------------------------------


class TestPreviewAuthz:
    @pytest.mark.asyncio
    async def test_viewer_gets_403_on_preview(self, authz_env):
        client, _writer, viewer_id, *_ = authz_env

        resp = await client.post(
            "/api/v1/flows/preview",
            json={"spec": _CELL_SPEC},
            headers=_auth_headers(viewer_id),
        )
        assert resp.status_code == 403, resp.text
        body = resp.json()
        assert body["error"]["code"] == "forbidden"

    @pytest.mark.asyncio
    async def test_writer_succeeds_on_preview(self, authz_env):
        client, writer_id, *_ = authz_env

        resp = await client.post(
            "/api/v1/flows/preview",
            json={"spec": _CELL_SPEC},
            headers=_auth_headers(writer_id),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "columns" in body
        assert "rows" in body


# ---------------------------------------------------------------------------
# 2. POST /flows/run-cell — viewer 403; writer 200
# ---------------------------------------------------------------------------


class TestRunCellAuthz:
    @pytest.mark.asyncio
    async def test_viewer_gets_403_on_run_cell(self, authz_env):
        client, _writer, viewer_id, *_ = authz_env

        resp = await client.post(
            "/api/v1/flows/run-cell",
            json={"spec": _CELL_SPEC},
            headers=_auth_headers(viewer_id),
        )
        assert resp.status_code == 403, resp.text
        body = resp.json()
        assert body["error"]["code"] == "forbidden"

    @pytest.mark.asyncio
    async def test_writer_succeeds_on_run_cell(self, authz_env):
        client, writer_id, *_ = authz_env

        resp = await client.post(
            "/api/v1/flows/run-cell",
            json={"spec": _CELL_SPEC, "cell_key": "cell_demo"},
            headers=_auth_headers(writer_id),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "rows" in body
        assert "cell_key" in body


# ---------------------------------------------------------------------------
# 3. POST /flows — viewer 403; writer 201
# ---------------------------------------------------------------------------


class TestCreateFlowAuthz:
    @pytest.mark.asyncio
    async def test_viewer_gets_403_on_create_flow(self, authz_env):
        client, _writer, viewer_id, *_ = authz_env

        resp = await client.post(
            "/api/v1/flows",
            json={"name": "Viewer Flow", "spec": _NOOP_SPEC},
            headers=_auth_headers(viewer_id),
        )
        assert resp.status_code == 403, resp.text

    @pytest.mark.asyncio
    async def test_writer_succeeds_on_create_flow(self, authz_env):
        client, writer_id, *_ = authz_env

        resp = await client.post(
            "/api/v1/flows",
            json={"name": "Writer Flow", "spec": _NOOP_SPEC},
            headers=_auth_headers(writer_id),
        )
        assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# 4. PUT /flows/{id} — viewer 403; writer 200
# ---------------------------------------------------------------------------


class TestUpdateFlowAuthz:
    @pytest.mark.asyncio
    async def test_viewer_gets_403_on_update_flow(self, authz_env):
        client, writer_id, viewer_id, org_id, store, *_ = authz_env

        # Writer creates the flow.
        create_resp = await client.post(
            "/api/v1/flows",
            json={"name": "My Flow", "spec": _NOOP_SPEC},
            headers=_auth_headers(writer_id),
        )
        assert create_resp.status_code == 201
        flow_id = create_resp.json()["id"]

        # Viewer tries to update it.
        resp = await client.put(
            f"/api/v1/flows/{flow_id}",
            json={"name": "Renamed"},
            headers=_auth_headers(viewer_id),
        )
        assert resp.status_code == 403, resp.text

    @pytest.mark.asyncio
    async def test_writer_succeeds_on_update_flow(self, authz_env):
        client, writer_id, *_ = authz_env

        create_resp = await client.post(
            "/api/v1/flows",
            json={"name": "Update Me", "spec": _NOOP_SPEC},
            headers=_auth_headers(writer_id),
        )
        assert create_resp.status_code == 201
        flow_id = create_resp.json()["id"]

        resp = await client.put(
            f"/api/v1/flows/{flow_id}",
            json={"name": "Renamed"},
            headers=_auth_headers(writer_id),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["name"] == "Renamed"


# ---------------------------------------------------------------------------
# 5. DELETE /flows/{id} — viewer 403; writer 204
# ---------------------------------------------------------------------------


class TestDeleteFlowAuthz:
    @pytest.mark.asyncio
    async def test_viewer_gets_403_on_delete_flow(self, authz_env):
        client, writer_id, viewer_id, *_ = authz_env

        create_resp = await client.post(
            "/api/v1/flows",
            json={"name": "To Delete", "spec": _NOOP_SPEC},
            headers=_auth_headers(writer_id),
        )
        assert create_resp.status_code == 201
        flow_id = create_resp.json()["id"]

        resp = await client.delete(
            f"/api/v1/flows/{flow_id}",
            headers=_auth_headers(viewer_id),
        )
        assert resp.status_code == 403, resp.text

    @pytest.mark.asyncio
    async def test_writer_succeeds_on_delete_flow(self, authz_env):
        client, writer_id, *_ = authz_env

        create_resp = await client.post(
            "/api/v1/flows",
            json={"name": "Delete Me", "spec": _NOOP_SPEC},
            headers=_auth_headers(writer_id),
        )
        assert create_resp.status_code == 201
        flow_id = create_resp.json()["id"]

        resp = await client.delete(
            f"/api/v1/flows/{flow_id}",
            headers=_auth_headers(writer_id),
        )
        assert resp.status_code == 204, resp.text


# ---------------------------------------------------------------------------
# 6. POST /flows/{id}/run — viewer 403; writer 200
# ---------------------------------------------------------------------------


class TestRunFlowAuthz:
    @pytest.mark.asyncio
    async def test_viewer_gets_403_on_run_flow(self, authz_env):
        client, writer_id, viewer_id, *_ = authz_env

        create_resp = await client.post(
            "/api/v1/flows",
            json={"name": "Runnable", "spec": _NOOP_SPEC},
            headers=_auth_headers(writer_id),
        )
        assert create_resp.status_code == 201
        flow_id = create_resp.json()["id"]

        resp = await client.post(
            f"/api/v1/flows/{flow_id}/run",
            json={},
            headers=_auth_headers(viewer_id),
        )
        assert resp.status_code == 403, resp.text

    @pytest.mark.asyncio
    async def test_writer_succeeds_on_run_flow(self, authz_env):
        client, writer_id, *_ = authz_env

        create_resp = await client.post(
            "/api/v1/flows",
            json={"name": "Runnable2", "spec": _NOOP_SPEC},
            headers=_auth_headers(writer_id),
        )
        assert create_resp.status_code == 201
        flow_id = create_resp.json()["id"]

        resp = await client.post(
            f"/api/v1/flows/{flow_id}/run",
            json={},
            headers=_auth_headers(writer_id),
        )
        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# 7. POST /flows/notebooks — viewer 403; writer 201
# ---------------------------------------------------------------------------


class TestSaveNotebookAuthz:
    @pytest.mark.asyncio
    async def test_viewer_gets_403_on_save_notebook(self, authz_env):
        client, _writer, viewer_id, *_ = authz_env

        resp = await client.post(
            "/api/v1/flows/notebooks",
            json={"notebook": _NOTEBOOK_SPEC},
            headers=_auth_headers(viewer_id),
        )
        assert resp.status_code == 403, resp.text

    @pytest.mark.asyncio
    async def test_writer_succeeds_on_save_notebook(self, authz_env):
        client, writer_id, *_ = authz_env

        resp = await client.post(
            "/api/v1/flows/notebooks",
            json={"notebook": _NOTEBOOK_SPEC},
            headers=_auth_headers(writer_id),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == "nb_flow"


# ---------------------------------------------------------------------------
# 8. RLS re-snapshot: save_notebook update path
#    When the existing notebook flow has a schedule or is enabled, saving it
#    again MUST re-snapshot the caller's owner policies onto the spec so the
#    scheduler runs under the last saver's RLS context.
# ---------------------------------------------------------------------------


class TestSaveNotebookRLSResnapshot:
    @pytest.mark.asyncio
    async def test_resnapshot_on_scheduled_notebook_update(self, authz_env, fake_db):
        """Updating a scheduled notebook flow re-snapshots owner policies."""
        client, writer_id, _viewer_id, org_id, store, repo = authz_env

        # ── Step 1: create a flow with a schedule via the flow store directly
        # (bypassing the API so we control the schedule field precisely).
        existing_spec = {
            "version": 1,
            "name": "nb_scheduled",
            "params": [],
            "tasks": [
                {
                    "key": "cell_q",
                    "kind": "query",
                    "needs": [],
                    "config": {"sql": "SELECT 2 AS m"},
                }
            ],
            # Deliberately EMPTY owner policies to simulate the stale-policy bug.
            "runtime_config": {OWNER_POLICIES_KEY: {}},
        }
        flow = await store.create_flow(
            org_id=org_id,
            created_by=writer_id,
            name="nb_scheduled",
            spec=existing_spec,
            enabled=True,
            schedule="@daily",
            next_run_at=None,
            project_id=None,
        )
        flow_id = str(flow["id"])

        # ── Step 2: call save_notebook with notebook_id pointing at that flow.
        notebook_spec_with_id = {
            **_NOTEBOOK_SPEC,
            "notebook_id": flow_id,
            "name": "nb_scheduled",
            "tasks": [
                {
                    "key": "cell_q",
                    "kind": "query",
                    "needs": [],
                    "config": {"sql": "SELECT 99 AS updated"},
                    "cell_type": "sql",
                    "execution_mode": "preview",
                }
            ],
        }
        resp = await client.post(
            "/api/v1/flows/notebooks",
            json={"notebook": notebook_spec_with_id},
            headers=_auth_headers(writer_id),
        )
        assert resp.status_code == 201, resp.text

        # ── Step 3: verify the stored spec now carries owner policies.
        updated_flow = await store.get_flow(flow_id)
        assert updated_flow is not None, "Flow should still exist after update"
        stored_spec = updated_flow.get("spec") or {}
        runtime_config = stored_spec.get("runtime_config") or {}
        assert OWNER_POLICIES_KEY in runtime_config, (
            "Owner policies must be re-snapshotted onto the spec when the "
            "existing notebook flow has a schedule. "
            f"runtime_config keys found: {list(runtime_config.keys())}"
        )

    @pytest.mark.asyncio
    async def test_resnapshot_on_enabled_notebook_update(self, authz_env):
        """Updating an enabled (but unscheduled) notebook flow also re-snapshots."""
        client, writer_id, _viewer_id, org_id, store, repo = authz_env

        # Create an enabled, unscheduled flow.
        existing_spec = {
            "version": 1,
            "name": "nb_enabled",
            "params": [],
            "tasks": [
                {
                    "key": "cell_q",
                    "kind": "query",
                    "needs": [],
                    "config": {"sql": "SELECT 3 AS k"},
                }
            ],
            "runtime_config": {},  # no owner policies yet
        }
        flow = await store.create_flow(
            org_id=org_id,
            created_by=writer_id,
            name="nb_enabled",
            spec=existing_spec,
            enabled=True,
            schedule=None,
            next_run_at=None,
            project_id=None,
        )
        flow_id = str(flow["id"])

        notebook_spec_with_id = {
            **_NOTEBOOK_SPEC,
            "notebook_id": flow_id,
            "name": "nb_enabled",
        }
        resp = await client.post(
            "/api/v1/flows/notebooks",
            json={"notebook": notebook_spec_with_id},
            headers=_auth_headers(writer_id),
        )
        assert resp.status_code == 201, resp.text

        updated_flow = await store.get_flow(flow_id)
        assert updated_flow is not None
        stored_spec = updated_flow.get("spec") or {}
        runtime_config = stored_spec.get("runtime_config") or {}
        assert OWNER_POLICIES_KEY in runtime_config, (
            "Owner policies must be snapshotted on enabled notebook flow updates."
        )

    @pytest.mark.asyncio
    async def test_new_notebook_gets_owner_policies_on_create(self, authz_env):
        """Creating a new notebook flow always snapshots owner policies."""
        client, writer_id, _viewer_id, org_id, store, repo = authz_env

        resp = await client.post(
            "/api/v1/flows/notebooks",
            json={"notebook": _NOTEBOOK_SPEC},
            headers=_auth_headers(writer_id),
        )
        assert resp.status_code == 201, resp.text
        flow_id = resp.json()["id"]

        created_flow = await store.get_flow(flow_id)
        assert created_flow is not None
        stored_spec = created_flow.get("spec") or {}
        runtime_config = stored_spec.get("runtime_config") or {}
        assert OWNER_POLICIES_KEY in runtime_config, (
            "Owner policies must be snapshotted on new notebook flow creation."
        )
