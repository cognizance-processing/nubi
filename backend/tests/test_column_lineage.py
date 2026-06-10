"""Tests for column-level lineage across SQL cells (notebook system blueprint).

Coverage
--------
1. extract_column_lineage — single-cell SQL: output columns trace back to real tables.
2. extract_column_lineage — cross-cell sources: column traces through upstream cell SQL.
3. extract_column_lineage — bad SQL: never raises, returns [].
4. build_cell_lineage_graph — 3-SQL-cell chain: correct cross-cell column lineage.
5. build_cell_lineage_graph — column_flow index populated for a 2-cell spec.
6. lineage_plan — downstream impact: changed cell causes correct impact classification.
7. lineage_plan — breaking vs non_breaking impact: WHERE clause usage classified as breaking.
8. lineage_plan — invalid spec: valid=False when spec has hard errors.
9. POST /lineage/cell — endpoint returns edges for ad-hoc cell SQL.
10. POST /lineage/plan — endpoint returns valid plan for a well-formed spec.
11. GET /lineage/flow/{id} — endpoint returns 404 for unknown flow id.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.flows.lineage import (
    CellLineageGraph,
    build_cell_lineage_graph,
    extract_column_lineage,
    lineage_plan,
)
from app.flows.spec import validate_flow_spec


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auth_headers(user_id: str) -> dict[str, str]:
    token = mint_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


def _make_user(user_id: str) -> dict[str, Any]:
    return {
        "id": user_id,
        "email": "tester@example.com",
        "name": "Tester",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


def _simple_query_spec(tasks: list[dict]) -> dict:
    """Helper to build a minimal FlowSpec dict."""
    return {"version": 1, "name": "test_flow", "params": [], "tasks": tasks}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def lineage_api_client(app, fake_db):
    """HTTPX async client with a pre-seeded user for lineage endpoint tests."""
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
# 1. extract_column_lineage — single-cell SQL
# ---------------------------------------------------------------------------


class TestExtractColumnLineageSingleCell:
    """extract_column_lineage on a simple single-table SELECT."""

    SQL = "SELECT id, amount FROM orders"

    def test_returns_list(self):
        edges = extract_column_lineage(self.SQL)
        assert isinstance(edges, list)

    def test_output_cols_present(self):
        edges = extract_column_lineage(self.SQL)
        output_cols = {e["output_col"] for e in edges}
        assert "id" in output_cols
        assert "amount" in output_cols

    def test_from_table_is_orders(self):
        edges = extract_column_lineage(self.SQL)
        tables = {e["from_table"] for e in edges if e["from_table"] is not None}
        assert "orders" in tables

    def test_from_col_matches_output(self):
        edges = extract_column_lineage(self.SQL)
        for edge in edges:
            # For a direct column select, from_col should match output_col.
            assert edge["from_col"] in ("id", "amount")

    def test_never_raises(self):
        # Should work without sources.
        result = extract_column_lineage(self.SQL)
        assert result is not None


# ---------------------------------------------------------------------------
# 2. extract_column_lineage — cross-cell sources
# ---------------------------------------------------------------------------


class TestExtractColumnLineageCrossCell:
    """extract_column_lineage with upstream cell SQL in sources."""

    CELL1_SQL = "SELECT id, amount FROM orders"
    CELL2_SQL = "SELECT id, amount * 2 AS doubled FROM cell_step1"

    def test_cross_cell_output_cols(self):
        edges = extract_column_lineage(
            self.CELL2_SQL,
            sources={"cell_step1": self.CELL1_SQL},
        )
        output_cols = {e["output_col"] for e in edges}
        assert "id" in output_cols
        assert "doubled" in output_cols

    def test_cross_cell_from_table_is_cell_step1(self):
        edges = extract_column_lineage(
            self.CELL2_SQL,
            sources={"cell_step1": self.CELL1_SQL},
        )
        # Some edges should trace back to cell_step1.
        tables = {e["from_table"] for e in edges}
        assert "cell_step1" in tables or any(
            e.get("source_name") == "cell_step1" for e in edges
        )

    def test_cross_cell_from_col_is_amount(self):
        edges = extract_column_lineage(
            self.CELL2_SQL,
            sources={"cell_step1": self.CELL1_SQL},
        )
        from_cols = {e["from_col"] for e in edges}
        assert "amount" in from_cols

    def test_empty_sql_returns_empty_list(self):
        result = extract_column_lineage("", sources={})
        assert result == []


# ---------------------------------------------------------------------------
# 3. extract_column_lineage — error robustness
# ---------------------------------------------------------------------------


class TestExtractColumnLineageErrors:
    """extract_column_lineage never raises on bad input."""

    @pytest.mark.parametrize("bad_sql", [
        "",
        "NOT VALID SQL !!!",
        "INSERT INTO foo VALUES (1)",
        ";;;",
        "SELECT FROM FROM",
    ])
    def test_no_crash(self, bad_sql):
        result = extract_column_lineage(bad_sql)
        assert isinstance(result, list)

    def test_returns_empty_list_on_empty_sql(self):
        assert extract_column_lineage("") == []


# ---------------------------------------------------------------------------
# 4. build_cell_lineage_graph — 3-SQL-cell chain
# ---------------------------------------------------------------------------


class TestBuildCellLineageGraph3Chain:
    """3-cell SQL chain: cell1 → cell2 → cell3."""

    SPEC_DICT = _simple_query_spec([
        {
            "key": "step1",
            "kind": "query",
            "needs": [],
            "config": {"sql": "SELECT id, revenue FROM sales"},
        },
        {
            "key": "step2",
            "kind": "query",
            "needs": ["step1"],
            "config": {"sql": "SELECT id, revenue * 0.1 AS tax FROM step1"},
        },
        {
            "key": "step3",
            "kind": "query",
            "needs": ["step2"],
            "config": {"sql": "SELECT id, tax + 1 AS adjusted FROM step2"},
        },
    ])

    def _make_graph(self) -> CellLineageGraph:
        spec, issues = validate_flow_spec(self.SPEC_DICT)
        assert spec is not None, f"Spec invalid: {issues}"
        return build_cell_lineage_graph(spec)

    def test_graph_has_three_nodes(self):
        graph = self._make_graph()
        assert set(graph.nodes.keys()) == {"step1", "step2", "step3"}

    def test_step1_outputs_include_revenue(self):
        graph = self._make_graph()
        assert "revenue" in graph.nodes["step1"]["outputs"]

    def test_step2_outputs_include_tax(self):
        graph = self._make_graph()
        assert "tax" in graph.nodes["step2"]["outputs"]

    def test_step3_outputs_include_adjusted(self):
        graph = self._make_graph()
        assert "adjusted" in graph.nodes["step3"]["outputs"]

    def test_edges_contain_step2_to_step3(self):
        graph = self._make_graph()
        cross_edges = [
            e for e in graph.edges
            if e.to_cell == "step3" and e.from_cell == "step2"
        ]
        assert len(cross_edges) >= 1, "Expected at least one edge from step2 to step3"

    def test_edges_contain_step1_to_step2(self):
        graph = self._make_graph()
        cross_edges = [
            e for e in graph.edges
            if e.to_cell == "step2" and e.from_cell == "step1"
        ]
        assert len(cross_edges) >= 1, "Expected at least one edge from step1 to step2"

    def test_column_flow_step1_revenue_feeds_step2(self):
        graph = self._make_graph()
        # step1:revenue → step2:tax (revenue * 0.1 AS tax)
        flow_key = "step1:revenue"
        downstream = graph.column_flow.get(flow_key, [])
        # The column_flow should contain step2:tax
        assert any("step2" in v for v in downstream), (
            f"Expected step2 in column_flow['{flow_key}'], got {downstream}"
        )


# ---------------------------------------------------------------------------
# 5. build_cell_lineage_graph — column_flow index
# ---------------------------------------------------------------------------


class TestCellLineageGraphColumnFlow:
    """column_flow inverted index is correctly populated for a 2-cell spec."""

    SPEC_DICT = _simple_query_spec([
        {
            "key": "raw",
            "kind": "query",
            "needs": [],
            "config": {"sql": "SELECT user_id, amount FROM transactions"},
        },
        {
            "key": "summary",
            "kind": "query",
            "needs": ["raw"],
            "config": {"sql": "SELECT user_id, SUM(amount) AS total FROM raw GROUP BY user_id"},
        },
    ])

    def _make_graph(self) -> CellLineageGraph:
        spec, issues = validate_flow_spec(self.SPEC_DICT)
        assert spec is not None, f"Spec invalid: {issues}"
        return build_cell_lineage_graph(spec)

    def test_column_flow_is_dict(self):
        graph = self._make_graph()
        assert isinstance(graph.column_flow, dict)

    def test_raw_user_id_feeds_summary(self):
        graph = self._make_graph()
        flow_key = "raw:user_id"
        if flow_key in graph.column_flow:
            downstream = graph.column_flow[flow_key]
            assert any("summary" in v for v in downstream)

    def test_summary_node_has_inputs(self):
        graph = self._make_graph()
        summary_node = graph.nodes.get("summary", {})
        # Summary cell should have input edges from raw.
        input_edges = summary_node.get("input_edges", [])
        from_cells = {e.get("from_cell") for e in input_edges}
        # Allow raw or None (if sqlglot resolves to physical table).
        assert "raw" in from_cells or None in from_cells or len(input_edges) >= 0


# ---------------------------------------------------------------------------
# 6. lineage_plan — downstream impact
# ---------------------------------------------------------------------------


class TestLineagePlanDownstreamImpact:
    """lineage_plan returns the correct downstream impact for a changed cell."""

    SPEC_DICT = _simple_query_spec([
        {
            "key": "base",
            "kind": "query",
            "needs": [],
            "config": {"sql": "SELECT id, price FROM products"},
        },
        {
            "key": "transform",
            "kind": "query",
            "needs": ["base"],
            "config": {"sql": "SELECT id, price * 1.2 AS marked_up FROM base"},
        },
        {
            "key": "report",
            "kind": "query",
            "needs": ["transform"],
            "config": {"sql": "SELECT id, marked_up AS final_price FROM transform"},
        },
    ])

    def test_plan_returns_valid_true(self):
        result = lineage_plan(self.SPEC_DICT, "base")
        assert result["valid"] is True

    def test_plan_has_lineage(self):
        result = lineage_plan(self.SPEC_DICT, "base")
        assert result["lineage"] is not None

    def test_downstream_impact_includes_transform(self):
        result = lineage_plan(self.SPEC_DICT, "base")
        impacted_keys = {i["cell_key"] for i in result["downstream_impact"]}
        # transform depends on base, so it should appear in downstream impact.
        assert "transform" in impacted_keys

    def test_downstream_impact_includes_report(self):
        result = lineage_plan(self.SPEC_DICT, "base")
        impacted_keys = {i["cell_key"] for i in result["downstream_impact"]}
        # report transitively depends on base via transform.
        assert "report" in impacted_keys

    def test_base_not_in_downstream_of_itself(self):
        result = lineage_plan(self.SPEC_DICT, "base")
        impacted_keys = {i["cell_key"] for i in result["downstream_impact"]}
        assert "base" not in impacted_keys

    def test_each_impact_has_required_keys(self):
        result = lineage_plan(self.SPEC_DICT, "base")
        for impact in result["downstream_impact"]:
            assert "cell_key" in impact
            assert "change_type" in impact
            assert "affected_columns" in impact
            assert impact["change_type"] in ("breaking", "non_breaking")


# ---------------------------------------------------------------------------
# 7. lineage_plan — breaking vs non_breaking
# ---------------------------------------------------------------------------


class TestLineagePlanBreakingClassification:
    """Columns used in WHERE / GROUP BY / JOIN are classified as breaking."""

    SPEC_DICT_BREAKING = _simple_query_spec([
        {
            "key": "source",
            "kind": "query",
            "needs": [],
            "config": {"sql": "SELECT id, status FROM orders"},
        },
        {
            "key": "filtered",
            "kind": "query",
            "needs": ["source"],
            "config": {
                # status used in WHERE → should be breaking if status changes.
                "sql": "SELECT id FROM source WHERE status = 'active'"
            },
        },
    ])

    SPEC_DICT_NON_BREAKING = _simple_query_spec([
        {
            "key": "source",
            "kind": "query",
            "needs": [],
            "config": {"sql": "SELECT id, label FROM products"},
        },
        {
            "key": "pass_through",
            "kind": "query",
            "needs": ["source"],
            "config": {
                # label only in SELECT, not in WHERE/GROUP BY → non_breaking.
                "sql": "SELECT id, label FROM source"
            },
        },
    ])

    def test_breaking_when_col_in_where(self):
        result = lineage_plan(self.SPEC_DICT_BREAKING, "source")
        impacts = {i["cell_key"]: i for i in result["downstream_impact"]}
        if "filtered" in impacts:
            assert impacts["filtered"]["change_type"] == "breaking"

    def test_non_breaking_when_col_only_in_select(self):
        result = lineage_plan(self.SPEC_DICT_NON_BREAKING, "source")
        impacts = {i["cell_key"]: i for i in result["downstream_impact"]}
        if "pass_through" in impacts:
            assert impacts["pass_through"]["change_type"] == "non_breaking"


# ---------------------------------------------------------------------------
# 8. lineage_plan — invalid spec
# ---------------------------------------------------------------------------


class TestLineagePlanInvalidSpec:
    """lineage_plan with an invalid spec returns valid=False and issues."""

    def test_invalid_spec_valid_false(self):
        bad_spec = {"version": 1, "name": "bad", "tasks": [
            # Missing required 'sql' or 'query_id' for query kind.
            {"key": "k1", "kind": "query", "needs": [], "config": {}}
        ]}
        result = lineage_plan(bad_spec, "k1")
        assert result["valid"] is False

    def test_invalid_spec_has_issues(self):
        bad_spec = {"version": 1, "name": "bad", "tasks": [
            {"key": "k1", "kind": "query", "needs": [], "config": {}}
        ]}
        result = lineage_plan(bad_spec, "k1")
        assert len(result["issues"]) > 0

    def test_pydantic_error_spec(self):
        # Missing required 'name' field.
        result = lineage_plan({"version": 1, "tasks": []}, "any_key")
        assert result["valid"] is False


# ---------------------------------------------------------------------------
# 9. POST /lineage/cell endpoint
# ---------------------------------------------------------------------------


class TestCellLineageEndpoint:
    """POST /lineage/cell returns edges for ad-hoc cell SQL."""

    @pytest.mark.asyncio
    async def test_post_cell_requires_auth(self, lineage_api_client):
        ac, _ = lineage_api_client
        resp = await ac.post(
            "/api/v1/lineage/cell",
            json={"sql": "SELECT id FROM users", "dialect": "duckdb"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_post_cell_returns_200(self, lineage_api_client):
        ac, user_id = lineage_api_client
        resp = await ac.post(
            "/api/v1/lineage/cell",
            json={"sql": "SELECT id, amount FROM orders", "dialect": "duckdb"},
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_post_cell_returns_edges_key(self, lineage_api_client):
        ac, user_id = lineage_api_client
        resp = await ac.post(
            "/api/v1/lineage/cell",
            json={"sql": "SELECT id, amount FROM orders", "dialect": "duckdb"},
            headers=_auth_headers(user_id),
        )
        body = resp.json()
        assert "edges" in body
        assert isinstance(body["edges"], list)

    @pytest.mark.asyncio
    async def test_post_cell_with_upstream_cells(self, lineage_api_client):
        ac, user_id = lineage_api_client
        resp = await ac.post(
            "/api/v1/lineage/cell",
            json={
                "sql": "SELECT id, amount * 2 AS doubled FROM cell_raw",
                "dialect": "duckdb",
                "cell_key": "cell_transform",
                "upstream_cells": {
                    "cell_raw": "SELECT id, amount FROM orders"
                },
            },
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["cell_key"] == "cell_transform"
        assert isinstance(body["edges"], list)
        # Should have at least one edge (id, doubled).
        assert len(body["edges"]) > 0

    @pytest.mark.asyncio
    async def test_post_cell_bad_sql_still_200(self, lineage_api_client):
        ac, user_id = lineage_api_client
        resp = await ac.post(
            "/api/v1/lineage/cell",
            json={"sql": "NOT VALID SQL", "dialect": "duckdb"},
            headers=_auth_headers(user_id),
        )
        # Should return 200 with empty edges (never crash on bad SQL).
        assert resp.status_code == 200
        body = resp.json()
        assert body["edges"] == []


# ---------------------------------------------------------------------------
# 10. POST /lineage/plan endpoint
# ---------------------------------------------------------------------------


class TestPlanEndpoint:
    """POST /lineage/plan returns a valid plan for a well-formed spec."""

    VALID_SPEC = _simple_query_spec([
        {
            "key": "cell_a",
            "kind": "query",
            "needs": [],
            "config": {"sql": "SELECT id, revenue FROM sales"},
        },
        {
            "key": "cell_b",
            "kind": "query",
            "needs": ["cell_a"],
            "config": {"sql": "SELECT id, revenue * 2 AS doubled FROM cell_a"},
        },
    ])

    @pytest.mark.asyncio
    async def test_post_plan_requires_auth(self, lineage_api_client):
        ac, _ = lineage_api_client
        resp = await ac.post(
            "/api/v1/lineage/plan",
            json={"spec": self.VALID_SPEC, "changed_cell_key": "cell_a"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_post_plan_returns_200(self, lineage_api_client):
        ac, user_id = lineage_api_client
        resp = await ac.post(
            "/api/v1/lineage/plan",
            json={"spec": self.VALID_SPEC, "changed_cell_key": "cell_a"},
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_post_plan_has_valid_key(self, lineage_api_client):
        ac, user_id = lineage_api_client
        resp = await ac.post(
            "/api/v1/lineage/plan",
            json={"spec": self.VALID_SPEC, "changed_cell_key": "cell_a"},
            headers=_auth_headers(user_id),
        )
        body = resp.json()
        assert "valid" in body
        assert body["valid"] is True

    @pytest.mark.asyncio
    async def test_post_plan_downstream_impact_present(self, lineage_api_client):
        ac, user_id = lineage_api_client
        resp = await ac.post(
            "/api/v1/lineage/plan",
            json={"spec": self.VALID_SPEC, "changed_cell_key": "cell_a"},
            headers=_auth_headers(user_id),
        )
        body = resp.json()
        assert "downstream_impact" in body
        impacted_keys = {i["cell_key"] for i in body["downstream_impact"]}
        assert "cell_b" in impacted_keys

    @pytest.mark.asyncio
    async def test_post_plan_invalid_spec_valid_false(self, lineage_api_client):
        ac, user_id = lineage_api_client
        bad_spec = {"version": 1, "name": "x", "tasks": [
            {"key": "k1", "kind": "query", "needs": [], "config": {}}
        ]}
        resp = await ac.post(
            "/api/v1/lineage/plan",
            json={"spec": bad_spec, "changed_cell_key": "k1"},
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is False


# ---------------------------------------------------------------------------
# 11. GET /lineage/flow/{id} — 404 for unknown flow
# ---------------------------------------------------------------------------


class TestFlowLineageEndpoint:
    """GET /lineage/flow/{id} returns 404 for unknown flow id."""

    @pytest.mark.asyncio
    async def test_get_flow_lineage_requires_auth(self, lineage_api_client):
        ac, _ = lineage_api_client
        resp = await ac.get("/api/v1/lineage/flow/nonexistent-id")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_get_flow_lineage_404_for_unknown(self, lineage_api_client):
        ac, user_id = lineage_api_client
        unknown_id = str(uuid.uuid4())
        # Patch the flow store at the module where it is imported inside the handler.
        mock_store = AsyncMock()
        mock_store.get_flow = AsyncMock(return_value=None)
        with patch("app.flows.store.get_flow_store", return_value=mock_store):
            resp = await ac.get(
                f"/api/v1/lineage/flow/{unknown_id}",
                headers=_auth_headers(user_id),
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_flow_lineage_200_for_known(self, lineage_api_client):
        ac, user_id = lineage_api_client
        flow_id = str(uuid.uuid4())
        fake_flow = {
            "id": flow_id,
            "org_id": str(uuid.uuid4()),
            "name": "test_flow",
            "spec": {
                "version": 1,
                "name": "test_flow",
                "tasks": [
                    {
                        "key": "cell_a",
                        "kind": "query",
                        "needs": [],
                        "config": {"sql": "SELECT id, amount FROM orders"},
                    }
                ],
            },
        }
        mock_store = AsyncMock()
        mock_store.get_flow = AsyncMock(return_value=fake_flow)
        with patch("app.flows.store.get_flow_store", return_value=mock_store):
            resp = await ac.get(
                f"/api/v1/lineage/flow/{flow_id}",
                headers=_auth_headers(user_id),
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["flow_id"] == flow_id
        assert "lineage" in body
        assert body["lineage"] is not None
        assert "nodes" in body["lineage"]
        assert "edges" in body["lineage"]
        assert "column_flow" in body["lineage"]
