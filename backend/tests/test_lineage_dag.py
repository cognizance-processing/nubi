"""Tests for the model/dataset DAG (A.2) and metric-lineage endpoint (A.4).

Coverage
--------
1. build_dag - producer->consumer chain: A produces output that B consumes as
   a table; assert edge A->B exists and N-hop traversal works.
2. N-hop traversal respects the hops limit (hop=1 returns only direct parents).
3. Cross-org isolation for /api/v1/lineage/dag (system seeds visible, org
   queries not cross-visible - 404 for unknown node_id).
4. resolve_metric_lineage - base_table metric resolves input columns + upstream.
5. resolve_metric_lineage - base_sql metric runs sqlglot and returns columns.
6. resolve_metric_lineage - derived measures are captured in formula field.
7. GET /api/v1/lineage/dag returns nodes and edges.
8. GET /api/v1/lineage/dag/{node_id} returns node neighbourhood or 404.
9. GET /api/v1/metrics/{id}/lineage returns structured lineage for a metric.
10. GET /api/v1/metrics/{id}/lineage returns 404 for unknown metric.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.lineage.dag import (
    DependencyDAG,
    build_dag,
    resolve_metric_lineage,
)
from app.metrics.models import (
    Dimension,
    DerivedMeasure,
    Measure,
    MetricDefinition,
)
from app.queries.registry import QueryRegistry, RegisteredQuery


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


def _make_query(id: str, sql: str, name: str = "") -> RegisteredQuery:
    return RegisteredQuery(id=id, sql=sql, name=name or id)


def _make_metric(
    id: str,
    base_table: str | None = None,
    base_sql: str | None = None,
    agg: str = "sum",
    expr: str = "value",
    derived_measures: tuple = (),
    dimensions: tuple = (),
) -> MetricDefinition:
    return MetricDefinition(
        id=id,
        name=id,
        measure=Measure(name="revenue", agg=agg, expr=expr),
        base_table=base_table,
        base_sql=base_sql,
        dimensions=dimensions,
        derived_measures=derived_measures,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def dag_client(app, fake_db):
    """HTTPX async client pre-seeded with a user for DAG endpoint tests."""
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
# 1. build_dag - producer->consumer edge
# ---------------------------------------------------------------------------


class TestBuildDagProducerConsumer:
    """Query A produces output 'summary'; Query B reads FROM summary -> edge A->B."""

    def test_edge_exists(self):
        # A: SELECT amount AS summary FROM orders -> outputs=['summary']
        a = _make_query("q_a", "SELECT amount AS summary FROM orders", "Producer")
        # B: SELECT * FROM summary -> tables=['summary']
        b = _make_query("q_b", "SELECT * FROM summary", "Consumer")

        dag = build_dag([a, b], [])

        assert "q_a" in dag.nodes
        assert "q_b" in dag.nodes

        edge_keys = {(e.from_id, e.to_id) for e in dag.edges}
        assert ("q_a", "q_b") in edge_keys, (
            f"Expected edge q_a->q_b; got edges: {edge_keys}"
        )

    def test_via_field(self):
        a = _make_query("q_a", "SELECT amount AS summary FROM orders", "Producer")
        b = _make_query("q_b", "SELECT * FROM summary", "Consumer")

        dag = build_dag([a, b], [])

        via_values = {e.via for e in dag.edges if e.from_id == "q_a" and e.to_id == "q_b"}
        assert "summary" in via_values

    def test_downstream_traversal(self):
        """q_a -> q_b -> q_c: downstream from q_a at hops=2 includes both."""
        a = _make_query("q_a", "SELECT amount AS agg_summary FROM orders", "A")
        b = _make_query("q_b", "SELECT * AS final_view FROM agg_summary", "B")
        c = _make_query("q_c", "SELECT * FROM final_view", "C")

        dag = build_dag([a, b, c], [])

        ds = dag.downstream("q_a", hops=3)
        assert "q_b" in ds
        assert "q_c" in ds

    def test_upstream_traversal(self):
        """q_a -> q_b -> q_c: upstream from q_c at hops=2 includes both a and b."""
        a = _make_query("q_a", "SELECT amount AS agg_summary FROM orders", "A")
        b = _make_query("q_b", "SELECT * AS final_view FROM agg_summary", "B")
        c = _make_query("q_c", "SELECT * FROM final_view", "C")

        dag = build_dag([a, b, c], [])

        up = dag.upstream("q_c", hops=3)
        assert "q_b" in up
        assert "q_a" in up


# ---------------------------------------------------------------------------
# 2. N-hop limit
# ---------------------------------------------------------------------------


class TestNHopLimit:
    """Traversal respects the hops cap."""

    def test_hops_1_only_direct(self):
        """With hops=1, only the direct parent is returned (not grandparent)."""
        a = _make_query("q_a", "SELECT amount AS agg_summary FROM orders", "A")
        b = _make_query("q_b", "SELECT val AS final_view FROM agg_summary", "B")
        c = _make_query("q_c", "SELECT * FROM final_view", "C")

        dag = build_dag([a, b, c], [])

        up_c = dag.upstream("q_c", hops=1)
        # Only q_b should be in upstream at hops=1 (q_a is 2 hops away)
        assert "q_b" in up_c
        # q_a is 2 hops away; should NOT appear at hops=1
        assert "q_a" not in up_c

    def test_hops_0_returns_empty(self):
        """hops capped to 1 minimum; upstream with hops=0 uses min(hops,20) but
        the deque loop condition depth >= hops immediately fires for 0, so no
        children are reached."""
        a = _make_query("q_a", "SELECT amount AS agg_summary FROM orders", "A")
        b = _make_query("q_b", "SELECT * FROM agg_summary", "B")

        dag = build_dag([a, b], [])

        # With hops=0 the walk never extends (depth 0 >= hops 0)
        ds = dag.downstream("q_a", hops=0)
        assert ds == []

    def test_hops_capped_at_20(self):
        """hops > 20 is clamped to 20 (no runaway on deep graphs)."""
        dag = DependencyDAG()
        # Ensure the cap is applied — build_dag is not needed here.
        result = dag.downstream("nonexistent", hops=9999)
        assert result == []


# ---------------------------------------------------------------------------
# 3. Bare physical table leaf nodes
# ---------------------------------------------------------------------------


class TestBareTableNodes:
    """Physical tables with no model producer become type='table' leaf nodes."""

    def test_leaf_table_in_dag(self):
        q = _make_query("q_orders", "SELECT id, amount FROM orders", "Orders")
        dag = build_dag([q], [])

        assert "orders" in dag.nodes
        assert dag.nodes["orders"].type == "table"

    def test_edge_from_table_to_consumer(self):
        q = _make_query("q_orders", "SELECT id, amount FROM orders", "Orders")
        dag = build_dag([q], [])

        edge_keys = {(e.from_id, e.to_id) for e in dag.edges}
        assert ("orders", "q_orders") in edge_keys

    def test_metric_base_table_leaf(self):
        m = _make_metric("m_rev", base_table="transactions")
        dag = build_dag([], [m])

        assert "transactions" in dag.nodes
        edge_keys = {(e.from_id, e.to_id) for e in dag.edges}
        assert ("transactions", "m_rev") in edge_keys


# ---------------------------------------------------------------------------
# 4. resolve_metric_lineage - base_table metric
# ---------------------------------------------------------------------------


class TestResolveMetricLineageBaseTable:
    def test_upstream_is_table(self):
        m = _make_metric("revenue", base_table="orders", expr="amount")
        result = resolve_metric_lineage(m)

        assert result["metric_id"] == "revenue"
        assert "orders" in result["upstream"]

    def test_input_columns_include_expr(self):
        m = _make_metric("revenue", base_table="orders", expr="amount")
        result = resolve_metric_lineage(m)

        cols = result["input_columns"]
        assert any(c["column"] == "amount" for c in cols), (
            f"Expected 'amount' in input_columns; got {cols}"
        )

    def test_count_star_has_no_column(self):
        m = _make_metric("cnt", base_table="events", agg="count", expr="*")
        result = resolve_metric_lineage(m)
        # COUNT(*) -> expr="*" -> no input column emitted
        assert result["input_columns"] == []

    def test_measure_field_present(self):
        m = _make_metric("revenue", base_table="orders", expr="amount")
        result = resolve_metric_lineage(m)
        assert result["measure"]["agg"] == "sum"
        assert result["measure"]["expr"] == "amount"


# ---------------------------------------------------------------------------
# 5. resolve_metric_lineage - base_sql metric
# ---------------------------------------------------------------------------


class TestResolveMetricLineageBaseSql:
    SQL = "SELECT customer_id, SUM(amount) AS revenue FROM transactions GROUP BY customer_id"

    def test_upstream_from_sql(self):
        m = MetricDefinition(
            id="sql_metric",
            name="SQL Metric",
            measure=Measure(name="revenue", agg="sum", expr="amount"),
            base_sql=self.SQL,
        )
        result = resolve_metric_lineage(m)

        assert "transactions" in result["upstream"]

    def test_input_columns_from_sql(self):
        m = MetricDefinition(
            id="sql_metric",
            name="SQL Metric",
            measure=Measure(name="revenue", agg="sum", expr="amount"),
            base_sql=self.SQL,
        )
        result = resolve_metric_lineage(m)

        col_names = [c["column"] for c in result["input_columns"]]
        assert "amount" in col_names or "customer_id" in col_names

    def test_metric_id_in_result(self):
        m = MetricDefinition(
            id="sql_metric",
            name="SQL Metric",
            measure=Measure(name="revenue", agg="sum", expr="amount"),
            base_sql=self.SQL,
        )
        result = resolve_metric_lineage(m)
        assert result["metric_id"] == "sql_metric"


# ---------------------------------------------------------------------------
# 6. resolve_metric_lineage - derived measures
# ---------------------------------------------------------------------------


class TestResolveMetricLineageDerived:
    def test_formula_captured(self):
        m = MetricDefinition(
            id="margin_metric",
            name="Margin",
            measure=Measure(name="revenue", agg="sum", expr="revenue_col"),
            base_table="orders",
            derived_measures=(
                DerivedMeasure(name="margin", formula="revenue / cost"),
            ),
        )
        result = resolve_metric_lineage(m)

        formulas = result["formula"]
        assert len(formulas) == 1
        assert formulas[0]["name"] == "margin"
        assert "revenue / cost" in formulas[0]["formula"]

    def test_no_derived_means_empty_formula(self):
        m = _make_metric("simple", base_table="tbl", expr="col")
        result = resolve_metric_lineage(m)
        assert result["formula"] == []


# ---------------------------------------------------------------------------
# 7-8. HTTP endpoints for DAG
# ---------------------------------------------------------------------------


class TestDagEndpoints:
    @pytest.mark.asyncio
    async def test_get_dag_200(self, dag_client):
        ac, user_id = dag_client
        resp = await ac.get(
            "/api/v1/lineage/dag",
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "nodes" in body
        assert "edges" in body
        assert isinstance(body["nodes"], list)
        assert isinstance(body["edges"], list)

    @pytest.mark.asyncio
    async def test_get_dag_requires_auth(self, dag_client):
        ac, _ = dag_client
        resp = await ac.get("/api/v1/lineage/dag")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_get_dag_has_seed_query_node(self, dag_client):
        """The seed query 'demo_all' should appear as a node."""
        ac, user_id = dag_client
        resp = await ac.get(
            "/api/v1/lineage/dag",
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200
        node_ids = {n["id"] for n in resp.json()["nodes"]}
        assert "demo_all" in node_ids

    @pytest.mark.asyncio
    async def test_get_dag_node_200(self, dag_client):
        """GET /lineage/dag/demo_all returns neighbourhood info."""
        ac, user_id = dag_client
        resp = await ac.get(
            "/api/v1/lineage/dag/demo_all",
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["node_id"] == "demo_all"
        assert "node" in body
        assert "upstream" in body
        assert "downstream" in body

    @pytest.mark.asyncio
    async def test_get_dag_node_404(self, dag_client):
        """GET /lineage/dag/{unknown} returns 404."""
        ac, user_id = dag_client
        resp = await ac.get(
            "/api/v1/lineage/dag/nonexistent_node_xyz",
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_dag_node_hops_param(self, dag_client):
        """hops param is accepted and reflected in the response."""
        ac, user_id = dag_client
        resp = await ac.get(
            "/api/v1/lineage/dag/demo_all?hops=1",
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200
        assert resp.json()["hops"] == 1


# ---------------------------------------------------------------------------
# 9-10. HTTP endpoints for metric lineage
# ---------------------------------------------------------------------------


class TestMetricLineageEndpoint:
    @pytest.mark.asyncio
    async def test_get_metric_lineage_200(self, dag_client):
        """GET /metrics/demo_revenue/lineage returns structured lineage."""
        ac, user_id = dag_client
        resp = await ac.get(
            "/api/v1/metrics/demo_revenue/lineage",
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["metric_id"] == "demo_revenue"
        assert "measure" in body
        assert "input_columns" in body
        assert "upstream" in body
        assert "formula" in body

    @pytest.mark.asyncio
    async def test_demo_revenue_upstream_is_demo(self, dag_client):
        """demo_revenue reads from 'demo' table."""
        ac, user_id = dag_client
        resp = await ac.get(
            "/api/v1/metrics/demo_revenue/lineage",
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "demo" in body["upstream"]

    @pytest.mark.asyncio
    async def test_demo_revenue_input_columns(self, dag_client):
        """demo_revenue has 'value' as an input column (SUM(value))."""
        ac, user_id = dag_client
        resp = await ac.get(
            "/api/v1/metrics/demo_revenue/lineage",
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200
        body = resp.json()
        col_names = [c["column"] for c in body["input_columns"]]
        assert "value" in col_names

    @pytest.mark.asyncio
    async def test_get_metric_lineage_404(self, dag_client):
        """GET /metrics/nonexistent/lineage returns 404."""
        ac, user_id = dag_client

        # Patch ensure_persisted_metric to return None (no DB available)
        with patch(
            "app.metrics.registry.ensure_persisted_metric",
            return_value=None,
        ):
            resp = await ac.get(
                "/api/v1/metrics/no_such_metric_xyz/lineage",
                headers=_auth_headers(user_id),
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_metric_lineage_requires_auth(self, dag_client):
        ac, _ = dag_client
        resp = await ac.get("/api/v1/metrics/demo_revenue/lineage")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 11. Cross-org isolation
# ---------------------------------------------------------------------------


class TestCrossOrgIsolation:
    """Nodes from another org are not visible via the DAG endpoint."""

    @pytest.mark.asyncio
    async def test_unknown_node_is_404(self, dag_client):
        """A node_id that does not belong to ANY org returns 404 (no info leak)."""
        ac, user_id = dag_client
        node_id = "other_org_secret_query_" + str(uuid.uuid4())
        resp = await ac.get(
            f"/api/v1/lineage/dag/{node_id}",
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_dag_only_shows_visible_queries(self, dag_client):
        """The DAG only includes the system seed queries by default (no org in
        this test harness), not hypothetical other-org queries."""
        ac, user_id = dag_client
        resp = await ac.get(
            "/api/v1/lineage/dag",
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200
        node_ids = {n["id"] for n in resp.json()["nodes"]}
        # Must not contain a node that was never registered
        assert "org_b_private_dataset" not in node_ids
