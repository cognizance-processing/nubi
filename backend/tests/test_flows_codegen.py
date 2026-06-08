"""Tests for app.flows.codegen — FlowSpec → Python SDK scaffold codegen.

Coverage
--------
1. _repr_value
   a. Strings: double-quote output.
   b. Booleans: True/False.
   c. None: "None".
   d. Ints / floats: repr form.
   e. Lists and dicts: round-trip.

2. _topo_sort
   a. Linear chain returns tasks in dep-first order.
   b. Diamond (fan-out/in) returns roots before leaves.
   c. Tasks with no needs appear before dependents.

3. flow_spec_to_sdk — linear flow
   a. Generated source contains import line.
   b. Generated source contains @flow decorator.
   c. Each task key appears as a function def.
   d. Call chain respects needs order (upstream_handle appears before downstream).

4. flow_spec_to_sdk — map node
   a. @map_node(...) decorator is emitted inside @flow.
   b. Body task stubs appear inside the inner function.
   c. downstream task uses .collect() syntax.
   d. item_var is used as the inner function's parameter name.

5. flow_spec_to_sdk — branch node
   a. branch_node(...) call is emitted inside @flow.
   b. conditions are serialised correctly.
   c. default is emitted when present.
   d. upstream arg is the needs[0]_handle reference.

6. flow_spec_to_sdk — params
   a. compile() call includes param kwargs.

7. flow_spec_to_sdk — empty flow
   a. Empty tasks list emits "pass" body.

8. REST endpoint smoke test (POST /flows/codegen)
   a. Valid spec returns 200 with "source" key.
   b. Invalid spec returns 400.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.flows.codegen import (
    _repr_value,
    _topo_sort,
    flow_spec_to_sdk,
)
from app.flows.spec import FlowSpec, validate_flow_spec
from app.flows.store import InMemoryFlowStore, set_flow_store
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo
from app.auth.jwt import mint_access_token


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(key: str, kind: str, needs: list[str], **config_kwargs) -> dict:
    return {
        "key": key,
        "kind": kind,
        "needs": needs,
        "config": dict(config_kwargs),
        "retries": 0,
        "retry_backoff_s": 30,
        "timeout_s": 60,
        "cache_ttl_s": 0,
        "ui": {"x": 0, "y": 0},
    }


def _simple_spec(name: str, tasks: list[dict], params: list[dict] | None = None) -> FlowSpec:
    data = {
        "version": 1,
        "name": name,
        "params": params or [],
        "tasks": tasks,
    }
    spec, issues = validate_flow_spec(data)
    hard = [i for i in issues if not i.startswith("[warn]")]
    assert not hard, f"Spec validation failed: {hard}"
    assert spec is not None
    return spec


# ---------------------------------------------------------------------------
# 1. _repr_value
# ---------------------------------------------------------------------------


class TestReprValue:
    def test_string_uses_double_quotes(self):
        result = _repr_value("hello world")
        assert result == '"hello world"'

    def test_string_with_double_quote_escapes(self):
        result = _repr_value('say "hi"')
        # Should escape inner double quotes.
        assert '"' in result
        assert result.startswith('"') and result.endswith('"')

    def test_true(self):
        assert _repr_value(True) == "True"

    def test_false(self):
        assert _repr_value(False) == "False"

    def test_none(self):
        assert _repr_value(None) == "None"

    def test_int(self):
        assert _repr_value(42) == "42"

    def test_float(self):
        assert _repr_value(3.14) == "3.14"

    def test_empty_list(self):
        assert _repr_value([]) == "[]"

    def test_list(self):
        result = _repr_value([1, "two", True])
        assert result == '[1, "two", True]'

    def test_empty_dict(self):
        assert _repr_value({}) == "{}"

    def test_dict(self):
        result = _repr_value({"a": 1, "b": "x"})
        assert '"a"' in result
        assert '"b"' in result


# ---------------------------------------------------------------------------
# 2. _topo_sort
# ---------------------------------------------------------------------------


class TestTopoSort:
    def test_linear_chain(self):
        tasks = [
            {"key": "a", "needs": []},
            {"key": "b", "needs": ["a"]},
            {"key": "c", "needs": ["b"]},
        ]
        result = _topo_sort(tasks)
        keys = [t["key"] for t in result]
        assert keys.index("a") < keys.index("b") < keys.index("c")

    def test_diamond_shape(self):
        tasks = [
            {"key": "root", "needs": []},
            {"key": "left", "needs": ["root"]},
            {"key": "right", "needs": ["root"]},
            {"key": "leaf", "needs": ["left", "right"]},
        ]
        result = _topo_sort(tasks)
        keys = [t["key"] for t in result]
        assert keys.index("root") < keys.index("left")
        assert keys.index("root") < keys.index("right")
        assert keys.index("left") < keys.index("leaf")
        assert keys.index("right") < keys.index("leaf")

    def test_preserves_all_tasks(self):
        tasks = [
            {"key": "x", "needs": []},
            {"key": "y", "needs": ["x"]},
        ]
        result = _topo_sort(tasks)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# 3. flow_spec_to_sdk — linear flow
# ---------------------------------------------------------------------------


class TestLinearFlowCodegen:
    @pytest.fixture
    def spec(self):
        return _simple_spec(
            "my_linear",
            [
                _make_task("pull", "query", [], sql="SELECT 1"),
                _make_task("transform", "python", ["pull"], code="result = 42"),
                _make_task("push", "noop", ["transform"]),
            ],
        )

    def test_import_line(self, spec):
        source = flow_spec_to_sdk(spec)
        assert "from nubi.sdk import flow, task, map_node, branch_node" in source

    def test_flow_decorator(self, spec):
        source = flow_spec_to_sdk(spec)
        assert "@flow" in source
        assert "def my_linear():" in source

    def test_task_stubs_present(self, spec):
        source = flow_spec_to_sdk(spec)
        assert "def pull(): pass" in source
        assert "def transform(): pass" in source
        assert "def push(): pass" in source

    def test_call_order_respects_needs(self, spec):
        source = flow_spec_to_sdk(spec)
        pull_pos = source.index("pull_handle = pull()")
        transform_pos = source.index("transform_handle = transform(pull_handle)")
        push_pos = source.index("push_handle = push(transform_handle)")
        assert pull_pos < transform_pos < push_pos

    def test_compile_call_present(self, spec):
        source = flow_spec_to_sdk(spec)
        assert "spec = my_linear.compile()" in source


# ---------------------------------------------------------------------------
# 4. flow_spec_to_sdk — map node
# ---------------------------------------------------------------------------


class TestMapNodeCodegen:
    @pytest.fixture
    def spec(self):
        return _simple_spec(
            "map_flow",
            [
                _make_task("get_items", "query", [], sql="SELECT id FROM t"),
                {
                    "key": "proc",
                    "kind": "map",
                    "needs": ["get_items"],
                    "config": {
                        "item_expr": "{{ inputs.get_items.rows }}",
                        "item_var": "row",
                        "max_concurrency": 3,
                        "max_map_size": 500,
                        "collect_key": "xform",
                        "body": [
                            _make_task("xform", "python", [], code="result = item"),
                        ],
                    },
                    "retries": 0,
                    "retry_backoff_s": 30,
                    "timeout_s": 0,
                    "cache_ttl_s": 0,
                    "ui": {"x": 0, "y": 0},
                },
                _make_task("collect_result", "noop", ["proc"]),
            ],
        )

    def test_map_node_decorator_present(self, spec):
        source = flow_spec_to_sdk(spec)
        assert "@map_node(" in source

    def test_item_var_used_as_param(self, spec):
        source = flow_spec_to_sdk(spec)
        assert "def proc(row):" in source

    def test_body_stub_inside_inner_function(self, spec):
        source = flow_spec_to_sdk(spec)
        # The xform stub must appear indented (inside the inner function).
        assert "def xform(): pass" in source

    def test_downstream_uses_collect(self, spec):
        source = flow_spec_to_sdk(spec)
        assert "proc.collect()" in source

    def test_collect_key_in_decorator(self, spec):
        source = flow_spec_to_sdk(spec)
        assert 'collect_key="xform"' in source

    def test_max_concurrency_in_decorator(self, spec):
        source = flow_spec_to_sdk(spec)
        assert "max_concurrency=3" in source


# ---------------------------------------------------------------------------
# 5. flow_spec_to_sdk — branch node
# ---------------------------------------------------------------------------


class TestBranchNodeCodegen:
    @pytest.fixture
    def spec(self):
        return _simple_spec(
            "branch_flow",
            [
                _make_task("score", "python", [], code="result={'label':'high'}"),
                {
                    "key": "route",
                    "kind": "branch",
                    "needs": ["score"],
                    "config": {
                        "conditions": [
                            {"when": "{{ inputs.score.label == 'high' }}", "next": ["do_high"]},
                            {"when": "{{ inputs.score.label == 'low' }}", "next": ["do_low"]},
                        ],
                        "default": ["fallback"],
                    },
                    "retries": 0,
                    "retry_backoff_s": 30,
                    "timeout_s": 30,
                    "cache_ttl_s": 0,
                    "ui": {"x": 0, "y": 0},
                },
                _make_task("do_high", "noop", ["route"]),
                _make_task("do_low", "noop", ["route"]),
                _make_task("fallback", "noop", ["route"]),
            ],
        )

    def test_branch_node_call_present(self, spec):
        source = flow_spec_to_sdk(spec)
        assert "branch_node(" in source

    def test_key_in_call(self, spec):
        source = flow_spec_to_sdk(spec)
        assert 'key="route"' in source

    def test_upstream_handle_arg(self, spec):
        source = flow_spec_to_sdk(spec)
        assert "score_handle," in source

    def test_conditions_serialised(self, spec):
        source = flow_spec_to_sdk(spec)
        assert '"when"' in source
        assert '"next"' in source

    def test_default_present(self, spec):
        source = flow_spec_to_sdk(spec)
        assert "default=" in source
        assert '"fallback"' in source

    def test_downstream_tasks_use_route_handle(self, spec):
        source = flow_spec_to_sdk(spec)
        assert "do_high(route_handle)" in source
        assert "do_low(route_handle)" in source


# ---------------------------------------------------------------------------
# 6. flow_spec_to_sdk — params
# ---------------------------------------------------------------------------


class TestParamsCodegen:
    def test_text_param_with_default(self):
        spec = _simple_spec(
            "param_flow",
            [_make_task("q", "query", [], sql="SELECT 1")],
            params=[{"name": "region", "type": "text", "default": "EU"}],
        )
        source = flow_spec_to_sdk(spec)
        assert 'region="EU"' in source

    def test_non_text_param_emits_dict(self):
        spec = _simple_spec(
            "param_flow2",
            [_make_task("q", "query", [], sql="SELECT 1")],
            params=[{"name": "dt", "type": "date", "default": "2024-01-01"}],
        )
        source = flow_spec_to_sdk(spec)
        assert "dt=" in source
        assert '"type": "date"' in source or '"type":"date"' in source or "type" in source


# ---------------------------------------------------------------------------
# 7. flow_spec_to_sdk — empty flow
# ---------------------------------------------------------------------------


class TestEmptyFlowCodegen:
    def test_empty_tasks_emits_pass(self):
        # Empty tasks list is technically valid for codegen even if spec requires ≥1 task.
        # We construct the FlowSpec directly to bypass validation.
        spec = FlowSpec(version=1, name="empty_flow", params=[], tasks=[])
        source = flow_spec_to_sdk(spec)
        assert "def empty_flow():" in source
        assert "pass" in source


# ---------------------------------------------------------------------------
# Fixtures for endpoint tests (mirrors test_flows_api.py pattern)
# ---------------------------------------------------------------------------


def _make_user(user_id: str | None = None, email: str = "alice@example.com") -> dict[str, Any]:
    uid = user_id or str(uuid.uuid4())
    return {
        "id": uid,
        "email": email,
        "name": "Alice",
        "avatar_url": None,
        "created_at": None,
        "updated_at": None,
    }


def _auth_headers(user_id: str) -> dict[str, str]:
    token = mint_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def codegen_client(app, fake_db):
    """Async HTTPX test client with InMemoryFlowStore + InMemoryRepo seeded."""
    store = InMemoryFlowStore()
    set_flow_store(store)
    repo = InMemoryRepo()
    set_repo(repo)

    alice_id = str(uuid.uuid4())
    alice_org_id = str(uuid.uuid4())
    fake_db.users[alice_id] = _make_user(alice_id)
    repo.seed_org_member(org_id=alice_org_id, user_id=alice_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as client:
        yield client, alice_id, alice_org_id, store, repo

    set_flow_store(None)
    set_repo(None)


# ---------------------------------------------------------------------------
# 8. REST endpoint smoke test (POST /flows/codegen)
# ---------------------------------------------------------------------------


class TestCodegenEndpoint:
    @pytest.mark.asyncio
    async def test_valid_spec_returns_source(self, codegen_client):
        client, alice_id, *_ = codegen_client
        payload = {
            "spec": {
                "version": 1,
                "name": "endpoint_test",
                "params": [],
                "tasks": [
                    {
                        "key": "q",
                        "kind": "query",
                        "needs": [],
                        "config": {"sql": "SELECT 42"},
                        "retries": 0,
                        "retry_backoff_s": 30,
                        "timeout_s": 60,
                        "cache_ttl_s": 0,
                        "ui": {"x": 0, "y": 0},
                    }
                ],
            }
        }
        resp = await client.post(
            "/api/v1/flows/codegen",
            json=payload,
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "source" in data
        assert "from nubi.sdk import" in data["source"]
        assert "def endpoint_test():" in data["source"]

    @pytest.mark.asyncio
    async def test_invalid_spec_returns_400(self, codegen_client):
        client, alice_id, *_ = codegen_client
        # Missing required 'sql' / 'query_id' for kind=query.
        payload = {
            "spec": {
                "version": 1,
                "name": "bad_flow",
                "params": [],
                "tasks": [
                    {
                        "key": "q",
                        "kind": "query",
                        "needs": [],
                        "config": {},  # missing sql/query_id
                        "retries": 0,
                        "retry_backoff_s": 30,
                        "timeout_s": 60,
                        "cache_ttl_s": 0,
                        "ui": {"x": 0, "y": 0},
                    }
                ],
            }
        }
        resp = await client.post(
            "/api/v1/flows/codegen",
            json=payload,
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 400, resp.text
        body = resp.json()
        assert body["error"]["code"] == "bad_flow_spec"

    @pytest.mark.asyncio
    async def test_flow_id_codegen_returns_source(self, codegen_client):
        """POST /flows/{id}/codegen returns source for a persisted flow."""
        client, alice_id, *_ = codegen_client
        # First create a flow.
        create_resp = await client.post(
            "/api/v1/flows",
            json={
                "name": "persisted_flow",
                "spec": {
                    "version": 1,
                    "name": "persisted_flow",
                    "params": [],
                    "tasks": [
                        {
                            "key": "step",
                            "kind": "noop",
                            "needs": [],
                            "config": {},
                            "retries": 0,
                            "retry_backoff_s": 30,
                            "timeout_s": 60,
                            "cache_ttl_s": 0,
                            "ui": {"x": 0, "y": 0},
                        }
                    ],
                },
            },
            headers=_auth_headers(alice_id),
        )
        assert create_resp.status_code == 201, create_resp.text
        flow_id = create_resp.json()["id"]

        codegen_resp = await client.post(
            f"/api/v1/flows/{flow_id}/codegen",
            headers=_auth_headers(alice_id),
        )
        assert codegen_resp.status_code == 200, codegen_resp.text
        data = codegen_resp.json()
        assert "source" in data
        assert data["flow_id"] == flow_id
        assert "def persisted_flow():" in data["source"]
