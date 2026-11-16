"""A registered query is only ever resolved for the organisation that owns it."""

from __future__ import annotations

import pathlib
import uuid
from datetime import datetime, timezone

import pytest

from tests.security import conftest_helpers  # noqa: F401  (env bootstrap)

ORG_A = str(uuid.uuid4())
ORG_B = str(uuid.uuid4())


@pytest.fixture
def other_orgs_query():
    from app.queries.registry import get_query_registry

    registry = get_query_registry()
    qid = f"sec-scope-{uuid.uuid4()}"
    registry.register(id=qid, sql="SELECT 42 AS secret_number", name="theirs", owner_org_id=ORG_B, internal=True)
    yield qid
    try:
        registry._queries.pop(qid, None)  # type: ignore[attr-defined]
    except AttributeError:
        pass


def test_lookup_helper_checks_the_owner(other_orgs_query):
    from app.queries.registry import registered_query_for_org

    assert registered_query_for_org(other_orgs_query, ORG_B) is not None
    assert registered_query_for_org(other_orgs_query, ORG_A) is None
    assert registered_query_for_org(other_orgs_query, None) is None
    assert registered_query_for_org("demo_all", ORG_A) is not None  # built-ins stay visible
    assert registered_query_for_org("no-such-query", ORG_A) is None


@pytest.mark.parametrize("org", [ORG_A, None])
def test_flow_query_task_cannot_bind_another_orgs_query(other_orgs_query, org):
    from app.errors import AppError
    from app.flows.executor import TaskContext
    from app.flows.registry import _handle_query

    ctx = TaskContext(flow_params={}, inputs={}, now=datetime.now(timezone.utc), secrets={}, org_id=org)
    with pytest.raises(AppError) as exc:
        _handle_query({"query_id": other_orgs_query}, ctx, {"org": org, "org_id": org})
    assert exc.value.code == "query_not_found"


def test_flow_bridge_query_cannot_bind_another_orgs_query(other_orgs_query):
    from app.errors import AppError
    from app.flows import executor
    from app.flows.executor import TaskContext

    ctx = TaskContext(flow_params={}, inputs={}, now=datetime.now(timezone.utc), secrets={}, org_id=ORG_A)
    with pytest.raises(AppError) as exc:
        executor._execute_query_with_bridge({"query_id": other_orgs_query}, ctx, {"org": ORG_A}, {})
    assert exc.value.code == "query_not_found"


def test_owner_can_still_bind_its_query(other_orgs_query):
    from app.flows.executor import TaskContext
    from app.flows.registry import _handle_query

    ctx = TaskContext(flow_params={}, inputs={}, now=datetime.now(timezone.utc), secrets={}, org_id=ORG_B)
    result = _handle_query({"query_id": other_orgs_query}, ctx, {"org": ORG_B, "org_id": ORG_B})
    assert result["rows"] == [{"secret_number": 42}]


@pytest.mark.asyncio
async def test_compute_run_cannot_bind_another_orgs_query(other_orgs_query):
    from unittest.mock import AsyncMock, patch

    from httpx import ASGITransport, AsyncClient

    from app.auth.jwt import mint_access_token

    patches = [
        patch("app.db.fetchrow", new=AsyncMock(return_value=None)),
        patch("app.db.fetch", new=AsyncMock(return_value=[])),
        patch("app.db.execute", new=AsyncMock(return_value="OK")),
        patch("app.db.init_db", new=AsyncMock()),
        patch("app.db.close_db", new=AsyncMock()),
        patch("app.auth.deps.fetchrow", new=AsyncMock(return_value=None)),
    ]
    for p in patches:
        p.start()
    try:
        import main as main_module

        app = main_module.create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            headers = {"Authorization": f"Bearer {mint_access_token('sec-scope-user')}"}
            for body in (
                {"code": "result = inputs['x']", "inputs": {"x": other_orgs_query}},
                {"code": "result = inputs['input']", "input_query_id": other_orgs_query},
            ):
                resp = await client.post("/api/v1/compute/run", json=body, headers=headers)
                assert resp.status_code == 404, resp.text
                assert resp.json()["error"]["code"] == "query_not_found"
                assert b"secret_number" not in resp.content
    finally:
        for p in patches:
            p.stop()


def test_request_paths_do_not_look_queries_up_without_an_owner():
    root = pathlib.Path(__file__).resolve().parents[2] / "app"
    for rel in ("routes/compute.py", "flows/executor.py", "flows/registry.py", "jobs/report.py"):
        text = (root / rel).read_text()
        assert "registry.get(query_id)" not in text, rel
