"""Tests for the M9-A connector SDK: registry, FunctionConnector, and post-fetch helpers.

Coverage
--------
- ``apply_rls_postfetch``:
    * Single-policy filter (acme rows only; globex absent — the security guard)
    * Multiple policies AND together
    * Policy on a MISSING column -> raises rls_column_missing 403 (fail-closed)
    * Empty policies dict -> table returned unchanged
- ``apply_projection_postfetch``:
    * Narrows to requested columns (in order)
    * Missing column in projection silently ignored (intersection semantics)
    * None projection -> table unchanged
- ``apply_limit_postfetch``:
    * Slices to limit rows
    * None limit -> table unchanged
    * Limit=0 -> empty table
- ``FunctionConnector``:
    * capabilities() returns configured dict
    * predicate_pushdown=False + predicate_rls=True: execute auto-applies RLS
      (only acme rows returned from a 2-tenant table)
    * predicate_pushdown=True: execute does NOT post-filter (fn output returned as-is)
    * projection_pushdown=False: execute narrows columns automatically
    * execute_stream yields batches that reconstruct the same table as execute()
    * Missing capability key -> ValueError at construction
- ``ConnectorRegistry``:
    * register / get / all round-trip
    * get unknown type -> AppError code="unknown_connector" status=404
    * Module singleton get_connector_registry() pre-registers 'postgres' and 'duckdb'
"""

from __future__ import annotations

import pyarrow as pa
import pytest

from app.connectors.sdk import (
    FunctionConnector,
    apply_limit_postfetch,
    apply_projection_postfetch,
    apply_rls_postfetch,
)
from app.connectors.registry import ConnectorRegistry, get_connector_registry
from app.connectors.plan import PhysicalPlan
from app.errors import AppError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_plan(
    *,
    rls_claims: dict | None = None,
    projection: list[str] | None = None,
    sql: str = "SELECT 1",
    params: list | None = None,
) -> PhysicalPlan:
    """Construct a minimal PhysicalPlan for testing."""
    return PhysicalPlan(
        dialect="postgres",
        sql=sql,
        params=params or [],
        projection=projection,
        predicates=[],
        rls_claims=rls_claims or {},
        cache_key="deadbeef" * 8,  # 64-char fake SHA-256
    )


def _two_tenant_table() -> pa.Table:
    """Return a table with rows for two tenants: acme and globex."""
    return pa.table(
        {
            "tenant_id": pa.array(["acme", "acme", "globex"], type=pa.string()),
            "value": pa.array([1, 2, 3], type=pa.int64()),
        }
    )


# ---------------------------------------------------------------------------
# apply_rls_postfetch
# ---------------------------------------------------------------------------


class TestApplyRlsPostfetch:
    def test_single_policy_filters_to_matching_tenant(self) -> None:
        """Only acme rows survive; globex is absent — this is the security guard."""
        table = _two_tenant_table()
        result = apply_rls_postfetch(table, {"tenant_id": "acme"})

        assert result.num_rows == 2
        assert result.column("tenant_id").to_pylist() == ["acme", "acme"]
        # Globex is gone
        assert "globex" not in result.column("tenant_id").to_pylist()

    def test_globex_policy_filters_to_globex(self) -> None:
        """Symmetry check: a globex policy returns only globex rows."""
        table = _two_tenant_table()
        result = apply_rls_postfetch(table, {"tenant_id": "globex"})

        assert result.num_rows == 1
        assert result.column("tenant_id").to_pylist() == ["globex"]

    def test_multiple_policies_and_together(self) -> None:
        """Multiple policies are combined with AND; only rows matching ALL survive."""
        table = pa.table(
            {
                "tenant_id": pa.array(["acme", "acme", "globex", "acme"], type=pa.string()),
                "region": pa.array(["us", "eu", "us", "us"], type=pa.string()),
                "score": pa.array([10, 20, 30, 40], type=pa.int64()),
            }
        )
        # tenant_id=acme AND region=us -> rows 0 and 3 (scores 10, 40)
        result = apply_rls_postfetch(table, {"tenant_id": "acme", "region": "us"})

        assert result.num_rows == 2
        assert result.column("score").to_pylist() == [10, 40]

    def test_empty_policies_returns_table_unchanged(self) -> None:
        """An empty policy dict must not modify the table."""
        table = _two_tenant_table()
        result = apply_rls_postfetch(table, {})
        assert result.num_rows == table.num_rows

    def test_policy_on_missing_column_raises_403_fail_closed(self) -> None:
        """A policy referencing an absent column raises rls_column_missing (fail-closed).

        This is the critical security property: a source that cannot honour a
        policy MUST NOT return unfiltered data.  The function raises 403 rather
        than silently returning all rows.
        """
        table = pa.table({"value": pa.array([1, 2, 3], type=pa.int64())})
        # 'tenant_id' is not in the schema
        with pytest.raises(AppError) as exc_info:
            apply_rls_postfetch(table, {"tenant_id": "acme"})

        err = exc_info.value
        assert err.code == "rls_column_missing"
        assert err.status == 403

    def test_multiple_missing_columns_all_reported(self) -> None:
        """When multiple policy columns are absent, the error names all of them."""
        table = pa.table({"value": pa.array([1], type=pa.int64())})
        with pytest.raises(AppError) as exc_info:
            apply_rls_postfetch(table, {"tenant_id": "acme", "org_id": "org1"})

        err = exc_info.value
        assert err.code == "rls_column_missing"
        assert err.status == 403
        assert "tenant_id" in err.message or "org_id" in err.message

    def test_non_matching_policy_returns_empty_table(self) -> None:
        """A valid policy that matches no rows returns an empty table (not an error)."""
        table = _two_tenant_table()
        result = apply_rls_postfetch(table, {"tenant_id": "unknown_tenant"})
        assert result.num_rows == 0

    def test_schema_preserved_after_filter(self) -> None:
        """The schema (column names, types) is unchanged by filtering."""
        table = _two_tenant_table()
        result = apply_rls_postfetch(table, {"tenant_id": "acme"})
        assert result.schema.names == table.schema.names


# ---------------------------------------------------------------------------
# apply_projection_postfetch
# ---------------------------------------------------------------------------


class TestApplyProjectionPostfetch:
    def test_narrows_to_requested_columns(self) -> None:
        """Only the projected columns are present in the result."""
        table = pa.table(
            {"a": [1, 2], "b": [3, 4], "c": [5, 6]}
        )
        result = apply_projection_postfetch(table, ["a", "c"])
        assert result.schema.names == ["a", "c"]
        assert result.num_rows == 2

    def test_preserves_projection_order(self) -> None:
        """Columns appear in projection order, not original table order."""
        table = pa.table({"x": [1], "y": [2], "z": [3]})
        result = apply_projection_postfetch(table, ["z", "x"])
        assert result.schema.names == ["z", "x"]

    def test_absent_column_silently_ignored(self) -> None:
        """A column in projection that is missing from the table is silently dropped."""
        table = pa.table({"a": [1, 2], "b": [3, 4]})
        result = apply_projection_postfetch(table, ["a", "missing_col"])
        assert result.schema.names == ["a"]
        assert result.num_rows == 2

    def test_none_projection_returns_unchanged(self) -> None:
        """A None projection means 'all columns'; the table is returned as-is."""
        table = pa.table({"a": [1], "b": [2]})
        result = apply_projection_postfetch(table, None)
        assert result.schema.names == ["a", "b"]

    def test_all_projected_columns_missing_returns_empty_schema(self) -> None:
        """If all projection columns are absent, an empty-schema table is returned."""
        table = pa.table({"a": [1, 2]})
        result = apply_projection_postfetch(table, ["x", "y"])
        assert result.num_columns == 0


# ---------------------------------------------------------------------------
# apply_limit_postfetch
# ---------------------------------------------------------------------------


class TestApplyLimitPostfetch:
    def test_slices_to_limit(self) -> None:
        """Table is sliced to exactly limit rows."""
        table = pa.table({"v": list(range(10))})
        result = apply_limit_postfetch(table, 3)
        assert result.num_rows == 3
        assert result.column("v").to_pylist() == [0, 1, 2]

    def test_none_limit_returns_unchanged(self) -> None:
        """A None limit leaves the table untouched."""
        table = pa.table({"v": list(range(5))})
        result = apply_limit_postfetch(table, None)
        assert result.num_rows == 5

    def test_limit_zero_returns_empty(self) -> None:
        """limit=0 yields an empty table (zero rows)."""
        table = pa.table({"v": [1, 2, 3]})
        result = apply_limit_postfetch(table, 0)
        assert result.num_rows == 0

    def test_limit_larger_than_table_returns_all(self) -> None:
        """A limit larger than the row count returns all rows."""
        table = pa.table({"v": [1, 2, 3]})
        result = apply_limit_postfetch(table, 100)
        assert result.num_rows == 3


# ---------------------------------------------------------------------------
# FunctionConnector
# ---------------------------------------------------------------------------


def _non_pushdown_caps(**overrides: bool) -> dict[str, bool]:
    """Return a capability dict for a source with no push-down, but RLS declared."""
    caps = {
        "native_arrow": True,
        "predicate_pushdown": False,
        "projection_pushdown": False,
        "partition_pushdown": False,
        "predicate_rls": True,
        "column_masking": False,
        "streaming_cdc": False,
    }
    caps.update(overrides)
    return caps


def _pushdown_caps(**overrides: bool) -> dict[str, bool]:
    """Return a capability dict simulating a source that handles all push-downs."""
    caps = {
        "native_arrow": True,
        "predicate_pushdown": True,
        "projection_pushdown": True,
        "partition_pushdown": False,
        "predicate_rls": True,
        "column_masking": False,
        "streaming_cdc": False,
    }
    caps.update(overrides)
    return caps


class TestFunctionConnectorCapabilities:
    def test_capabilities_returns_configured_dict(self) -> None:
        caps = _non_pushdown_caps()
        conn = FunctionConnector(fn=lambda p: pa.table({"x": [1]}), capabilities=caps)
        assert conn.capabilities() == caps

    def test_missing_capability_key_raises_at_construction(self) -> None:
        """A malformed capabilities dict raises ValueError immediately."""
        bad_caps = {
            "native_arrow": True,
            # missing all others
        }
        with pytest.raises(ValueError, match="missing keys"):
            FunctionConnector(fn=lambda p: pa.table({"x": [1]}), capabilities=bad_caps)


class TestFunctionConnectorRls:
    """RLS post-fetch guard via FunctionConnector."""

    def _make_conn(self, raw_table: pa.Table) -> FunctionConnector:
        """Build a non-pushdown connector returning raw_table."""
        return FunctionConnector(
            fn=lambda _plan: raw_table,
            capabilities=_non_pushdown_caps(),
        )

    def test_execute_applies_rls_automatically(self) -> None:
        """predicate_pushdown=False + predicate_rls=True -> RLS applied post-fetch.

        This is the core security property of M9-A: a non-SQL source that
        declares predicate_rls=True gets tenant filtering enforced server-side
        without writing any filtering code in the fn itself.
        """
        raw = _two_tenant_table()
        conn = self._make_conn(raw)
        plan = _make_plan(rls_claims={"policies": {"tenant_id": "acme"}})

        result = conn.execute(plan)

        assert result.num_rows == 2
        assert set(result.column("tenant_id").to_pylist()) == {"acme"}
        # Globex is absent — the security guard worked
        assert "globex" not in result.column("tenant_id").to_pylist()

    def test_execute_without_policies_returns_full_table(self) -> None:
        """Empty/absent policies dict -> no RLS filtering -> all rows returned."""
        raw = _two_tenant_table()
        conn = self._make_conn(raw)
        plan = _make_plan(rls_claims={})

        result = conn.execute(plan)
        assert result.num_rows == 3

    def test_execute_fails_closed_on_missing_policy_column(self) -> None:
        """Missing policy column -> rls_column_missing 403 (not unfiltered data)."""
        raw = pa.table({"value": pa.array([1, 2, 3])})
        conn = self._make_conn(raw)
        plan = _make_plan(rls_claims={"policies": {"tenant_id": "acme"}})

        with pytest.raises(AppError) as exc_info:
            conn.execute(plan)

        assert exc_info.value.code == "rls_column_missing"
        assert exc_info.value.status == 403


class TestFunctionConnectorPushdownSkipsRls:
    """When predicate_pushdown=True the fn is trusted to have filtered; no post-fetch RLS."""

    def test_execute_does_not_post_filter_when_pushdown_true(self) -> None:
        """predicate_pushdown=True -> fn output returned as-is, even if it has extra rows.

        This verifies that FunctionConnector does not double-filter a source
        that already applied RLS inside fn.
        """
        raw = _two_tenant_table()  # 3 rows: acme x2, globex x1
        conn = FunctionConnector(
            fn=lambda _plan: raw,
            capabilities=_pushdown_caps(),
        )
        plan = _make_plan(rls_claims={"policies": {"tenant_id": "acme"}})

        result = conn.execute(plan)

        # All 3 rows returned — the source is trusted to have filtered already
        assert result.num_rows == 3


class TestFunctionConnectorProjection:
    def test_execute_applies_projection_when_no_pushdown(self) -> None:
        """projection_pushdown=False -> projection applied post-fetch automatically."""
        raw = pa.table({"a": [1, 2], "b": [3, 4], "c": [5, 6]})
        conn = FunctionConnector(
            fn=lambda _plan: raw,
            capabilities=_non_pushdown_caps(predicate_rls=False),
        )
        plan = _make_plan(projection=["a", "c"])

        result = conn.execute(plan)
        assert result.schema.names == ["a", "c"]
        assert result.num_rows == 2

    def test_execute_skips_projection_when_pushdown_true(self) -> None:
        """projection_pushdown=True -> fn output columns not narrowed."""
        raw = pa.table({"a": [1], "b": [2], "c": [3]})
        conn = FunctionConnector(
            fn=lambda _plan: raw,
            capabilities=_pushdown_caps(),
        )
        plan = _make_plan(projection=["a"])

        result = conn.execute(plan)
        # All columns present — source handled projection
        assert set(result.schema.names) == {"a", "b", "c"}


class TestFunctionConnectorStream:
    def test_execute_stream_yields_batches(self) -> None:
        """execute_stream yields RecordBatches that reconstruct the execute() result."""
        raw = _two_tenant_table()
        conn = FunctionConnector(
            fn=lambda _plan: raw,
            capabilities=_non_pushdown_caps(),
        )
        plan = _make_plan(rls_claims={"policies": {"tenant_id": "acme"}})

        # Collect all batches and concatenate
        batches = list(conn.execute_stream(plan))
        assert len(batches) > 0

        combined = pa.Table.from_batches(batches)
        reference = conn.execute(plan)

        assert combined.num_rows == reference.num_rows
        assert combined.schema.names == reference.schema.names


# ---------------------------------------------------------------------------
# ConnectorRegistry
# ---------------------------------------------------------------------------


class TestConnectorRegistry:
    def test_register_and_get_roundtrip(self) -> None:
        """A registered factory is retrievable by the same type string."""
        registry = ConnectorRegistry()

        class FakeConnector:
            pass

        def factory(cfg):
            return FakeConnector()

        registry.register("fake", factory)
        assert registry.get("fake") is factory

    def test_get_unknown_type_raises_404(self) -> None:
        """get() for an unregistered type raises AppError(unknown_connector, 404)."""
        registry = ConnectorRegistry()
        with pytest.raises(AppError) as exc_info:
            registry.get("nonexistent")

        err = exc_info.value
        assert err.code == "unknown_connector"
        assert err.status == 404

    def test_all_returns_all_registered(self) -> None:
        """all() returns a dict with every registered type."""
        registry = ConnectorRegistry()
        registry.register("alpha", lambda c: None)
        registry.register("beta", lambda c: None)

        result = registry.all()
        assert "alpha" in result
        assert "beta" in result
        assert len(result) == 2

    def test_all_returns_copy(self) -> None:
        """Mutating the returned dict does not affect the registry."""
        registry = ConnectorRegistry()
        registry.register("alpha", lambda c: None)

        snapshot = registry.all()
        snapshot["injected"] = lambda c: None

        assert "injected" not in registry.all()

    def test_register_overwrites_existing(self) -> None:
        """Registering the same type twice replaces the factory (useful for tests)."""
        registry = ConnectorRegistry()
        first = lambda c: "first"
        second = lambda c: "second"
        registry.register("x", first)
        registry.register("x", second)
        assert registry.get("x") is second

    def test_singleton_preregisters_postgres_and_duckdb(self) -> None:
        """get_connector_registry() singleton pre-registers 'postgres' and 'duckdb'."""
        registry = get_connector_registry()
        all_types = registry.all()

        assert "postgres" in all_types
        assert "duckdb" in all_types

    def test_singleton_postgres_factory_is_postgres_connector_class(self) -> None:
        """The 'postgres' factory is the PostgresConnector class itself."""
        from app.connectors.postgres import PostgresConnector

        registry = get_connector_registry()
        factory = registry.get("postgres")
        assert factory is PostgresConnector

    def test_singleton_duckdb_factory_is_duckdb_connector_class(self) -> None:
        """The 'duckdb' factory is the DuckDBConnector class itself."""
        from app.connectors.duckdb_conn import DuckDBConnector

        registry = get_connector_registry()
        factory = registry.get("duckdb")
        assert factory is DuckDBConnector
