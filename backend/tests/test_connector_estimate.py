"""Pre-run compute estimate — Connector.estimate() (workstream A8).

Covers the additive estimate scaffolding:
  * the base Connector default returns None (estimate unsupported),
  * DuckDBConnector.estimate() returns a best-effort row estimate via EXPLAIN,
  * an estimate never raises and never executes the query.
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef")

import pyarrow as pa

from app.connectors.duckdb_conn import DuckDBConnector
from app.connectors.cache_key import compute_cache_key
from app.connectors.plan import PhysicalPlan, QueryEstimate


def _plan(sql: str) -> PhysicalPlan:
    return PhysicalPlan(
        sql=sql,
        params=[],
        rls_claims={},
        cache_key=compute_cache_key(sql=sql, params=[], rls_claims={}),
    )


def test_duckdb_estimate_returns_rows_via_explain() -> None:
    conn = DuckDBConnector()
    conn.register({"t": pa.table({"x": list(range(100))})})

    est = conn.estimate(_plan("SELECT * FROM t"))

    assert isinstance(est, QueryEstimate)
    assert est.mechanism == "duckdb_explain"
    assert est.exact is False
    # EXPLAIN should surface a cardinality estimate for a 100-row scan.
    assert est.est_rows is not None and est.est_rows >= 1


def test_duckdb_estimate_does_not_execute_or_raise_on_bad_sql() -> None:
    conn = DuckDBConnector()
    # Unparseable/again-invalid SQL must yield None, never propagate an error.
    assert conn.estimate(_plan("SELECT * FROM no_such_table_xyz")) is None


def test_base_connector_estimate_defaults_to_none() -> None:
    # A connector that does not override estimate() reports "unsupported".
    class _Bare(DuckDBConnector):
        def estimate(self, plan):  # type: ignore[override]
            return super(DuckDBConnector, self).estimate(plan)

    assert _Bare().estimate(_plan("SELECT 1")) is None


# ---------------------------------------------------------------------------
# B3 — DuckDB CREATE SECRET single-quote escaping (SQL-injection hardening)
# ---------------------------------------------------------------------------


def test_s3_secret_escapes_single_quotes():
    """A connector config value with a single quote must be doubled, never able
    to break out of its quoted SQL literal in CREATE SECRET."""
    from app.connectors import duckdb_conn

    captured = {}

    class _FakeConn:
        def execute(self, sql, *a, **k):
            captured["sql"] = sql
            return self

    cfg = {
        "s3_key_id": "AKIA'); ATTACH 'evil.db'; --",
        "s3_secret": "sec'ret",
        "s3_scope": "s3://b/x'); DROP --",
        "s3_endpoint": "http://minio:9000",
    }
    duckdb_conn.setup_s3_httpfs(_FakeConn(), cfg)
    sql = captured["sql"]
    # Every single quote in each value is doubled, so the value stays inside its
    # quoted literal (the breakout `'` is escaped to `''`).
    assert "KEY_ID 'AKIA''); ATTACH ''evil.db''; --'" in sql
    assert "SECRET 'sec''ret'" in sql
    assert "SCOPE 's3://b/x''); DROP --'" in sql
    # Sanity: no value contains a lone (odd) single quote that closes the literal
    # early — within each literal, quotes come in escaped pairs.
    assert "sec'ret" not in sql.replace("''", "")
