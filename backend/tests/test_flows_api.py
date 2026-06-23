"""Tests for the Flows REST API endpoints.

Coverage
--------
1. POST /flows          — 201 with valid spec; 400 on hard spec error; 400 on cycle.
2. GET  /flows          — list includes created flow.
3. GET  /flows/{id}     — 200; 404 cross-org.
4. PUT  /flows/{id}     — update name/enabled.
5. DELETE /flows/{id}   — 204.
6. POST /flows/validate — valid and invalid specs.
7. POST /flows/{id}/run — drains a linear 2-task noop flow to success; returns task_runs.
8. GET  /flows/{id}/runs — list includes the run.
9. GET  /flows/runs/{run_id} — returns flow_run + task_runs.
10. 401 without auth on all key endpoints.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.flows.store import InMemoryFlowStore, set_flow_store
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(user_id: str | None = None, email: str = "alice@example.com") -> dict[str, Any]:
    uid = user_id or str(uuid.uuid4())
    return {
        "id": uid,
        "email": email,
        "name": "Alice",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


def _auth_headers(user_id: str) -> dict[str, str]:
    token = mint_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


# A minimal 2-task linear flow using noop tasks (no external deps) that will
# always succeed in tests without a real query/agent backend.
_VALID_SPEC = {
    "version": 1,
    "name": "test_flow",
    "params": [],
    "tasks": [
        {
            "key": "step1",
            "kind": "noop",
            "needs": [],
            "config": {},
        },
        {
            "key": "step2",
            "kind": "noop",
            "needs": ["step1"],
            "config": {},
        },
    ],
}

_BAD_SPEC_MISSING_CONFIG = {
    "version": 1,
    "name": "bad_flow",
    "tasks": [
        {
            "key": "q1",
            "kind": "query",
            "needs": [],
            "config": {},  # missing query_id or sql → hard error
        },
    ],
}

_BAD_SPEC_CYCLE = {
    "version": 1,
    "name": "cycle_flow",
    "tasks": [
        {"key": "a", "kind": "noop", "needs": ["b"], "config": {}},
        {"key": "b", "kind": "noop", "needs": ["a"], "config": {}},
    ],
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def flows_app(app):
    """FastAPI app with InMemoryFlowStore + InMemoryRepo injected."""
    store = InMemoryFlowStore()
    set_flow_store(store)

    repo = InMemoryRepo()
    set_repo(repo)

    yield app, store, repo

    set_flow_store(None)
    set_repo(None)


@pytest_asyncio.fixture
async def flows_client(flows_app, fake_db):
    """Async HTTPX client pre-seeded with a user + org."""
    app, store, repo = flows_app

    alice_id = str(uuid.uuid4())
    alice_org_id = str(uuid.uuid4())
    alice = _make_user(user_id=alice_id, email="alice@example.com")

    fake_db.users[alice_id] = alice
    repo.seed_org_member(org_id=alice_org_id, user_id=alice_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as client:
        yield client, alice_id, alice_org_id, store, repo


# ---------------------------------------------------------------------------
# 1. POST /flows
# ---------------------------------------------------------------------------


class TestCreateFlow:
    @pytest.mark.asyncio
    async def test_create_flow_returns_201(self, flows_client):
        client, alice_id, org_id, store, repo = flows_client

        resp = await client.post(
            "/api/v1/flows",
            json={"name": "My Flow", "spec": _VALID_SPEC},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == "My Flow"
        assert body["org_id"] == org_id
        assert body["created_by"] == alice_id
        assert "id" in body
        assert "spec" in body

    @pytest.mark.asyncio
    async def test_create_flow_bad_spec_returns_400(self, flows_client):
        client, alice_id, *_ = flows_client

        resp = await client.post(
            "/api/v1/flows",
            json={"name": "Bad", "spec": _BAD_SPEC_MISSING_CONFIG},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 400, resp.text
        body = resp.json()
        assert body["error"]["code"] == "bad_flow_spec"

    @pytest.mark.asyncio
    async def test_create_flow_cycle_returns_400(self, flows_client):
        client, alice_id, *_ = flows_client

        resp = await client.post(
            "/api/v1/flows",
            json={"name": "Cyclic", "spec": _BAD_SPEC_CYCLE},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 400, resp.text
        body = resp.json()
        assert body["error"]["code"] == "bad_flow_spec"


# ---------------------------------------------------------------------------
# 2. GET /flows
# ---------------------------------------------------------------------------


class TestListFlows:
    @pytest.mark.asyncio
    async def test_list_includes_created_flow(self, flows_client):
        client, alice_id, org_id, *_ = flows_client

        await client.post(
            "/api/v1/flows",
            json={"name": "Flow A", "spec": _VALID_SPEC},
            headers=_auth_headers(alice_id),
        )

        resp = await client.get("/api/v1/flows", headers=_auth_headers(alice_id))
        assert resp.status_code == 200, resp.text
        flows = resp.json()
        assert isinstance(flows, list)
        assert any(f["name"] == "Flow A" for f in flows)


# ---------------------------------------------------------------------------
# 3. GET /flows/{id}
# ---------------------------------------------------------------------------


class TestGetFlow:
    @pytest.mark.asyncio
    async def test_get_flow_200(self, flows_client):
        client, alice_id, org_id, *_ = flows_client

        create_resp = await client.post(
            "/api/v1/flows",
            json={"name": "Gettable", "spec": _VALID_SPEC},
            headers=_auth_headers(alice_id),
        )
        flow_id = create_resp.json()["id"]

        resp = await client.get(f"/api/v1/flows/{flow_id}", headers=_auth_headers(alice_id))
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == flow_id

    @pytest.mark.asyncio
    async def test_get_flow_cross_org_404(self, flows_app, fake_db):
        app, store, repo = flows_app

        # Alice in org A
        alice_id = str(uuid.uuid4())
        alice_org = str(uuid.uuid4())
        fake_db.users[alice_id] = _make_user(alice_id, "alice@example.com")
        repo.seed_org_member(org_id=alice_org, user_id=alice_id)

        # Bob in org B
        bob_id = str(uuid.uuid4())
        bob_org = str(uuid.uuid4())
        fake_db.users[bob_id] = _make_user(bob_id, "bob@example.com")
        repo.seed_org_member(org_id=bob_org, user_id=bob_id)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver", follow_redirects=False) as client:
            create_resp = await client.post(
                "/api/v1/flows",
                json={"name": "Alice's Flow", "spec": _VALID_SPEC},
                headers=_auth_headers(alice_id),
            )
            assert create_resp.status_code == 201, create_resp.text
            flow_id = create_resp.json()["id"]

            # Bob tries to GET Alice's flow — must get 404.
            get_resp = await client.get(
                f"/api/v1/flows/{flow_id}",
                headers=_auth_headers(bob_id),
            )
            assert get_resp.status_code == 404, "Cross-org GET must return 404"


# ---------------------------------------------------------------------------
# 4. PUT /flows/{id}
# ---------------------------------------------------------------------------


class TestUpdateFlow:
    @pytest.mark.asyncio
    async def test_update_flow_name(self, flows_client):
        client, alice_id, org_id, *_ = flows_client

        create_resp = await client.post(
            "/api/v1/flows",
            json={"name": "Old Name", "spec": _VALID_SPEC},
            headers=_auth_headers(alice_id),
        )
        flow_id = create_resp.json()["id"]

        resp = await client.put(
            f"/api/v1/flows/{flow_id}",
            json={"name": "New Name"},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["name"] == "New Name"

    @pytest.mark.asyncio
    async def test_update_flow_enabled(self, flows_client):
        client, alice_id, org_id, *_ = flows_client

        create_resp = await client.post(
            "/api/v1/flows",
            json={"name": "Enabled Flow", "spec": _VALID_SPEC},
            headers=_auth_headers(alice_id),
        )
        flow_id = create_resp.json()["id"]

        resp = await client.put(
            f"/api/v1/flows/{flow_id}",
            json={"enabled": False},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["enabled"] is False


# ---------------------------------------------------------------------------
# 5. DELETE /flows/{id}
# ---------------------------------------------------------------------------


class TestDeleteFlow:
    @pytest.mark.asyncio
    async def test_delete_flow_204(self, flows_client):
        client, alice_id, org_id, *_ = flows_client

        create_resp = await client.post(
            "/api/v1/flows",
            json={"name": "Delete Me", "spec": _VALID_SPEC},
            headers=_auth_headers(alice_id),
        )
        flow_id = create_resp.json()["id"]

        del_resp = await client.delete(
            f"/api/v1/flows/{flow_id}",
            headers=_auth_headers(alice_id),
        )
        assert del_resp.status_code == 204

        get_resp = await client.get(
            f"/api/v1/flows/{flow_id}",
            headers=_auth_headers(alice_id),
        )
        assert get_resp.status_code == 404


# ---------------------------------------------------------------------------
# 6. POST /flows/validate
# ---------------------------------------------------------------------------


class TestValidateFlow:
    @pytest.mark.asyncio
    async def test_validate_valid_spec(self, flows_client):
        client, alice_id, *_ = flows_client

        resp = await client.post(
            "/api/v1/flows/validate",
            json={"spec": _VALID_SPEC},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["valid"] is True
        assert isinstance(body["issues"], list)

    @pytest.mark.asyncio
    async def test_validate_bad_spec(self, flows_client):
        client, alice_id, *_ = flows_client

        resp = await client.post(
            "/api/v1/flows/validate",
            json={"spec": _BAD_SPEC_MISSING_CONFIG},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["valid"] is False
        assert len(body["issues"]) > 0

    @pytest.mark.asyncio
    async def test_validate_cycle_spec(self, flows_client):
        client, alice_id, *_ = flows_client

        resp = await client.post(
            "/api/v1/flows/validate",
            json={"spec": _BAD_SPEC_CYCLE},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["valid"] is False
        assert any("Cycle" in i for i in body["issues"])


# ---------------------------------------------------------------------------
# 7. POST /flows/{id}/run
# ---------------------------------------------------------------------------


class TestRunFlow:
    @pytest.mark.asyncio
    async def test_run_flow_drains_to_success(self, flows_client):
        client, alice_id, org_id, *_ = flows_client

        # Create a flow with 2 noop tasks (always succeed).
        create_resp = await client.post(
            "/api/v1/flows",
            json={"name": "Runnable", "spec": _VALID_SPEC},
            headers=_auth_headers(alice_id),
        )
        assert create_resp.status_code == 201, create_resp.text
        flow_id = create_resp.json()["id"]

        run_resp = await client.post(
            f"/api/v1/flows/{flow_id}/run",
            json={},
            headers=_auth_headers(alice_id),
        )
        assert run_resp.status_code == 200, run_resp.text
        body = run_resp.json()

        # flow_run shape
        assert "id" in body
        assert body["state"] == "success"
        assert body["flow_id"] == flow_id

        # task_runs array
        assert "task_runs" in body
        task_runs = body["task_runs"]
        assert isinstance(task_runs, list)
        assert len(task_runs) == 2  # step1 + step2

        task_keys = {tr["task_key"] for tr in task_runs}
        assert task_keys == {"step1", "step2"}

        for tr in task_runs:
            assert tr["state"] == "success", f"Expected success, got {tr['state']} for {tr['task_key']}"

    @pytest.mark.asyncio
    async def test_run_flow_with_params(self, flows_client):
        client, alice_id, *_ = flows_client

        spec_with_params = {
            "version": 1,
            "name": "param_flow",
            "params": [{"name": "region", "type": "text", "default": "us", "required": False}],
            "tasks": [{"key": "s1", "kind": "noop", "needs": [], "config": {}}],
        }

        create_resp = await client.post(
            "/api/v1/flows",
            json={"name": "Param Flow", "spec": spec_with_params},
            headers=_auth_headers(alice_id),
        )
        flow_id = create_resp.json()["id"]

        run_resp = await client.post(
            f"/api/v1/flows/{flow_id}/run",
            json={"params": {"region": "eu"}},
            headers=_auth_headers(alice_id),
        )
        assert run_resp.status_code == 200, run_resp.text
        body = run_resp.json()
        assert body["state"] == "success"
        assert body["params"]["region"] == "eu"

    @pytest.mark.asyncio
    async def test_run_nonexistent_flow_404(self, flows_client):
        client, alice_id, *_ = flows_client

        resp = await client.post(
            f"/api/v1/flows/{uuid.uuid4()}/run",
            json={},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 8. GET /flows/{id}/runs
# ---------------------------------------------------------------------------


class TestListFlowRuns:
    @pytest.mark.asyncio
    async def test_list_runs_includes_run(self, flows_client):
        client, alice_id, *_ = flows_client

        create_resp = await client.post(
            "/api/v1/flows",
            json={"name": "Runs Flow", "spec": _VALID_SPEC},
            headers=_auth_headers(alice_id),
        )
        flow_id = create_resp.json()["id"]

        await client.post(
            f"/api/v1/flows/{flow_id}/run",
            json={},
            headers=_auth_headers(alice_id),
        )

        runs_resp = await client.get(
            f"/api/v1/flows/{flow_id}/runs",
            headers=_auth_headers(alice_id),
        )
        assert runs_resp.status_code == 200, runs_resp.text
        runs = runs_resp.json()
        assert isinstance(runs, list)
        assert len(runs) >= 1
        assert runs[0]["flow_id"] == flow_id


# ---------------------------------------------------------------------------
# 9. GET /flows/runs/{run_id}
# ---------------------------------------------------------------------------


class TestGetFlowRunById:
    @pytest.mark.asyncio
    async def test_get_flow_run_returns_task_runs(self, flows_client):
        client, alice_id, *_ = flows_client

        create_resp = await client.post(
            "/api/v1/flows",
            json={"name": "Run View Flow", "spec": _VALID_SPEC},
            headers=_auth_headers(alice_id),
        )
        flow_id = create_resp.json()["id"]

        run_resp = await client.post(
            f"/api/v1/flows/{flow_id}/run",
            json={},
            headers=_auth_headers(alice_id),
        )
        run_id = run_resp.json()["id"]

        get_resp = await client.get(
            f"/api/v1/flows/runs/{run_id}",
            headers=_auth_headers(alice_id),
        )
        assert get_resp.status_code == 200, get_resp.text
        body = get_resp.json()
        assert body["id"] == run_id
        assert "task_runs" in body
        assert isinstance(body["task_runs"], list)
        assert len(body["task_runs"]) == 2

    @pytest.mark.asyncio
    async def test_get_nonexistent_run_404(self, flows_client):
        client, alice_id, *_ = flows_client

        resp = await client.get(
            f"/api/v1/flows/runs/{uuid.uuid4()}",
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_run_cross_org_404(self, flows_app, fake_db):
        """Bob cannot see Alice's flow run."""
        app, store, repo = flows_app

        alice_id = str(uuid.uuid4())
        alice_org = str(uuid.uuid4())
        fake_db.users[alice_id] = _make_user(alice_id, "alice@example.com")
        repo.seed_org_member(org_id=alice_org, user_id=alice_id)

        bob_id = str(uuid.uuid4())
        bob_org = str(uuid.uuid4())
        fake_db.users[bob_id] = _make_user(bob_id, "bob@example.com")
        repo.seed_org_member(org_id=bob_org, user_id=bob_id)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver", follow_redirects=False) as client:
            create_resp = await client.post(
                "/api/v1/flows",
                json={"name": "Alice Flow", "spec": _VALID_SPEC},
                headers=_auth_headers(alice_id),
            )
            flow_id = create_resp.json()["id"]

            run_resp = await client.post(
                f"/api/v1/flows/{flow_id}/run",
                json={},
                headers=_auth_headers(alice_id),
            )
            run_id = run_resp.json()["id"]

            get_resp = await client.get(
                f"/api/v1/flows/runs/{run_id}",
                headers=_auth_headers(bob_id),
            )
            assert get_resp.status_code == 404, "Cross-org GET run must return 404"


# ---------------------------------------------------------------------------
# 10. 401 without auth
# ---------------------------------------------------------------------------


class TestFlowsAuthGuard:
    @pytest.mark.asyncio
    async def test_no_auth_list_returns_401(self, flows_client):
        client, *_ = flows_client
        resp = await client.get("/api/v1/flows")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_no_auth_create_returns_401(self, flows_client):
        client, *_ = flows_client
        resp = await client.post(
            "/api/v1/flows",
            json={"name": "x", "spec": _VALID_SPEC},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_no_auth_run_returns_401(self, flows_client):
        client, *_ = flows_client
        resp = await client.post(f"/api/v1/flows/{uuid.uuid4()}/run", json={})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_no_auth_validate_returns_401(self, flows_client):
        client, *_ = flows_client
        resp = await client.post("/api/v1/flows/validate", json={"spec": _VALID_SPEC})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_no_auth_get_run_returns_401(self, flows_client):
        client, *_ = flows_client
        resp = await client.get(f"/api/v1/flows/runs/{uuid.uuid4()}")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Scheduling: next_run_at on create/update + POST /flows/scheduled-query
# ---------------------------------------------------------------------------


class TestFlowScheduling:
    @pytest.mark.asyncio
    async def test_create_with_schedule_sets_next_run_at(self, flows_client):
        client, alice_id, *_ = flows_client
        resp = await client.post(
            "/api/v1/flows",
            json={"name": "Scheduled", "spec": _VALID_SPEC, "schedule": "interval:5m"},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["schedule"] == "interval:5m"
        assert body["next_run_at"] is not None

    @pytest.mark.asyncio
    async def test_create_without_schedule_has_no_next_run_at(self, flows_client):
        client, alice_id, *_ = flows_client
        resp = await client.post(
            "/api/v1/flows",
            json={"name": "Unscheduled", "spec": _VALID_SPEC},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["schedule"] is None
        assert body["next_run_at"] is None

    @pytest.mark.asyncio
    async def test_update_sets_and_clears_next_run_at(self, flows_client):
        client, alice_id, *_ = flows_client
        created = await client.post(
            "/api/v1/flows",
            json={"name": "ToSchedule", "spec": _VALID_SPEC},
            headers=_auth_headers(alice_id),
        )
        flow_id = created.json()["id"]

        # Set a schedule → next_run_at computed.
        upd = await client.put(
            f"/api/v1/flows/{flow_id}",
            json={"schedule": "interval:1h"},
            headers=_auth_headers(alice_id),
        )
        assert upd.status_code == 200, upd.text
        assert upd.json()["schedule"] == "interval:1h"
        assert upd.json()["next_run_at"] is not None

        # Clear the schedule → next_run_at cleared.
        cleared = await client.put(
            f"/api/v1/flows/{flow_id}",
            json={"schedule": None},
            headers=_auth_headers(alice_id),
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["schedule"] is None
        assert cleared.json()["next_run_at"] is None

    @pytest.mark.asyncio
    async def test_scheduled_query_creates_one_task_flow(self, flows_client):
        client, alice_id, org_id, *_ = flows_client
        resp = await client.post(
            "/api/v1/flows/scheduled-query",
            json={
                "name": "Daily Revenue",
                "query_id": "q-123",
                "schedule": "interval:1h",
                "params": {"region": "us"},
            },
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == "Daily Revenue"
        assert body["org_id"] == org_id
        assert body["enabled"] is True
        assert body["schedule"] == "interval:1h"
        assert body["next_run_at"] is not None
        tasks = body["spec"]["tasks"]
        assert len(tasks) == 1
        assert tasks[0]["kind"] == "query"
        assert tasks[0]["config"]["query_id"] == "q-123"
        assert tasks[0]["config"]["params"] == {"region": "us"}

    @pytest.mark.asyncio
    async def test_scheduled_query_requires_auth(self, flows_client):
        client, *_ = flows_client
        resp = await client.post(
            "/api/v1/flows/scheduled-query",
            json={"name": "x", "query_id": "q", "schedule": "interval:1h"},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# /flows/compile runs user Python through the hardened sandbox (run_sandboxed),
# NOT a raw subprocess.run that inherits the parent's full env / fs.
# ---------------------------------------------------------------------------


def test_compile_route_uses_run_sandboxed():
    """The compile route must dispatch user Python via
    app.compute.sandbox.run_sandboxed (the M4-SEC hardened path) and must NOT
    fall back to a raw subprocess.run."""
    import inspect

    from app.routes.flows import compile_code

    src = inspect.getsource(compile_code)
    assert "run_sandboxed(" in src, "compile route must use run_sandboxed"
    assert "subprocess.run(" not in src, (
        "compile route must NOT use raw subprocess.run (loses sandbox hardening)"
    )


def test_compile_route_maps_timeout_to_compile_error():
    """A sandbox timeout (run.timed_out) maps to AppError('compile_error', 400)."""
    import inspect

    from app.routes.flows import compile_code

    src = inspect.getsource(compile_code)
    assert "timed_out" in src
    # The timeout branch raises a compile_error AppError.
    assert "compile_error" in src


# ---------------------------------------------------------------------------
# SECURITY: owner RLS policy snapshot must NOT be exposed via GET /flows or
# GET /flows/{id}.  The stored spec (in the flow store) must still contain the
# snapshot so the scheduler tick can enforce RLS correctly.
# ---------------------------------------------------------------------------


class TestOwnerPoliciesNotExposed:
    """[LOW info-disclosure] __owner_policies__ must be stripped from outbound spec."""

    @pytest.mark.asyncio
    async def test_list_flows_omits_owner_policies(self, flows_client):
        from app.routes.flows import OWNER_POLICIES_KEY

        client, alice_id, org_id, store, repo = flows_client

        resp = await client.post(
            "/api/v1/flows",
            json={"name": "RLS Flow", "spec": _VALID_SPEC},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 201, resp.text

        list_resp = await client.get("/api/v1/flows", headers=_auth_headers(alice_id))
        assert list_resp.status_code == 200, list_resp.text

        flows = list_resp.json()
        assert len(flows) >= 1
        for flow in flows:
            spec = flow.get("spec") or {}
            rc = spec.get("runtime_config") or {}
            assert OWNER_POLICIES_KEY not in rc, (
                f"GET /flows must not expose {OWNER_POLICIES_KEY} in runtime_config"
            )

    @pytest.mark.asyncio
    async def test_get_flow_omits_owner_policies(self, flows_client):
        from app.routes.flows import OWNER_POLICIES_KEY

        client, alice_id, org_id, store, repo = flows_client

        create_resp = await client.post(
            "/api/v1/flows",
            json={"name": "RLS Flow Get", "spec": _VALID_SPEC},
            headers=_auth_headers(alice_id),
        )
        assert create_resp.status_code == 201, create_resp.text
        flow_id = create_resp.json()["id"]

        get_resp = await client.get(f"/api/v1/flows/{flow_id}", headers=_auth_headers(alice_id))
        assert get_resp.status_code == 200, get_resp.text

        spec = get_resp.json().get("spec") or {}
        rc = spec.get("runtime_config") or {}
        assert OWNER_POLICIES_KEY not in rc, (
            f"GET /flows/{{id}} must not expose {OWNER_POLICIES_KEY} in runtime_config"
        )

    @pytest.mark.asyncio
    async def test_owner_policies_snapshot_intact_in_store(self, flows_client):
        """Stripping the key from the response must NOT remove it from the store.

        The scheduler tick reads the snapshot from the stored spec, so after
        a create + GET the store must still hold __owner_policies__.
        """
        from app.routes.flows import OWNER_POLICIES_KEY

        client, alice_id, org_id, store, repo = flows_client

        create_resp = await client.post(
            "/api/v1/flows",
            json={"name": "RLS Store Check", "spec": _VALID_SPEC},
            headers=_auth_headers(alice_id),
        )
        assert create_resp.status_code == 201, create_resp.text
        flow_id = create_resp.json()["id"]

        # The API response must NOT expose the key.
        get_resp = await client.get(f"/api/v1/flows/{flow_id}", headers=_auth_headers(alice_id))
        assert get_resp.status_code == 200, get_resp.text
        api_spec = get_resp.json().get("spec") or {}
        api_rc = api_spec.get("runtime_config") or {}
        assert OWNER_POLICIES_KEY not in api_rc, "API response must not leak owner policies"

        # The in-memory store must STILL contain the snapshot.
        stored_flow = await store.get_flow(flow_id=flow_id)
        assert stored_flow is not None, "Flow should still exist in the store"
        stored_rc = (stored_flow.get("spec") or {}).get("runtime_config") or {}
        assert OWNER_POLICIES_KEY in stored_rc, (
            "Stored spec must retain __owner_policies__ for the scheduler tick"
        )


# ---------------------------------------------------------------------------
# FIX [HIGH resource] audit-43 #1: request-path runs are wall-clock bounded
# ---------------------------------------------------------------------------


class TestRunWallClockTimeout:
    """A pathological/slow drain must 504 at _RUN_TIMEOUT_S — never pin the worker."""

    @pytest.mark.asyncio
    async def test_run_flow_504_on_timeout(self, flows_client, monkeypatch):
        import asyncio as _asyncio

        import app.routes.flows as flows_mod

        client, alice_id, org_id, store, repo = flows_client

        create_resp = await client.post(
            "/api/v1/flows",
            json={"name": "Slow Flow", "spec": _VALID_SPEC},
            headers=_auth_headers(alice_id),
        )
        assert create_resp.status_code == 201, create_resp.text
        flow_id = create_resp.json()["id"]

        # Tighten the bound and make the underlying drain hang past it.
        monkeypatch.setattr(flows_mod, "_RUN_TIMEOUT_S", 0.05, raising=True)

        async def _hang(*_a, **_k):
            await _asyncio.sleep(5)
            return {}  # pragma: no cover — never reached

        monkeypatch.setattr(flows_mod, "drain_flow_run", _hang, raising=True)

        run_resp = await client.post(
            f"/api/v1/flows/{flow_id}/run",
            json={},
            headers=_auth_headers(alice_id),
        )
        assert run_resp.status_code == 504, run_resp.text
        assert run_resp.json()["error"]["code"] == "run_timeout"

    @pytest.mark.asyncio
    async def test_run_cell_504_on_timeout(self, flows_client, monkeypatch):
        import asyncio as _asyncio

        import app.routes.flows as flows_mod

        client, alice_id, org_id, store, repo = flows_client

        monkeypatch.setattr(flows_mod, "_RUN_TIMEOUT_S", 0.05, raising=True)

        async def _hang(*_a, **_k):
            await _asyncio.sleep(5)
            return {}  # pragma: no cover

        monkeypatch.setattr(flows_mod, "drain_flow_run", _hang, raising=True)

        resp = await client.post(
            "/api/v1/flows/run-cell",
            json={
                "spec": {
                    "version": 1,
                    "name": "rc",
                    "params": [],
                    "tasks": [{"key": "step1", "kind": "noop", "needs": [], "config": {}}],
                },
                "cell_key": "step1",
            },
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 504, resp.text
        assert resp.json()["error"]["code"] == "run_timeout"


# ---------------------------------------------------------------------------
# FIX [MED RLS] audit-43 #2: pinned-spec path also strips owner policies
# ---------------------------------------------------------------------------


class TestPinnedSpecStripsOwnerPolicies:
    @pytest.mark.asyncio
    async def test_env_pinned_spec_omits_owner_policies(self, flows_client, monkeypatch):
        """GET /flows/{id}?env=<key> serving a pinned snapshot must NOT leak the key."""
        import app.routes.flows as flows_mod
        from app.routes.flows import OWNER_POLICIES_KEY

        client, alice_id, org_id, store, repo = flows_client

        create_resp = await client.post(
            "/api/v1/flows",
            json={"name": "Pinned RLS", "spec": _VALID_SPEC},
            headers=_auth_headers(alice_id),
        )
        assert create_resp.status_code == 201, create_resp.text
        flow_id = create_resp.json()["id"]

        # Simulate a pinned version whose RAW config carries the owner snapshot
        # (this is exactly what version['config'] looks like in storage).
        pinned = {
            "version": 1,
            "name": "Pinned RLS",
            "params": [],
            "tasks": [{"key": "step1", "kind": "noop", "needs": [], "config": {}}],
            "runtime_config": {OWNER_POLICIES_KEY: {"tenant_id": "acme"}},
        }

        async def _fake_pinned(_flow, _org_id, _env_key):
            return pinned, {"id": "v1", "version": 1}

        monkeypatch.setattr(flows_mod, "_pinned_version_for_env", _fake_pinned, raising=True)

        resp = await client.get(
            f"/api/v1/flows/{flow_id}?env=prod", headers=_auth_headers(alice_id)
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        rc = (body.get("spec") or {}).get("runtime_config") or {}
        assert OWNER_POLICIES_KEY not in rc, (
            f"pinned-spec path must not expose {OWNER_POLICIES_KEY}"
        )
        assert body.get("resolved_version") == {"id": "v1", "version": 1}
