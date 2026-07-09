"""SQL-injection via UNESCAPED IDENTIFIER / FILE-PATH interpolation (CRITICAL 2).

Distinct from ``test_sec_sql_injection.py`` (which covers the named-param /
{{ }} template-value path — always positionally bound). This file covers the
places where a user/author-influenced *identifier* or *file path* was
f-string-interpolated into raw SQL:

- ``app.connectors.preagg`` — rollup materialization SQL (table / column /
  measure interpolated into ``"..."`` identifiers and ``func(...)``).
- ``app.metrics.compile`` / ``app.routes.metrics`` — a metric ``base_table``
  fed to ``sqlglot.parse_one`` (which parses a whole UNION/SELECT) and, on the
  legacy path, into ``f"SELECT * FROM {base_table}"``.
- ``app.flows.incremental`` — ``materialized.target``/``env``/``time_column``/
  ``unique_key`` interpolated into single-quoted DuckDB string literals
  (``read_parquet('...')`` / ``COPY ... TO '...'``) and ``"..."`` identifiers.
- ``app.jobs.drift_sweep`` — a dataset key interpolated into
  ``DESCRIBE SELECT * FROM '<key>'``.

Each test asserts a quote-breakout / UNION payload is rejected or neutralised
(quotes doubled / value validated), never executed as SQL.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef")
os.environ.setdefault("ENV", "test")


# ===========================================================================
# 1. preagg rollup SQL — identifier quoting + measure-func allowlist
# ===========================================================================


class TestPreaggIdentifierInjection:
    def test_quote_in_table_name_is_escaped_not_broken_out(self):
        """A table name containing a double-quote must be doubled (escaped) so
        the whole name stays a single quoted identifier — the embedded quote
        can never terminate the identifier and start a new statement."""
        from app.connectors.preagg import build_rollup_sql

        evil = 'orders" ; DROP TABLE users; --'
        sql = build_rollup_sql(evil, ["region"], ["sum(amount)"], [])
        # The embedded quote is doubled → the whole name stays one identifier.
        assert '"orders"" ; DROP TABLE users; --"' in sql
        # There is exactly ONE opening and ONE closing identifier quote pair
        # around the table (no odd/unbalanced quoting that would let the tail
        # escape the literal). Count of '"' chars is even.
        assert sql.count('"') % 2 == 0

    def test_quote_in_dimension_is_escaped(self):
        from app.connectors.preagg import build_rollup_sql

        sql = build_rollup_sql("orders", ['region" , 1 AS x --'], ["sum(amount)"], [])
        assert '"region"" , 1 AS x --"' in sql

    def test_quote_in_measure_column_is_escaped(self):
        from app.connectors.preagg import _measure_select_sql

        out = _measure_select_sql('sum(amount") , (SELECT 1) --)')
        # The column is quoted with the embedded quote doubled — the injected
        # ") , (SELECT 1) --" stays INSIDE the quoted identifier.
        assert '"amount"") , (SELECT 1) --"' in out

    def test_bogus_agg_func_is_rejected(self):
        """The aggregate function is interpolated UNQUOTED (it is a call
        keyword), so it must be allowlisted — a bogus func raises ValueError."""
        from app.connectors.preagg import _measure_select_sql

        with pytest.raises(ValueError):
            _measure_select_sql("evilfunc(amount)")
        with pytest.raises(ValueError):
            # A non-additive func (avg) is also refused for a rollup.
            _measure_select_sql("avg(amount)")

    def test_malformed_measure_shape_is_rejected(self):
        from app.connectors.preagg import _measure_select_sql

        with pytest.raises(ValueError):
            _measure_select_sql("no_parens_here")

    def test_valid_rollup_sql_unchanged_for_plain_identifiers(self):
        """Legitimate names round-trip byte-identically (no behaviour change)."""
        from app.connectors.preagg import build_rollup_sql

        sql = build_rollup_sql("orders", ["region"], ["sum(amount)"], ["tenant_id"])
        assert sql == (
            'SELECT "tenant_id", "region", SUM("amount") AS "sum_amount" '
            'FROM "orders" GROUP BY "tenant_id", "region"'
        )


# ===========================================================================
# 2. metric base_table — UNION injection via sqlglot.parse_one
# ===========================================================================


class TestMetricBaseTableInjection:
    def _metric(self, base_table: str):
        from app.metrics.models import Dimension, Measure, MetricDefinition

        return MetricDefinition(
            id="m",
            name="M",
            measure=Measure(name="revenue", agg="sum", expr="amount"),
            base_table=base_table,
            dimensions=(Dimension(name="region"),),
        )

    def test_union_base_table_rejected_by_compile(self):
        """base_table = a UNION/SELECT must be rejected by the governance gate,
        NOT parsed into a FROM (... UNION ALL SELECT secrets ...)."""
        from app.metrics.compile import compile_metric
        from app.metrics.models import MetricError, MetricQuery

        m = self._metric("orders UNION ALL SELECT password, email, 1 FROM users --")
        mq = MetricQuery(metric_id="m", dimensions=("region",))
        with pytest.raises(MetricError) as exc:
            compile_metric(m, mq)
        assert exc.value.code == "bad_base_table"

    def test_union_base_table_rejected_by_write_gate(self):
        """The /metrics write-path validator rejects it too (covers the legacy
        f-string ``SELECT * FROM {base_table}`` persistence path)."""
        from app.metrics.models import MetricError
        from app.routes.metrics import _build_definition

        data = {
            "name": "m",
            "measure": {"name": "revenue", "agg": "sum", "expr": "amount"},
            "base_table": "orders; DROP TABLE users; --",
            "dimensions": [{"name": "region"}],
        }
        with pytest.raises(MetricError) as exc:
            _build_definition(data, metric_id="m")
        assert exc.value.code == "bad_base_table"

    def test_plain_base_table_still_compiles(self):
        """A bare identifier base_table is unaffected."""
        from app.metrics.compile import compile_metric
        from app.metrics.models import MetricQuery

        m = self._metric("orders")
        mq = MetricQuery(metric_id="m", dimensions=("region",))
        sql, _ = compile_metric(m, mq)
        assert "orders" in sql


# ===========================================================================
# 3. flows/incremental — quote-breakout in target/env + identifier in cols
# ===========================================================================


class TestIncrementalSanitizers:
    def test_single_quote_in_target_rejected(self):
        """A target with a single quote (no '..'/'/') passed the old traversal
        check untouched and would break out of ``COPY ... TO '...'``."""
        from app.errors import AppError
        from app.flows.incremental import _sanitize_target_segment

        with pytest.raises(AppError):
            _sanitize_target_segment("foo' ) TO '/tmp/x' ; --")

    def test_semicolon_and_paren_in_target_rejected(self):
        from app.errors import AppError
        from app.flows.incremental import _sanitize_target_segment

        with pytest.raises(AppError):
            _sanitize_target_segment("a');ATTACH 'x'")

    def test_quote_in_env_rejected(self):
        from app.errors import AppError
        from app.flows.incremental import _sanitize_env_segment

        with pytest.raises(AppError):
            _sanitize_env_segment("dev' UNION SELECT")

    def test_plain_target_and_env_unchanged(self):
        from app.flows.incremental import (
            _sanitize_env_segment,
            _sanitize_target_segment,
        )

        assert _sanitize_target_segment("sales/daily") == "sales/daily"
        assert _sanitize_target_segment("reports/2025/q4") == "reports/2025/q4"
        assert _sanitize_env_segment("prod") == "prod"

    def test_bad_time_column_identifier_rejected(self):
        from app.errors import AppError
        from app.flows.incremental import _max_time

        with pytest.raises(AppError):
            # Never reaches conn.execute — validated first.
            _max_time(object(), "__combined_src__", 'ts" ; DROP TABLE t --')


# ===========================================================================
# 4. drift_sweep — dataset_key breakout in DESCRIBE ... FROM '<key>'
# ===========================================================================


class TestDriftSweepDatasetKeyInjection:
    @pytest.mark.asyncio
    async def test_quote_in_dataset_key_refused_before_execute(self):
        """A dataset_key with a single quote must be refused (return None)
        WITHOUT ever building the ``DESCRIBE SELECT * FROM '<key>'`` SQL."""
        from unittest.mock import MagicMock, patch

        from app.jobs.drift_sweep import _fetch_live_columns

        # Force the catalog path to miss so we reach the DuckDB branch.
        with patch("app.jobs.drift_sweep.DuckDBConnector", create=True) as _ignored:
            connector = MagicMock()
            with patch(
                "app.connectors.duckdb_conn.DuckDBConnector", return_value=connector
            ):
                out = await _fetch_live_columns(
                    "org-a", "orders' ; DROP TABLE users; --"
                )
        assert out is None
        connector.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_plain_dataset_key_reaches_connector(self):
        """A safe key (path-like) is allowed through to introspection."""
        from unittest.mock import MagicMock, patch

        from app.jobs.drift_sweep import _fetch_live_columns

        connector = MagicMock()
        connector.execute.return_value = MagicMock(num_rows=0)
        with patch(
            "app.connectors.duckdb_conn.DuckDBConnector", return_value=connector
        ):
            await _fetch_live_columns("org-a", "raw/orders")
        # The safe key was NOT rejected — the connector was consulted.
        assert connector.execute.called
