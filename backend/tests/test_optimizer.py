"""Tests for Optimizer.build() and Optimizer.maintain() — no longer stubs.

Coverage
--------
1. ``build()`` materializes rollups for auto-build candidates:
   - Returns a list of ``BuiltRollup`` objects.
   - Registers each rollup in the registry.
   - Query routing works immediately after build.
2. ``maintain()`` refreshes existing rollups:
   - Overwrites rollup data in place (same rollup_id).
   - Registry still contains the refreshed rollup.
   - Query still routes to the rollup after maintain.
3. Org-isolation: rollups built via Optimizer carry the org_id tag.
4. Empty plan / empty registry are handled gracefully.
5. ``build()`` skips candidates below threshold (auto_build=False).
"""

from __future__ import annotations

import os
import tempfile

import duckdb
import pytest

from app.connectors.preagg import RollupCandidate, RollupRegistry
from app.connectors.planner import plan, route_to_rollup_shape
from app.connectors.query_log import QueryLog
from app.lakehouse.optimizer import DEFAULT_BUILD_THRESHOLD, Optimizer, OptimizerPlan, PlannedRollup, LayoutHint

# Tenant tag for build+route tests. The router REFUSES to route for an unscoped
# (org_id=None) caller, so a single-org test must build AND route under a
# concrete org tag to exercise the rollup rewrite path.
_TEST_ORG = "test_org"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def source_db() -> str:
    """Temp DuckDB file with an ``orders`` fact table.

    Columns: tenant_id (RLS key), region (dim), amount (measure).
    Two tenants × two regions so re-aggregation is non-trivial.
    """
    fd, path = tempfile.mkstemp(suffix=".duckdb")
    os.close(fd)
    os.remove(path)
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


def _make_optimizer(registry: RollupRegistry) -> Optimizer:
    """Return an Optimizer with build_threshold=1 to ensure auto_build fires."""
    return Optimizer(registry=registry, build_threshold=1)


def _make_candidate(
    table: str = "orders",
    dimensions: list[str] | None = None,
    measures: list[str] | None = None,
    score: int = 100,
) -> RollupCandidate:
    return RollupCandidate(
        table=table,
        dimensions=dimensions or ["region"],
        measures=measures or ["sum(amount)", "count(*)"],
        score=score,
        sample_count=5,
        est_bytes=score // 5,
        cluster_key=f"{table}|{'|'.join(sorted(dimensions or ['region']))}",
    )


def _make_plan_with_candidate(
    candidate: RollupCandidate,
    *,
    auto_build: bool = True,
) -> OptimizerPlan:
    """Wrap a single candidate in an OptimizerPlan with a dummy LayoutHint."""
    layout = LayoutHint(table=candidate.table)
    planned = PlannedRollup(
        candidate=candidate,
        layout=layout,
        score=candidate.score,
        est_bytes_saved=candidate.est_bytes,
        auto_build=auto_build,
        reason="test",
    )
    return OptimizerPlan(rollups=[planned], threshold=DEFAULT_BUILD_THRESHOLD)


# ---------------------------------------------------------------------------
# 1. build() materializes rollups
# ---------------------------------------------------------------------------


class TestOptimizerBuild:
    def test_build_returns_built_rollup_list(self, source_db: str) -> None:
        """build() returns one BuiltRollup per auto-build candidate."""
        reg = RollupRegistry()
        opt = _make_optimizer(reg)
        candidate = _make_candidate()
        plan_obj = _make_plan_with_candidate(candidate, auto_build=True)

        result = opt.build(plan_obj, source_database=source_db)

        assert len(result) == 1
        rollup = result[0]
        # The BuiltRollup carries the expected metadata.
        assert rollup.source_table == "orders"
        assert "region" in rollup.dimensions
        assert any("sum" in m.lower() for m in rollup.measures)

    def test_build_registers_rollup_in_registry(self, source_db: str) -> None:
        """After build(), the rollup appears in the registry for routing."""
        reg = RollupRegistry()
        opt = _make_optimizer(reg)
        candidate = _make_candidate()
        plan_obj = _make_plan_with_candidate(candidate, auto_build=True)

        opt.build(plan_obj, source_database=source_db)

        rollups = reg.candidates_for_table("orders")
        assert len(rollups) == 1
        assert rollups[0].source_table == "orders"

    def test_build_query_routes_after_build(self, source_db: str) -> None:
        """A query matching the built rollup is transparently routed to it."""
        reg = RollupRegistry()
        opt = _make_optimizer(reg)
        candidate = _make_candidate()
        plan_obj = _make_plan_with_candidate(candidate, auto_build=True)

        opt.build(plan_obj, source_database=source_db, org_id=_TEST_ORG)

        # The same query pattern that was materialized should now route.
        p = plan("SELECT region, SUM(amount) FROM orders GROUP BY region")
        result = route_to_rollup_shape(p, reg, org_id=_TEST_ORG)

        assert result.routed is True, f"Expected routed=True, got: {result.reason}"
        assert "rollup_orders" in result.plan.sql.lower()

    def test_build_routed_result_is_correct(self, source_db: str) -> None:
        """The rollup-routed query produces the same aggregate as the raw table."""
        reg = RollupRegistry()
        opt = _make_optimizer(reg)
        candidate = _make_candidate()
        plan_obj = _make_plan_with_candidate(candidate, auto_build=True)

        built_list = opt.build(plan_obj, source_database=source_db)
        built = built_list[0]

        # Re-aggregate from rollup.
        roll_conn = duckdb.connect(built.database, read_only=True)
        rolled = roll_conn.execute(
            f'SELECT region, SUM("sum_amount") AS total '
            f'FROM "{built.table}" GROUP BY region ORDER BY region'
        ).fetchall()
        roll_conn.close()

        # Compare to raw source.
        raw_conn = duckdb.connect(source_db, read_only=True)
        expected = raw_conn.execute(
            "SELECT region, SUM(amount) AS total FROM orders GROUP BY region ORDER BY region"
        ).fetchall()
        raw_conn.close()

        assert rolled == expected, f"Rollup re-aggregation mismatch: {rolled} != {expected}"

    def test_build_skips_non_auto_build_candidates(self, source_db: str) -> None:
        """Candidates with auto_build=False are NOT materialized."""
        reg = RollupRegistry()
        opt = _make_optimizer(reg)
        candidate = _make_candidate()
        plan_obj = _make_plan_with_candidate(candidate, auto_build=False)

        result = opt.build(plan_obj, source_database=source_db)

        assert result == []
        assert reg.all_rollups() == []

    def test_build_empty_plan_returns_empty(self, source_db: str) -> None:
        """An empty plan (no rollups) returns an empty list without error."""
        reg = RollupRegistry()
        opt = _make_optimizer(reg)
        empty_plan = OptimizerPlan(rollups=[], threshold=DEFAULT_BUILD_THRESHOLD)

        result = opt.build(empty_plan, source_database=source_db)

        assert result == []
        assert reg.all_rollups() == []

    def test_build_with_rls_keys(self, source_db: str) -> None:
        """build() passes rls_keys_by_table through to build_rollup."""
        reg = RollupRegistry()
        opt = _make_optimizer(reg)
        candidate = _make_candidate()
        plan_obj = _make_plan_with_candidate(candidate, auto_build=True)

        result = opt.build(
            plan_obj,
            source_database=source_db,
            rls_keys_by_table={"orders": ["tenant_id"]},
        )

        assert len(result) == 1
        rollup = result[0]
        # RLS key is preserved in the materialized rollup.
        assert "tenant_id" in rollup.rls_keys
        # Confirm the key is physically present in the rollup table.
        conn = duckdb.connect(rollup.database, read_only=True)
        cols = [c[0] for c in conn.execute(
            f'SELECT * FROM "{rollup.table}" LIMIT 0'
        ).description]
        conn.close()
        assert "tenant_id" in cols

    def test_build_org_id_tagged(self, source_db: str) -> None:
        """Rollups built via build() carry the org_id for isolation."""
        reg = RollupRegistry()
        opt = _make_optimizer(reg)
        candidate = _make_candidate()
        plan_obj = _make_plan_with_candidate(candidate, auto_build=True)

        result = opt.build(plan_obj, source_database=source_db, org_id="org-test")

        assert len(result) == 1
        assert result[0].org_id == "org-test"

        # The rollup is visible to org-test but not to another org.
        assert len(reg.candidates_for_table("orders", org_id="org-test")) == 1
        assert len(reg.candidates_for_table("orders", org_id="other-org")) == 0

    def test_build_multiple_candidates(self, source_db: str) -> None:
        """build() handles a plan with multiple auto-build candidates."""
        reg = RollupRegistry()
        opt = _make_optimizer(reg)

        cand1 = RollupCandidate(
            table="orders",
            dimensions=["region"],
            measures=["sum(amount)"],
            score=200,
            sample_count=4,
            est_bytes=50,
            cluster_key="orders|region",
        )
        cand2 = RollupCandidate(
            table="orders",
            dimensions=["tenant_id"],
            measures=["count(*)"],
            score=100,
            sample_count=3,
            est_bytes=33,
            cluster_key="orders|tenant_id",
        )

        layout = LayoutHint(table="orders")
        plan_obj = OptimizerPlan(
            rollups=[
                PlannedRollup(candidate=cand1, layout=layout, score=200, auto_build=True, reason=""),
                PlannedRollup(candidate=cand2, layout=layout, score=100, auto_build=True, reason=""),
            ],
            threshold=1,
        )

        result = opt.build(plan_obj, source_database=source_db)

        assert len(result) == 2
        assert len(reg.all_rollups()) == 2


# ---------------------------------------------------------------------------
# 2. maintain() refreshes existing rollups
# ---------------------------------------------------------------------------


class TestOptimizerMaintain:
    def test_maintain_empty_registry_returns_zero_counts(self, source_db: str) -> None:
        """With no registered rollups, maintain() returns all-zero counts."""
        reg = RollupRegistry()
        opt = _make_optimizer(reg)

        result = opt.maintain(source_database=source_db)

        assert result["refreshed"] == 0
        assert result["failed"] == 0
        assert result["skipped"] == 0
        assert result["errors"] == []

    def test_maintain_refreshes_existing_rollup(self, source_db: str) -> None:
        """maintain() rebuilds a previously built rollup in place."""
        reg = RollupRegistry()
        opt = _make_optimizer(reg)
        candidate = _make_candidate()
        plan_obj = _make_plan_with_candidate(candidate, auto_build=True)

        # Build the rollup first.
        built_list = opt.build(plan_obj, source_database=source_db)
        assert len(built_list) == 1
        original_rollup_id = built_list[0].rollup_id

        # Now maintain (refresh).
        result = opt.maintain(source_database=source_db)

        assert result["refreshed"] == 1
        assert result["failed"] == 0
        assert result["errors"] == []

        # The rollup is still in the registry under the same id.
        refreshed_rollup = reg.get_rollup(original_rollup_id)
        assert refreshed_rollup is not None
        assert refreshed_rollup.rollup_id == original_rollup_id
        assert refreshed_rollup.source_table == "orders"

    def test_maintain_query_still_routes_after_refresh(self, source_db: str) -> None:
        """After maintain(), queries still route to the refreshed rollup."""
        reg = RollupRegistry()
        opt = _make_optimizer(reg)
        candidate = _make_candidate()
        plan_obj = _make_plan_with_candidate(candidate, auto_build=True)

        opt.build(plan_obj, source_database=source_db, org_id=_TEST_ORG)
        opt.maintain(source_database=source_db)

        p = plan("SELECT region, SUM(amount) FROM orders GROUP BY region")
        result = route_to_rollup_shape(p, reg, org_id=_TEST_ORG)
        assert result.routed is True, f"Expected routed=True after maintain, got: {result.reason}"
        assert "rollup_orders" in result.plan.sql.lower()

    def test_maintain_result_correct_after_refresh(self, source_db: str) -> None:
        """The refreshed rollup produces the same correct aggregate as the raw table."""
        reg = RollupRegistry()
        opt = _make_optimizer(reg)
        candidate = _make_candidate()
        plan_obj = _make_plan_with_candidate(candidate, auto_build=True)

        built_list = opt.build(plan_obj, source_database=source_db)
        opt.maintain(source_database=source_db)

        rollup = reg.get_rollup(built_list[0].rollup_id)
        assert rollup is not None

        roll_conn = duckdb.connect(rollup.database, read_only=True)
        rolled = roll_conn.execute(
            f'SELECT region, SUM("sum_amount") AS total '
            f'FROM "{rollup.table}" GROUP BY region ORDER BY region'
        ).fetchall()
        roll_conn.close()

        raw_conn = duckdb.connect(source_db, read_only=True)
        expected = raw_conn.execute(
            "SELECT region, SUM(amount) AS total FROM orders GROUP BY region ORDER BY region"
        ).fetchall()
        raw_conn.close()

        assert rolled == expected, f"Post-maintain rollup mismatch: {rolled} != {expected}"

    def test_maintain_skips_rollup_without_source_database(self) -> None:
        """A rollup with no database path is skipped (not failed)."""
        from app.connectors.preagg import BuiltRollup  # noqa: PLC0415

        reg = RollupRegistry()
        # Manually insert a rollup with no database path.
        rollup = BuiltRollup(
            rollup_id="test-no-db",
            table="rollup_orders",
            source_table="orders",
            dimensions=["region"],
            measures=["sum(amount)"],
            database=None,  # no path
        )
        reg.add_rollup(rollup)

        opt = _make_optimizer(reg)
        result = opt.maintain(source_database=None)  # also no override

        assert result["skipped"] == 1
        assert result["refreshed"] == 0
        assert result["failed"] == 0

    def test_maintain_multiple_rollups_all_refreshed(self, source_db: str) -> None:
        """maintain() refreshes all registered rollups, not just the first."""
        reg = RollupRegistry()
        opt = _make_optimizer(reg)

        cand1 = RollupCandidate(
            table="orders",
            dimensions=["region"],
            measures=["sum(amount)"],
            score=200,
            sample_count=4,
            est_bytes=50,
            cluster_key="orders|region",
        )
        cand2 = RollupCandidate(
            table="orders",
            dimensions=["tenant_id"],
            measures=["count(*)"],
            score=100,
            sample_count=3,
            est_bytes=33,
            cluster_key="orders|tenant_id",
        )
        layout = LayoutHint(table="orders")
        plan_obj = OptimizerPlan(
            rollups=[
                PlannedRollup(candidate=cand1, layout=layout, score=200, auto_build=True, reason=""),
                PlannedRollup(candidate=cand2, layout=layout, score=100, auto_build=True, reason=""),
            ],
            threshold=1,
        )

        opt.build(plan_obj, source_database=source_db)
        assert len(reg.all_rollups()) == 2

        result = opt.maintain(source_database=source_db)

        assert result["refreshed"] == 2
        assert result["failed"] == 0
        assert result["errors"] == []


# ---------------------------------------------------------------------------
# 3. Observe → decide → build → route end-to-end pipeline
# ---------------------------------------------------------------------------


class TestOptimizerPipeline:
    def test_observe_decide_build_pipeline(self, source_db: str) -> None:
        """Full pipeline: observe log → decide → build → query routes to rollup."""
        reg = RollupRegistry()
        opt = Optimizer(registry=reg, build_threshold=1)

        # Seed query log with a high-frequency pattern.
        log = QueryLog()
        sql = "SELECT region, SUM(amount) FROM orders GROUP BY region"
        for _ in range(5):
            log.record(sql, cache_key="k", byte_size=1000)

        # Observe → candidates.
        candidates = opt.observe(log, min_hits=3)
        assert len(candidates) == 1

        # Decide → plan.
        plan_obj = opt.decide(candidates)
        assert len(plan_obj.to_build) == 1

        # Build → materialize.
        built_list = opt.build(plan_obj, source_database=source_db, org_id=_TEST_ORG)
        assert len(built_list) == 1

        # Routing: query matches the built rollup.
        p = plan(sql)
        result = route_to_rollup_shape(p, reg, org_id=_TEST_ORG)
        assert result.routed is True, f"Pipeline: expected routed=True, got: {result.reason}"
        assert "rollup_orders" in result.plan.sql.lower()

    def test_observe_decide_build_maintain_pipeline(self, source_db: str) -> None:
        """Full lifecycle including maintain keeps the rollup working and correct."""
        reg = RollupRegistry()
        opt = Optimizer(registry=reg, build_threshold=1)

        log = QueryLog()
        sql = "SELECT region, SUM(amount) FROM orders GROUP BY region"
        for _ in range(5):
            log.record(sql, cache_key="k", byte_size=1000)

        candidates = opt.observe(log, min_hits=3)
        plan_obj = opt.decide(candidates)
        built_list = opt.build(plan_obj, source_database=source_db, org_id=_TEST_ORG)
        assert len(built_list) == 1

        # Maintain: full rebuild.
        maintain_result = opt.maintain(source_database=source_db)
        assert maintain_result["refreshed"] == 1
        assert maintain_result["failed"] == 0

        # Query still routes.
        p = plan(sql)
        result = route_to_rollup_shape(p, reg, org_id=_TEST_ORG)
        assert result.routed is True, f"After maintain: expected routed=True, got: {result.reason}"
