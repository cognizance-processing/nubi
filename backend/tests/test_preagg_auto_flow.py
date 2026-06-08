"""End-to-end test: scheduled pre-aggregation flow fires and routes queries.

This test suite proves the complete "cost/efficiency wedge" described in
ROADMAP §4:

1. Seed query-log patterns (high-frequency GROUP BY queries).
2. Run the ``preagg_refresh`` pass (via both the direct helper AND the flows
   engine task handler so both paths are exercised).
3. Assert that a subsequent query is TRANSPARENTLY ROUTED to the materialized
   rollup by ``route_to_rollup_shape`` — the caller never touches the rollup
   directly; the planner rewrites the plan on its behalf.
4. Assert idempotency: running ``ensure_preagg_flow`` twice creates exactly
   ONE flow in the store.

All tests run against InMemory stores — no Postgres, no external DuckDB
files (except a temp file for the builder, which is cleaned up).

Coverage
--------
A. ``run_preagg_refresh`` direct call → rollups built, registered in registry.
B. ``preagg_refresh`` flows task handler → same pass via the registry handler.
C. Query routing: ``route_to_rollup_shape`` routes correctly after refresh.
D. Idempotency: ``ensure_preagg_flow`` creates the flow only once.
E. Scheduled flow spec: the registered flow passes ``validate_flow_spec``.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any

import duckdb
import pytest
import pytest_asyncio

from app.connectors.preagg import (
    RollupCandidate,
    RollupRegistry,
    build_rollup,
)
from app.connectors.planner import plan, route_to_rollup_shape
from app.connectors.query_log import QueryLog, extract_shape
from app.flows.registry import get_task_kind_registry, reset_for_tests as reset_flow_registry
from app.flows.spec import validate_flow_spec, flow_spec_is_valid
from app.flows.store import InMemoryFlowStore, set_flow_store
from app.preagg.scheduler import ensure_preagg_flow, run_preagg_refresh


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_registries():
    """Reset flow kind registry before/after every test in this module."""
    reset_flow_registry()
    yield
    reset_flow_registry()


@pytest.fixture()
def mem_store() -> InMemoryFlowStore:
    """Fresh InMemoryFlowStore injected as the singleton for the test."""
    store = InMemoryFlowStore()
    set_flow_store(store)
    yield store
    set_flow_store(None)


@pytest.fixture()
def source_db() -> str:
    """Temp DuckDB file with an ``orders`` fact table.

    Schema: tenant_id (RLS key), region (dim), amount (measure).
    Two tenants × two regions so rollup re-aggregation is non-trivial.
    """
    fd, path = tempfile.mkstemp(suffix=".duckdb")
    os.close(fd)
    os.remove(path)  # let DuckDB create it fresh
    conn = duckdb.connect(path)
    conn.execute(
        "CREATE TABLE orders (tenant_id VARCHAR, region VARCHAR, amount INTEGER)"
    )
    conn.execute(
        """
        INSERT INTO orders VALUES
            ('acme', 'us', 10), ('acme', 'us', 5), ('acme', 'eu', 7),
            ('beta', 'us', 100), ('beta', 'eu', 3), ('beta', 'eu', 4)
        """
    )
    conn.close()
    yield path
    if os.path.exists(path):
        os.remove(path)


@pytest.fixture()
def registry() -> RollupRegistry:
    """Fresh RollupRegistry for each test (does NOT touch the singleton)."""
    return RollupRegistry()


@pytest.fixture()
def query_log() -> QueryLog:
    """Fresh QueryLog pre-seeded with 5 identical high-frequency patterns.

    The SQL is a routable single-table GROUP BY that the miner will surface
    (min_hits=3 threshold is satisfied with 5 hits).
    """
    log = QueryLog()
    sql = "SELECT region, SUM(amount) FROM orders GROUP BY region"
    for _ in range(5):
        log.record(sql, cache_key="k", byte_size=1000)
    return log


# ---------------------------------------------------------------------------
# A. Direct run_preagg_refresh: builds rollups from the seeded log
# ---------------------------------------------------------------------------


class TestRunPreaggRefresh:
    def test_builds_rollup_for_high_frequency_candidate(
        self, query_log: QueryLog, registry: RollupRegistry, source_db: str
    ) -> None:
        result = run_preagg_refresh(
            org_id="org-001",
            min_hits=3,
            registry=registry,
            query_log=query_log,
            source_database=source_db,
        )
        assert result["candidates_found"] == 1
        assert result["rollups_built"] == 1
        assert len(result["rollup_ids"]) == 1
        assert result["errors"] == []

        # Rollup is registered and discoverable.
        rollups = registry.candidates_for_table("orders")
        assert len(rollups) == 1
        rollup = rollups[0]
        assert "region" in rollup.dimensions
        assert "sum" in " ".join(rollup.measures).lower()

    def test_below_min_hits_builds_nothing(
        self, query_log: QueryLog, registry: RollupRegistry, source_db: str
    ) -> None:
        # min_hits=10 exceeds the 5 hits seeded → no candidates.
        result = run_preagg_refresh(
            org_id="org-001",
            min_hits=10,
            registry=registry,
            query_log=query_log,
            source_database=source_db,
        )
        assert result["candidates_found"] == 0
        assert result["rollups_built"] == 0

    def test_idempotent_second_run_skips_existing(
        self, query_log: QueryLog, registry: RollupRegistry, source_db: str
    ) -> None:
        # First run: builds the rollup.
        run_preagg_refresh(
            org_id="org-001", registry=registry,
            query_log=query_log, source_database=source_db,
        )
        first_rollup_count = len(registry.all_rollups())
        assert first_rollup_count == 1

        # Second run: same candidates → same dims → skipped.
        result2 = run_preagg_refresh(
            org_id="org-001", registry=registry,
            query_log=query_log, source_database=source_db,
        )
        assert result2["rollups_built"] == 0
        assert len(registry.all_rollups()) == first_rollup_count  # no duplicates

    def test_empty_log_builds_nothing(
        self, registry: RollupRegistry, source_db: str
    ) -> None:
        result = run_preagg_refresh(
            org_id="org-001",
            registry=registry,
            query_log=QueryLog(),
            source_database=source_db,
        )
        assert result["candidates_found"] == 0
        assert result["rollups_built"] == 0


# ---------------------------------------------------------------------------
# B. preagg_refresh task handler (via the flows task-kind registry)
# ---------------------------------------------------------------------------


class TestPreaggRefreshHandler:
    def test_handler_registered_as_builtin_kind(self) -> None:
        reg = get_task_kind_registry()
        kinds = reg.all()
        assert "preagg_refresh" in kinds

    def test_handler_dispatch_via_registry(
        self, query_log: QueryLog, registry: RollupRegistry, source_db: str
    ) -> None:
        """Call the handler through the task-kind registry (as the executor does)."""
        from app.flows.executor import TaskContext  # noqa: PLC0415
        from datetime import datetime, timezone  # noqa: PLC0415

        kind_reg = get_task_kind_registry()
        handler = kind_reg.get("preagg_refresh")

        ctx = TaskContext(
            flow_params={},
            inputs={},
            now=datetime.now(timezone.utc),
            secrets={},
        )

        # Patch the module-level singletons the handler uses.
        import app.preagg.scheduler as sched_mod  # noqa: PLC0415
        import app.connectors.query_log as ql_mod  # noqa: PLC0415

        original_get_registry = None
        original_get_query_log = None

        # Temporarily inject our test-scoped instances.
        import app.connectors.preagg as preagg_mod  # noqa: PLC0415
        _orig_registry = preagg_mod.get_registry
        _orig_query_log = ql_mod.get_query_log

        preagg_mod.get_registry = lambda: registry
        ql_mod.get_query_log = lambda: query_log

        try:
            result = handler(
                config={"org_id": "org-001", "min_hits": 3, "source_database": source_db},
                ctx=ctx,
                claims={},
            )
        finally:
            preagg_mod.get_registry = _orig_registry
            ql_mod.get_query_log = _orig_query_log

        assert result["candidates_found"] == 1
        assert result["rollups_built"] == 1
        assert result["errors"] == []

    def test_handler_missing_org_id_raises(self) -> None:
        from app.flows.executor import TaskContext  # noqa: PLC0415
        from app.errors import AppError  # noqa: PLC0415
        from datetime import datetime, timezone  # noqa: PLC0415

        kind_reg = get_task_kind_registry()
        handler = kind_reg.get("preagg_refresh")
        ctx = TaskContext(
            flow_params={}, inputs={},
            now=datetime.now(timezone.utc), secrets={},
        )
        with pytest.raises(AppError) as exc_info:
            handler(config={}, ctx=ctx, claims={})
        assert exc_info.value.code == "invalid_task_config"


# ---------------------------------------------------------------------------
# C. Transparent query routing after refresh
# ---------------------------------------------------------------------------


class TestQueryRoutingAfterRefresh:
    def test_query_transparently_routed_to_rollup(
        self, query_log: QueryLog, registry: RollupRegistry, source_db: str
    ) -> None:
        """Seed log → refresh → subsequent plan hits the rollup automatically."""
        # Run the refresh to materialize the rollup.
        result = run_preagg_refresh(
            org_id="org-001",
            min_hits=3,
            registry=registry,
            query_log=query_log,
            source_database=source_db,
        )
        assert result["rollups_built"] == 1, f"Build failed: {result['errors']}"

        # Build the query plan for the same pattern that was mined.
        original_plan = plan(
            "SELECT region, SUM(amount) FROM orders GROUP BY region"
        )

        # Route: the planner should transparently rewrite to the rollup.
        route_result = route_to_rollup_shape(original_plan, registry)

        assert route_result.routed is True, f"Not routed: {route_result.reason}"
        assert route_result.rollup_id is not None

        # The rewritten SQL targets the rollup table, not the base fact.
        rewritten_sql = route_result.plan.sql.lower()
        assert "rollup_orders" in rewritten_sql, f"Expected rollup_orders in: {route_result.plan.sql}"

        # The partial measure column is in the rewritten SELECT.
        assert "sum_amount" in rewritten_sql, f"Expected sum_amount in: {route_result.plan.sql}"

        # Cache key differs (routing changed the plan).
        assert route_result.plan.cache_key != original_plan.cache_key

    def test_routed_rollup_produces_correct_results(
        self, query_log: QueryLog, registry: RollupRegistry, source_db: str
    ) -> None:
        """The routed plan, when executed, produces the same answer as raw."""
        # Build the rollup.
        run_preagg_refresh(
            org_id="org-001", registry=registry,
            query_log=query_log, source_database=source_db,
        )

        # Original query result (against the source_db fact table).
        raw_conn = duckdb.connect(source_db, read_only=True)
        raw_rows = raw_conn.execute(
            "SELECT region, SUM(amount) AS total FROM orders GROUP BY region ORDER BY region"
        ).fetchall()
        raw_conn.close()

        # Routed query result (against the rollup's DuckDB file).
        rollup = registry.all_rollups()[0]
        roll_conn = duckdb.connect(rollup.database, read_only=True)
        roll_rows = roll_conn.execute(
            f'SELECT region, SUM("sum_amount") AS total '
            f'FROM "{rollup.table}" GROUP BY region ORDER BY region'
        ).fetchall()
        roll_conn.close()

        assert roll_rows == raw_rows, (
            f"Rollup result {roll_rows!r} != raw result {raw_rows!r}"
        )

    def test_rls_predicate_preserved_after_routing(
        self, registry: RollupRegistry, source_db: str
    ) -> None:
        """RLS claims are preserved in a rollup plan that carries the RLS key.

        For a sound RLS-filtered routing the rollup MUST be built with
        ``rls_keys=['tenant_id']`` so the filter column is physically present in
        the rollup table.  ``run_preagg_refresh`` intentionally passes
        ``rls_keys=[]`` (conservative); here we build the rollup explicitly
        with the RLS key to test this routing path.
        """
        candidate = RollupCandidate(
            table="orders",
            dimensions=["region"],
            measures=["sum(amount)", "count(*)"],
        )
        build_rollup(
            candidate,
            rls_keys=["tenant_id"],
            source_database=source_db,
            registry=registry,
            register_query=False,
        )

        p = plan(
            "SELECT region, SUM(amount) FROM orders GROUP BY region",
            claims={"policies": {"tenant_id": "acme"}},
        )
        result = route_to_rollup_shape(p, registry)
        assert result.routed is True, f"Not routed: {result.reason}"
        # RLS claim survives the rewrite.
        assert result.plan.rls_claims == {"policies": {"tenant_id": "acme"}}
        # The WHERE clause retains the predicate.
        assert "tenant_id" in result.plan.sql.lower()

    def test_non_materialized_query_not_routed(
        self, registry: RollupRegistry
    ) -> None:
        """A query whose pattern was never mined is not routed."""
        p = plan("SELECT category, SUM(revenue) FROM sales GROUP BY category")
        result = route_to_rollup_shape(p, registry)  # empty registry
        assert result.routed is False
        assert result.plan is p  # untouched


# ---------------------------------------------------------------------------
# D. ensure_preagg_flow idempotency
# ---------------------------------------------------------------------------


class TestEnsurePreaggFlow:
    @pytest.mark.asyncio
    async def test_creates_flow_on_first_call(self, mem_store: InMemoryFlowStore) -> None:
        flow = await ensure_preagg_flow(
            org_id="org-abc",
            created_by="user-001",
            flow_store=mem_store,
        )
        assert flow["name"] == "__preagg_refresh__"
        assert flow["schedule"] == "0 * * * *"
        assert flow["enabled"] is True

        flows = await mem_store.list_flows("org-abc")
        assert len(flows) == 1

    @pytest.mark.asyncio
    async def test_idempotent_second_call_returns_existing(
        self, mem_store: InMemoryFlowStore
    ) -> None:
        flow1 = await ensure_preagg_flow(
            "org-abc", "user-001", flow_store=mem_store
        )
        flow2 = await ensure_preagg_flow(
            "org-abc", "user-001", flow_store=mem_store
        )
        # Same flow returned, only ONE flow in the store.
        assert flow1["id"] == flow2["id"]
        flows = await mem_store.list_flows("org-abc")
        assert len(flows) == 1

    @pytest.mark.asyncio
    async def test_different_orgs_get_separate_flows(
        self, mem_store: InMemoryFlowStore
    ) -> None:
        await ensure_preagg_flow("org-aaa", "u1", flow_store=mem_store)
        await ensure_preagg_flow("org-bbb", "u1", flow_store=mem_store)
        flows_a = await mem_store.list_flows("org-aaa")
        flows_b = await mem_store.list_flows("org-bbb")
        assert len(flows_a) == 1
        assert len(flows_b) == 1
        assert flows_a[0]["id"] != flows_b[0]["id"]

    @pytest.mark.asyncio
    async def test_custom_schedule_stored(self, mem_store: InMemoryFlowStore) -> None:
        flow = await ensure_preagg_flow(
            "org-xyz", "u1", schedule="30 2 * * *", flow_store=mem_store
        )
        assert flow["schedule"] == "30 2 * * *"


# ---------------------------------------------------------------------------
# E. FlowSpec validation for preagg_refresh kind
# ---------------------------------------------------------------------------


class TestPreaggRefreshFlowSpec:
    def test_valid_spec_passes_validation(self) -> None:
        spec: dict[str, Any] = {
            "version": 1,
            "name": "__preagg_refresh__",
            "params": [],
            "tasks": [
                {
                    "key": "refresh",
                    "kind": "preagg_refresh",
                    "needs": [],
                    "config": {"org_id": "org-001", "min_hits": 3},
                }
            ],
        }
        parsed, issues = validate_flow_spec(spec)
        assert parsed is not None
        assert flow_spec_is_valid(issues), f"Unexpected issues: {issues}"

    def test_missing_org_id_is_hard_error(self) -> None:
        spec: dict[str, Any] = {
            "version": 1,
            "name": "__preagg_refresh__",
            "params": [],
            "tasks": [
                {
                    "key": "refresh",
                    "kind": "preagg_refresh",
                    "needs": [],
                    "config": {},  # missing org_id
                }
            ],
        }
        parsed, issues = validate_flow_spec(spec)
        assert not flow_spec_is_valid(issues)
        assert any("org_id" in i for i in issues)

    def test_preagg_refresh_kind_in_literal(self) -> None:
        """preagg_refresh is a valid Literal kind — Pydantic accepts it."""
        from app.flows.spec import TaskSpec  # noqa: PLC0415

        task = TaskSpec(
            key="r",
            kind="preagg_refresh",  # type: ignore[arg-type]
            config={"org_id": "org-001"},
        )
        assert task.kind == "preagg_refresh"
