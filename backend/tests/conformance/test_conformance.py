"""M1-C Conformance Suite — golden-fixture tests.

What this suite verifies
------------------------
For every case in ``CONFORMANCE_CASES``:

1. **Cache key** — ``plan(sql, claims).cache_key`` must equal the frozen
   ``expected_cache_key`` literal.  Any drift in the planner or cache-key
   algorithm immediately breaks this test.

2. **Arrow schema** — the result table's column names and Arrow type strings
   must match ``expected_schema`` exactly.

3. **Rows** — the result rows (as plain Python dicts) must match
   ``expected_rows`` after order-normalisation (both sides sorted by the
   same key sequence).  Floating-point values use ``pytest.approx`` with
   a tight relative tolerance (1e-9) to survive IEEE-754 rounding artefacts
   while still catching real computation errors.

4. **IPC round-trip** — the table is serialised to Arrow IPC stream bytes via
   ``table_to_ipc_bytes``, read back with ``pyarrow.ipc``, and compared
   byte-for-byte (schema + rows) against the original.  This validates the
   wire format used by the query endpoint.

5. **RLS security guard** — for the ``rls_tenant_filter`` case the test also
   explicitly asserts that ``globex`` rows are absent from the result.

6. **Cache-key spec regression guard** — the four test vectors documented in
   ``docs/cache-key-spec.md`` are re-computed with the live ``compute_cache_key``
   function and compared against the spec's expected values.  This links the
   spec document to the running code so that any algorithm change that breaks
   the spec vectors also breaks CI.

How to run
----------
From the ``backend/`` directory::

    python -m pytest tests/conformance -q

The suite requires no network access.  All data is seeded from the
``seeded_connector`` fixture in ``conftest.py``.

Rust executor conformance requirement
--------------------------------------
A future Rust/WASM executor MUST pass this suite unchanged (same inputs, same
expected outputs) before it can be considered a valid replacement for the Python
executor.  See ROADMAP §3.1 rule 4 and ``docs/conformance.md``.
"""

from __future__ import annotations

import io
from typing import Any

import pyarrow as pa
import pyarrow.ipc as pa_ipc
import pytest

from app.connectors.arrow_io import table_to_ipc_bytes
from app.connectors.cache_key import compute_cache_key
from app.connectors.duckdb_conn import DuckDBConnector
from app.connectors.planner import plan

from .cases import CONFORMANCE_CASES

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sort_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort a list of row dicts deterministically for order-independent comparison.

    Rows are sorted by the tuple of their values in sorted-key order so that
    queries without an ORDER BY clause can still be compared reliably.
    """
    if not rows:
        return rows

    sort_keys = sorted(rows[0].keys())

    def _key(row: dict[str, Any]) -> tuple:
        return tuple(
            # Replace None with "" for stable sort; use str() for mixed types.
            ("" if row[k] is None else str(row[k]))
            for k in sort_keys
        )

    return sorted(rows, key=_key)


def _table_to_rows(table: pa.Table) -> list[dict[str, Any]]:
    """Convert an Arrow table to a list of plain Python dicts."""
    return [
        {col: table.column(col)[i].as_py() for col in table.column_names}
        for i in range(table.num_rows)
    ]


def _approx_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Wrap float values in ``pytest.approx`` for tolerant comparison."""
    result = []
    for row in rows:
        wrapped = {}
        for k, v in row.items():
            if isinstance(v, float):
                wrapped[k] = pytest.approx(v, rel=1e-9)
            else:
                wrapped[k] = v
        result.append(wrapped)
    return result


# ---------------------------------------------------------------------------
# Main conformance tests — parametrised over CONFORMANCE_CASES
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", CONFORMANCE_CASES, ids=lambda c: c["id"])
class TestConformance:
    """Golden-fixture conformance tests for every registered case."""

    def test_cache_key(self, case: dict, seeded_connector: DuckDBConnector) -> None:
        """plan(sql, claims).cache_key must equal the frozen expected_cache_key."""
        physical_plan = plan(case["sql"], claims=case["claims"])
        assert physical_plan.cache_key == case["expected_cache_key"], (
            f"[{case['id']}] cache_key mismatch.\n"
            f"  got:      {physical_plan.cache_key}\n"
            f"  expected: {case['expected_cache_key']}\n"
            f"  rewritten sql: {physical_plan.sql}"
        )

    def test_schema(self, case: dict, seeded_connector: DuckDBConnector) -> None:
        """Result Arrow schema must match expected_schema (column names + types)."""
        physical_plan = plan(case["sql"], claims=case["claims"])
        result: pa.Table = seeded_connector.execute(physical_plan)

        actual_schema = {f.name: str(f.type) for f in result.schema}
        assert actual_schema == case["expected_schema"], (
            f"[{case['id']}] schema mismatch.\n"
            f"  got:      {actual_schema}\n"
            f"  expected: {case['expected_schema']}"
        )

    def test_rows(self, case: dict, seeded_connector: DuckDBConnector) -> None:
        """Rows (order-normalised) must match expected_rows.

        Floats are compared with ``pytest.approx(rel=1e-9)`` to tolerate
        IEEE-754 rounding while still catching real computation errors.
        """
        physical_plan = plan(case["sql"], claims=case["claims"])
        result: pa.Table = seeded_connector.execute(physical_plan)

        actual_rows = _sort_rows(_table_to_rows(result))
        expected_rows = _sort_rows(case["expected_rows"])

        assert _approx_rows(actual_rows) == _approx_rows(expected_rows), (
            f"[{case['id']}] row mismatch.\n"
            f"  got:      {actual_rows}\n"
            f"  expected: {expected_rows}"
        )

    def test_ipc_round_trip(self, case: dict, seeded_connector: DuckDBConnector) -> None:
        """table_to_ipc_bytes → parse → identical schema and rows (wire-format guard)."""
        physical_plan = plan(case["sql"], claims=case["claims"])
        original: pa.Table = seeded_connector.execute(physical_plan)

        # Serialise to IPC bytes (the wire format used by the query endpoint).
        raw_bytes: bytes = table_to_ipc_bytes(original)
        assert isinstance(raw_bytes, bytes), "table_to_ipc_bytes must return bytes"
        assert len(raw_bytes) > 0, "IPC bytes must not be empty"

        # Deserialise.
        reader = pa_ipc.open_stream(io.BytesIO(raw_bytes))
        round_tripped: pa.Table = reader.read_all()

        # Schema must be identical.
        assert round_tripped.schema == original.schema, (
            f"[{case['id']}] IPC round-trip schema mismatch.\n"
            f"  original: {original.schema}\n"
            f"  parsed:   {round_tripped.schema}"
        )

        # Rows must be identical (order-normalised, float-tolerant).
        original_rows = _sort_rows(_table_to_rows(original))
        rt_rows = _sort_rows(_table_to_rows(round_tripped))
        assert _approx_rows(original_rows) == _approx_rows(rt_rows), (
            f"[{case['id']}] IPC round-trip row mismatch.\n"
            f"  original: {original_rows}\n"
            f"  parsed:   {rt_rows}"
        )


# ---------------------------------------------------------------------------
# RLS security regression guard (dedicated test, not parametrised)
# ---------------------------------------------------------------------------


class TestRLSSecurityGuard:
    """Explicit security assertion: RLS must filter out non-matching tenant rows."""

    def test_acme_filter_excludes_globex(self, seeded_connector: DuckDBConnector) -> None:
        """Rows belonging to 'globex' must be absent when RLS claims tenant_id='acme'.

        This test is the multi-tenant security regression guard described in
        ROADMAP §3.1.  If the planner stops injecting the RLS predicate, globex
        rows will appear and this test will fail immediately.
        """
        claims = {"policies": {"tenant_id": "acme"}}
        physical_plan = plan("SELECT * FROM users", claims=claims)

        # Verify the predicate was actually injected into the SQL.
        assert "tenant_id" in physical_plan.sql.lower(), (
            "Planner must inject 'tenant_id' predicate into the rewritten SQL."
        )
        assert "acme" in physical_plan.sql, (
            "Planner must embed the claim value 'acme' into the rewritten SQL."
        )

        result: pa.Table = seeded_connector.execute(physical_plan)
        tenant_ids = result.column("tenant_id").to_pylist()

        # All returned rows must belong to acme.
        assert all(tid == "acme" for tid in tenant_ids), (
            f"All rows must have tenant_id='acme'.  Got: {tenant_ids}"
        )

        # globex rows (ids 4, 5, 6) must be absent.
        row_ids = result.column("id").to_pylist()
        globex_ids = {4, 5, 6}
        leaked = set(row_ids) & globex_ids
        assert not leaked, (
            f"RLS filter FAILED — globex row ids leaked: {leaked}"
        )

        # Sanity: we expect exactly 3 acme rows.
        assert result.num_rows == 3, (
            f"Expected 3 acme rows after RLS filter, got {result.num_rows}."
        )


# ---------------------------------------------------------------------------
# Cache-key spec regression guard (spec doc <-> live code)
# ---------------------------------------------------------------------------


class TestCacheKeySpecVectors:
    """Spec-doc test vectors must match the live compute_cache_key implementation.

    These are the four vectors documented in ``docs/cache-key-spec.md``.
    If the algorithm changes and the spec is not updated (or vice-versa), this
    test fails, forcing a conscious version bump and spec update.
    """

    def test_vector_1_simple_select(self) -> None:
        """Vector 1 — simple SELECT, no params, no RLS."""
        key = compute_cache_key(
            sql="SELECT id, name FROM users",
            params=[],
            rls_claims={},
        )
        assert key == "2da34f05b16152c531c0a460dc7b0ec722affc90998d44f4fa663b134f487054", (
            "cache-key-spec.md Vector 1 mismatch"
        )

    def test_vector_2_params_and_multi_rls(self) -> None:
        """Vector 2 — SELECT with params and multi-key RLS (non-policy claims excluded)."""
        key = compute_cache_key(
            sql="SELECT * FROM orders",
            params=[1, "active"],
            rls_claims={
                "sub": "user-123",
                "exp": 9999999999,
                "policies": {
                    "tenant_id": "acme",
                    "region": "us-east",
                },
            },
        )
        assert key == "fce38ca3a5d762ab04bfe02ce6f5cfc32dccb4b0ed294cf0feeb24865ed2fd31", (
            "cache-key-spec.md Vector 2 mismatch"
        )

    def test_vector_3_order_independence(self) -> None:
        """Vector 3 — policy key order must not affect the cache key."""
        key_a = compute_cache_key(
            sql="SELECT id FROM events",
            params=[],
            rls_claims={"policies": {"region": "us-east", "tenant_id": "acme"}},
        )
        key_b = compute_cache_key(
            sql="SELECT id FROM events",
            params=[],
            rls_claims={"policies": {"tenant_id": "acme", "region": "us-east"}},
        )
        expected = "1caaa6835929382919e423ee76bd14566ea8acb9d12b703e1876db27fd66a185"
        assert key_a == expected, "cache-key-spec.md Vector 3 (order A) mismatch"
        assert key_b == expected, "cache-key-spec.md Vector 3 (order B) mismatch"
        assert key_a == key_b, "Key must be order-independent"

    def test_vector_4_integer_policy(self) -> None:
        """Vector 4 — integer policy value serialises as a bare JSON number."""
        key = compute_cache_key(
            sql="SELECT * FROM logs WHERE level > 2",
            params=[2],
            rls_claims={"policies": {"org_id": 42}},
        )
        assert key == "fa39b9faa32aa1bf8763fe9adee97046388f3fe070f85c444f3defa17321e32c", (
            "cache-key-spec.md Vector 4 mismatch"
        )
