"""Tests for M21-A: AI agent with tool registry.

Coverage
--------
1. Tool registry: all tools have well-formed JSON schemas.
2. Tool ``get_schema``: returns catalog dict with expected keys.
3. Tool ``list_queries``: returns list of registered queries.
4. Tool ``generate_sql``: returns {sql, valid, issues}.
5. Tool ``create_query``: registers a query retrievable from the registry.
6. Tool ``run_query`` (RLS enforcement):
   - Unrestricted claims → all rows returned.
   - Claims with ``policies: {active: True}`` → only active rows returned (RLS).
   - Claims with an impossible policy → zero rows returned (RLS enforced).
7. Tool ``edit_dashboard``:
   - ``add_widget`` adds a widget and the result validates.
   - ``move_widget`` updates position.
   - ``remove_widget`` removes the widget.
8. Tool ``execute_tool``:
   - Unknown tool → AppError(tool_not_found, 404).
   - Missing required argument → AppError(invalid_tool_input, 400).
9. Agent ``run_agent`` with NullProvider:
   - Terminates (returns a reply string).
   - Returns non-empty ``actions`` list.
   - "chart" intent → actions include generate_sql and create_dashboard.
   - "run"/"query" intent → actions include generate_sql and run_query.
   - max_steps is respected (no infinite loop).
10. ``POST /ai/chat``: returns 200 with {reply, actions}; 401 without auth.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.ai.agent import run_agent
from app.ai.provider import NullProvider
from app.ai.tools import all_tools, execute_tool, get_tool
from app.auth.jwt import mint_access_token


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _empty_claims() -> dict[str, Any]:
    """First-party claims with no RLS policies."""
    return {"kind": "access", "sub": "test-user", "policies": {}, "scope": ["read:*"]}


def _claims_with_policy(col: str, val: Any) -> dict[str, Any]:
    """Claims with an equality RLS policy."""
    return {"kind": "access", "sub": "test-user", "policies": {col: val}, "scope": ["read:*"]}


def _auth_headers(user_id: str) -> dict[str, str]:
    token = mint_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


def _make_user(user_id: str) -> dict[str, Any]:
    return {
        "id": user_id,
        "email": "agent-tester@example.com",
        "name": "Agent Tester",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def agent_client(app, fake_db):
    """HTTPX async client with a pre-seeded user for agent endpoint tests."""
    user_id = str(uuid.uuid4())
    fake_db.users[user_id] = _make_user(user_id)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as ac:
        yield ac, user_id


# ---------------------------------------------------------------------------
# 1. Tool registry — well-formed JSON schemas
# ---------------------------------------------------------------------------


class TestToolRegistry:
    """All registered tools have a well-formed JSON Schema."""

    def test_all_tools_non_empty(self):
        tools = all_tools()
        assert len(tools) > 0

    def test_every_tool_has_name(self):
        for tool in all_tools():
            assert isinstance(tool.name, str) and tool.name, f"Tool missing name: {tool}"

    def test_every_tool_has_description(self):
        for tool in all_tools():
            assert isinstance(tool.description, str) and tool.description, (
                f"Tool {tool.name!r} missing description"
            )

    def test_every_tool_schema_is_object_type(self):
        for tool in all_tools():
            schema = tool.json_schema
            assert isinstance(schema, dict), f"Tool {tool.name!r}: schema not a dict"
            assert schema.get("type") == "object", (
                f"Tool {tool.name!r}: schema type must be 'object', got {schema.get('type')!r}"
            )

    def test_every_tool_schema_has_properties(self):
        for tool in all_tools():
            schema = tool.json_schema
            assert "properties" in schema, (
                f"Tool {tool.name!r}: schema missing 'properties' key"
            )

    def test_get_tool_returns_correct_tool(self):
        for tool in all_tools():
            found = get_tool(tool.name)
            assert found is not None, f"get_tool({tool.name!r}) returned None"
            assert found.name == tool.name

    def test_get_tool_unknown_returns_none(self):
        result = get_tool("nonexistent_tool_xyz")
        assert result is None

    def test_required_tools_present(self):
        expected = {
            "get_schema",
            "list_queries",
            "generate_sql",
            "create_query",
            "run_query",
            "create_dashboard",
            "edit_dashboard",
        }
        actual = {t.name for t in all_tools()}
        missing = expected - actual
        assert not missing, f"Missing tools: {missing}"

    def test_all_tools_callable(self):
        for tool in all_tools():
            assert callable(tool.fn), f"Tool {tool.name!r}: fn is not callable"


# ---------------------------------------------------------------------------
# 2. Tool: get_schema
# ---------------------------------------------------------------------------


class TestGetSchemaTool:
    def test_get_schema_returns_catalog_shape(self):
        result = execute_tool("get_schema", {}, _empty_claims())
        assert isinstance(result, dict)
        assert "tables" in result
        assert "queries" in result

    def test_get_schema_tables_is_dict(self):
        result = execute_tool("get_schema", {}, _empty_claims())
        assert isinstance(result["tables"], dict)

    def test_get_schema_queries_is_list(self):
        result = execute_tool("get_schema", {}, _empty_claims())
        assert isinstance(result["queries"], list)


# ---------------------------------------------------------------------------
# 3. Tool: list_queries
# ---------------------------------------------------------------------------


class TestListQueriesTool:
    def test_list_queries_returns_queries_key(self):
        result = execute_tool("list_queries", {}, _empty_claims())
        assert "queries" in result
        assert isinstance(result["queries"], list)

    def test_list_queries_includes_demo_all(self):
        result = execute_tool("list_queries", {}, _empty_claims())
        ids = [q["id"] for q in result["queries"]]
        assert "demo_all" in ids

    def test_list_queries_items_have_required_fields(self):
        result = execute_tool("list_queries", {}, _empty_claims())
        for q in result["queries"]:
            assert "id" in q
            assert "name" in q
            assert "params" in q


# ---------------------------------------------------------------------------
# 4. Tool: generate_sql
# ---------------------------------------------------------------------------


class TestGenerateSqlTool:
    def test_generate_sql_returns_expected_keys(self):
        result = execute_tool(
            "generate_sql", {"question": "show all demo rows"}, _empty_claims()
        )
        assert "sql" in result
        assert "valid" in result
        assert "issues" in result

    def test_generate_sql_valid_true_with_null_provider(self):
        result = execute_tool(
            "generate_sql", {"question": "show all demo rows"}, _empty_claims()
        )
        assert result["valid"] is True

    def test_generate_sql_sql_is_string(self):
        result = execute_tool(
            "generate_sql", {"question": "list all users"}, _empty_claims()
        )
        assert isinstance(result["sql"], str) and result["sql"].strip()

    def test_generate_sql_issues_is_list(self):
        result = execute_tool(
            "generate_sql", {"question": "list all users"}, _empty_claims()
        )
        assert isinstance(result["issues"], list)

    def test_generate_sql_missing_question_raises(self):
        from app.errors import AppError

        with pytest.raises(AppError) as exc_info:
            execute_tool("generate_sql", {}, _empty_claims())
        assert exc_info.value.status == 400


# ---------------------------------------------------------------------------
# 5. Tool: create_query
# ---------------------------------------------------------------------------


class TestCreateQueryTool:
    def test_create_query_registers_retrievable_query(self):
        from app.queries.registry import get_query_registry

        qid = f"test_agent_q_{uuid.uuid4().hex[:8]}"
        sql = "SELECT 1 AS x"
        execute_tool("create_query", {"id": qid, "sql": sql}, _empty_claims())
        rq = get_query_registry().get(qid)
        assert rq is not None
        assert rq.sql == sql

    def test_create_query_returns_registered_true(self):
        qid = f"test_agent_q_{uuid.uuid4().hex[:8]}"
        result = execute_tool(
            "create_query", {"id": qid, "sql": "SELECT 2 AS y"}, _empty_claims()
        )
        assert result["registered"] is True
        assert result["id"] == qid

    def test_create_query_with_params(self):
        from app.queries.registry import get_query_registry

        qid = f"test_agent_q_{uuid.uuid4().hex[:8]}"
        params = [{"name": "tenant_id", "type": "text", "required": True}]
        execute_tool(
            "create_query",
            {"id": qid, "sql": "SELECT * FROM demo WHERE id = {{tenant_id}}", "params": params},
            _empty_claims(),
        )
        rq = get_query_registry().get(qid)
        assert rq is not None
        assert len(rq.params) == 1
        assert rq.params[0].name == "tenant_id"

    def test_create_query_missing_id_raises(self):
        from app.errors import AppError

        with pytest.raises(AppError) as exc_info:
            execute_tool("create_query", {"sql": "SELECT 1"}, _empty_claims())
        assert exc_info.value.status == 400

    def test_create_query_missing_sql_raises(self):
        from app.errors import AppError

        with pytest.raises(AppError) as exc_info:
            execute_tool("create_query", {"id": "some_id"}, _empty_claims())
        assert exc_info.value.status == 400


# ---------------------------------------------------------------------------
# 6. Tool: run_query — RLS enforcement
# ---------------------------------------------------------------------------


class TestRunQueryTool:
    """run_query must enforce caller claims (RLS)."""

    def test_run_query_demo_all_returns_rows(self):
        """demo_all returns rows with empty claims (no RLS filter)."""
        result = execute_tool("run_query", {"query_id": "demo_all"}, _empty_claims())
        assert "rows" in result
        assert "row_count" in result
        assert result["row_count"] > 0

    def test_run_query_demo_all_returns_columns(self):
        result = execute_tool("run_query", {"query_id": "demo_all"}, _empty_claims())
        assert "columns" in result
        assert isinstance(result["columns"], list)
        assert len(result["columns"]) > 0

    def test_run_query_rls_active_true_filters_rows(self):
        """Claims with policies={active: True} must filter out inactive rows."""
        # Without RLS — all 5 demo rows.
        all_result = execute_tool("run_query", {"query_id": "demo_all"}, _empty_claims())
        total = all_result["row_count"]

        # With RLS — only active=True rows.
        rls_result = execute_tool(
            "run_query",
            {"query_id": "demo_all"},
            _claims_with_policy("active", True),
        )
        active_count = rls_result["row_count"]

        # RLS must narrow the result.
        assert active_count <= total, (
            f"RLS should narrow rows: all={total}, rls_active={active_count}"
        )
        # All returned rows must be active.
        for row in rls_result["rows"]:
            assert row.get("active") is True, f"Row violates RLS: {row}"

    def test_run_query_rls_impossible_policy_returns_zero(self):
        """An impossible policy must return zero rows — never exceed caller scope."""
        result = execute_tool(
            "run_query",
            {"query_id": "demo_all"},
            _claims_with_policy("id", -999),  # id -999 does not exist
        )
        assert result["row_count"] == 0, (
            f"Expected 0 rows with impossible RLS, got {result['row_count']}"
        )

    def test_run_query_adhoc_sql(self):
        result = execute_tool(
            "run_query",
            {"sql": "SELECT 42 AS answer"},
            _empty_claims(),
        )
        assert result["row_count"] == 1
        assert result["rows"][0]["answer"] == 42

    def test_run_query_unknown_query_id_raises(self):
        from app.errors import AppError

        with pytest.raises(AppError) as exc_info:
            execute_tool(
                "run_query", {"query_id": "definitely_nonexistent_xyz"}, _empty_claims()
            )
        assert exc_info.value.status == 404

    def test_run_query_no_sql_or_id_raises(self):
        from app.errors import AppError

        with pytest.raises(AppError):
            execute_tool("run_query", {}, _empty_claims())


# ---------------------------------------------------------------------------
# 7. Tool: edit_dashboard
# ---------------------------------------------------------------------------


class TestEditDashboardTool:
    """edit_dashboard mutates a spec and re-validates it."""

    def _minimal_spec(self) -> dict[str, Any]:
        return {
            "version": 1,
            "title": "Test Dashboard",
            "layout": {"cols": 12, "row_height": 60},
            "widgets": [
                {
                    "id": "w1",
                    "type": "table",
                    "query_id": "demo_all",
                    "encoding": {},
                    "props": {"limit": 50},
                    "pos": {"x": 1, "y": 1, "w": 12, "h": 3},
                }
            ],
        }

    def _new_table_widget(self) -> dict[str, Any]:
        return {
            "id": "w2",
            "type": "table",
            "query_id": "demo_all",
            "encoding": {},
            "props": {},
            "pos": {"x": 1, "y": 4, "w": 6, "h": 2},
        }

    def test_add_widget_increases_count(self):
        spec = self._minimal_spec()
        result = execute_tool(
            "edit_dashboard",
            {"spec": spec, "op": {"action": "add_widget", "widget": self._new_table_widget()}},
            _empty_claims(),
        )
        assert "spec" in result
        assert len(result["spec"]["widgets"]) == 2

    def test_add_widget_result_validates(self):
        spec = self._minimal_spec()
        result = execute_tool(
            "edit_dashboard",
            {"spec": spec, "op": {"action": "add_widget", "widget": self._new_table_widget()}},
            _empty_claims(),
        )
        from app.dashboards.spec import validate_spec  # noqa: PLC0415

        validated_spec, issues = validate_spec(result["spec"])
        assert validated_spec is not None, f"edit_dashboard result failed validation: {issues}"

    def test_add_widget_does_not_mutate_original(self):
        spec = self._minimal_spec()
        original_count = len(spec["widgets"])
        execute_tool(
            "edit_dashboard",
            {"spec": spec, "op": {"action": "add_widget", "widget": self._new_table_widget()}},
            _empty_claims(),
        )
        # Original spec must be unchanged.
        assert len(spec["widgets"]) == original_count

    def test_move_widget_updates_pos(self):
        spec = self._minimal_spec()
        new_pos = {"x": 3, "y": 5, "w": 6, "h": 4}
        result = execute_tool(
            "edit_dashboard",
            {"spec": spec, "op": {"action": "move_widget", "widget_id": "w1", "pos": new_pos}},
            _empty_claims(),
        )
        widget = next(w for w in result["spec"]["widgets"] if w["id"] == "w1")
        assert widget["pos"] == new_pos

    def test_remove_widget_decreases_count(self):
        spec = self._minimal_spec()
        result = execute_tool(
            "edit_dashboard",
            {"spec": spec, "op": {"action": "remove_widget", "widget_id": "w1"}},
            _empty_claims(),
        )
        assert len(result["spec"]["widgets"]) == 0

    def test_configure_widget_updates_props(self):
        spec = self._minimal_spec()
        result = execute_tool(
            "edit_dashboard",
            {
                "spec": spec,
                "op": {
                    "action": "configure_widget",
                    "widget_id": "w1",
                    "updates": {"props": {"limit": 100}},
                },
            },
            _empty_claims(),
        )
        widget = next(w for w in result["spec"]["widgets"] if w["id"] == "w1")
        assert widget["props"]["limit"] == 100

    def test_unknown_action_raises(self):
        from app.errors import AppError

        spec = self._minimal_spec()
        with pytest.raises(AppError) as exc_info:
            execute_tool(
                "edit_dashboard",
                {"spec": spec, "op": {"action": "teleport_widget"}},
                _empty_claims(),
            )
        assert exc_info.value.status == 400

    def test_add_chart_widget_validates(self):
        """Adding a valid chart widget should produce a valid spec."""
        spec = self._minimal_spec()
        chart_widget = {
            "id": "w_chart",
            "type": "chart",
            "query_id": "demo_all",
            "chart_type": "scatter",
            "encoding": {"x": "id", "y": "value"},
            "props": {},
            "pos": {"x": 1, "y": 4, "w": 12, "h": 4},
        }
        result = execute_tool(
            "edit_dashboard",
            {"spec": spec, "op": {"action": "add_widget", "widget": chart_widget}},
            _empty_claims(),
        )
        from app.dashboards.spec import validate_spec  # noqa: PLC0415

        validated_spec, issues = validate_spec(result["spec"])
        assert validated_spec is not None


# ---------------------------------------------------------------------------
# 8. execute_tool — error paths
# ---------------------------------------------------------------------------


class TestExecuteToolErrors:
    def test_unknown_tool_raises_404(self):
        from app.errors import AppError

        with pytest.raises(AppError) as exc_info:
            execute_tool("no_such_tool", {}, _empty_claims())
        assert exc_info.value.status == 404
        assert "tool_not_found" in exc_info.value.code

    def test_missing_required_arg_raises_400(self):
        from app.errors import AppError

        with pytest.raises(AppError) as exc_info:
            execute_tool("generate_sql", {}, _empty_claims())
        assert exc_info.value.status == 400

    def test_extra_arg_on_strict_schema_raises(self):
        from app.errors import AppError

        with pytest.raises(AppError) as exc_info:
            execute_tool(
                "generate_sql",
                {"question": "test", "unexpected_field": "value"},
                _empty_claims(),
            )
        assert exc_info.value.status == 400


# ---------------------------------------------------------------------------
# 9. run_agent with NullProvider — termination and determinism
# ---------------------------------------------------------------------------


class TestRunAgent:
    """run_agent terminates and returns expected structure."""

    def _null_provider(self) -> NullProvider:
        return NullProvider()

    def test_run_agent_terminates_default(self):
        """Agent must return without infinite loop."""
        result = run_agent(
            [{"role": "user", "content": "Tell me about the data."}],
            self._null_provider(),
            _empty_claims(),
        )
        assert isinstance(result, dict)
        assert "reply" in result
        assert "actions" in result

    def test_run_agent_reply_is_string(self):
        result = run_agent(
            [{"role": "user", "content": "What tables exist?"}],
            self._null_provider(),
            _empty_claims(),
        )
        assert isinstance(result["reply"], str)
        assert len(result["reply"]) > 0

    def test_run_agent_actions_is_list(self):
        result = run_agent(
            [{"role": "user", "content": "What tables exist?"}],
            self._null_provider(),
            _empty_claims(),
        )
        assert isinstance(result["actions"], list)

    def test_run_agent_actions_non_empty(self):
        result = run_agent(
            [{"role": "user", "content": "run the demo query"}],
            self._null_provider(),
            _empty_claims(),
        )
        assert len(result["actions"]) > 0

    def test_run_agent_chart_intent_calls_generate_sql_and_create_dashboard(self):
        """Message with 'chart' → agent calls generate_sql then create_dashboard."""
        result = run_agent(
            [{"role": "user", "content": "make a chart of the demo data"}],
            self._null_provider(),
            _empty_claims(),
        )
        tool_names = [a["tool"] for a in result["actions"]]
        assert "generate_sql" in tool_names, f"Expected generate_sql in {tool_names}"
        assert "create_dashboard" in tool_names, f"Expected create_dashboard in {tool_names}"

    def test_run_agent_run_intent_calls_generate_sql_and_run_query(self):
        """Message with 'run' → agent calls generate_sql then run_query."""
        result = run_agent(
            [{"role": "user", "content": "run a query on the demo table"}],
            self._null_provider(),
            _empty_claims(),
        )
        tool_names = [a["tool"] for a in result["actions"]]
        assert "generate_sql" in tool_names, f"Expected generate_sql in {tool_names}"
        assert "run_query" in tool_names, f"Expected run_query in {tool_names}"

    def test_run_agent_dashboard_intent(self):
        """Message with 'dashboard' → chart path."""
        result = run_agent(
            [{"role": "user", "content": "create a dashboard for sales data"}],
            self._null_provider(),
            _empty_claims(),
        )
        tool_names = [a["tool"] for a in result["actions"]]
        assert "create_dashboard" in tool_names, f"Expected create_dashboard in {tool_names}"

    def test_run_agent_actions_have_tool_key(self):
        result = run_agent(
            [{"role": "user", "content": "run the demo query"}],
            self._null_provider(),
            _empty_claims(),
        )
        for action in result["actions"]:
            assert "tool" in action, f"Action missing 'tool' key: {action}"
            assert "arguments" in action, f"Action missing 'arguments' key: {action}"
            assert "result" in action, f"Action missing 'result' key: {action}"

    def test_run_agent_max_steps_respected(self):
        """Actions count must not exceed max_steps."""
        result = run_agent(
            [{"role": "user", "content": "make a chart of the demo data"}],
            self._null_provider(),
            _empty_claims(),
            max_steps=1,
        )
        # max_steps=1 limits actions to at most 1.
        assert len(result["actions"]) <= 1

    def test_run_agent_rls_passed_through(self):
        """Claims with RLS policy are passed to tools (run_query enforces them)."""
        rls_claims = _claims_with_policy("active", True)
        result = run_agent(
            [{"role": "user", "content": "run the demo query"}],
            self._null_provider(),
            rls_claims,
        )
        # Agent should have run — just check it completes without error.
        assert isinstance(result["reply"], str)

    def test_run_agent_empty_messages(self):
        """Empty message list should produce a fallback reply without crash."""
        result = run_agent([], self._null_provider(), _empty_claims())
        assert isinstance(result["reply"], str)

    def test_run_agent_is_deterministic_with_null_provider(self):
        """Same input + NullProvider → same output both times."""
        msgs = [{"role": "user", "content": "show me a chart of the data"}]
        r1 = run_agent(msgs, self._null_provider(), _empty_claims())
        r2 = run_agent(msgs, self._null_provider(), _empty_claims())
        # Tool names in actions should be identical.
        assert [a["tool"] for a in r1["actions"]] == [a["tool"] for a in r2["actions"]]


# ---------------------------------------------------------------------------
# 10. POST /ai/chat endpoint
# ---------------------------------------------------------------------------


class TestAiChatEndpoint:
    @pytest.mark.asyncio
    async def test_chat_requires_auth(self, agent_client):
        ac, _ = agent_client
        resp = await ac.post(
            "/api/v1/ai/chat",
            json={"messages": [{"role": "user", "content": "hello"}]},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_chat_returns_200_with_auth(self, agent_client):
        ac, user_id = agent_client
        resp = await ac.post(
            "/api/v1/ai/chat",
            json={"messages": [{"role": "user", "content": "show me the data"}]},
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_chat_response_has_reply(self, agent_client):
        ac, user_id = agent_client
        resp = await ac.post(
            "/api/v1/ai/chat",
            json={"messages": [{"role": "user", "content": "list all demo rows"}]},
            headers=_auth_headers(user_id),
        )
        body = resp.json()
        assert "reply" in body
        assert isinstance(body["reply"], str)
        assert len(body["reply"]) > 0

    @pytest.mark.asyncio
    async def test_chat_response_has_actions(self, agent_client):
        ac, user_id = agent_client
        resp = await ac.post(
            "/api/v1/ai/chat",
            json={"messages": [{"role": "user", "content": "show me a chart"}]},
            headers=_auth_headers(user_id),
        )
        body = resp.json()
        assert "actions" in body
        assert isinstance(body["actions"], list)

    @pytest.mark.asyncio
    async def test_chat_actions_non_empty_for_chart(self, agent_client):
        ac, user_id = agent_client
        resp = await ac.post(
            "/api/v1/ai/chat",
            json={"messages": [{"role": "user", "content": "make me a chart of demo data"}]},
            headers=_auth_headers(user_id),
        )
        body = resp.json()
        assert len(body["actions"]) > 0

    @pytest.mark.asyncio
    async def test_chat_with_board_id(self, agent_client):
        """board_id param is accepted and does not cause an error."""
        ac, user_id = agent_client
        resp = await ac.post(
            "/api/v1/ai/chat",
            json={
                "messages": [{"role": "user", "content": "show me data"}],
                "board_id": "some-board-uuid",
            },
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_chat_missing_messages_returns_422(self, agent_client):
        ac, user_id = agent_client
        resp = await ac.post(
            "/api/v1/ai/chat",
            json={},
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_chat_chart_intent_actions_include_create_dashboard(self, agent_client):
        ac, user_id = agent_client
        resp = await ac.post(
            "/api/v1/ai/chat",
            json={"messages": [{"role": "user", "content": "create a dashboard"}]},
            headers=_auth_headers(user_id),
        )
        body = resp.json()
        tool_names = [a["tool"] for a in body["actions"]]
        assert "create_dashboard" in tool_names, f"Tool names: {tool_names}"

    # -------------------------------------------------------------------------
    # Model param tests (added for multi-model support)
    # -------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_chat_with_model_param_returns_200(self, agent_client):
        """Passing model= does not break the endpoint — still 200."""
        ac, user_id = agent_client
        resp = await ac.post(
            "/api/v1/ai/chat",
            json={
                "messages": [{"role": "user", "content": "show me the data"}],
                "model": "claude-3-5-sonnet-latest",
            },
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_chat_with_model_param_echoes_model_in_response(self, agent_client):
        """The requested model is echoed back in the response body."""
        ac, user_id = agent_client
        model_name = "claude-3-5-sonnet-latest"
        resp = await ac.post(
            "/api/v1/ai/chat",
            json={
                "messages": [{"role": "user", "content": "show me the data"}],
                "model": model_name,
            },
            headers=_auth_headers(user_id),
        )
        body = resp.json()
        assert body.get("model") == model_name, (
            f"Expected model={model_name!r} in response, got model={body.get('model')!r}"
        )

    @pytest.mark.asyncio
    async def test_chat_without_model_param_returns_none_model(self, agent_client):
        """When model is not supplied the response field is null/None."""
        ac, user_id = agent_client
        resp = await ac.post(
            "/api/v1/ai/chat",
            json={"messages": [{"role": "user", "content": "show me the data"}]},
            headers=_auth_headers(user_id),
        )
        body = resp.json()
        # model field is optional — either absent or null
        assert body.get("model") is None

    @pytest.mark.asyncio
    async def test_chat_with_model_param_still_returns_reply_and_actions(self, agent_client):
        """Adding model= must not break the {reply, actions} shape."""
        ac, user_id = agent_client
        resp = await ac.post(
            "/api/v1/ai/chat",
            json={
                "messages": [{"role": "user", "content": "run the demo query"}],
                "model": "gpt-4o",
            },
            headers=_auth_headers(user_id),
        )
        body = resp.json()
        assert "reply" in body, "Response must include 'reply'"
        assert "actions" in body, "Response must include 'actions'"
        assert isinstance(body["reply"], str) and body["reply"]
        assert isinstance(body["actions"], list)
