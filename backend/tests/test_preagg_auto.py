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
