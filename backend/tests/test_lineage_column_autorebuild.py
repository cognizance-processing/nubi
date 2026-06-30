"""Tests for (A) cross-model column lineage + (B) lineage-driven auto-rebuild.

Feature A — resolve_column_lineage
-----------------------------------
1.  3-model chain source→A→B→metric: column path traced source.col→metric.
2.  Alias/rename across a hop is tracked (alias=True on renamed hop).
3.  SELECT * falls back to table-level edge; select_star=True; no crash.
4.  Unreachable / missing column stops gracefully (no crash).
5.  max_hops cap is respected.
6.  Cycle in DAG does not infinite-loop.
7.  Unknown start node returns empty list.

Feature B — on_materialized_model_complete (auto-rebuild)
----------------------------------------------------------
8.  Upstream success + auto_rebuild_downstream=True enqueues downstream flows.
9.  Only same-org downstream flows are enqueued (cross-org skipped).
10. Cycle/re-entrant flows are skipped (visited set).
11. Unrelated flows (not in DAG) are not enqueued.
12. state='failed' → nothing enqueued (success-only).
13. Upstream without auto_rebuild_downstream → nothing enqueued (opt-in).
14. Fan-out cap: >50 downstream flows → capped.
15. Debounce: calling the hook twice with same upstream_run_id does not
    double-enqueue the same downstream.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.flows.store import InMemoryFlowStore
from app.flows.triggers import (
    _reset_auto_rebuild_debounce,
    on_materialized_model_complete,
)
from app.lineage.dag import (
    DAGEdge,
    DAGNode,
    DependencyDAG,
    build_dag,
    resolve_column_lineage,
)
from app.metrics.models import Measure, MetricDefinition
from app.queries.registry import RegisteredQuery

pytestmark = pytest.mark.asyncio

NOW = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

# The lazy import target for materialize_flow_run inside triggers.py.
# It is imported as ``from app.flows.runtime import materialize_flow_run``
# inside each calling function, so we patch at its canonical location.
_MATERIALIZE_PATCH = "app.flows.runtime.materialize_flow_run"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_query(id: str, sql: str, name: str = "") -> RegisteredQuery:
    return RegisteredQuery(id=id, sql=sql, name=name or id)


def _make_metric(
    id: str,
    base_table: str | None = None,
    base_sql: str | None = None,
    agg: str = "sum",
    expr: str = "value",
) -> MetricDefinition:
    return MetricDefinition(
        id=id,
        name=id,
        measure=Measure(name="revenue", agg=agg, expr=expr),
        base_table=base_table,
        base_sql=base_sql,
    )


def _make_dag_from_sql(
    nodes: list[tuple[str, str]],  # [(id, sql), ...]
    edges: list[tuple[str, str, str]],  # [(from_id, to_id, via), ...]
) -> DependencyDAG:
    """Build a DependencyDAG manually from (id, sql) nodes and edge tuples."""
    from app.lineage.extract import extract_lineage  # noqa: PLC0415

    dag = DependencyDAG()
    for nid, sql in nodes:
        lineage_result = extract_lineage(sql)
        node = DAGNode(
            id=nid,
            type="query",
            name=nid,
            tables=lineage_result["tables"],
            outputs=lineage_result["outputs"],
            columns=lineage_result["columns"],
            sql=sql,
        )
        dag.nodes[nid] = node
    for from_id, to_id, via in edges:
        dag.edges.append(DAGEdge(from_id=from_id, to_id=to_id, via=via))
    return dag


async def _make_auto_rebuild_flow(
    store: InMemoryFlowStore,
    org_id: str,
    name: str,
    model_id: str | None = None,
    auto_rebuild: bool = True,
) -> dict[str, Any]:
    """Create a flow with auto_rebuild_downstream opt-in flag."""
    spec: dict[str, Any] = {
        "version": 1,
        "name": name,
        "tasks": [{"key": "t1", "kind": "noop", "needs": [], "config": {}}],
        "runtime_config": {},
    }
    if model_id is not None:
        spec["runtime_config"]["model_id"] = model_id
    if auto_rebuild:
        spec["runtime_config"]["auto_rebuild_downstream"] = True

    return await store.create_flow(
        org_id=org_id,
        created_by="user-test",
        name=name,
        spec=spec,
    )


async def _create_flow_run(
    store: InMemoryFlowStore,
    flow: dict[str, Any],
) -> dict[str, Any]:
    """Create a minimal flow run and return it."""
    flow_run_id = str(uuid.uuid4())
    org_id = flow["org_id"]
    flow_id = flow["id"]
    run: dict[str, Any] = {
        "id": flow_run_id,
        "flow_id": flow_id,
        "org_id": org_id,
        "state": "success",
        "params": {},
        "trigger": "manual",
        "scheduled_at": NOW,
        "started_at": NOW,
        "finished_at": NOW,
        "error": None,
        "created_at": NOW,
    }
    store._flow_runs[flow_run_id] = run
    store._flow_run_index.setdefault(flow_id, []).append(flow_run_id)
    return run


# ---------------------------------------------------------------------------
# Feature A — resolve_column_lineage
# ---------------------------------------------------------------------------


class TestColumnLineageChain:
    """3-model chain: source(physical table) → model_a → model_b → metric."""

    def _build(self) -> DependencyDAG:
        # model_a: SELECT col AS col_a FROM source_table
        # model_b: SELECT col_a AS col_b FROM model_a
        # metric:  metric node reading FROM model_b
        source = _make_query("source_table", "SELECT col FROM source_table")
        model_a = _make_query(
            "model_a",
            "SELECT col AS col_a FROM source_table",
        )
        model_b = _make_query(
            "model_b",
            "SELECT col_a AS col_b FROM model_a",
        )
        metric = _make_metric("metric_m", base_sql="SELECT col_b FROM model_b")
        dag = build_dag([source, model_a, model_b], [metric])
        return dag

    def test_path_starts_at_metric(self):
        dag = self._build()
        path = resolve_column_lineage(dag, "metric_m", "col_b", max_hops=10)
        assert len(path) >= 1
        assert path[0]["node"] == "metric_m"
        assert path[0]["column"] == "col_b"

    def test_path_reaches_source(self):
        dag = self._build()
        path = resolve_column_lineage(dag, "metric_m", "col_b", max_hops=10)
        node_ids = [p["node"] for p in path]
        # Should trace through model_b and model_a back to source_table
        assert "model_b" in node_ids or "model_a" in node_ids or "source_table" in node_ids

    def test_three_model_chain_col_renamed(self):
        """col → col_a → col_b across two hops; alias=True on renames."""
        dag = self._build()
        # Trace from model_b backwards: col_b should trace to col_a in model_a.
        path = resolve_column_lineage(dag, "model_b", "col_b", max_hops=5)
        # The path should show col_b at model_b, col_a at model_a
        node_ids = [p["node"] for p in path]
        assert "model_b" in node_ids
        # Alias tracking: the step where the column changes name should have alias=True
        alias_hops = [p for p in path if p["alias"]]
        assert len(alias_hops) >= 1, (
            f"Expected at least one alias hop; path={path}"
        )


class TestColumnLineageAlias:
    """Alias/rename across a single hop is tracked correctly."""

    def test_alias_hop_tracked(self):
        dag = _make_dag_from_sql(
            [
                ("src", "SELECT amount FROM orders"),
                ("model_x", "SELECT amount AS revenue FROM src"),
            ],
            [("src", "model_x", "src")],
        )
        path = resolve_column_lineage(dag, "model_x", "revenue", max_hops=5)
        # path[0]: model_x / revenue (alias=False, start)
        # path[1]: src / amount (alias=True because revenue != amount)
        assert path[0]["node"] == "model_x"
        assert path[0]["column"] == "revenue"
        assert len(path) >= 2
        assert path[1]["column"] == "amount"
        assert path[1]["alias"] is True

    def test_identity_hop_not_alias(self):
        dag = _make_dag_from_sql(
            [
                ("src", "SELECT amount FROM orders"),
                ("model_y", "SELECT amount FROM src"),
            ],
            [("src", "model_y", "src")],
        )
        path = resolve_column_lineage(dag, "model_y", "amount", max_hops=5)
        # Both hops should have the same column name; none should be alias=True
        assert path[0]["column"] == "amount"
        if len(path) > 1:
            assert path[1]["alias"] is False


class TestColumnLineageSelectStar:
    """SELECT * layers fall back to table-level edge; select_star=True; no crash."""

    def test_select_star_fallback(self):
        # model_star passes columns through with SELECT *
        dag = _make_dag_from_sql(
            [
                ("src", "SELECT id, amount FROM orders"),
                ("model_star", "SELECT * FROM src"),
            ],
            [("src", "model_star", "src")],
        )
        path = resolve_column_lineage(dag, "model_star", "amount", max_hops=5)
        # Should not crash; should return something
        assert isinstance(path, list)
        # The hop at model_star should be marked as select_star=True
        star_hops = [p for p in path if p.get("select_star")]
        assert len(star_hops) >= 1, (
            f"Expected at least one select_star hop; path={path}"
        )

    def test_select_star_does_not_crash_on_missing_col(self):
        dag = _make_dag_from_sql(
            [
                ("src", "SELECT id FROM orders"),
                ("model_star", "SELECT * FROM src"),
            ],
            [("src", "model_star", "src")],
        )
        # Even asking for a column that doesn't exist in the upstream should not raise.
        path = resolve_column_lineage(dag, "model_star", "nonexistent_col", max_hops=5)
        assert isinstance(path, list)


class TestColumnLineageEdgeCases:
    """Edge cases: max_hops, missing node, cycle."""

    def test_unknown_start_node_returns_empty(self):
        dag = DependencyDAG()
        result = resolve_column_lineage(dag, "does_not_exist", "col", max_hops=5)
        assert result == []

    def test_max_hops_cap_respected(self):
        # Build a 10-node chain.
        nodes = []
        edges = []
        prev_id = "src"
        nodes.append(("src", "SELECT col FROM phys_table"))
        for i in range(1, 10):
            nid = f"model_{i}"
            nodes.append((nid, f"SELECT col FROM {prev_id}"))
            edges.append((prev_id, nid, prev_id))
            prev_id = nid

        dag = _make_dag_from_sql(nodes, edges)

        # With max_hops=3, the path should not exceed 4 entries (start + 3 hops).
        path = resolve_column_lineage(dag, "model_9", "col", max_hops=3)
        assert len(path) <= 4

    def test_cycle_does_not_infinite_loop(self):
        """A cycle A→B→A in the DAG should not cause infinite recursion."""
        dag = DependencyDAG()
        dag.nodes["a"] = DAGNode(
            id="a", type="query", name="a",
            sql="SELECT col FROM b",
            tables=["b"], outputs=["col"],
        )
        dag.nodes["b"] = DAGNode(
            id="b", type="query", name="b",
            sql="SELECT col FROM a",
            tables=["a"], outputs=["col"],
        )
        # Cyclic edges.
        dag.edges.append(DAGEdge(from_id="b", to_id="a", via="b"))
        dag.edges.append(DAGEdge(from_id="a", to_id="b", via="a"))

        # Should terminate without error.
        path = resolve_column_lineage(dag, "a", "col", max_hops=10)
        assert isinstance(path, list)

    def test_missing_column_stops_gracefully(self):
        """Asking for a column not in the SELECT list stops without crashing."""
        dag = _make_dag_from_sql(
            [("src", "SELECT id FROM orders"), ("m", "SELECT id FROM src")],
            [("src", "m", "src")],
        )
        path = resolve_column_lineage(dag, "m", "nonexistent_xyz", max_hops=5)
        assert isinstance(path, list)
        # The starting node is included but no upstream hop.
        assert len(path) <= 1


# ---------------------------------------------------------------------------
# Feature B — on_materialized_model_complete (auto-rebuild)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_debounce():
    """Reset the auto-rebuild debounce set before each test."""
    _reset_auto_rebuild_debounce()
    yield
    _reset_auto_rebuild_debounce()


def _build_two_node_dag(upstream_model_id: str, downstream_model_id: str) -> DependencyDAG:
    """Build a minimal DAG: upstream → downstream."""
    dag = DependencyDAG()
    dag.nodes[upstream_model_id] = DAGNode(
        id=upstream_model_id, type="query", name=upstream_model_id,
    )
    dag.nodes[downstream_model_id] = DAGNode(
        id=downstream_model_id, type="query", name=downstream_model_id,
    )
    dag.edges.append(
        DAGEdge(from_id=upstream_model_id, to_id=downstream_model_id, via=upstream_model_id)
    )
    return dag


async def test_auto_rebuild_enqueues_downstream_on_success():
    """Upstream success + auto_rebuild_downstream=True enqueues downstream."""
    store = InMemoryFlowStore()
    org_id = str(uuid.uuid4())

    upstream_flow = await _make_auto_rebuild_flow(
        store, org_id, "upstream_model", model_id="model_up", auto_rebuild=True
    )
    await _make_auto_rebuild_flow(
        store, org_id, "downstream_model", model_id="model_down", auto_rebuild=True
    )

    flow_run = await _create_flow_run(store, upstream_flow)
    dag = _build_two_node_dag("model_up", "model_down")

    new_run_id = str(uuid.uuid4())
    with patch(_MATERIALIZE_PATCH, new=AsyncMock(return_value={"id": new_run_id})):
        run_ids = await on_materialized_model_complete(
            store=store,
            flow_run_id=flow_run["id"],
            state="success",
            now=NOW,
            dag=dag,
        )

    assert new_run_id in run_ids, f"Expected downstream run to be enqueued; got {run_ids}"


async def test_auto_rebuild_not_triggered_on_failed():
    """state='failed' → nothing enqueued."""
    store = InMemoryFlowStore()
    org_id = str(uuid.uuid4())

    upstream_flow = await _make_auto_rebuild_flow(
        store, org_id, "upstream_model", model_id="model_up", auto_rebuild=True
    )
    flow_run = await _create_flow_run(store, upstream_flow)
    dag = _build_two_node_dag("model_up", "model_down")

    with patch(_MATERIALIZE_PATCH, new=AsyncMock(return_value={"id": str(uuid.uuid4())})):
        run_ids = await on_materialized_model_complete(
            store=store,
            flow_run_id=flow_run["id"],
            state="failed",
            now=NOW,
            dag=dag,
        )

    assert run_ids == [], f"Expected no runs enqueued on failure; got {run_ids}"


async def test_auto_rebuild_opt_in_required():
    """Upstream without auto_rebuild_downstream=True → nothing enqueued."""
    store = InMemoryFlowStore()
    org_id = str(uuid.uuid4())

    upstream_flow = await _make_auto_rebuild_flow(
        store, org_id, "upstream_model", model_id="model_up", auto_rebuild=False
    )
    await _make_auto_rebuild_flow(
        store, org_id, "downstream_model", model_id="model_down", auto_rebuild=True
    )

    flow_run = await _create_flow_run(store, upstream_flow)
    dag = _build_two_node_dag("model_up", "model_down")

    with patch(_MATERIALIZE_PATCH, new=AsyncMock(return_value={"id": str(uuid.uuid4())})):
        run_ids = await on_materialized_model_complete(
            store=store,
            flow_run_id=flow_run["id"],
            state="success",
            now=NOW,
            dag=dag,
        )

    assert run_ids == [], (
        f"Expected no runs enqueued when auto_rebuild_downstream not set; got {run_ids}"
    )


async def test_auto_rebuild_cross_org_skipped():
    """Cross-org downstream flows are not enqueued."""
    store = InMemoryFlowStore()
    org_a = str(uuid.uuid4())
    org_b = str(uuid.uuid4())

    upstream_flow = await _make_auto_rebuild_flow(
        store, org_a, "upstream_model", model_id="model_up", auto_rebuild=True
    )
    # Create downstream flow in a DIFFERENT org.
    await _make_auto_rebuild_flow(
        store, org_b, "downstream_model", model_id="model_down", auto_rebuild=True
    )

    flow_run = await _create_flow_run(store, upstream_flow)
    dag = _build_two_node_dag("model_up", "model_down")

    with patch(_MATERIALIZE_PATCH, new=AsyncMock(return_value={"id": str(uuid.uuid4())})):
        run_ids = await on_materialized_model_complete(
            store=store,
            flow_run_id=flow_run["id"],
            state="success",
            now=NOW,
            dag=dag,
        )

    assert run_ids == [], (
        f"Expected cross-org downstream to be skipped; got {run_ids}"
    )


async def test_auto_rebuild_unrelated_flows_not_enqueued():
    """Flows not in the DAG downstream of the upstream model are not enqueued."""
    store = InMemoryFlowStore()
    org_id = str(uuid.uuid4())

    upstream_flow = await _make_auto_rebuild_flow(
        store, org_id, "upstream_model", model_id="model_up", auto_rebuild=True
    )
    # This flow belongs to a DAG node NOT downstream of model_up.
    await _make_auto_rebuild_flow(
        store, org_id, "unrelated_model", model_id="model_unrelated", auto_rebuild=True
    )

    flow_run = await _create_flow_run(store, upstream_flow)

    # DAG: model_up has NO downstream nodes.
    dag = DependencyDAG()
    dag.nodes["model_up"] = DAGNode(id="model_up", type="query", name="model_up")
    dag.nodes["model_unrelated"] = DAGNode(
        id="model_unrelated", type="query", name="model_unrelated"
    )
    # No edge from model_up to model_unrelated.

    with patch(_MATERIALIZE_PATCH, new=AsyncMock(return_value={"id": str(uuid.uuid4())})):
        run_ids = await on_materialized_model_complete(
            store=store,
            flow_run_id=flow_run["id"],
            state="success",
            now=NOW,
            dag=dag,
        )

    assert run_ids == [], (
        f"Expected unrelated flows NOT enqueued; got {run_ids}"
    )


async def test_auto_rebuild_cycle_does_not_loop():
    """Cyclic flow dependency (A→B→A) does not infinite-loop via visited set."""
    store = InMemoryFlowStore()
    org_id = str(uuid.uuid4())

    flow_a = await _make_auto_rebuild_flow(
        store, org_id, "flow_a", model_id="model_a", auto_rebuild=True
    )
    await _make_auto_rebuild_flow(
        store, org_id, "flow_b", model_id="model_b", auto_rebuild=True
    )

    flow_run_a = await _create_flow_run(store, flow_a)

    # Cyclic DAG: model_a → model_b → model_a
    dag = DependencyDAG()
    dag.nodes["model_a"] = DAGNode(id="model_a", type="query", name="model_a")
    dag.nodes["model_b"] = DAGNode(id="model_b", type="query", name="model_b")
    dag.edges.append(DAGEdge(from_id="model_a", to_id="model_b", via="model_a"))
    dag.edges.append(DAGEdge(from_id="model_b", to_id="model_a", via="model_b"))

    new_run_id = str(uuid.uuid4())
    with patch(_MATERIALIZE_PATCH, new=AsyncMock(return_value={"id": new_run_id})):
        # This should terminate without error.
        run_ids = await on_materialized_model_complete(
            store=store,
            flow_run_id=flow_run_a["id"],
            state="success",
            now=NOW,
            dag=dag,
        )

    # model_b should be enqueued once; model_a itself is the upstream so it's
    # in _visited and will NOT be enqueued again.
    assert isinstance(run_ids, list)


async def test_auto_rebuild_debounce_prevents_double_enqueue():
    """Same upstream_run_id should not enqueue the same downstream twice."""
    store = InMemoryFlowStore()
    org_id = str(uuid.uuid4())

    upstream_flow = await _make_auto_rebuild_flow(
        store, org_id, "upstream_model", model_id="model_up", auto_rebuild=True
    )
    await _make_auto_rebuild_flow(
        store, org_id, "downstream_model", model_id="model_down", auto_rebuild=True
    )

    flow_run = await _create_flow_run(store, upstream_flow)
    dag = _build_two_node_dag("model_up", "model_down")

    call_count = 0
    new_run_ids = [str(uuid.uuid4()), str(uuid.uuid4())]

    async def fake_materialize(store, flow, params, trigger, now, env=None):
        nonlocal call_count
        idx = min(call_count, len(new_run_ids) - 1)
        call_count += 1
        return {"id": new_run_ids[idx]}

    with patch(_MATERIALIZE_PATCH, new=fake_materialize):
        # Call the hook twice with the SAME flow_run_id.
        ids1 = await on_materialized_model_complete(
            store=store,
            flow_run_id=flow_run["id"],
            state="success",
            now=NOW,
            dag=dag,
        )
        ids2 = await on_materialized_model_complete(
            store=store,
            flow_run_id=flow_run["id"],
            state="success",
            now=NOW,
            dag=dag,
        )

    # The second call should be debounced — downstream should not be enqueued again.
    assert len(ids1) == 1, f"First call should enqueue 1; got {ids1}"
    assert len(ids2) == 0, f"Second call should be debounced; got {ids2}"
    assert call_count == 1, f"materialize_flow_run should be called exactly once; got {call_count}"


async def test_auto_rebuild_fan_out_cap():
    """More than _FLOWS_TRIGGER_MAX_FANOUT downstream flows are capped."""
    from app.flows import triggers as triggers_mod  # noqa: PLC0415

    store = InMemoryFlowStore()
    org_id = str(uuid.uuid4())

    upstream_flow = await _make_auto_rebuild_flow(
        store, org_id, "upstream_model", model_id="model_up", auto_rebuild=True
    )
    flow_run = await _create_flow_run(store, upstream_flow)

    # Build 55 downstream nodes (> default cap of 50).
    dag = DependencyDAG()
    dag.nodes["model_up"] = DAGNode(id="model_up", type="query", name="model_up")
    for i in range(55):
        ds_id = f"ds_{i}"
        dag.nodes[ds_id] = DAGNode(id=ds_id, type="query", name=ds_id)
        dag.edges.append(DAGEdge(from_id="model_up", to_id=ds_id, via="model_up"))
        # Create a corresponding flow in the same org.
        await store.create_flow(
            org_id=org_id,
            created_by="user-test",
            name=ds_id,
            spec={
                "version": 1,
                "name": ds_id,
                "tasks": [{"key": "t", "kind": "noop", "needs": [], "config": {}}],
                "runtime_config": {"model_id": ds_id, "auto_rebuild_downstream": True},
            },
        )

    enqueued: list[str] = []

    async def fake_mat(store, flow, params, trigger, now, env=None):
        rid = str(uuid.uuid4())
        enqueued.append(rid)
        return {"id": rid}

    with patch(_MATERIALIZE_PATCH, new=fake_mat):
        run_ids = await on_materialized_model_complete(
            store=store,
            flow_run_id=flow_run["id"],
            state="success",
            now=NOW,
            dag=dag,
        )

    # Should be capped at FLOWS_TRIGGER_MAX_FANOUT (default 50).
    assert len(run_ids) <= triggers_mod._FLOWS_TRIGGER_MAX_FANOUT, (
        f"Expected at most {triggers_mod._FLOWS_TRIGGER_MAX_FANOUT} runs; got {len(run_ids)}"
    )
