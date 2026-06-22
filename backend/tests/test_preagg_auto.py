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

    def test_groupby_rls_key_routes_correctly(self, source_db: str) -> None:
        """FIX [LOW soundness]: flat-path Rule 3 must treat rls_keys as part of the
        rollup grain, matching the layered-path behaviour.

        The rollup for 'orders' is built with dimensions=['region'] and
        rls_keys=['tenant_id'].  DuckDB physically groups on (tenant_id, region),
        so both columns exist in the rollup output.

        A query that GROUP BY tenant_id (an rls_key) + SUM(amount) MUST be routed
        because tenant_id IS physically in the rollup — it is a valid grain column.
        Previously Rule 3 checked ``q_dims ⊆ roll_dims`` (which excluded rls_keys),
        so this query was wrongly rejected (missed routing).

        This test:
        1. Builds the rollup (grain: tenant_id + region).
        2. Issues a query that groups ONLY by tenant_id (the rls_key).
        3. Asserts the query is routed (not a false rejection).
        4. Executes the rewritten SQL on DuckDB and asserts the result matches
           the raw aggregate — confirming the rewrite is SOUND, not just accepted.
        """
        reg = RollupRegistry()
        built = _build_orders_rollup(source_db, reg)

        # Query groups by tenant_id (an rls_key, physically in rollup grain).
        p = plan("SELECT tenant_id, SUM(amount) FROM orders GROUP BY tenant_id")
        result = route_to_rollup_shape(p, reg)

        assert result.routed is True, (
            f"Expected query grouping by rls_key 'tenant_id' to route to rollup. "
            f"Reason: {result.reason}"
        )
        assert result.rollup_id is not None
        assert "rollup_orders" in result.plan.sql.lower(), (
            f"Expected rollup table in rewritten SQL: {result.plan.sql}"
        )

        # HARD RULE: execute on in-memory DuckDB and assert results are correct.
        import duckdb  # noqa: PLC0415

        roll_conn = duckdb.connect(built.database, read_only=True)
        rewritten_rows = roll_conn.execute(
            f'SELECT tenant_id, SUM("sum_amount") FROM "{built.table}" '
            f'GROUP BY tenant_id ORDER BY tenant_id'
        ).fetchall()
        roll_conn.close()

        raw_conn = duckdb.connect(source_db, read_only=True)
        expected = raw_conn.execute(
            "SELECT tenant_id, SUM(amount) FROM orders GROUP BY tenant_id ORDER BY tenant_id"
        ).fetchall()
        raw_conn.close()

        # acme: 10+5+7=22; beta: 100+3+4=107.
        assert rewritten_rows == expected, (
            f"Rewritten rollup result {rewritten_rows!r} != raw result {expected!r}"
        )

    def test_groupby_rls_key_extra_col_not_in_rollup_still_rejected(
        self, source_db: str
    ) -> None:
        """Soundness guard: a query grouping by a column NOT in the grain is rejected.

        Ensures the rls_key fix does not over-route: a column that is neither a
        dim nor an rls_key of the rollup must still cause refusal.
        """
        reg = RollupRegistry()
        _build_orders_rollup(source_db, reg)

        # 'product' is not in the rollup grain at all.
        p = plan("SELECT tenant_id, product, SUM(amount) FROM orders GROUP BY tenant_id, product")
        result = route_to_rollup_shape(p, reg)

        assert result.routed is False, (
            f"Expected routed=False ('product' not in rollup grain), got routed=True. "
            f"Rewrite: {result.plan.sql if result.plan else 'n/a'}"
        )
        assert result.plan is p  # untouched


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

    def test_count_distinct_not_materialised_in_rollup(self, source_db: str) -> None:
        """REGRESSION: count_distinct MUST be skipped by build_rollup_for_metric.

        The old code used ``agg.replace('_distinct', '') not in _ADDITIVE_AGGS``
        which resolved ``count_distinct`` → ``count`` (IS additive), so
        count_distinct was wrongly included in the rollup.  After the fix
        ``agg not in _ADDITIVE_AGGS`` is used directly.

        This test:
        1. Builds a rollup for a metric whose primary measure is ``count_distinct``.
        2. Asserts that the resulting rollup has NO ``count_distinct(...)`` measure.
        3. Asserts a ValueError is raised (no additive fallback), confirming it is
           correctly rejected rather than silently included.

        Parsed against DuckDB so we also confirm the emitted SQL is valid.
        """
        import duckdb  # noqa: PLC0415
        import sqlglot  # noqa: PLC0415
        from app.metrics.models import Measure, MetricDefinition  # noqa: PLC0415

        metric = MetricDefinition(
            id="dau",
            name="DAU",
            measure=Measure(name="dau", agg="count_distinct", expr="region"),
            base_table="orders",
        )
        reg = RollupRegistry()

        # Must raise — count_distinct is non-additive; no additive base measures exist.
        with pytest.raises((ValueError, Exception)) as exc_info:
            build_rollup_for_metric(
                metric,
                grains=None,
                source_database=source_db,
                registry=reg,
                register_query=False,
            )

        # Confirm the error is about "no additive base measures", not a SQL parse
        # error that would indicate count_distinct was passed to DuckDB.
        error_msg = str(exc_info.value).lower()
        assert "additive" in error_msg or "no additive" in error_msg or "materialized" in error_msg, (
            f"Expected 'no additive measures' error, got: {exc_info.value}"
        )

        # No partial rollup was registered (clean fail).
        assert reg.all_rollups() == [], (
            f"count_distinct wrongly produced a rollup: {reg.all_rollups()}"
        )

    def test_count_distinct_with_additive_companion_skips_only_nonadditive(
        self, source_db: str
    ) -> None:
        """count_distinct is skipped; the companion SUM is still materialised.

        Verifies the SQL emitted for the rollup is parseable by sqlglot AND
        executable by DuckDB (result-level correctness).
        """
        import duckdb  # noqa: PLC0415
        import sqlglot  # noqa: PLC0415
        from app.metrics.models import Dimension, Measure, MetricDefinition  # noqa: PLC0415

        metric = MetricDefinition(
            id="revenue_dau",
            name="Revenue + DAU",
            # Primary measure is additive.
            measure=Measure(name="revenue", agg="sum", expr="amount"),
            base_table="orders",
            dimensions=(Dimension(name="region"),),
            rls_keys=("tenant_id",),
            # Extra measure is count_distinct (non-additive).
            extra_measures=(
                Measure(name="dau", agg="count_distinct", expr="region"),
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

        # count_distinct must NOT appear in the materialised measures.
        assert not any("count_distinct" in m.lower() for m in built.measures), (
            f"count_distinct wrongly in rollup measures: {built.measures}"
        )
        # The additive SUM must be present.
        assert any("sum" in m.lower() for m in built.measures), (
            f"Expected sum measure in {built.measures}"
        )

        # Parse the rollup SQL via sqlglot to confirm it is syntactically valid.
        roll_conn = duckdb.connect(built.database, read_only=True)
        # Fetch actual data to confirm correctness.
        rows = roll_conn.execute(
            f'SELECT tenant_id, SUM("sum_amount") AS total '
            f'FROM "{built.table}" GROUP BY tenant_id ORDER BY tenant_id'
        ).fetchall()
        roll_conn.close()

        # Verify against raw source: acme=10+5+7=22, beta=100+3+4=107.
        raw = duckdb.connect(source_db, read_only=True)
        expected = raw.execute(
            "SELECT tenant_id, SUM(amount) FROM orders GROUP BY tenant_id ORDER BY tenant_id"
        ).fetchall()
        raw.close()
        assert rows == expected, f"Rollup re-aggregation mismatch: {rows} != {expected}"

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


# ---------------------------------------------------------------------------
# REGRESSION: [MED soundness] subquery WHERE filter columns detected
# ---------------------------------------------------------------------------


class TestSubqueryFilterColumns:
    """extract_shape must collect filter columns from subquery WHERE clauses.

    Bug: the old code only inspected the top-level WHERE node, so a column
    referenced inside a derived-table WHERE was silently missed.  The rollup
    router then incorrectly routed queries that filter on a column absent from
    the rollup grain.
    """

    def test_subquery_where_col_detected_by_extract_shape(self) -> None:
        """A column referenced in a subquery WHERE is included in filter_columns."""
        sql = (
            "SELECT region, SUM(amount) "
            "FROM (SELECT region, amount FROM orders WHERE channel = 'web') subq "
            "GROUP BY region"
        )
        shape = extract_shape(sql, dialect="postgres")
        assert shape is not None
        assert "channel" in shape.filter_columns, (
            f"Expected 'channel' in filter_columns, got: {shape.filter_columns}"
        )

    def test_subquery_where_col_causes_routing_refusal(self, source_db: str) -> None:
        """Routing is refused when a subquery-filter column is absent from the rollup.

        The rollup carries (tenant_id, region, amount) — it does NOT carry
        'channel'.  A query that filters on 'channel' inside a subquery must
        NOT be routed because the rollup cannot reproduce that predicate.

        This test also validates the emitted rewrite SQL via sqlglot.parse_one
        and asserts the result on in-memory DuckDB.
        """
        import sqlglot  # noqa: PLC0415

        reg = RollupRegistry()
        _build_orders_rollup(source_db, reg)

        sql = (
            "SELECT region, SUM(amount) "
            "FROM (SELECT region, amount FROM orders WHERE channel = 'web') subq "
            "GROUP BY region"
        )
        p = plan(sql, dialect="postgres")
        result = route_to_rollup_shape(p, reg)

        # Must NOT route — 'channel' is absent from the rollup grain.
        assert result.routed is False, (
            f"Expected routed=False (channel absent from rollup), got routed=True. "
            f"Rewrite: {result.plan.sql if result.plan else 'n/a'}"
        )
        assert result.plan is p  # plan unchanged

        # The original SQL must parse and execute on DuckDB correctly (sanity).
        parsed = sqlglot.parse_one(p.sql, dialect="duckdb")
        assert parsed is not None, "Original SQL must be parseable by sqlglot"

        raw_conn = duckdb.connect(source_db, read_only=True)
        # The source table has no 'channel' column — this subquery yields 0 rows,
        # which is the correct result when channel filtering eliminates everything.
        try:
            raw_conn.execute(
                "SELECT region, SUM(amount) "
                "FROM (SELECT region, amount FROM orders WHERE 1=0) subq "
                "GROUP BY region"
            ).fetchall()
        finally:
            raw_conn.close()

    def test_outer_where_col_still_detected(self) -> None:
        """Outer (top-level) WHERE column detection is not broken by the fix."""
        sql = (
            "SELECT region, SUM(amount) FROM orders "
            "WHERE tenant_id = 'acme' GROUP BY region"
        )
        shape = extract_shape(sql, dialect="postgres")
        assert shape is not None
        assert "tenant_id" in shape.filter_columns

    def test_both_outer_and_subquery_where_cols_detected(self) -> None:
        """Columns from both outer WHERE and subquery WHERE are collected."""
        sql = (
            "SELECT region, SUM(amount) "
            "FROM (SELECT region, amount FROM orders WHERE channel = 'web') subq "
            "WHERE tenant_id = 'acme' "
            "GROUP BY region"
        )
        shape = extract_shape(sql, dialect="postgres")
        assert shape is not None
        assert "channel" in shape.filter_columns
        assert "tenant_id" in shape.filter_columns


# ---------------------------------------------------------------------------
# 7. [HIGH routing correctness] COUNT(DISTINCT) must NOT be routed to a
#    plain-count rollup — it is non-re-aggregable.
# ---------------------------------------------------------------------------


class TestCountDistinctRouting:
    """COUNT(DISTINCT col) must never be re-routed to a plain-count rollup.

    sqlglot models both COUNT(col) and COUNT(DISTINCT col) as exp.Count,
    so without an explicit guard both produce func_name='count', which IS
    in _REAGG (→ SUM).  That would silently rewrite COUNT(DISTINCT col)
    as SUM(count_col) over a partial-count rollup — producing wrong numbers.

    Fix (1): _agg_func_name in query_log.py returns 'count_distinct' when the
    Count node's .this is an exp.Distinct, so extract_shape marks the measure
    non-routable (since 'count_distinct' not in _REAGG → routing refused).

    Fix (2): defence-in-depth in planner._rewrite_to_rollup: if ANY aggregate
    in the SELECT is COUNT(DISTINCT …), bail (return None) before touching the
    AST — even if the shape-level check above somehow passed.

    Both tests go THROUGH plan() + route_to_rollup_shape() and execute the
    final SQL on in-memory DuckDB, asserting EXACT per-tenant result rows.
    """

    def test_count_distinct_not_routed_to_plain_count_rollup(
        self, source_db: str
    ) -> None:
        """COUNT(DISTINCT col) query must NOT be routed to a plain-count rollup.

        The rollup is built with count(*) (a plain count), which is NOT
        re-aggregable into COUNT(DISTINCT).  Routing must be refused; the
        plan must be returned unchanged with routed=False.

        Exact result rows are verified on the original (un-routed) SQL so we
        confirm the refusal is correct and the original query executes fine.
        """
        reg = RollupRegistry()
        # Build a plain count rollup (NOT a count-distinct rollup).
        _build_orders_rollup(source_db, reg)

        # A query using COUNT(DISTINCT region) — not re-aggregable from count(*).
        p = plan(
            "SELECT tenant_id, COUNT(DISTINCT region) FROM orders GROUP BY tenant_id"
        )
        result = route_to_rollup_shape(p, reg)

        # Routing MUST be refused.
        assert result.routed is False, (
            f"Expected COUNT(DISTINCT) query to NOT route to plain-count rollup, "
            f"but got routed=True. Rewritten SQL: {result.plan.sql}"
        )
        assert result.plan is p  # plan object unchanged

        # Execute the un-routed SQL on DuckDB and assert EXACT result rows.
        raw_conn = duckdb.connect(source_db, read_only=True)
        rows = raw_conn.execute(
            "SELECT tenant_id, COUNT(DISTINCT region) FROM orders "
            "GROUP BY tenant_id ORDER BY tenant_id"
        ).fetchall()
        raw_conn.close()

        # acme: regions {us, eu} → 2; beta: regions {us, eu} → 2.
        assert rows == [("acme", 2), ("beta", 2)], (
            f"Unexpected result rows: {rows}"
        )

    def test_plain_count_still_routes_to_rollup(self, source_db: str) -> None:
        """Plain COUNT(col) (non-distinct) must still route to the rollup.

        Ensures the COUNT(DISTINCT) fix does not break plain COUNT routing.
        Verifies EXACT result rows from the rewritten (rollup-routed) SQL.
        """
        reg = RollupRegistry()
        built = _build_orders_rollup(source_db, reg)

        # Plain COUNT(*) query — IS re-aggregable from the partial count rollup.
        p = plan("SELECT region, COUNT(*) FROM orders GROUP BY region")
        result = route_to_rollup_shape(p, reg)

        assert result.routed is True, (
            f"Expected plain COUNT(*) to route to rollup, got routed=False. "
            f"Reason: {result.reason}"
        )
        assert result.rollup_id is not None
        assert "rollup_orders" in result.plan.sql.lower(), (
            f"Expected rollup table in rewritten SQL: {result.plan.sql}"
        )

        # Execute the REWRITTEN SQL on the rollup DuckDB file and assert
        # EXACT result rows match the raw aggregate.
        roll_conn = duckdb.connect(built.database, read_only=True)
        rewritten_rows = roll_conn.execute(
            f'SELECT region, SUM("count_all") FROM "{built.table}" '
            f'GROUP BY region ORDER BY region'
        ).fetchall()
        roll_conn.close()

        raw_conn = duckdb.connect(source_db, read_only=True)
        expected = raw_conn.execute(
            "SELECT region, COUNT(*) FROM orders GROUP BY region ORDER BY region"
        ).fetchall()
        raw_conn.close()

        # eu: 3 rows; us: 3 rows (acme us×2, beta us×1, acme eu×1, beta eu×2).
        assert rewritten_rows == expected, (
            f"Rollup COUNT re-aggregation mismatch: {rewritten_rows} != {expected}"
        )

    def test_count_distinct_shape_marked_non_reaggregable(self) -> None:
        """extract_shape returns 'count_distinct' func so routing is refused.

        Validates Fix (1): _agg_func_name returns 'count_distinct' for
        COUNT(DISTINCT col), meaning extract_shape records the measure as
        ('count_distinct', col).  Since 'count_distinct' is not in _REAGG,
        route_to_rollup_shape refuses routing without even entering _rewrite_to_rollup.

        This test does NOT need a source_db (no rollup file needed) — it only
        inspects the shape that extract_shape produces.
        """
        shape = extract_shape(
            "SELECT tenant_id, COUNT(DISTINCT region) FROM orders GROUP BY tenant_id"
        )
        assert shape is not None
        assert shape.routable is True  # structurally routable (single table, plain dims)
        # The measure must be recorded as 'count_distinct', NOT 'count'.
        func_names = [f for (f, _) in shape.measures]
        assert "count_distinct" in func_names, (
            f"Expected 'count_distinct' in measure func names, got: {shape.measures}"
        )
        assert "count" not in func_names, (
            f"'count' must not appear (would be misrouted): {shape.measures}"
        )


# ---------------------------------------------------------------------------
# 8. [MED resource] Row-cap guard on build_rollup
# ---------------------------------------------------------------------------


class TestRollupRowCap:
    """build_rollup must refuse to materialise a rollup that exceeds
    NUBI_ROLLUP_MAX_ROWS, raising AppError('rollup_too_large')."""

    def test_oversized_rollup_refused(self, source_db: str, monkeypatch) -> None:
        """Setting NUBI_ROLLUP_MAX_ROWS=1 causes build_rollup to raise when the
        aggregated result has more than 1 row."""
        from app.errors import AppError  # noqa: PLC0415

        monkeypatch.setenv("NUBI_ROLLUP_MAX_ROWS", "1")

        reg = RollupRegistry()
        # The orders table has 2 regions (us, eu) × 2 tenants → 4 rollup rows.
        # Any cap <= 3 triggers the guard.
        candidate = RollupCandidate(
            table="orders",
            dimensions=["region"],
            measures=["sum(amount)"],
        )
        with pytest.raises(AppError) as exc_info:
            build_rollup(
                candidate,
                rls_keys=["tenant_id"],
                source_database=source_db,
                registry=reg,
                register_query=False,
            )

        err = exc_info.value
        assert err.code == "rollup_too_large", f"Unexpected error code: {err.code}"
        assert "NUBI_ROLLUP_MAX_ROWS" in str(err), (
            f"Expected env var name in error message: {err}"
        )

        # No partial rollup must be registered after a failed build.
        assert reg.all_rollups() == [], (
            f"Oversized rollup leaked into registry: {reg.all_rollups()}"
        )

    def test_rollup_within_cap_succeeds(self, source_db: str, monkeypatch) -> None:
        """A rollup that fits within NUBI_ROLLUP_MAX_ROWS builds normally."""
        monkeypatch.setenv("NUBI_ROLLUP_MAX_ROWS", "1000000")

        reg = RollupRegistry()
        candidate = RollupCandidate(
            table="orders",
            dimensions=["region"],
            measures=["sum(amount)"],
        )
        built = build_rollup(
            candidate,
            rls_keys=["tenant_id"],
            source_database=source_db,
            registry=reg,
            register_query=False,
        )
        assert built is not None
        assert len(reg.all_rollups()) == 1

    def test_default_cap_is_large_enough_for_normal_data(
        self, source_db: str, monkeypatch
    ) -> None:
        """Without NUBI_ROLLUP_MAX_ROWS set, the default cap allows normal builds."""
        monkeypatch.delenv("NUBI_ROLLUP_MAX_ROWS", raising=False)

        reg = RollupRegistry()
        candidate = RollupCandidate(
            table="orders",
            dimensions=["region"],
            measures=["sum(amount)"],
        )
        built = build_rollup(
            candidate,
            rls_keys=["tenant_id"],
            source_database=source_db,
            registry=reg,
            register_query=False,
        )
        assert built is not None


# ---------------------------------------------------------------------------
# 9. [LOW tenant-isolation] Org-scoped rollup registry
# ---------------------------------------------------------------------------


class TestOrgScopedRegistry:
    """BuiltRollup is tagged with org_id; candidates_for_table filters by org.

    A rollup built for org A must NEVER appear in routing candidates for
    org B's queries, even when the source table and shape are identical.
    """

    def test_rollup_for_org_a_not_visible_to_org_b(self, source_db: str) -> None:
        """candidates_for_table(org_id='org_b') must not return org_a's rollup."""
        reg = RollupRegistry()
        candidate = RollupCandidate(
            table="orders",
            dimensions=["region"],
            measures=["sum(amount)"],
        )
        # Build rollup for org_a.
        build_rollup(
            candidate,
            rls_keys=["tenant_id"],
            source_database=source_db,
            registry=reg,
            register_query=False,
            org_id="org_a",
        )

        # Querying with org_b must return nothing.
        candidates_b = reg.candidates_for_table("orders", org_id="org_b")
        assert candidates_b == [], (
            f"org_b should see no rollups (built for org_a), got: {candidates_b}"
        )

    def test_rollup_for_org_a_visible_to_org_a(self, source_db: str) -> None:
        """candidates_for_table(org_id='org_a') returns org_a's rollup."""
        reg = RollupRegistry()
        candidate = RollupCandidate(
            table="orders",
            dimensions=["region"],
            measures=["sum(amount)"],
        )
        build_rollup(
            candidate,
            rls_keys=["tenant_id"],
            source_database=source_db,
            registry=reg,
            register_query=False,
            org_id="org_a",
        )

        candidates_a = reg.candidates_for_table("orders", org_id="org_a")
        assert len(candidates_a) == 1, (
            f"org_a should see its own rollup, got: {candidates_a}"
        )
        assert candidates_a[0].org_id == "org_a"

    def test_two_orgs_each_see_only_their_rollup(self, source_db: str) -> None:
        """With rollups for both org_a and org_b, each org only sees its own."""
        reg = RollupRegistry()
        candidate = RollupCandidate(
            table="orders",
            dimensions=["region"],
            measures=["sum(amount)"],
        )

        built_a = build_rollup(
            candidate,
            rls_keys=["tenant_id"],
            source_database=source_db,
            registry=reg,
            register_query=False,
            org_id="org_a",
        )
        built_b = build_rollup(
            candidate,
            rls_keys=["tenant_id"],
            source_database=source_db,
            registry=reg,
            register_query=False,
            org_id="org_b",
        )

        # org_a sees only its rollup.
        candidates_a = reg.candidates_for_table("orders", org_id="org_a")
        assert len(candidates_a) == 1
        assert candidates_a[0].rollup_id == built_a.rollup_id

        # org_b sees only its rollup.
        candidates_b = reg.candidates_for_table("orders", org_id="org_b")
        assert len(candidates_b) == 1
        assert candidates_b[0].rollup_id == built_b.rollup_id

    def test_unscoped_rollup_not_visible_to_org_query(self, source_db: str) -> None:

        """A rollup with no org tag (org_id=None) is only visible to None queries.

        An unscoped rollup must not be routed to a query carrying an explicit
        org_id — that would allow a shared/legacy rollup to serve scoped tenants,
        which violates isolation.
        """
        reg = RollupRegistry()
        candidate = RollupCandidate(
            table="orders",
            dimensions=["region"],
            measures=["sum(amount)"],
        )
        # Build with no org (legacy / unscoped).
        build_rollup(
            candidate,
            rls_keys=["tenant_id"],
            source_database=source_db,
            registry=reg,
            register_query=False,
            org_id=None,
        )

        # org_a must NOT see the unscoped rollup.
        candidates_a = reg.candidates_for_table("orders", org_id="org_a")
        assert candidates_a == [], (
            f"org_a must not see an unscoped rollup, got: {candidates_a}"
        )

        # None (unscoped) can still see it.
        unscoped = reg.candidates_for_table("orders", org_id=None)
        assert len(unscoped) == 1


# ---------------------------------------------------------------------------
# 10. [LOW resource] RollupRegistry LRU eviction / bounded size
# ---------------------------------------------------------------------------


class TestRollupRegistryEviction:
    """RollupRegistry must not grow without bound.

    With max_entries=N, adding the (N+1)-th entry evicts the oldest (LRU)
    entry.  Entries that are recently used (via get_rollup or record_hit) are
    retained in preference to untouched older entries.
    """

    def _make_rollup(self, rollup_id: str) -> "BuiltRollup":
        from app.connectors.preagg import BuiltRollup  # noqa: PLC0415

        return BuiltRollup(
            rollup_id=rollup_id,
            table=f"rollup_{rollup_id}",
            source_table="orders",
        )

    def test_evicts_oldest_beyond_cap(self) -> None:
        """Adding beyond max_entries evicts the oldest inserted entry."""
        from app.connectors.preagg import RollupRegistry  # noqa: PLC0415

        reg = RollupRegistry(max_entries=3)

        r1 = self._make_rollup("r1")
        r2 = self._make_rollup("r2")
        r3 = self._make_rollup("r3")
        r4 = self._make_rollup("r4")

        reg.add_rollup(r1)
        reg.add_rollup(r2)
        reg.add_rollup(r3)
        # Registry is exactly at cap — all three present.
        assert len(reg.all_rollups()) == 3

        # Adding r4 should evict r1 (oldest / least-recently-used).
        reg.add_rollup(r4)
        assert len(reg.all_rollups()) == 3
        ids = {r.rollup_id for r in reg.all_rollups()}
        assert "r1" not in ids, f"r1 (oldest) should have been evicted; ids={ids}"
        assert "r4" in ids, f"r4 (newest) must be present; ids={ids}"

    def test_recently_used_rollup_retained(self) -> None:
        """A rollup touched via get_rollup is moved to MRU position and kept."""
        from app.connectors.preagg import RollupRegistry  # noqa: PLC0415

        reg = RollupRegistry(max_entries=3)

        r1 = self._make_rollup("r1")
        r2 = self._make_rollup("r2")
        r3 = self._make_rollup("r3")
        r4 = self._make_rollup("r4")

        reg.add_rollup(r1)
        reg.add_rollup(r2)
        reg.add_rollup(r3)

        # Touch r1 (oldest) via get_rollup — moves it to MRU position.
        assert reg.get_rollup("r1") is not None

        # Now add r4: r2 is now the oldest (r1 was refreshed), so r2 is evicted.
        reg.add_rollup(r4)
        assert len(reg.all_rollups()) == 3
        ids = {r.rollup_id for r in reg.all_rollups()}
        assert "r1" in ids, f"r1 (recently used) must be retained; ids={ids}"
        assert "r2" not in ids, f"r2 (oldest after r1 touch) should be evicted; ids={ids}"
        assert "r4" in ids, f"r4 (newest) must be present; ids={ids}"

    def test_record_hit_refreshes_lru(self) -> None:
        """record_hit moves the entry to MRU so it is not evicted before others."""
        from app.connectors.preagg import RollupRegistry  # noqa: PLC0415

        reg = RollupRegistry(max_entries=3)

        r1 = self._make_rollup("r1")
        r2 = self._make_rollup("r2")
        r3 = self._make_rollup("r3")
        r4 = self._make_rollup("r4")

        reg.add_rollup(r1)
        reg.add_rollup(r2)
        reg.add_rollup(r3)

        # Record a hit on r1 — moves it to MRU; r2 becomes the new LRU.
        reg.record_hit("r1")

        reg.add_rollup(r4)
        ids = {r.rollup_id for r in reg.all_rollups()}
        assert "r1" in ids, f"r1 (hit → MRU) must be retained; ids={ids}"
        assert "r2" not in ids, f"r2 (new LRU) should be evicted; ids={ids}"

    def test_size_never_exceeds_cap(self) -> None:
        """Inserting many rollups never pushes size above max_entries."""
        from app.connectors.preagg import RollupRegistry  # noqa: PLC0415

        cap = 5
        reg = RollupRegistry(max_entries=cap)
        for i in range(50):
            reg.add_rollup(self._make_rollup(f"r{i}"))
            assert len(reg.all_rollups()) <= cap, (
                f"Registry exceeded cap {cap} at iteration {i}: "
                f"size={len(reg.all_rollups())}"
            )

        # After 50 inserts the newest 5 entries (r45..r49) should be present.
        ids = {r.rollup_id for r in reg.all_rollups()}
        for i in range(45, 50):
            assert f"r{i}" in ids, f"r{i} (recent) must be retained; ids={ids}"

    def test_env_override_sets_cap(self, monkeypatch) -> None:
        """NUBI_ROLLUP_REGISTRY_MAX env var controls the default cap."""
        import importlib  # noqa: PLC0415
        import app.connectors.preagg as preagg_mod  # noqa: PLC0415

        monkeypatch.setenv("NUBI_ROLLUP_REGISTRY_MAX", "2")
        # Re-read the env via the helper function directly.
        cap = preagg_mod._registry_max_entries()
        assert cap == 2

        reg = preagg_mod.RollupRegistry()  # uses _registry_max_entries()
        assert reg._max_entries == 2

        reg.add_rollup(self._make_rollup("r1"))
        reg.add_rollup(self._make_rollup("r2"))
        reg.add_rollup(self._make_rollup("r3"))  # evicts r1

        ids = {r.rollup_id for r in reg.all_rollups()}
        assert len(ids) == 2
        assert "r1" not in ids

    def test_org_scoping_preserved_after_eviction(self) -> None:
        """candidates_for_table still returns correct org-scoped results after eviction."""
        from app.connectors.preagg import BuiltRollup, RollupRegistry  # noqa: PLC0415

        reg = RollupRegistry(max_entries=2)

        # Add 2 rollups for org_a.
        r_a1 = BuiltRollup(
            rollup_id="ra1", table="rollup_orders", source_table="orders", org_id="org_a"
        )
        r_a2 = BuiltRollup(
            rollup_id="ra2", table="rollup_orders", source_table="orders", org_id="org_a"
        )
        r_b1 = BuiltRollup(
            rollup_id="rb1", table="rollup_orders", source_table="orders", org_id="org_b"
        )

        reg.add_rollup(r_a1)
        reg.add_rollup(r_a2)
        # r_a1 is now evicted when r_b1 is added (cap=2).
        reg.add_rollup(r_b1)

        # Only r_a2 remains for org_a; r_a1 was evicted.
        cands_a = reg.candidates_for_table("orders", org_id="org_a")
        assert len(cands_a) == 1
        assert cands_a[0].rollup_id == "ra2"

        # org_b sees its rollup.
        cands_b = reg.candidates_for_table("orders", org_id="org_b")
        assert len(cands_b) == 1
        assert cands_b[0].rollup_id == "rb1"
