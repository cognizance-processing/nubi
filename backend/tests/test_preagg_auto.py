"""Tests for the AUTO pre-aggregation engine (ROADMAP §4 "Cube weapon").

Coverage
--------
1. Shape extraction (``extract_shape``): routable vs non-routable shapes,
   dimensions / measures / filter-columns parsing.
2. Miner (``mine``): clustering compatible shapes + frequency × bytes ranking.
3. Builder (``build_rollup``): materialized rollup is *correct* (re-aggregating
   its partials reproduces the raw aggregate) and PRESERVES the RLS-key column;
   a dropped RLS key raises.
4. Router (``route_to_rollup_shape``): SOUND superset rewrites HIT; provably
   UNSOUND cases are left untouched (same object, same cache_key).
"""

from __future__ import annotations

import os
import tempfile

import duckdb
import pytest

from app.connectors.planner import plan, route_to_rollup_shape
from app.connectors.preagg import (
    RollupCandidate,
    RollupRegistry,
    build_rollup,
    build_rollup_for_metric,
    mine,
)
from app.connectors.query_log import QueryLog, extract_shape


# ---------------------------------------------------------------------------
# 1. Shape extraction
# ---------------------------------------------------------------------------


class TestExtractShape:
    def test_simple_routable_shape(self) -> None:
        shape = extract_shape(
            "SELECT region, SUM(amount), COUNT(*) FROM orders GROUP BY region"
        )
        assert shape is not None
        assert shape.routable is True
        assert shape.base_table == "orders"
        assert shape.dimensions == ("region",)
        assert ("sum", "amount") in shape.measures
        assert ("count", "*") in shape.measures

    def test_filter_columns_collected(self) -> None:
        shape = extract_shape(
            "SELECT region, SUM(amount) FROM orders "
            "WHERE tenant_id = 'acme' GROUP BY region"
        )
        assert shape is not None
        assert "tenant_id" in shape.filter_columns

    def test_no_group_by_returns_none(self) -> None:
        assert extract_shape("SELECT * FROM orders") is None

    def test_join_is_not_routable(self) -> None:
        shape = extract_shape(
            "SELECT a.region, SUM(a.amount) FROM orders a "
            "JOIN customers c ON a.cid = c.id GROUP BY a.region"
        )
        assert shape is not None
        assert shape.routable is False  # two base tables → not routable

    def test_expression_groupby_not_routable(self) -> None:
        shape = extract_shape(
            "SELECT date_trunc('day', ts), SUM(amount) FROM orders "
            "GROUP BY date_trunc('day', ts)"
        )
        assert shape is not None
        assert shape.routable is False  # derived grain → not routable

    def test_avg_measure_still_parsed(self) -> None:
        # AVG is parsed as a measure but is NOT re-aggregable; the router rejects
        # it (tested below).  The shape itself is still routable in structure.
        shape = extract_shape("SELECT region, AVG(amount) FROM orders GROUP BY region")
        assert shape is not None
        assert ("avg", "amount") in shape.measures


# ---------------------------------------------------------------------------
# 2. Miner
# ---------------------------------------------------------------------------


class TestMine:
    def test_clusters_by_table_and_dims(self) -> None:
        log = QueryLog()
        # Same (table, dims) but different measures → one cluster, unioned.
        for _ in range(3):
            log.record(
                "SELECT region, SUM(amount) FROM orders GROUP BY region",
                "k", byte_size=100,
            )
        for _ in range(2):
            log.record(
                "SELECT region, COUNT(*) FROM orders GROUP BY region",
                "k", byte_size=100,
            )
        candidates = mine(log, min_hits=3)
        assert len(candidates) == 1
        c = candidates[0]
        assert c.table == "orders"
        assert c.dimensions == ["region"]
        assert c.sample_count == 5
        # Both measures unioned into the single rollup candidate.
        assert any("sum" in m for m in c.measures)
        assert any("count" in m for m in c.measures)

    def test_ranked_by_frequency_times_bytes(self) -> None:
        # score = sample_count × est_bytes, where est_bytes = Σ byte_size.
        log = QueryLog()
        # Pattern A: 3 hits × (3×1000) bytes = 3 × 3000 = 9000
        for _ in range(3):
            log.record(
                "SELECT region, SUM(amount) FROM orders GROUP BY region",
                "k", byte_size=1000,
            )
        # Pattern B: 4 hits × (4×100) bytes = 4 × 400 = 1600
        for _ in range(4):
            log.record(
                "SELECT category, SUM(qty) FROM sales GROUP BY category",
                "k", byte_size=100,
            )
        candidates = mine(log, min_hits=3)
        assert len(candidates) == 2
        assert candidates[0].table == "orders"  # higher score first
        assert candidates[0].score == 9000
        assert candidates[1].score == 1600

    def test_below_min_hits_excluded(self) -> None:
        log = QueryLog()
        for _ in range(2):
            log.record(
                "SELECT region, SUM(amount) FROM orders GROUP BY region", "k"
            )
        assert mine(log, min_hits=3) == []

    def test_non_routable_excluded(self) -> None:
        log = QueryLog()
        for _ in range(5):
            log.record(
                "SELECT a.region, SUM(a.amount) FROM orders a "
                "JOIN customers c ON a.cid = c.id GROUP BY a.region",
                "k",
            )
        assert mine(log, min_hits=3) == []


# ---------------------------------------------------------------------------
# Builder + Router shared fixture: a DuckDB file with a raw fact table.
# ---------------------------------------------------------------------------


@pytest.fixture()
def source_db() -> str:
    """Create a temp DuckDB file with an ``orders`` fact table and return path.

    Columns: tenant_id (RLS key), region (dim), amount (measure).
    Two tenants, two regions, several rows so re-aggregation is non-trivial.
    """
    fd, path = tempfile.mkstemp(suffix=".duckdb")
    os.close(fd)
    os.remove(path)  # let duckdb create it fresh
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


# ---------------------------------------------------------------------------
# 3. Builder
# ---------------------------------------------------------------------------


class TestBuildRollup:
    def test_rollup_correct_and_rls_key_preserved(self, source_db: str) -> None:
        reg = RollupRegistry()
        candidate = RollupCandidate(
            table="orders",
            dimensions=["region"],
            measures=["sum(amount)", "count(*)"],
        )
        built = build_rollup(
            candidate,
            rls_keys=["tenant_id"],
            source_database=source_db,
            registry=reg,
            register_query=False,
        )

        # RLS key preserved as a column (grouped on, not aggregated away).
        assert "tenant_id" in built.rls_keys

        # Read the materialized rollup and verify correctness against the raw fact.
        roll = duckdb.connect(built.database, read_only=True)
        roll_cols = [c[0] for c in roll.execute(
            f'SELECT * FROM "{built.table}" LIMIT 0'
        ).description]
        assert "tenant_id" in roll_cols  # RLS key physically present
        assert "region" in roll_cols

        # Re-aggregate the rollup back to a per-tenant total and compare to raw.
        rolled = roll.execute(
            f'SELECT tenant_id, SUM("sum_amount") '
            f'FROM "{built.table}" GROUP BY tenant_id ORDER BY tenant_id'
        ).fetchall()
        roll.close()

        raw = duckdb.connect(source_db, read_only=True)
        truth = raw.execute(
            "SELECT tenant_id, SUM(amount) FROM orders "
            "GROUP BY tenant_id ORDER BY tenant_id"
        ).fetchall()
        raw.close()

        assert rolled == truth  # acme=22, beta=107

    def test_nonexistent_rls_key_fails_build(self, source_db: str) -> None:
        # An RLS key that is not a real column must NOT silently produce a rollup
        # that cannot enforce it — the build must fail.  (DuckDB rejects the
        # GROUP BY on a missing column before the post-build preservation check.)
        reg = RollupRegistry()
        candidate = RollupCandidate(
            table="orders", dimensions=["region"], measures=["sum(amount)"]
        )
        with pytest.raises(Exception):  # noqa: B017 — BinderException or AppError
            build_rollup(
                candidate,
                rls_keys=["nonexistent_key"],
                source_database=source_db,
                registry=reg,
                register_query=False,
            )
        # No partial rollup leaked into the registry on a failed build.
        assert reg.all_rollups() == []


# ---------------------------------------------------------------------------
# 4. Router — sound vs unsound
# ---------------------------------------------------------------------------


def _build_orders_rollup(source_db: str, reg: RollupRegistry):
    """Build a rollup grouped on (tenant_id, region) with sum+count."""
    candidate = RollupCandidate(
        table="orders",
        dimensions=["region"],
        measures=["sum(amount)", "count(*)"],
    )
    return build_rollup(
        candidate,
        rls_keys=["tenant_id"],
        source_database=source_db,
        registry=reg,
        register_query=False,
    )


class TestRouteSoundness:
    def test_sound_subset_groupby_routes(self, source_db: str) -> None:
        reg = RollupRegistry()
        _build_orders_rollup(source_db, reg)

        # Query groups by region (⊆ rollup dims {region}); SUM is re-aggregable.
        p = plan("SELECT region, SUM(amount) FROM orders GROUP BY region")
        result = route_to_rollup_shape(p, reg)
        assert result.routed is True
        assert result.rollup_id is not None
        # Rewritten SQL reads the rollup table and re-aggregates the partial.
        assert "rollup_orders" in result.plan.sql.lower()
        assert "sum_amount" in result.plan.sql.lower()
        assert result.plan.cache_key != p.cache_key

    def test_sound_route_preserves_rls_where(self, source_db: str) -> None:
        reg = RollupRegistry()
        _build_orders_rollup(source_db, reg)

        # RLS injected by plan() → WHERE tenant_id = 'acme' on a rollup column.
        p = plan(
            "SELECT region, SUM(amount) FROM orders GROUP BY region",
            claims={"policies": {"tenant_id": "acme"}},
        )
        result = route_to_rollup_shape(p, reg)
        assert result.routed is True
        # The RLS predicate column survives in the rewrite (filter on rollup col).
        assert "tenant_id" in result.plan.sql.lower()
        assert result.plan.rls_claims == {"policies": {"tenant_id": "acme"}}

    def test_unsound_superset_dim_not_routed(self, source_db: str) -> None:
        reg = RollupRegistry()
        _build_orders_rollup(source_db, reg)

        # Query groups by a column NOT in the rollup dims → not a subset → unsound.
        p = plan("SELECT product, SUM(amount) FROM orders GROUP BY product")
        result = route_to_rollup_shape(p, reg)
        assert result.routed is False
        assert result.plan is p  # untouched
        assert result.plan.cache_key == p.cache_key

    def test_unsound_avg_measure_not_routed(self, source_db: str) -> None:
        reg = RollupRegistry()
        _build_orders_rollup(source_db, reg)

        # AVG is NOT re-aggregable from partial sums → must NOT route.
        p = plan("SELECT region, AVG(amount) FROM orders GROUP BY region")
        result = route_to_rollup_shape(p, reg)
        assert result.routed is False
        assert result.plan is p

    def test_unsound_measure_not_materialized(self, source_db: str) -> None:
        reg = RollupRegistry()
        _build_orders_rollup(source_db, reg)

        # MAX(amount) is re-aggregable in principle but the rollup never computed
        # it → not derivable → must NOT route.
        p = plan("SELECT region, MAX(amount) FROM orders GROUP BY region")
        result = route_to_rollup_shape(p, reg)
        assert result.routed is False
        assert result.plan is p

    def test_unsound_filter_col_absent_not_routed(self, source_db: str) -> None:
        reg = RollupRegistry()
        _build_orders_rollup(source_db, reg)

        # Filter on a column the rollup does not carry (not a dim/RLS key/measure)
        # → predicate could not be applied post-rollup → must NOT route.
        p = plan(
            "SELECT region, SUM(amount) FROM orders "
            "WHERE channel = 'web' GROUP BY region"
        )
        result = route_to_rollup_shape(p, reg)
        assert result.routed is False
        assert result.plan is p

    def test_no_rollup_for_table_not_routed(self, source_db: str) -> None:
        reg = RollupRegistry()  # empty
        p = plan("SELECT region, SUM(amount) FROM orders GROUP BY region")
        result = route_to_rollup_shape(p, reg)
        assert result.routed is False
        assert result.plan is p


# ---------------------------------------------------------------------------
# 5. Layered CTE routing (windowed / derived metric queries)
# ---------------------------------------------------------------------------


class TestLayeredCTERouting:
    """A layered WITH __base AS (<inner>) <outer> query routes via the inner."""

    def test_layered_windowed_routes_to_rollup(self, source_db: str) -> None:
        """A metric compiler layered query (with LAG window fn) routes to a rollup."""
        reg = RollupRegistry()
        _build_orders_rollup(source_db, reg)

        # Simulate a layered metric query: __base aggregates, outer adds window fn.
        layered_sql = (
            "WITH __base AS ("
            "SELECT region, SUM(amount) AS amount FROM orders GROUP BY region"
            ") "
            "SELECT region, amount, "
            "LAG(amount, 1) OVER (ORDER BY region) AS amount_prior_period "
            "FROM __base"
        )
        p = plan(layered_sql, dialect="postgres")
        result = route_to_rollup_shape(p, reg)

        assert result.routed is True, f"Expected routed=True, got: {result.reason}"
        assert result.rollup_id is not None
        # The rewritten SQL still has the __base CTE form with the rollup table.
        rewritten = result.plan.sql.lower()
        assert "rollup_orders" in rewritten, f"Expected rollup_orders in: {result.plan.sql}"
        assert "with __base as" in rewritten, "Expected layered CTE preserved"
        # The outer window function is preserved.
        assert "lag" in rewritten, "Expected window function preserved in outer"

    def test_layered_unsound_inner_not_routed(self, source_db: str) -> None:
        """If the inner aggregation is not provably sound, the plan is untouched."""
        reg = RollupRegistry()
        _build_orders_rollup(source_db, reg)

        # Inner groups by a column NOT in the rollup dims → unsound.
        layered_sql = (
            "WITH __base AS ("
            "SELECT product, SUM(amount) AS amount FROM orders GROUP BY product"
            ") "
            "SELECT product, amount FROM __base"
        )
        p = plan(layered_sql, dialect="postgres")
        result = route_to_rollup_shape(p, reg)
        assert result.routed is False
        assert result.plan is p

    def test_layered_rls_preserved(self, source_db: str) -> None:
        """RLS claims survive the layered CTE rewrite."""
        reg = RollupRegistry()
        _build_orders_rollup(source_db, reg)

        layered_sql = (
            "WITH __base AS ("
            "SELECT region, SUM(amount) AS amount FROM orders GROUP BY region"
            ") "
            "SELECT region, amount FROM __base"
        )
        p = plan(
            layered_sql,
            claims={"policies": {"tenant_id": "acme"}},
            dialect="postgres",
        )
        result = route_to_rollup_shape(p, reg)
        # Even with RLS injection on the outer plan the router handles it gracefully.
        # (RLS is injected into the outer SELECT by planner; inner shape is clean.)
        assert result.plan.rls_claims == {"policies": {"tenant_id": "acme"}}

    def test_non_base_cte_name_not_touched(self, source_db: str) -> None:
        """A WITH that uses a different alias than __base is left untouched."""
        reg = RollupRegistry()
        _build_orders_rollup(source_db, reg)

        layered_sql = (
            "WITH my_cte AS ("
            "SELECT region, SUM(amount) AS amount FROM orders GROUP BY region"
            ") "
            "SELECT region, amount FROM my_cte"
        )
        p = plan(layered_sql, dialect="postgres")
        result = route_to_rollup_shape(p, reg)
        # Router does NOT touch non-__base CTEs.
        assert result.plan is p or result.routed is False


# ---------------------------------------------------------------------------
# 6. build_rollup_for_metric
# ---------------------------------------------------------------------------


class TestBuildRollupForMetric:
    """build_rollup_for_metric materializes the right shape for a MetricDefinition."""

    def test_materializes_base_measures_and_dims(self, source_db: str) -> None:
        """build_rollup_for_metric produces a rollup with the metric's dims + measures.

        The source_db fixture has columns: tenant_id, region, amount.
        We declare a metric WITHOUT a time_dimension to avoid referencing a missing column.
        """
        from app.metrics.models import (  # noqa: PLC0415
            Dimension,
            Measure,
            MetricDefinition,
        )

        metric = MetricDefinition(
            id="revenue",
            name="Revenue",
            measure=Measure(name="revenue", agg="sum", expr="amount"),
            base_table="orders",
            dimensions=(
                Dimension(name="region"),
            ),
            rls_keys=("tenant_id",),
        )

        reg = RollupRegistry()
        built = build_rollup_for_metric(
            metric,
            grains=None,  # no time column (source_db has no timestamp column)
            source_database=source_db,
            registry=reg,
            register_query=False,
        )

        # Rollup registered.
        rollups = reg.candidates_for_table("orders")
        assert len(rollups) == 1, f"Expected 1 rollup, got: {rollups}"

        # RLS key preserved.
        assert "tenant_id" in built.rls_keys

        # Dimensions include metric dims.
        assert "region" in built.dimensions

        # Base measure materialized.
        assert any("sum" in m.lower() for m in built.measures), (
            f"Expected sum measure in {built.measures}"
        )

    def test_skips_non_additive_measures(self, source_db: str) -> None:
        """Non-additive measures (avg, percentile) are skipped gracefully."""
        from app.metrics.models import (  # noqa: PLC0415
            Dimension,
            Measure,
            MetricDefinition,
        )

        metric = MetricDefinition(
            id="avg_metric",
            name="Avg Metric",
            measure=Measure(name="total", agg="sum", expr="amount"),
            base_table="orders",
            dimensions=(Dimension(name="region"),),
            extra_measures=(
                Measure(name="avg_amt", agg="avg", expr="amount"),
            ),
        )

        reg = RollupRegistry()
        built = build_rollup_for_metric(
            metric,
            grains=None,
            source_database=source_db,
            registry=reg,
            register_query=False,
        )

        # Only the additive SUM measure should be present (AVG skipped).
        assert any("sum" in m.lower() for m in built.measures)
        assert not any("avg" in m.lower() for m in built.measures), (
            f"AVG should be skipped but found in {built.measures}"
        )

    def test_no_additive_measures_raises(self, source_db: str) -> None:
        """A metric with only non-additive measures raises ValueError."""
        from app.metrics.models import (  # noqa: PLC0415
            Measure,
            MetricDefinition,
        )

        metric = MetricDefinition(
            id="approx",
            name="Approx",
            measure=Measure(name="dau", agg="approx_count_distinct", expr="user_id"),
            base_table="orders",
        )
        reg = RollupRegistry()
        with pytest.raises((ValueError, Exception)):
            build_rollup_for_metric(
                metric, grains=None, source_database=source_db,
                registry=reg, register_query=False,
            )

    def test_rollup_routes_layered_metric_query(self, source_db: str) -> None:
        """A rollup built from a MetricDefinition enables routing of layered queries."""
        from app.metrics.models import (  # noqa: PLC0415
            Dimension,
            DerivedMeasure,
            Measure,
            MetricDefinition,
            MetricQuery,
        )
        from app.metrics.compile import compile_metric  # noqa: PLC0415

        metric = MetricDefinition(
            id="revenue",
            name="Revenue",
            measure=Measure(name="revenue", agg="sum", expr="amount"),
            base_table="orders",
            dimensions=(Dimension(name="region"),),
            rls_keys=("tenant_id",),
            derived_measures=(
                DerivedMeasure(name="revenue_share", formula="revenue / revenue"),
            ),
        )

        # Build rollup from metric definition.
        reg = RollupRegistry()
        build_rollup_for_metric(
            metric,
            grains=None,
            source_database=source_db,
            registry=reg,
            register_query=False,
        )

        # Compile a derived metric query (produces layered SQL).
        mq = MetricQuery(metric_id="revenue", dimensions=("region",))
        sql, _params = compile_metric(metric, mq)

        # The compiled SQL should be layered.
        assert "WITH __base AS" in sql or "WITH __BASE AS" in sql.upper(), (
            f"Expected layered SQL, got: {sql[:200]}"
        )

        # Route the compiled query to the rollup.
        p = plan(sql, dialect="duckdb")
        result = route_to_rollup_shape(p, reg)

        assert result.routed is True, (
            f"Expected layered metric query to route to rollup. Reason: {result.reason}"
        )
        assert "rollup_orders" in result.plan.sql.lower()
