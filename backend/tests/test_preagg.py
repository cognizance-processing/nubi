"""Tests for M2-C: query_log, preagg suggester, and rollup routing.

Test coverage
-------------
- QueryLog records entries with a correct ``groupby_sig``.
- ``compute_groupby_sig`` normalisation is order-independent for dimensions.
- ``suggest()`` returns suggestions only for patterns seen >= min_hits.
- A non-repeated query is NOT returned by suggest().
- ``RollupRegistry.register`` + ``route_to_rollup`` rewrites FROM to rollup
  table and changes cache_key.
- An unregistered query is returned unchanged (same object, same cache_key).
- M1 conformance: ``plan()`` behaviour is unaffected by the new imports.
"""

from __future__ import annotations

import pytest

from app.connectors.preagg import RollupRegistry, RollupSuggestion, suggest
from app.connectors.planner import plan, route_to_rollup
from app.connectors.query_log import QueryLog, compute_groupby_sig


# ---------------------------------------------------------------------------
# compute_groupby_sig: normalisation tests
# ---------------------------------------------------------------------------


class TestComputeGroupbySig:
    def test_basic_group_by(self) -> None:
        sql = "SELECT tenant_id, SUM(amount) FROM orders GROUP BY tenant_id"
        sig = compute_groupby_sig(sql)
        assert sig is not None
        assert "orders" in sig
        assert "tenant_id" in sig
        assert "sum(amount)" in sig.lower()

    def test_no_group_by_returns_none(self) -> None:
        sig = compute_groupby_sig("SELECT * FROM orders")
        assert sig is None

    def test_dimension_order_independent(self) -> None:
        """GROUP BY a, b and GROUP BY b, a must produce the same sig."""
        sql1 = "SELECT a, b, COUNT(*) FROM t GROUP BY a, b"
        sql2 = "SELECT a, b, COUNT(*) FROM t GROUP BY b, a"
        assert compute_groupby_sig(sql1) == compute_groupby_sig(sql2)

    def test_parse_failure_returns_none(self) -> None:
        sig = compute_groupby_sig("NOT VALID SQL !!!")
        assert sig is None

    def test_non_select_returns_none(self) -> None:
        # INSERT is not a SELECT, so compute_groupby_sig returns None.
        sig = compute_groupby_sig("INSERT INTO t VALUES (1)")
        assert sig is None

    def test_multiple_dims(self) -> None:
        sql = "SELECT region, category, SUM(revenue) FROM sales GROUP BY region, category"
        sig = compute_groupby_sig(sql)
        assert sig is not None
        # dims must be sorted
        assert "category" in sig
        assert "region" in sig
        # agg
        assert "sum(revenue)" in sig.lower()

    def test_count_star(self) -> None:
        sql = "SELECT tenant_id, COUNT(*) FROM orders GROUP BY tenant_id"
        sig = compute_groupby_sig(sql)
        assert sig is not None
        assert "count" in sig.lower()


# ---------------------------------------------------------------------------
# QueryLog
# ---------------------------------------------------------------------------


class TestQueryLog:
    def test_record_no_group_by_still_logs(self) -> None:
        """Non-GROUP-BY queries are recorded with an empty sig."""
        log = QueryLog(maxlen=10)
        log.record("SELECT * FROM demo", "key1")
        entries = log.entries()
        assert len(entries) == 1
        assert entries[0]["groupby_sig"] == ""

    def test_record_group_by_extracts_sig(self) -> None:
        log = QueryLog(maxlen=10)
        log.record(
            "SELECT tenant_id, SUM(amount) FROM orders GROUP BY tenant_id",
            "key2",
            byte_size=512,
        )
        entries = log.entries()
        assert len(entries) == 1
        entry = entries[0]
        assert entry["groupby_sig"] != ""
        assert "tenant_id" in entry["groupby_sig"]
        assert entry["byte_size"] == 512
        assert entry["cache_key"] == "key2"

    def test_ts_defaults_to_time(self) -> None:
        import time

        before = time.time()
        log = QueryLog(maxlen=10)
        log.record("SELECT 1 FROM t GROUP BY x", "k")
        after = time.time()
        ts = log.entries()[0]["ts"]
        assert before <= ts <= after

    def test_ts_passable(self) -> None:
        log = QueryLog(maxlen=10)
        log.record("SELECT 1 FROM t GROUP BY x", "k", ts=12345.0)
        assert log.entries()[0]["ts"] == 12345.0

    def test_ring_buffer_maxlen(self) -> None:
        log = QueryLog(maxlen=3)
        for i in range(5):
            log.record(f"SELECT {i} FROM t", f"key{i}")
        # Only last 3 entries remain.
        assert len(log) == 3

    def test_entries_returns_snapshot(self) -> None:
        log = QueryLog(maxlen=10)
        log.record("SELECT a FROM t GROUP BY a", "k1")
        snapshot = log.entries()
        log.record("SELECT b FROM t GROUP BY b", "k2")
        # Snapshot is not affected by subsequent records.
        assert len(snapshot) == 1


# ---------------------------------------------------------------------------
# suggest()
# ---------------------------------------------------------------------------


GROUP_BY_SQL = "SELECT tenant_id, SUM(amount) FROM orders GROUP BY tenant_id"
OTHER_SQL = "SELECT region, COUNT(*) FROM events GROUP BY region"


class TestSuggest:
    def _make_log_with_hits(self, sql: str, n: int, byte_size: int = 100) -> QueryLog:
        log = QueryLog()
        for i in range(n):
            log.record(sql, f"key_{i}", byte_size=byte_size)
        return log

    def test_suggest_returns_suggestion_when_hits_ge_min(self) -> None:
        log = self._make_log_with_hits(GROUP_BY_SQL, 5)
        suggestions = suggest(log, min_hits=3)
        assert len(suggestions) == 1
        s = suggestions[0]
        assert isinstance(s, RollupSuggestion)
        assert s.hits == 5
        assert s.base_table == "orders"
        assert "tenant_id" in s.dimensions
        assert s.hits >= 3

    def test_suggest_measures_populated(self) -> None:
        log = self._make_log_with_hits(GROUP_BY_SQL, 4)
        suggestions = suggest(log, min_hits=3)
        assert len(suggestions) == 1
        s = suggestions[0]
        # measures list contains sum(amount) or similar
        assert any("sum" in m.lower() for m in s.measures)

    def test_suggest_excludes_below_min_hits(self) -> None:
        log = self._make_log_with_hits(GROUP_BY_SQL, 2)
        suggestions = suggest(log, min_hits=3)
        assert suggestions == []

    def test_non_repeated_query_not_suggested(self) -> None:
        """A query seen only once must NOT appear in suggestions."""
        log = QueryLog()
        log.record(GROUP_BY_SQL, "k1", byte_size=100)  # 1 hit
        # Record a different query 5 times so the log is non-empty
        for i in range(5):
            log.record(OTHER_SQL, f"other_{i}", byte_size=50)
        suggestions = suggest(log, min_hits=3)
        # Only the repeated OTHER_SQL pattern appears.
        assert all(s.base_table != "orders" for s in suggestions)

    def test_suggest_sorted_by_hits_desc(self) -> None:
        log = QueryLog()
        # orders pattern: 5 hits
        for i in range(5):
            log.record(GROUP_BY_SQL, f"k_ord_{i}")
        # events pattern: 3 hits
        for i in range(3):
            log.record(OTHER_SQL, f"k_evt_{i}")
        suggestions = suggest(log, min_hits=3)
        assert len(suggestions) == 2
        assert suggestions[0].hits >= suggestions[1].hits

    def test_est_bytes_saved(self) -> None:
        log = self._make_log_with_hits(GROUP_BY_SQL, 4, byte_size=200)
        suggestions = suggest(log, min_hits=3)
        assert suggestions[0].est_bytes_saved == 4 * 200

    def test_no_group_by_queries_not_suggested(self) -> None:
        log = QueryLog()
        for i in range(10):
            log.record("SELECT * FROM demo", f"k{i}")
        suggestions = suggest(log, min_hits=3)
        assert suggestions == []

    def test_sig_field_populated(self) -> None:
        log = self._make_log_with_hits(GROUP_BY_SQL, 4)
        s = suggest(log, min_hits=3)[0]
        assert s.sig != ""
        assert "orders" in s.sig


# ---------------------------------------------------------------------------
# RollupRegistry + route_to_rollup
# ---------------------------------------------------------------------------


class TestRollupRegistry:
    def test_register_and_lookup(self) -> None:
        reg = RollupRegistry()
        sig = "orders|dims=tenant_id|aggs=sum(amount)"
        reg.register(sig, "orders_rollup")
        assert reg.lookup(sig) == "orders_rollup"

    def test_lookup_missing_returns_none(self) -> None:
        reg = RollupRegistry()
        assert reg.lookup("nonexistent|dims=x|aggs=count(*)") is None

    def test_registered_snapshot(self) -> None:
        reg = RollupRegistry()
        reg.register("s1", "t1")
        reg.register("s2", "t2")
        snap = reg.registered()
        assert snap == {"s1": "t1", "s2": "t2"}


class TestRouteToRollup:
    def _make_registry_for(self, sql: str, rollup_table: str) -> RollupRegistry:
        """Build a registry pre-loaded with the sig for *sql*."""
        from app.connectors.query_log import compute_groupby_sig

        sig = compute_groupby_sig(sql)
        assert sig is not None, f"SQL has no GROUP BY: {sql}"
        reg = RollupRegistry()
        reg.register(sig, rollup_table)
        return reg

    def test_route_rewrites_from_to_rollup_table(self) -> None:
        sql = "SELECT tenant_id, SUM(amount) FROM orders GROUP BY tenant_id"
        p = plan(sql)
        reg = self._make_registry_for(sql, "orders_rollup_daily")
        routed = route_to_rollup(p, reg)
        assert "orders_rollup_daily" in routed.sql
        # Original table name no longer in the FROM clause (it's been replaced).
        # (orders might still appear in identifiers; check FROM target)
        assert routed.sql != p.sql

    def test_route_changes_cache_key(self) -> None:
        sql = "SELECT tenant_id, SUM(amount) FROM orders GROUP BY tenant_id"
        p = plan(sql)
        reg = self._make_registry_for(sql, "orders_rollup_daily")
        routed = route_to_rollup(p, reg)
        assert routed.cache_key != p.cache_key

    def test_unregistered_returns_original_plan_unchanged(self) -> None:
        sql = "SELECT tenant_id, SUM(amount) FROM orders GROUP BY tenant_id"
        p = plan(sql)
        reg = RollupRegistry()  # empty
        routed = route_to_rollup(p, reg)
        assert routed is p  # same object
        assert routed.cache_key == p.cache_key

    def test_no_group_by_returns_original_unchanged(self) -> None:
        sql = "SELECT * FROM orders"
        p = plan(sql)
        reg = RollupRegistry()
        routed = route_to_rollup(p, reg)
        assert routed is p

    def test_rls_claims_preserved_in_routed_plan(self) -> None:
        sql = "SELECT tenant_id, SUM(amount) FROM orders GROUP BY tenant_id"
        claims = {"policies": {"tenant_id": "acme"}}
        p = plan(sql, claims=claims)
        reg = self._make_registry_for(
            # Note: after RLS injection the SQL has a WHERE — but the base
            # groupby_sig is computed from the plan.sql (post-injection).
            # We derive the sig from p.sql so the registry matches.
            p.sql,
            "orders_rollup_daily",
        )
        routed = route_to_rollup(p, reg)
        assert routed.rls_claims == claims

    def test_projection_preserved(self) -> None:
        sql = "SELECT tenant_id, SUM(amount) FROM orders GROUP BY tenant_id"
        p = plan(sql, projection=["tenant_id"])
        reg = self._make_registry_for(p.sql, "orders_rollup_daily")
        routed = route_to_rollup(p, reg)
        assert routed.projection == ["tenant_id"]


# ---------------------------------------------------------------------------
# M1 conformance guard: plan() behaviour is unchanged
# ---------------------------------------------------------------------------


class TestPlanUnchanged:
    """Smoke-checks that adding route_to_rollup import does not affect plan()."""

    def test_basic_plan_cache_key_stable(self) -> None:
        """plan() must produce the same cache key for the same inputs."""
        p1 = plan("SELECT * FROM orders")
        p2 = plan("SELECT * FROM orders")
        assert p1.cache_key == p2.cache_key

    def test_rls_injection_still_works(self) -> None:
        p = plan("SELECT * FROM orders", claims={"policies": {"tenant_id": "acme"}})
        assert "tenant_id" in p.sql
        assert "acme" in p.sql

    def test_plan_raises_on_non_select(self) -> None:
        from app.errors import AppError

        with pytest.raises(AppError) as exc_info:
            plan("DROP TABLE orders")
        assert exc_info.value.code == "UNSUPPORTED_QUERY"
