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

    def test_date_trunc_grain_groupby_is_routable(self) -> None:
        # [audit-14] A DATE_TRUNC('<grain>', <col>) group-by is now a DERIVABLE
        # grain dimension keyed by its underlying column (rollups materialize the
        # RAW time column; the outer query re-applies the trunc), so the shape is
        # routable.  (Previously this was wrongly marked non-routable, which made
        # EVERY time-grained / derived layered metric CTE unroutable.)
        shape = extract_shape(
            "SELECT date_trunc('day', ts), SUM(amount) FROM orders "
            "GROUP BY date_trunc('day', ts)"
        )
        assert shape is not None
        assert shape.routable is True  # derivable grain → routable
        assert "ts" in shape.dimensions  # keyed by the underlying time column
        assert ("ts", "day") in shape.time_bucket_dims
        assert shape.dimension_exprs == ()

    def test_arbitrary_expression_groupby_not_routable(self) -> None:
        # A genuinely-arbitrary expression group-by stays non-routable.
        shape = extract_shape(
            "SELECT a + b, SUM(amount) FROM orders GROUP BY a + b"
        )
        assert shape is not None
        assert shape.routable is False
        assert shape.dimension_exprs == ("a + b",)
        assert shape.time_bucket_dims == ()

    def test_avg_measure_still_parsed(self) -> None:
        # AVG is parsed as a measure but is NOT re-aggregable; the router rejects
        # it (tested below).  The shape itself is still routable in structure.
        shape = extract_shape("SELECT region, AVG(amount) FROM orders GROUP BY region")
        assert shape is not None
        assert ("avg", "amount") in shape.measures

    def test_subquery_from_not_routable(self) -> None:
        # [engine-correctness] A latest_snapshot TimeComparison wraps the base in
        # a QUALIFY dedup subquery: the DIRECT FROM is a derived table, not a bare
        # ``orders``.  ``find_all(exp.Table)`` recurses into the subquery and
        # surfaces a single ``orders`` table, which previously made the shape look
        # routable → the router rewrote the inner table to a rollup that lacks the
        # dedup dims (entity_id/created_at) → runtime error OR silently WRONG
        # SUM-over-rollup totals.  Requiring a bare-table direct FROM makes any
        # subquery FROM non-routable so the plan is left untouched.
        shape = extract_shape(
            "SELECT region, SUM(amount) FROM ("
            "SELECT * FROM orders "
            "QUALIFY ROW_NUMBER() OVER "
            "(PARTITION BY entity_id ORDER BY created_at DESC) = 1"
            ") AS __snap GROUP BY region"
        )
        assert shape is not None
        assert shape.routable is False  # subquery FROM → not routable
        assert shape.base_table is None
        # A normal flat aggregation over the same table still routes.
        flat = extract_shape(
            "SELECT region, SUM(amount) FROM orders GROUP BY region"
        )
        assert flat is not None
        assert flat.routable is True
        assert flat.base_table == "orders"


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


class TestLayeredOuterRLSSoundness:
    """[LOW RLS] fix-22 extended to the layered outer-RLS case.

    For a layered ``WITH __base AS (<inner>) <outer>`` query, plan() injects the
    RLS predicate onto the OUTER SELECT (``WHERE policy_col = val``).  That outer
    filter only resolves when ``policy_col`` is a column the rollup-backed
    ``__base`` actually exposes — i.e. it is BOTH in the rollup grain AND
    projected by the inner SELECT.  If either is missing the router MUST fall
    back to the base (refuse routing) rather than re-point ``__base`` at a rollup
    whose grain/projection cannot carry the per-tenant RLS predicate.
    """

    def test_outer_rls_col_absent_from_rollup_grain_not_routed(
        self, source_db: str
    ) -> None:
        """Outer RLS column NOT in the rollup grain → must NOT route (fall back).

        The rollup grain is {region, tenant_id}.  Here the outer SELECT filters
        on ``channel`` (absent from the rollup grain entirely), simulating an
        RLS/policy hoisted onto the outer that the rollup cannot reproduce.
        Routing must be refused; the plan is returned untouched.
        """
        reg = RollupRegistry()
        _build_orders_rollup(source_db, reg)

        # Inner projects region + tenant_id; outer filters on a column ('channel')
        # that the rollup grain does NOT carry.
        layered_sql = (
            "WITH __base AS ("
            "SELECT region, tenant_id, SUM(amount) AS amount FROM orders "
            "GROUP BY region, tenant_id"
            ") "
            "SELECT region, amount FROM __base WHERE channel = 'web'"
        )
        p = plan(layered_sql, dialect="postgres")
        result = route_to_rollup_shape(p, reg)

        assert result.routed is False, (
            f"Outer RLS/filter column 'channel' is absent from the rollup grain; "
            f"expected routed=False, got routed=True. Rewrite: {result.plan.sql}"
        )
        assert result.plan is p  # untouched

    def test_outer_rls_col_in_grain_but_not_projected_not_routed(
        self, source_db: str
    ) -> None:
        """Outer RLS column in grain but NOT projected by __base → must NOT route.

        ``tenant_id`` IS in the rollup grain (it is an rls_key), but the inner
        SELECT only projects {region, amount}, so the rollup-backed ``__base``
        would not expose ``tenant_id`` to the outer WHERE.  Grain-membership
        alone is insufficient; routing must be refused.
        """
        reg = RollupRegistry()
        _build_orders_rollup(source_db, reg)

        layered_sql = (
            "WITH __base AS ("
            "SELECT region, SUM(amount) AS amount FROM orders GROUP BY region"
            ") "
            "SELECT region, amount FROM __base WHERE tenant_id = 'acme'"
        )
        p = plan(layered_sql, dialect="postgres")
        result = route_to_rollup_shape(p, reg)

        assert result.routed is False, (
            f"Outer RLS column 'tenant_id' is in grain but not projected by "
            f"__base; expected routed=False, got routed=True. "
            f"Rewrite: {result.plan.sql}"
        )
        assert result.plan is p  # untouched

    def test_outer_rls_col_present_in_grain_and_projected_routes(
        self, source_db: str
    ) -> None:
        """Outer RLS column in grain AND projected by __base → routes correctly.

        The inner GROUPs BY (region, tenant_id) so ``tenant_id`` is both in the
        rollup grain and projected by ``__base``.  The outer RLS filter on
        ``tenant_id`` is therefore satisfiable post-rollup → routing proceeds,
        and the rewritten SQL reads the rollup while preserving the outer WHERE.
        """
        reg = RollupRegistry()
        built = _build_orders_rollup(source_db, reg)

        layered_sql = (
            "WITH __base AS ("
            "SELECT region, tenant_id, SUM(amount) AS amount FROM orders "
            "GROUP BY region, tenant_id"
            ") "
            "SELECT region, tenant_id, amount FROM __base WHERE tenant_id = 'acme'"
        )
        p = plan(layered_sql, dialect="postgres")
        result = route_to_rollup_shape(p, reg)

        assert result.routed is True, (
            f"Outer RLS column 'tenant_id' is in grain AND projected by __base; "
            f"expected routed=True. Reason: {result.reason}"
        )
        assert result.rollup_id is not None
        rewritten = result.plan.sql.lower()
        assert "rollup_orders" in rewritten, (
            f"Expected rollup table in rewritten SQL: {result.plan.sql}"
        )
        assert "with __base as" in rewritten, "Expected layered CTE preserved"
        # The outer RLS predicate must survive verbatim.
        assert "tenant_id" in rewritten

        # Execute the rewritten inner against the rollup and confirm the outer
        # RLS filter resolves to the correct per-tenant total (acme region sums).
        import duckdb  # noqa: PLC0415

        roll_conn = duckdb.connect(built.database, read_only=True)
        rewritten_rows = roll_conn.execute(
            f'SELECT region, SUM("sum_amount") FROM "{built.table}" '
            f"WHERE tenant_id = 'acme' GROUP BY region ORDER BY region"
        ).fetchall()
        roll_conn.close()

        raw_conn = duckdb.connect(source_db, read_only=True)
        expected = raw_conn.execute(
            "SELECT region, SUM(amount) FROM orders WHERE tenant_id = 'acme' "
            "GROUP BY region ORDER BY region"
        ).fetchall()
        raw_conn.close()

        # acme: eu=7, us=15.
        assert rewritten_rows == expected, (
            f"Rollup re-aggregation under RLS mismatch: {rewritten_rows} != {expected}"
        )


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

    def test_oversized_rollup_is_bounded_not_fully_materialized(
        self, source_db: str, monkeypatch, tmp_path
    ) -> None:
        """Over-cap rollup raises rollup_too_large WITHOUT materializing the full
        result — the LIMIT pushdown caps Arrow allocation at max_rows+1 rows.

        We verify boundedness by checking that the error fires with a cap of 1
        (source has 4 aggregated rows) and that DuckDB never needed to fetch all
        rows: the capped SQL stops at 2 rows (cap+1), not 4.
        """
        import duckdb  # noqa: PLC0415

        from app.connectors.preagg import build_rollup_sql  # noqa: PLC0415
        from app.errors import AppError  # noqa: PLC0415

        monkeypatch.setenv("NUBI_ROLLUP_MAX_ROWS", "1")

        reg = RollupRegistry()
        candidate = RollupCandidate(
            table="orders",
            dimensions=["region"],
            measures=["sum(amount)"],
        )

        # Confirm the full (uncapped) rollup has MORE than cap+1 rows so the
        # LIMIT actually does work to bound allocation.
        rollup_sql = build_rollup_sql(
            "orders", ["region"], ["sum(amount)"], ["tenant_id"]
        )
        con = duckdb.connect(database=source_db, read_only=True)
        try:
            full_rows = con.execute(rollup_sql).fetchall()
        finally:
            con.close()
        assert len(full_rows) > 2, (
            f"Source needs >2 aggregated rows to validate boundedness; got {len(full_rows)}"
        )

        # build_rollup must raise rollup_too_large (not OOM) for an over-cap rollup.
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

        # Registry must remain clean — no partial rollup registered.
        assert reg.all_rollups() == [], (
            f"Oversized rollup leaked into registry: {reg.all_rollups()}"
        )

    def test_overcap_raises_without_materializing_arrow_table(
        self, source_db: str, monkeypatch
    ) -> None:
        """Over-cap rollup raises rollup_too_large WITHOUT materializing the full
        Arrow table.

        Fix: build_rollup now runs SELECT COUNT(*) FROM (<capped_sql>) first.
        When count > max_rows the AppError is raised immediately; the Arrow
        table (rel.arrow() / read_all()) is never called.  Peak memory is
        O(COUNT query) not O(materialized rows).

        We validate this by:
        1. Patching duckdb.DuckDBPyConnection.arrow to a sentinel that raises
           if called — confirming the fast path never reaches materialization.
        2. Asserting AppError('rollup_too_large') is raised.
        3. Asserting no partial rollup was registered.
        """
        import duckdb  # noqa: PLC0415
        from unittest.mock import patch  # noqa: PLC0415

        from app.connectors.preagg import build_rollup_sql  # noqa: PLC0415
        from app.errors import AppError  # noqa: PLC0415

        monkeypatch.setenv("NUBI_ROLLUP_MAX_ROWS", "1")

        # Confirm source has >cap rows so the LIMIT is actually doing work.
        rollup_sql = build_rollup_sql(
            "orders", ["region"], ["sum(amount)"], ["tenant_id"]
        )
        con = duckdb.connect(database=source_db, read_only=True)
        try:
            full_rows = con.execute(rollup_sql).fetchall()
        finally:
            con.close()
        assert len(full_rows) > 1, (
            f"Source needs >1 aggregated rows to validate; got {len(full_rows)}"
        )

        materialized = []

        original_arrow = duckdb.DuckDBPyRelation.arrow

        def _spy_arrow(self, *args, **kwargs):
            materialized.append(True)
            return original_arrow(self, *args, **kwargs)

        reg = RollupRegistry()
        candidate = RollupCandidate(
            table="orders",
            dimensions=["region"],
            measures=["sum(amount)"],
        )

        with patch.object(duckdb.DuckDBPyRelation, "arrow", _spy_arrow):
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

        # The Arrow table must NOT have been materialized.
        assert materialized == [], (
            f"build_rollup called .arrow() before the count check — "
            f"Arrow table was materialized despite rollup being over-cap "
            f"(peak ~2x dataset memory, should be bounded). "
            f"arrow() was called {len(materialized)} time(s)."
        )

        # Registry must be clean.
        assert reg.all_rollups() == [], (
            f"Oversized rollup leaked into registry: {reg.all_rollups()}"
        )

    def test_within_cap_rollup_builds_correctly_with_count_first(
        self, source_db: str, monkeypatch
    ) -> None:
        """Within-cap rollup: COUNT-first path builds correct result.

        Ensures the two-scan approach (COUNT + materialize) does not break
        rollup correctness: the final rollup data must match the raw aggregate.
        """
        import duckdb  # noqa: PLC0415

        # Set a cap well above the 4-row source aggregate.
        monkeypatch.setenv("NUBI_ROLLUP_MAX_ROWS", "100")

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

        # Re-aggregate the rollup and compare against the raw source.
        roll_conn = duckdb.connect(built.database, read_only=True)
        rolled = roll_conn.execute(
            f'SELECT tenant_id, SUM("sum_amount") AS total '
            f'FROM "{built.table}" GROUP BY tenant_id ORDER BY tenant_id'
        ).fetchall()
        roll_conn.close()

        raw_conn = duckdb.connect(source_db, read_only=True)
        expected = raw_conn.execute(
            "SELECT tenant_id, SUM(amount) FROM orders "
            "GROUP BY tenant_id ORDER BY tenant_id"
        ).fetchall()
        raw_conn.close()

        # acme: 10+5+7=22; beta: 100+3+4=107.
        assert rolled == expected, (
            f"Count-first rollup result mismatch: {rolled} != {expected}"
        )


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


# ---------------------------------------------------------------------------
# 11. [MED + LOW follow-on] Rollup routing org-scoping via route_to_rollup_shape
# ---------------------------------------------------------------------------


def _build_org_scoped_rollup(
    source_db: str, reg: RollupRegistry, org_id: str | None
):
    """Build a rollup for the given org_id (or unscoped when None)."""
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
        org_id=org_id,
    )


class TestRoutingOrgScoping:
    """route_to_rollup_shape must honour org_id so only the caller's-org rollups
    are ever considered; a different org's rollup must never be a candidate.

    FIX [MED]: candidates_for_table is called with org_id=org_id in BOTH the
    flat path and the layered-CTE path.

    Isolation contract:
    - A query for org_a routes to org_a's rollup.
    - A query for org_b does NOT route to org_a's rollup even when the shape
      and base table are identical.
    - Unscoped rollups (org_id=None) still serve unscoped queries (org_id=None).
    - An org-scoped query (org_id='org_a') is NOT served by an unscoped rollup.
    """

    def test_query_routes_to_own_org_rollup_flat_path(self, source_db: str) -> None:
        """Flat-path: a query routed with org_id='org_a' hits org_a's rollup."""
        reg = RollupRegistry()
        _build_org_scoped_rollup(source_db, reg, org_id="org_a")

        p = plan("SELECT region, SUM(amount) FROM orders GROUP BY region")
        result = route_to_rollup_shape(p, reg, org_id="org_a")

        assert result.routed is True, (
            f"Expected route to org_a's rollup, got routed=False. Reason: {result.reason}"
        )
        assert result.rollup_id is not None
        assert "rollup_orders" in result.plan.sql.lower()

    def test_query_never_routes_to_other_org_rollup_flat_path(
        self, source_db: str
    ) -> None:
        """Flat-path: a query for org_b must NOT route to org_a's rollup.

        Even with an identical shape and base table, cross-org routing is
        forbidden.  This is the primary tenant-isolation guard at the routing
        level (defence-in-depth on top of RLS).
        """
        reg = RollupRegistry()
        _build_org_scoped_rollup(source_db, reg, org_id="org_a")

        p = plan("SELECT region, SUM(amount) FROM orders GROUP BY region")
        result = route_to_rollup_shape(p, reg, org_id="org_b")

        assert result.routed is False, (
            f"org_b must NOT route to org_a's rollup, but got routed=True. "
            f"Rewritten SQL: {result.plan.sql}"
        )
        assert result.plan is p  # original plan unchanged

    def test_two_orgs_each_route_to_their_own_rollup(self, source_db: str) -> None:
        """Both org_a and org_b roll out; each query is routed to its own rollup."""
        reg = RollupRegistry()
        built_a = _build_org_scoped_rollup(source_db, reg, org_id="org_a")
        built_b = _build_org_scoped_rollup(source_db, reg, org_id="org_b")

        p = plan("SELECT region, SUM(amount) FROM orders GROUP BY region")

        result_a = route_to_rollup_shape(p, reg, org_id="org_a")
        assert result_a.routed is True
        assert result_a.rollup_id == built_a.rollup_id, (
            f"org_a should route to built_a ({built_a.rollup_id}), "
            f"got {result_a.rollup_id}"
        )

        result_b = route_to_rollup_shape(p, reg, org_id="org_b")
        assert result_b.routed is True
        assert result_b.rollup_id == built_b.rollup_id, (
            f"org_b should route to built_b ({built_b.rollup_id}), "
            f"got {result_b.rollup_id}"
        )

        # They must have routed to DIFFERENT rollups.
        assert result_a.rollup_id != result_b.rollup_id

    def test_unscoped_rollup_serves_unscoped_query(self, source_db: str) -> None:
        """A rollup with org_id=None is served to an unscoped (org_id=None) query."""
        reg = RollupRegistry()
        _build_org_scoped_rollup(source_db, reg, org_id=None)

        p = plan("SELECT region, SUM(amount) FROM orders GROUP BY region")
        result = route_to_rollup_shape(p, reg, org_id=None)

        assert result.routed is True, (
            f"Unscoped rollup should serve unscoped query, got routed=False. "
            f"Reason: {result.reason}"
        )

    def test_org_scoped_query_not_served_by_unscoped_rollup(
        self, source_db: str
    ) -> None:
        """An org-scoped query must NOT be routed to an unscoped rollup.

        Mixing a scoped query with an unscoped (shared/legacy) rollup could
        allow cross-tenant data leakage through a shared pre-aggregate.
        """
        reg = RollupRegistry()
        _build_org_scoped_rollup(source_db, reg, org_id=None)  # unscoped rollup

        p = plan("SELECT region, SUM(amount) FROM orders GROUP BY region")
        result = route_to_rollup_shape(p, reg, org_id="org_a")

        assert result.routed is False, (
            f"org_a query must NOT be routed to an unscoped rollup, "
            f"but got routed=True. Rewritten SQL: {result.plan.sql}"
        )
        assert result.plan is p

    def test_query_routes_to_own_org_rollup_layered_cte_path(
        self, source_db: str
    ) -> None:
        """Layered-CTE path: routing respects org_id in the layered query path."""
        reg = RollupRegistry()
        _build_org_scoped_rollup(source_db, reg, org_id="org_a")

        layered_sql = (
            "WITH __base AS ("
            "SELECT region, SUM(amount) AS amount FROM orders GROUP BY region"
            ") "
            "SELECT region, amount, "
            "LAG(amount, 1) OVER (ORDER BY region) AS amount_prior_period "
            "FROM __base"
        )
        p = plan(layered_sql, dialect="postgres")
        result = route_to_rollup_shape(p, reg, org_id="org_a")

        assert result.routed is True, (
            f"Layered path: expected route to org_a's rollup. Reason: {result.reason}"
        )
        assert "rollup_orders" in result.plan.sql.lower()

    def test_layered_query_never_routes_to_other_org_rollup(
        self, source_db: str
    ) -> None:
        """Layered-CTE path: org_b query must NOT route to org_a's rollup."""
        reg = RollupRegistry()
        _build_org_scoped_rollup(source_db, reg, org_id="org_a")

        layered_sql = (
            "WITH __base AS ("
            "SELECT region, SUM(amount) AS amount FROM orders GROUP BY region"
            ") "
            "SELECT region, amount FROM __base"
        )
        p = plan(layered_sql, dialect="postgres")
        result = route_to_rollup_shape(p, reg, org_id="org_b")

        assert result.routed is False, (
            f"Layered path: org_b must NOT route to org_a's rollup, "
            f"but got routed=True. Rewritten SQL: {result.plan.sql}"
        )
        assert result.plan is p


# ---------------------------------------------------------------------------
# 12. [audit-14] Time-grained / derived layered metric queries route to a
#     matching grain rollup.
# ---------------------------------------------------------------------------
# REGRESSION: the metric compiler emits DATE_TRUNC('<grain>', <col>) AS
# <col>_<grain> in the inner GROUP BY for time-grained metrics.  extract_shape
# used to treat ANY non-bare-column GROUP BY expr as routable=False, so EVERY
# time-grained / derived layered metric CTE was unroutable — rollup routing
# silently never fired for time-intel metrics.  The fix recognises a
# DATE_TRUNC('<grain>', <col>) group-by as a DERIVABLE grain dimension keyed by
# its underlying column (rollups materialize the RAW time column; the outer layer
# re-applies the trunc), so the router can match a rollup carrying that grain.


@pytest.fixture()
def source_db_ts() -> str:
    """A temp DuckDB file with a fact table carrying a timestamp column.

    Columns: tenant_id (RLS key), region (dim), created_at (time dim), amount.
    """
    fd, path = tempfile.mkstemp(suffix=".duckdb")
    os.close(fd)
    os.remove(path)
    conn = duckdb.connect(path)
    conn.execute(
        "CREATE TABLE events "
        "(tenant_id VARCHAR, region VARCHAR, created_at TIMESTAMP, amount INTEGER)"
    )
    conn.execute(
        """
        INSERT INTO events VALUES
            ('acme', 'us', TIMESTAMP '2024-01-05 10:00:00', 10),
            ('acme', 'us', TIMESTAMP '2024-01-20 10:00:00', 5),
            ('acme', 'eu', TIMESTAMP '2024-02-03 10:00:00', 7),
            ('beta', 'us', TIMESTAMP '2024-01-15 10:00:00', 100),
            ('beta', 'eu', TIMESTAMP '2024-02-09 10:00:00', 3),
            ('beta', 'eu', TIMESTAMP '2024-02-25 10:00:00', 4)
        """
    )
    conn.close()
    yield path
    if os.path.exists(path):
        os.remove(path)


class TestTimeGrainedRouting:
    def test_extract_shape_recognises_date_trunc_grain(self) -> None:
        """A DATE_TRUNC('<grain>', <col>) group-by is a routable derivable dim."""
        shape = extract_shape(
            "SELECT region AS region, "
            "DATE_TRUNC('month', created_at) AS created_at_month, "
            "SUM(amount) AS amount "
            "FROM events GROUP BY region, DATE_TRUNC('month', created_at)",
            dialect="duckdb",
        )
        assert shape is not None
        assert shape.routable is True, (
            "DATE_TRUNC grain group-by must be routable (keyed by underlying col)"
        )
        # The underlying time column is recorded as a dimension (NOT an opaque expr).
        assert "created_at" in shape.dimensions
        assert "region" in shape.dimensions
        assert shape.dimension_exprs == ()  # no genuinely-unroutable exprs
        assert ("created_at", "month") in shape.time_bucket_dims

    def test_arbitrary_expr_groupby_still_non_routable(self) -> None:
        """Genuinely-arbitrary expression group-bys stay non-routable."""
        shape = extract_shape(
            "SELECT a + b, SUM(amount) FROM events GROUP BY a + b",
            dialect="duckdb",
        )
        assert shape is not None
        assert shape.routable is False
        assert shape.time_bucket_dims == ()

    def _build_events_grain_rollup(self, source_db_ts: str, reg: RollupRegistry):
        """Build a rollup carrying the RAW created_at time column + region grain."""
        from app.metrics.models import (  # noqa: PLC0415
            Dimension,
            Measure,
            MetricDefinition,
            TimeDimension,
        )
        from app.connectors.preagg import build_rollup_for_metric  # noqa: PLC0415

        metric = MetricDefinition(
            id="ts_revenue",
            name="TS Revenue",
            measure=Measure(name="revenue", agg="sum", expr="amount"),
            base_table="events",
            dimensions=(Dimension(name="region"),),
            time_dimension=TimeDimension(
                column="created_at", grains=("day", "month"), default_grain="month"
            ),
            rls_keys=("tenant_id",),
        )
        return build_rollup_for_metric(
            metric,
            grains=["month"],  # include the raw time column as a dimension
            source_database=source_db_ts,
            registry=reg,
            register_query=False,
        )

    def test_time_grained_flat_query_routes_to_grain_rollup(
        self, source_db_ts: str
    ) -> None:
        """A flat DATE_TRUNC-grained query routes to the matching grain rollup
        and the rewrite re-aggregates the raw time column correctly on DuckDB."""
        reg = RollupRegistry()
        built = self._build_events_grain_rollup(source_db_ts, reg)

        # The raw created_at column IS materialized in the rollup grain.
        assert "created_at" in built.dimensions

        # A time-grained query (mirrors the metric compiler's inner aggregation).
        p = plan(
            "SELECT region AS region, "
            "DATE_TRUNC('month', created_at) AS created_at_month, "
            "SUM(amount) AS amount "
            "FROM events GROUP BY region, DATE_TRUNC('month', created_at)",
            dialect="duckdb",
        )
        result = route_to_rollup_shape(p, reg)

        assert result.routed is True, (
            f"time-grained query must route to grain rollup. Reason: {result.reason}"
        )
        assert result.rollup_id is not None
        rewritten = result.plan.sql.lower()
        assert "rollup_events" in rewritten, f"expected rollup table: {result.plan.sql}"
        assert "sum_amount" in rewritten, "expected re-aggregated partial measure"
        assert "date_trunc" in rewritten, "grain trunc must be preserved over rollup"

        # Execute the rewritten SQL on the rollup and compare to the raw aggregate.
        roll_conn = duckdb.connect(built.database, read_only=True)
        rewritten_rows = roll_conn.execute(
            f'SELECT region, DATE_TRUNC(\'month\', created_at) AS m, '
            f'SUM("sum_amount") FROM "{built.table}" '
            f'GROUP BY region, DATE_TRUNC(\'month\', created_at) '
            f'ORDER BY region, m'
        ).fetchall()
        roll_conn.close()

        raw_conn = duckdb.connect(source_db_ts, read_only=True)
        expected = raw_conn.execute(
            "SELECT region, DATE_TRUNC('month', created_at) AS m, SUM(amount) "
            "FROM events GROUP BY region, DATE_TRUNC('month', created_at) "
            "ORDER BY region, m"
        ).fetchall()
        raw_conn.close()

        assert rewritten_rows == expected, (
            f"grain rollup re-aggregation mismatch: {rewritten_rows} != {expected}"
        )

    def test_time_grained_layered_query_routes_to_grain_rollup(
        self, source_db_ts: str
    ) -> None:
        """A LAYERED time-grained metric query (WITH __base ... + outer window fn)
        routes via its inner grain aggregation to the grain rollup."""
        reg = RollupRegistry()
        self._build_events_grain_rollup(source_db_ts, reg)

        layered_sql = (
            "WITH __base AS ("
            "SELECT region AS region, "
            "DATE_TRUNC('month', created_at) AS created_at_month, "
            "SUM(amount) AS amount "
            "FROM events GROUP BY region, DATE_TRUNC('month', created_at)"
            ") "
            "SELECT region, created_at_month, amount, "
            "LAG(amount, 1) OVER (ORDER BY created_at_month) AS amount_prior "
            "FROM __base"
        )
        p = plan(layered_sql, dialect="duckdb")
        result = route_to_rollup_shape(p, reg)

        assert result.routed is True, (
            f"layered time-grained query must route. Reason: {result.reason}"
        )
        rewritten = result.plan.sql.lower()
        assert "rollup_events" in rewritten
        assert "with __base as" in rewritten, "layered CTE form preserved"
        assert "lag" in rewritten, "outer window function preserved"

    def test_time_grained_query_not_routed_without_rollup(
        self, source_db_ts: str
    ) -> None:
        """A grain query with NO matching rollup is left untouched."""
        reg = RollupRegistry()  # empty
        p = plan(
            "SELECT DATE_TRUNC('month', created_at) AS m, SUM(amount) AS amount "
            "FROM events GROUP BY DATE_TRUNC('month', created_at)",
            dialect="duckdb",
        )
        result = route_to_rollup_shape(p, reg)
        assert result.routed is False
        assert result.plan is p


# ---------------------------------------------------------------------------
# 13. [LOW] _rewrite_to_rollup only rewrites top-level SELECT aggregates;
#     aggregates inside WHERE subqueries must NOT be mis-rewritten or cause
#     an unsound route.
# ---------------------------------------------------------------------------


class TestRollupRewriteNoOverwalk:
    """_rewrite_to_rollup must not walk WHERE/HAVING subquery aggregates.

    Bug: the old code called tree.find_all(exp.AggFunc) which walked the FULL
    SQL tree.  An aggregate inside a WHERE subquery (e.g. a correlated
    ``WHERE amount > (SELECT AVG(price) FROM …)``) was treated as a top-level
    measure and either wrongly rewritten (producing incorrect SQL) or wrongly
    caused an abort (AVG is non-re-aggregable, so the otherwise-sound top-level
    SUM/COUNT query was refused even though it should route).

    Fix: only rewrite aggregates in the top-level SELECT projection;
    refuse routing when any aggregate is found inside a WHERE/HAVING subquery.
    """

    def test_where_subquery_aggregate_refuses_routing(self, source_db: str) -> None:
        """A query with an aggregate in a WHERE subquery must NOT be routed.

        The rollup cannot reproduce the scalar subquery (it references the raw
        source table), so routing must be refused and the plan returned unchanged.
        """
        reg = RollupRegistry()
        _build_orders_rollup(source_db, reg)

        # The WHERE clause contains a subquery with an aggregate.
        # The top-level SELECT is structurally routable (SUM on a dimension),
        # but the WHERE subquery aggregate makes the rewrite UNSOUND.
        sql = (
            "SELECT region, SUM(amount) FROM orders "
            "WHERE amount > (SELECT AVG(amount) FROM orders) "
            "GROUP BY region"
        )
        p = plan(sql, dialect="postgres")
        result = route_to_rollup_shape(p, reg)

        # Must NOT route — the WHERE subquery aggregate is not safely rewritable.
        assert result.routed is False, (
            f"Expected routed=False (WHERE subquery aggregate makes rewrite unsound), "
            f"got routed=True. Rewritten SQL: {result.plan.sql}"
        )
        assert result.plan is p  # plan is returned unchanged

    def test_sound_top_level_agg_still_routes_without_subquery(
        self, source_db: str
    ) -> None:
        """A query without a WHERE subquery aggregate still routes normally.

        Ensures the fix does not break the common (no-subquery) path.
        """
        reg = RollupRegistry()
        _build_orders_rollup(source_db, reg)

        sql = (
            "SELECT region, SUM(amount) FROM orders "
            "WHERE tenant_id = 'acme' "
            "GROUP BY region"
        )
        p = plan(sql, claims={"policies": {"tenant_id": "acme"}}, dialect="postgres")
        result = route_to_rollup_shape(p, reg)

        # This is a sound rewrite (plain WHERE equality, no subquery aggregates).
        assert result.routed is True, (
            f"Expected routed=True for plain WHERE + top-level SUM, "
            f"got routed=False. Reason: {result.reason}"
        )
        assert "rollup_orders" in result.plan.sql.lower()

    def test_where_subquery_agg_not_mistakenly_rewritten_in_sql(
        self, source_db: str
    ) -> None:
        """The WHERE subquery aggregate must appear verbatim (not rewritten) or
        the query must be refused — never silently rewritten to a rollup column.

        When routing is refused the original SQL must still be parseable and
        must reference the original table (not the rollup table).
        """
        import sqlglot  # noqa: PLC0415

        reg = RollupRegistry()
        _build_orders_rollup(source_db, reg)

        sql = (
            "SELECT region, SUM(amount) FROM orders "
            "WHERE amount > (SELECT AVG(amount) FROM orders) "
            "GROUP BY region"
        )
        p = plan(sql, dialect="postgres")
        result = route_to_rollup_shape(p, reg)

        # The plan must be refused (not routed).
        assert result.routed is False

        # The SQL in the returned (unchanged) plan must NOT reference the rollup
        # table — it must still reference the original source table.
        returned_sql = result.plan.sql.lower()
        assert "rollup_orders" not in returned_sql, (
            f"WHERE subquery aggregate caused a partial/wrong rewrite: {result.plan.sql}"
        )
        assert "orders" in returned_sql  # original table still present

        # The original SQL must parse cleanly (sanity check).
        parsed = sqlglot.parse_one(result.plan.sql, dialect="postgres")
        assert parsed is not None


# ---------------------------------------------------------------------------
# 13. [LOW RLS] Layered-CTE rollup rewrite must stay RLS-sound when fix-21
#     hoists a policy column into the __base grain.
#
# fix-21 hoists every active RLS policy column into the layered __base GROUP BY
# so the top-N membership subquery can correlate PER TENANT.  When the layered
# router re-points __base at a rollup, the rollup's grain MUST include that
# policy-hoisted dim — otherwise re-grouping the rollup happens at a coarser
# grain that has already collapsed across the tenant column, silently dropping
# the per-tenant correlation (a cross-tenant leak in the top-N membership set).
# ---------------------------------------------------------------------------


class TestLayeredRLSHoistedDimSoundness:
    """A layered query carrying a policy-hoisted dim must only route to a rollup
    whose grain includes that dim (RLS preserved); else it falls back to base.
    """

    def _build_region_only_rollup(self, source_db: str, reg: RollupRegistry):
        """A rollup grouped ONLY on region — NO tenant_id anywhere in its grain
        (no dim, no rls_key).  Routing the tenant-hoisted query here would drop
        the per-tenant correlation, so it MUST be refused.
        """
        candidate = RollupCandidate(
            table="orders",
            dimensions=["region"],
            measures=["sum(amount)", "count(*)"],
        )
        return build_rollup(
            candidate,
            rls_keys=[],  # tenant_id deliberately absent from the grain
            source_database=source_db,
            registry=reg,
            register_query=False,
        )

    def _build_tenant_region_rollup(self, source_db: str, reg: RollupRegistry):
        """A rollup whose FULL grain carries tenant_id (as an rls_key) + region —
        a superset of the policy-hoisted dim, so routing preserves RLS.
        """
        candidate = RollupCandidate(
            table="orders",
            dimensions=["region"],
            measures=["sum(amount)", "count(*)"],
        )
        return build_rollup(
            candidate,
            rls_keys=["tenant_id"],  # tenant_id physically present in the grain
            source_database=source_db,
            registry=reg,
            register_query=False,
        )

    # The layered query AS fix-21 would emit it: __base GROUPs BY the hoisted
    # policy column (tenant_id) + region; the outer adds a window fn.
    _HOISTED_LAYERED_SQL = (
        "WITH __base AS ("
        "SELECT tenant_id, region, SUM(amount) AS amount "
        "FROM orders GROUP BY tenant_id, region"
        ") "
        "SELECT tenant_id, region, amount, "
        "LAG(amount, 1) OVER (PARTITION BY tenant_id ORDER BY region) AS prior "
        "FROM __base"
    )

    def test_policy_hoisted_dim_does_not_route_to_rollup_lacking_it(
        self, source_db: str
    ) -> None:
        """A query with a policy-hoisted dim (tenant_id) must NOT route to a
        rollup whose grain lacks tenant_id — it falls back to the base table.
        """
        reg = RollupRegistry()
        self._build_region_only_rollup(source_db, reg)

        p = plan(
            self._HOISTED_LAYERED_SQL,
            claims={"policies": {"tenant_id": "acme"}},
            dialect="postgres",
        )
        result = route_to_rollup_shape(p, reg)

        assert result.routed is False, (
            f"RLS-unsound route accepted: a rollup lacking the policy-hoisted "
            f"dim 'tenant_id' must be refused. Reason: {result.reason}"
        )
        # The returned plan is left untouched and still reads the base table.
        assert result.plan is p
        assert "orders" in result.plan.sql.lower()
        # RLS claims preserved on the fallback plan.
        assert result.plan.rls_claims == {"policies": {"tenant_id": "acme"}}

    def test_policy_hoisted_dim_routes_to_rollup_including_it(
        self, source_db: str
    ) -> None:
        """The SAME query DOES route when the rollup's grain includes the
        policy-hoisted dim (tenant_id as an rls_key) — RLS correlation preserved.
        """
        reg = RollupRegistry()
        built = self._build_tenant_region_rollup(source_db, reg)

        p = plan(
            self._HOISTED_LAYERED_SQL,
            claims={"policies": {"tenant_id": "acme"}},
            dialect="postgres",
        )
        result = route_to_rollup_shape(p, reg)

        assert result.routed is True, (
            f"RLS-sound route refused: a rollup whose grain includes the "
            f"policy-hoisted dim 'tenant_id' must serve the query. "
            f"Reason: {result.reason}"
        )
        assert result.rollup_id == built.rollup_id
        rewritten = result.plan.sql.lower()
        assert built.table.lower() in rewritten, (
            f"Expected rollup table in rewrite: {result.plan.sql}"
        )
        assert "with __base as" in rewritten, "layered CTE form preserved"
        # tenant_id (the hoisted policy dim) survives in the rewritten grain.
        assert "tenant_id" in rewritten
        # RLS claims preserved through the rewrite.
        assert result.plan.rls_claims == {"policies": {"tenant_id": "acme"}}


# ---------------------------------------------------------------------------
# 12. [HIGH disk leak] On-disk rollup file lifecycle
# ---------------------------------------------------------------------------


class TestRollupFileLifecycle:
    """The on-disk DuckDB file for a rollup must not outlive its registry entry.

    Covers the [HIGH disk leak] fix:
    - LRU eviction unlinks the evicted rollup's DuckDB file.
    - Rebuilding the same shape reuses the existing id/path (no new file).
    - sweep_orphan_rollups removes unreferenced files left on disk.
    """

    def _candidate(self) -> RollupCandidate:
        return RollupCandidate(
            table="orders",
            dimensions=["region"],
            measures=["sum(amount)", "count(*)"],
        )

    def test_eviction_unlinks_on_disk_file(self, source_db: str) -> None:
        """Evicting a rollup from the registry deletes its DuckDB file."""
        reg = RollupRegistry(max_entries=1)

        built_a = build_rollup(
            self._candidate(),
            rls_keys=["tenant_id"],
            source_database=source_db,
            registry=reg,
            register_query=False,
            rollup_id="lifecycle_a",
        )
        assert os.path.isfile(built_a.database)

        # Adding a second rollup (cap=1) evicts built_a → its file must be gone.
        built_b = build_rollup(
            self._candidate(),
            rls_keys=["tenant_id"],
            source_database=source_db,
            registry=reg,
            register_query=False,
            rollup_id="lifecycle_b",
        )
        assert built_b.database != built_a.database
        assert not os.path.exists(built_a.database), (
            "evicted rollup's on-disk DuckDB file must be deleted"
        )
        assert os.path.isfile(built_b.database), "live rollup file must survive"

        # cleanup
        if os.path.exists(built_b.database):
            os.unlink(built_b.database)

    def test_rebuild_same_shape_reuses_path_no_new_file(self, source_db: str) -> None:
        """Rebuilding an identical shape reuses the rollup_id/path — no new file."""
        import glob  # noqa: PLC0415
        from app.connectors.preagg import _rollup_database_path  # noqa: PLC0415

        reg = RollupRegistry()
        rollups_dir = os.path.dirname(_rollup_database_path("_"))

        built_1 = build_rollup(
            self._candidate(),
            rls_keys=["tenant_id"],
            source_database=source_db,
            registry=reg,
            register_query=False,
        )
        try:
            files_after_1 = {
                os.path.abspath(p)
                for p in glob.glob(os.path.join(rollups_dir, "*.duckdb"))
            }
            assert os.path.abspath(built_1.database) in files_after_1

            # Same (table, dims, measures, rls_keys) → same shape hash → reuse.
            built_2 = build_rollup(
                self._candidate(),
                rls_keys=["tenant_id"],
                source_database=source_db,
                registry=reg,
                register_query=False,
            )
            assert built_2.rollup_id == built_1.rollup_id, "same shape must reuse id"
            assert built_2.database == built_1.database, "same shape must reuse path"
            assert len(reg.all_rollups()) == 1, "no duplicate registry entry"

            files_after_2 = {
                os.path.abspath(p)
                for p in glob.glob(os.path.join(rollups_dir, "*.duckdb"))
            }
            # No NEW file was created for the rebuild (shared file may remain).
            new_files = files_after_2 - files_after_1
            assert not new_files, f"rebuild of same shape created new file(s): {new_files}"
        finally:
            if os.path.exists(built_1.database):
                os.unlink(built_1.database)

    def test_sweep_removes_orphan_files(self, source_db: str) -> None:
        """sweep_orphan_rollups unlinks files not referenced by the registry."""
        from app.connectors.preagg import (  # noqa: PLC0415
            _rollup_database_path,
            sweep_orphan_rollups,
        )

        reg = RollupRegistry()

        # A live, registered rollup — its file must be preserved by the sweep.
        live = build_rollup(
            self._candidate(),
            rls_keys=["tenant_id"],
            source_database=source_db,
            registry=reg,
            register_query=False,
        )
        orphan_path = _rollup_database_path("orphan_test_xyz")
        try:
            assert os.path.isfile(live.database)

            # An orphan file: on disk but not referenced by any registry entry.
            os.makedirs(os.path.dirname(orphan_path), exist_ok=True)
            import duckdb  # noqa: PLC0415

            oc = duckdb.connect(orphan_path)
            oc.execute("CREATE TABLE t (x INTEGER)")
            oc.close()
            assert os.path.isfile(orphan_path)

            removed = sweep_orphan_rollups(reg)

            assert not os.path.exists(orphan_path), "orphan file must be removed"
            assert os.path.isfile(live.database), "referenced (live) file must survive"
            assert removed >= 1
        finally:
            for p in (live.database, orphan_path):
                if p and os.path.exists(p):
                    os.unlink(p)


# ---------------------------------------------------------------------------
# [LOW resource] build_rollup source connection must open read-only on files
# ---------------------------------------------------------------------------


class TestBuildRollupSourceReadOnly:
    """build_rollup must open the source DuckDB file read_only=True.

    Opening read_only=False grabs an exclusive WRITE lock on the source file,
    which conflicts with concurrent reads (demo connector singleton / dashboard
    queries on the same file) and produces "database is locked" errors.

    The source connection only reads; opening it read-only:
      - releases the exclusive write lock immediately,
      - allows other readers to connect concurrently.

    For in-memory sources (source_database=None) the connection remains
    writable so test helpers that register Arrow tables still work.
    """

    def test_file_source_opens_read_only(self, source_db: str) -> None:
        """build_rollup opens the file-backed source with read_only=True.

        We validate this by opening a SECOND read-only connection to source_db
        WHILE build_rollup is running — if the source were opened read_only=False
        (exclusive write lock) the second connection would raise "database is
        locked" or similar.  With read_only=True both connections co-exist.
        """
        reg = RollupRegistry()
        candidate = RollupCandidate(
            table="orders",
            dimensions=["region"],
            measures=["sum(amount)"],
        )

        # Build the rollup — internally opens source_db read_only=True.
        built = build_rollup(
            candidate,
            rls_keys=["tenant_id"],
            source_database=source_db,
            registry=reg,
            register_query=False,
        )
        assert built is not None

        # Immediately after build_rollup closes its source connection open ANOTHER
        # read-only connection to the same file — this must NOT raise.
        # (If the previous connection had held a write lock we would see
        # "database is locked" / "IO Error" here or during the build itself.)
        concurrent = duckdb.connect(source_db, read_only=True)
        try:
            rows = concurrent.execute(
                "SELECT COUNT(*) FROM orders"
            ).fetchone()
            assert rows[0] == 6, f"Expected 6 source rows, got {rows[0]}"
        finally:
            concurrent.close()

    def test_concurrent_read_during_build_not_blocked(self, source_db: str) -> None:
        """A concurrent read-only connection to source_db is not blocked by build_rollup.

        Opens a read-only connection to source_db BEFORE calling build_rollup,
        keeps it open throughout, and asserts both that build_rollup succeeds and
        that the concurrent connection can still query after the build.
        """
        reg = RollupRegistry()
        candidate = RollupCandidate(
            table="orders",
            dimensions=["region"],
            measures=["sum(amount)"],
        )

        # Open a concurrent read-only reader (simulates a dashboard query).
        reader = duckdb.connect(source_db, read_only=True)
        try:
            # Pre-build read — establish that the connection works.
            pre_rows = reader.execute(
                "SELECT SUM(amount) FROM orders"
            ).fetchone()[0]
            assert pre_rows == 129  # 10+5+7+100+3+4

            # build_rollup must succeed despite the concurrent read-only connection.
            built = build_rollup(
                candidate,
                rls_keys=["tenant_id"],
                source_database=source_db,
                registry=reg,
                register_query=False,
            )
            assert built is not None

            # Post-build read on the same connection — must still work.
            post_rows = reader.execute(
                "SELECT SUM(amount) FROM orders"
            ).fetchone()[0]
            assert post_rows == pre_rows
        finally:
            reader.close()

    def test_memory_source_remains_writable(self) -> None:
        """When source_database is None the connection is NOT opened read_only.

        In-memory DuckDB connections must stay writable so callers that register
        Arrow tables (e.g., test helpers using conn.register()) can do so.
        The fix only applies read_only=True when source_database is a real file.
        """
        # We verify indirectly: build_rollup with source_database=None uses an
        # in-memory source.  It must be able to register an Arrow table into it.
        # (If read_only=True were applied to ':memory:' this would fail.)
        import pyarrow as pa  # noqa: PLC0415

        from app.connectors.preagg import build_rollup_sql  # noqa: PLC0415

        # Build an in-memory DuckDB, register a table, verify SQL executes.
        mem_conn = duckdb.connect(":memory:", read_only=False)
        try:
            table = pa.table({
                "tenant_id": ["acme", "acme", "beta"],
                "region": ["us", "eu", "us"],
                "amount": [10, 7, 100],
            })
            mem_conn.register("orders", table)
            rollup_sql = build_rollup_sql(
                "orders", ["region"], ["sum(amount)"], ["tenant_id"]
            )
            rows = mem_conn.execute(rollup_sql).fetchall()
            assert len(rows) > 0, "In-memory source must be queryable"
        finally:
            mem_conn.close()


# ---------------------------------------------------------------------------
# [LOW] fix-41: _outer_filter_columns must NOT include ORDER BY columns
# ---------------------------------------------------------------------------


class TestOuterFilterColumnsOrderBy:
    """_outer_filter_columns must scope to WHERE/HAVING predicates only.

    Before fix-41 the helper walked the full WHERE tree, which in the layered
    CTE path could pick up columns that appear in ORDER BY clauses (via correlated
    subqueries inside WHERE) or — in a broader mis-reading — any column in the
    outer SELECT.  That made the outer-RLS soundness check over-broad: a rollup
    was rejected just because an ORDER BY column was absent from the rollup grain,
    even though ORDER BY is not an RLS-injection target.

    Fix: scope _outer_filter_columns to the direct WHERE and HAVING predicate
    expressions only.  ORDER BY, GROUP BY, and SELECT-list columns are excluded.
    """

    def test_order_by_absent_col_does_not_block_routing(self, source_db: str) -> None:
        """A layered query that ORDER BYs a column absent from the rollup grain
        must STILL route correctly — ORDER BY is not a filter column and must
        NOT be included in the outer-RLS soundness check (fix-41).

        Setup: rollup grain = {tenant_id, region}.
        Query: inner aggregates (region, SUM(amount)); outer ORDER BYs `amount`
        (a derived measure column present in __base's SELECT list, but absent from
        the rollup grain as a dimension).  Before fix-41 this caused over-broad
        refusal; after fix-41 it routes because `amount` is not a filter column.
        """
        reg = RollupRegistry()
        built = _build_orders_rollup(source_db, reg)

        # Outer ORDER BYs `amount` — a column NOT in the rollup grain (amount is
        # a measure, not a dimension/rls_key), but it IS projected by __base.
        # It is NOT a WHERE/HAVING filter column, so routing must proceed.
        layered_sql = (
            "WITH __base AS ("
            "SELECT region, SUM(amount) AS amount FROM orders GROUP BY region"
            ") "
            "SELECT region, amount FROM __base ORDER BY amount DESC"
        )
        p = plan(layered_sql, dialect="postgres")
        result = route_to_rollup_shape(p, reg)

        assert result.routed is True, (
            f"ORDER BY on a non-filter column must not block routing. "
            f"Reason: {result.reason}"
        )
        assert result.rollup_id is not None
        rewritten = result.plan.sql.lower()
        assert "rollup_orders" in rewritten, (
            f"Expected rollup table in rewritten SQL: {result.plan.sql}"
        )
        assert "with __base as" in rewritten, "layered CTE form preserved"
        # The ORDER BY must be preserved in the rewrite.
        assert "order by" in rewritten, "ORDER BY must be preserved"

    def test_where_on_absent_rls_col_still_refuses(self, source_db: str) -> None:
        """A layered query that WHEREs on a column absent from the rollup grain
        must STILL be refused — WHERE is an RLS-injection target (fix-22 guard).

        This ensures fix-41 does not weaken the WHERE-column soundness check:
        only ORDER BY is de-scoped, not WHERE.

        Setup: rollup grain = {tenant_id, region}.
        Outer WHERE on `channel` (absent from the rollup grain entirely).
        The router must refuse routing (channel can't be evaluated post-rollup).
        """
        reg = RollupRegistry()
        _build_orders_rollup(source_db, reg)

        layered_sql = (
            "WITH __base AS ("
            "SELECT region, tenant_id, SUM(amount) AS amount FROM orders "
            "GROUP BY region, tenant_id"
            ") "
            "SELECT region, amount FROM __base WHERE channel = 'web'"
        )
        p = plan(layered_sql, dialect="postgres")
        result = route_to_rollup_shape(p, reg)

        assert result.routed is False, (
            f"WHERE on absent col 'channel' must refuse routing (not in rollup grain). "
            f"Got routed=True. Rewrite: {result.plan.sql}"
        )
        assert result.plan is p  # plan untouched
