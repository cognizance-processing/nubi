"""Tests for the Flows AI tools (app/ai/flow_tools.py).

Each tool is executed via NullProvider (no API keys, no network).

Coverage
--------
1. list_flows — returns empty list for new org; includes flow after create.
2. create_flow — valid spec → id returned; invalid spec → valid=False.
3. run_flow — runs a noop flow to success; returns task_runs.
4. get_flow_run — returns state + task_runs after a run.
5. generate_flow — returns a spec with NullProvider; spec passes validate.
6. Tools are registered in the global registry (all_tools).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest

from app.flows.store import InMemoryFlowStore, set_flow_store

# The flow store is async; tests that touch it directly are async.  The AI tools
# themselves stay synchronous (the agent loop is sync) and bridge internally.
pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Test claims (first-party, no RLS restrictions)
# ---------------------------------------------------------------------------

_ORG_ID = str(uuid.uuid4())
_USER_ID = str(uuid.uuid4())

_CLAIMS: dict[str, Any] = {
    "kind": "access",
    "sub": _USER_ID,
    "org_id": _ORG_ID,
    "policies": {},
    "scope": ["read:*", "write:*"],
}

# Minimal valid noop flow spec — always succeeds without external dependencies.
_VALID_SPEC = {
    "version": 1,
    "name": "tool_test_flow",
    "params": [],
    "tasks": [
        {"key": "step1", "kind": "noop", "needs": [], "config": {}},
        {"key": "step2", "kind": "noop", "needs": ["step1"], "config": {}},
    ],
}

_BAD_SPEC = {
    "version": 1,
    "name": "bad",
    "tasks": [
        {"key": "q", "kind": "query", "needs": [], "config": {}},  # missing query_id/sql
    ],
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _inject_flow_store():
    """Inject a fresh InMemoryFlowStore before each test; reset after."""
    store = InMemoryFlowStore()
    set_flow_store(store)
    yield store
    set_flow_store(None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _invoke(tool_name: str, **kwargs: Any) -> dict[str, Any]:
    """Execute a tool via the global registry."""
    from app.ai.tools import execute_tool  # noqa: PLC0415
    return execute_tool(tool_name, kwargs, _CLAIMS)


# ---------------------------------------------------------------------------
# 1. list_flows
# ---------------------------------------------------------------------------


class TestListFlowsTool:
    async def test_empty_org_returns_empty_list(self):
        result = _invoke("list_flows")
        assert "flows" in result
        assert result["flows"] == []

    async def test_includes_created_flow(self, _inject_flow_store):
        store = _inject_flow_store
        await store.create_flow(
            org_id=_ORG_ID,
            created_by=_USER_ID,
            name="My Flow",
            spec=_VALID_SPEC,
        )

        result = _invoke("list_flows")
        assert len(result["flows"]) == 1
        assert result["flows"][0]["name"] == "My Flow"
        assert "id" in result["flows"][0]

    async def test_does_not_leak_other_org_flows(self, _inject_flow_store):
        store = _inject_flow_store
        # Create a flow for a different org.
        await store.create_flow(
            org_id=str(uuid.uuid4()),  # different org
            created_by=_USER_ID,
            name="Other Org Flow",
            spec=_VALID_SPEC,
        )

        result = _invoke("list_flows")
        assert result["flows"] == []


# ---------------------------------------------------------------------------
# 2. create_flow
# ---------------------------------------------------------------------------


class TestCreateFlowTool:
    async def test_create_valid_flow(self):
        result = _invoke("create_flow", name="New Flow", spec=_VALID_SPEC)
        assert result["valid"] is True
        assert result["id"] is not None
        assert isinstance(result["issues"], list)

    async def test_create_flow_is_persisted(self, _inject_flow_store):
        store = _inject_flow_store
        result = _invoke("create_flow", name="Persisted", spec=_VALID_SPEC)
        flow_id = result["id"]

        flow = await store.get_flow(flow_id)
        assert flow is not None
        assert flow["name"] == "Persisted"
        assert flow["org_id"] == _ORG_ID

    async def test_create_flow_bad_spec_returns_invalid(self):
        result = _invoke("create_flow", name="Bad", spec=_BAD_SPEC)
        assert result["valid"] is False
        assert result["id"] is None
        assert len(result["issues"]) > 0

    async def test_create_flow_cycle_returns_invalid(self):
        cycle_spec = {
            "version": 1,
            "name": "cycle",
            "tasks": [
                {"key": "a", "kind": "noop", "needs": ["b"], "config": {}},
                {"key": "b", "kind": "noop", "needs": ["a"], "config": {}},
            ],
        }
        result = _invoke("create_flow", name="Cycle", spec=cycle_spec)
        assert result["valid"] is False


# ---------------------------------------------------------------------------
# 3. run_flow
# ---------------------------------------------------------------------------


class TestRunFlowTool:
    async def test_run_noop_flow_succeeds(self, _inject_flow_store):
        store = _inject_flow_store
        # First create a flow.
        flow = await store.create_flow(
            org_id=_ORG_ID,
            created_by=_USER_ID,
            name="Runnable",
            spec=_VALID_SPEC,
        )

        result = _invoke("run_flow", flow_id=flow["id"])
        assert "flow_run_id" in result
        assert result["state"] == "success"
        assert "task_runs" in result

        task_runs = result["task_runs"]
        assert isinstance(task_runs, list)
        assert len(task_runs) == 2
        keys = {tr["task_key"] for tr in task_runs}
        assert keys == {"step1", "step2"}
        for tr in task_runs:
            assert tr["state"] == "success"

    async def test_run_flow_with_params(self, _inject_flow_store):
        store = _inject_flow_store
        spec = {
            "version": 1,
            "name": "p_flow",
            "params": [{"name": "x", "type": "text", "required": False, "default": "a"}],
            "tasks": [{"key": "s1", "kind": "noop", "needs": [], "config": {}}],
        }
        flow = await store.create_flow(
            org_id=_ORG_ID, created_by=_USER_ID, name="P Flow", spec=spec
        )
        result = _invoke("run_flow", flow_id=flow["id"], params={"x": "b"})
        assert result["state"] == "success"

    async def test_run_nonexistent_flow_raises(self):
        from app.errors import AppError
        with pytest.raises(AppError) as exc_info:
            _invoke("run_flow", flow_id=str(uuid.uuid4()))
        assert exc_info.value.status == 404


# ---------------------------------------------------------------------------
# 4. get_flow_run
# ---------------------------------------------------------------------------


class TestGetFlowRunTool:
    async def test_get_flow_run_after_run(self, _inject_flow_store):
        store = _inject_flow_store
        flow = await store.create_flow(
            org_id=_ORG_ID,
            created_by=_USER_ID,
            name="GFR Flow",
            spec=_VALID_SPEC,
        )

        run_result = _invoke("run_flow", flow_id=flow["id"])
        run_id = run_result["flow_run_id"]

        get_result = _invoke("get_flow_run", flow_run_id=run_id)
        assert get_result["state"] == "success"
        assert "task_runs" in get_result
        task_runs = get_result["task_runs"]
        assert len(task_runs) == 2
        for tr in task_runs:
            assert "task_key" in tr
            assert "state" in tr

    async def test_get_nonexistent_run_raises(self):
        from app.errors import AppError
        with pytest.raises(AppError) as exc_info:
            _invoke("get_flow_run", flow_run_id=str(uuid.uuid4()))
        assert exc_info.value.status == 404


# ---------------------------------------------------------------------------
# 5. generate_flow
# ---------------------------------------------------------------------------


class TestGenerateFlowTool:
    async def test_generate_flow_returns_spec(self):
        result = _invoke("generate_flow", question="Show daily revenue by region")
        assert "spec" in result
        spec = result["spec"]
        assert "tasks" in spec
        assert len(spec["tasks"]) >= 1

    async def test_generated_spec_passes_validation(self):
        from app.flows.spec import flow_spec_is_valid, validate_flow_spec  # noqa: PLC0415

        result = _invoke("generate_flow", question="Analyze sales data")
        spec = result["spec"]
        _, issues = validate_flow_spec(spec)
        assert flow_spec_is_valid(issues), f"Generated spec has hard errors: {issues}"

    async def test_generate_flow_provider_field(self):
        result = _invoke("generate_flow", question="test")
        assert "provider" in result
        # NullProvider is the default in tests.
        assert result["provider"] == "null"


# ---------------------------------------------------------------------------
# 6. Tools registered in the global registry
# ---------------------------------------------------------------------------


class TestFlowToolsRegistration:
    async def test_all_flow_tools_in_registry(self):
        from app.ai.tools import all_tools  # noqa: PLC0415

        names = {t.name for t in all_tools()}
        assert "list_flows" in names
        assert "create_flow" in names
        assert "run_flow" in names
        assert "get_flow_run" in names
        assert "generate_flow" in names

    async def test_original_tools_still_present(self):
        """Adding flow tools must not remove any existing tools."""
        from app.ai.tools import all_tools  # noqa: PLC0415

        names = {t.name for t in all_tools()}
        assert "get_schema" in names
        assert "run_query" in names
        assert "create_dashboard" in names
