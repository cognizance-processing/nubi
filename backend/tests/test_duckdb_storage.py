"""Tests for DuckDBStorageConnector — local Parquet I/O + s3:// SQL generation.

Strategy
--------
Group A — LOCAL round-trip (no network, no MinIO required):
    Uses a ``tmp_path`` directory as a stand-in for a bucket.  DuckDB can
    read and write Parquet on the local filesystem without httpfs.

    A1. ``write_result`` produces a readable Parquet file.
    A2. ``read_parquet`` reads it back and the round-trip is lossless.
    A3. ``from_config`` with a local path opens the file in read-only mode.
    A4. ``for_memory`` → ``register`` → ``execute`` → plan returns correct rows.
    A5. ``write_result`` with a ``file://`` prefix URI is handled correctly.
    A6. RLS predicate (injected by the planner into plan.sql) is preserved
        through execute() — the connector NEVER strips predicates.

Group B — s3:// SQL generation (unit tests, no real network):
    Mocks ``duckdb.connect`` so no DuckDB is opened.  Verifies that
    ``for_s3`` issues the correct SQL statements:

    B1. ``INSTALL httpfs`` and ``LOAD httpfs`` are both called.
    B2. ``CREATE OR REPLACE SECRET`` contains the expected TYPE / KEY_ID /
        SECRET / REGION / USE_SSL / URL_STYLE / ENDPOINT values.
    B3. When ``endpoint`` starts with ``http://``, USE_SSL is ``false``.
    B4. When no ``endpoint`` is supplied, USE_SSL is ``true`` and no
        ENDPOINT clause is generated.
    B5. ``from_config`` with an ``s3://`` database URI routes to the S3 path
        (verified by checking the SQL statements, not actual S3 connectivity).

Group C — scheme detection:
    C1. ``:memory:`` / absent → None (local/in-memory).
    C2. ``s3://bucket/key`` → ``"s3"``.
    C3. ``/abs/path/file.duckdb`` → None (local).
    C4. ``s3a://bucket/key`` → ``"s3a"``.
    C5. ``gs://bucket/key`` → ``"gs"``.

All tests are synchronous (DuckDB execute is synchronous; the connector
itself is sync).  No asyncpg / FastAPI needed.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any
from unittest.mock import MagicMock, call, patch

import pyarrow as pa
import pytest

from app.connectors.duckdb_storage import (
    DuckDBStorageConnector,
    _detect_scheme,
    _get_creds,
    _install_httpfs,
    _register_s3_secret,
)
from app.connectors.plan import PhysicalPlan
from app.connectors.cache_key import compute_cache_key


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_plan(sql: str, rls_claims: dict | None = None) -> PhysicalPlan:
    """Build a minimal PhysicalPlan for testing."""
    if rls_claims is None:
        rls_claims = {}
    return PhysicalPlan(
        sql=sql,
        params=[],
        rls_claims=rls_claims,
        cache_key=compute_cache_key(sql=sql, params=[], rls_claims=rls_claims),
    )


def _small_table() -> pa.Table:
    """Return a small deterministic PyArrow table for round-trip tests."""
    return pa.table(
        {
            "id": pa.array([1, 2, 3], type=pa.int32()),
            "label": pa.array(["alpha", "beta", "gamma"], type=pa.string()),
            "amount": pa.array([10.5, 20.0, 30.75], type=pa.float64()),
        }
    )


# ---------------------------------------------------------------------------
# Group C — scheme detection (pure functions, no DuckDB)
# ---------------------------------------------------------------------------


class TestDetectScheme:
    """C1–C5: _detect_scheme returns the correct scheme for each URI form."""

    def test_c1_memory_returns_none(self):
        assert _detect_scheme(":memory:") is None

    def test_c1_empty_returns_none(self):
        assert _detect_scheme("") is None

    def test_c1_none_returns_none(self):
        assert _detect_scheme(None) is None  # type: ignore[arg-type]

    def test_c2_s3_scheme(self):
        assert _detect_scheme("s3://my-bucket/data/file.parquet") == "s3"

    def test_c3_abs_path_returns_none(self):
        assert _detect_scheme("/var/data/warehouse.duckdb") is None

    def test_c3_rel_path_returns_none(self):
        assert _detect_scheme("./data/local.duckdb") is None

    def test_c4_s3a_scheme(self):
        assert _detect_scheme("s3a://hadoop-bucket/key") == "s3a"

    def test_c5_gcs_scheme(self):
        assert _detect_scheme("gs://my-gcs-bucket/key") == "gs"


# ---------------------------------------------------------------------------
# Group A — Local round-trip
# ---------------------------------------------------------------------------


class TestLocalRoundTrip:
    """A1–A6: write and read Parquet locally; RLS predicate preservation."""

    def test_a4_for_memory_register_execute(self):
        """A4: in-memory connector registers and executes correctly."""
        connector = DuckDBStorageConnector.for_memory()
        connector.register({"test_tbl": _small_table()})
        plan = _make_plan("SELECT * FROM test_tbl")
        result = connector.execute(plan)
        assert result.num_rows == 3
        assert set(result.schema.names) == {"id", "label", "amount"}

    def test_a1_write_result_creates_parquet(self, tmp_path):
        """A1: write_result writes a Parquet file to a local path."""
        connector = DuckDBStorageConnector.for_memory()
        connector.register({"orders": _small_table()})

        dest = str(tmp_path / "out.parquet")
        returned_uri = connector.write_result("SELECT * FROM orders", dest)

        assert returned_uri == dest
        assert os.path.isfile(dest), "Parquet file was not created"

    def test_a2_read_parquet_round_trip(self, tmp_path):
        """A2: read_parquet reads back data written by write_result faithfully."""
        connector = DuckDBStorageConnector.for_memory()
        original = _small_table()
        connector.register({"orders": original})

        dest = str(tmp_path / "round_trip.parquet")
        connector.write_result("SELECT * FROM orders", dest)

        # Read back via the same connector instance.
        result = connector.read_parquet(dest)
        assert result.num_rows == original.num_rows
        assert set(result.schema.names) == set(original.schema.names)
        # Verify data fidelity on the 'id' column.
        assert result.column("id").to_pylist() == original.column("id").to_pylist()

    def test_a5_write_result_file_uri(self, tmp_path):
        """A5: write_result strips 'file://' from the dest_uri before DuckDB sees it."""
        connector = DuckDBStorageConnector.for_memory()
        connector.register({"t": _small_table()})

        abs_path = str(tmp_path / "via_file_uri.parquet")
        file_uri = f"file://{abs_path}"
        returned = connector.write_result("SELECT * FROM t", file_uri)
        # Returns the original uri (with file://)
        assert returned == file_uri
        # But the file must be written at the bare path.
        assert os.path.isfile(abs_path), "File was not written when uri had file:// prefix"

    def test_a3_from_config_local_path(self, tmp_path):
        """A3: from_config with a local .duckdb file opens it read-only."""
        import duckdb  # noqa: PLC0415

        # Create a real .duckdb file with a table.
        db_path = str(tmp_path / "test.duckdb")
        setup_conn = duckdb.connect(database=db_path)
        setup_conn.execute(
            "CREATE TABLE widgets (id INTEGER, name TEXT); "
            "INSERT INTO widgets VALUES (1, 'bolt'), (2, 'nut');"
        )
        setup_conn.close()

        # Open via from_config (read-only).
        cfg = {"connector_type": "duckdb", "database": db_path}
        connector = DuckDBStorageConnector.from_config(cfg)
        plan = _make_plan("SELECT * FROM widgets")
        result = connector.execute(plan)
        assert result.num_rows == 2
        names = result.column("name").to_pylist()
        assert set(names) == {"bolt", "nut"}

    def test_a6_rls_predicate_preserved(self):
        """A6: RLS predicates injected into plan.sql are executed unchanged."""
        connector = DuckDBStorageConnector.for_memory()
        # Seed a table with a tenant_id column.
        tbl = pa.table(
            {
                "id": pa.array([1, 2, 3], type=pa.int32()),
                "tenant_id": pa.array(["acme", "acme", "other"], type=pa.string()),
                "value": pa.array([100, 200, 300], type=pa.float64()),
            }
        )
        connector.register({"events": tbl})

        # Simulate what the planner emits after RLS injection:
        # "SELECT * FROM events WHERE tenant_id = 'acme'"
        plan = _make_plan(
            "SELECT * FROM events WHERE tenant_id = 'acme'",
            rls_claims={"policies": {"tenant_id": "acme"}},
        )
        result = connector.execute(plan)
        # Only rows for 'acme' (2 rows) should be returned.
        assert result.num_rows == 2
        tenant_values = result.column("tenant_id").to_pylist()
        assert all(v == "acme" for v in tenant_values)

    def test_a_execute_stream(self):
        """execute_stream yields record batches that compose to the full table."""
        connector = DuckDBStorageConnector.for_memory()
        connector.register({"s": _small_table()})
        plan = _make_plan("SELECT * FROM s")
        batches = list(connector.execute_stream(plan))
        combined = pa.Table.from_batches(batches)
        assert combined.num_rows == 3

    def test_a_read_parquet_subdirectory(self, tmp_path):
        """write_result creates nested directories as needed."""
        connector = DuckDBStorageConnector.for_memory()
        connector.register({"t": _small_table()})
        dest = str(tmp_path / "nested" / "dir" / "out.parquet")
        connector.write_result("SELECT * FROM t", dest)
        assert os.path.isfile(dest)


# ---------------------------------------------------------------------------
# Group B — s3:// SQL generation (mock DuckDB, no real network)
# ---------------------------------------------------------------------------


class TestS3SqlGeneration:
    """B1–B5: verify httpfs + CREATE SECRET SQL without a real S3 endpoint."""

    def _make_mock_conn(self) -> MagicMock:
        """Return a mock DuckDB connection that records execute() calls."""
        mock = MagicMock()
        mock.execute = MagicMock()
        return mock

    def _install_calls(self, mock_conn: MagicMock) -> list[str]:
        """Extract all SQL strings passed to mock_conn.execute()."""
        return [c.args[0] for c in mock_conn.execute.call_args_list if c.args]

    def test_b1_httpfs_install_and_load(self):
        """B1: _install_httpfs calls INSTALL httpfs and LOAD httpfs in order."""
        conn = self._make_mock_conn()
        _install_httpfs(conn)
        calls = self._install_calls(conn)
        assert any("INSTALL httpfs" in s for s in calls), f"INSTALL httpfs missing: {calls}"
        assert any("LOAD httpfs" in s for s in calls), f"LOAD httpfs missing: {calls}"
        # INSTALL must come before LOAD
        install_idx = next(i for i, s in enumerate(calls) if "INSTALL httpfs" in s)
        load_idx = next(i for i, s in enumerate(calls) if "LOAD httpfs" in s)
        assert install_idx < load_idx, "INSTALL httpfs must precede LOAD httpfs"

    def test_b2_create_secret_contains_required_fields(self):
        """B2: _register_s3_secret emits TYPE s3, KEY_ID, SECRET, REGION, URL_STYLE."""
        conn = self._make_mock_conn()
        creds = {
            "key_id": "AKIAIOSFODNN7EXAMPLE",
            "secret": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "region": "us-east-1",
            "endpoint": "http://localhost:9000",
            "url_style": "path",
        }
        _register_s3_secret(conn, creds)
        calls = self._install_calls(conn)
        assert len(calls) == 1
        sql = calls[0]
        assert "CREATE OR REPLACE SECRET" in sql
        assert "TYPE s3" in sql
        assert "KEY_ID 'AKIAIOSFODNN7EXAMPLE'" in sql
        assert "SECRET 'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY'" in sql
        assert "REGION 'us-east-1'" in sql
        assert "URL_STYLE 'path'" in sql
        assert "ENDPOINT 'localhost:9000'" in sql

    def test_b3_http_endpoint_sets_use_ssl_false(self):
        """B3: http:// endpoint → USE_SSL false."""
        conn = self._make_mock_conn()
        creds = {
            "key_id": "k", "secret": "s", "region": "us-east-1",
            "endpoint": "http://minio.local:9000", "url_style": "path",
        }
        _register_s3_secret(conn, creds)
        sql = self._install_calls(conn)[0]
        assert "USE_SSL false" in sql
        assert "ENDPOINT 'minio.local:9000'" in sql

    def test_b4_no_endpoint_use_ssl_true_no_endpoint_clause(self):
        """B4: no endpoint → USE_SSL true, no ENDPOINT clause."""
        conn = self._make_mock_conn()
        creds = {
            "key_id": "k", "secret": "s", "region": "us-east-1",
            "endpoint": "", "url_style": "vhost",
        }
        _register_s3_secret(conn, creds)
        sql = self._install_calls(conn)[0]
        assert "USE_SSL true" in sql
        assert "ENDPOINT" not in sql

    def test_b5_from_config_s3_uri_calls_httpfs(self, tmp_path):
        """B5: from_config with s3:// database URI routes to the S3 code path.

        We intercept duckdb.connect to get the connection mock and verify
        the INSTALL/LOAD/CREATE SECRET SQL statements are generated.
        """
        executed_sqls: list[str] = []

        class _MockRel:
            def arrow(self) -> pa.Table:
                return pa.table({"x": pa.array([1])})
            def read_all(self) -> pa.Table:
                return self.arrow()

        class _MockConn:
            def execute(self, sql: str, *args: Any) -> "_MockRel":
                executed_sqls.append(sql)
                return _MockRel()

        with patch("duckdb.connect", return_value=_MockConn()):
            cfg = {
                "connector_type": "duckdb",
                "database": "s3://my-bucket/warehouse.duckdb",
                "aws_access_key_id": "TESTKEY",
                "aws_secret_access_key": "TESTSECRET",
                "s3_endpoint": "http://localhost:9000",
                "s3_url_style": "path",
            }
            connector = DuckDBStorageConnector.from_config(cfg)

        assert connector._is_cloud is True
        assert any("INSTALL httpfs" in s for s in executed_sqls), (
            f"INSTALL httpfs not found in: {executed_sqls}"
        )
        assert any("LOAD httpfs" in s for s in executed_sqls), (
            f"LOAD httpfs not found in: {executed_sqls}"
        )
        assert any("CREATE OR REPLACE SECRET" in s for s in executed_sqls), (
            f"CREATE OR REPLACE SECRET not found in: {executed_sqls}"
        )
        # Credentials must be embedded.
        secret_sql = next(s for s in executed_sqls if "CREATE OR REPLACE SECRET" in s)
        assert "TESTKEY" in secret_sql
        assert "TESTSECRET" in secret_sql


# ---------------------------------------------------------------------------
# Group B — _get_creds env-var fallback
# ---------------------------------------------------------------------------


class TestGetCreds:
    """_get_creds correctly prefers config over env, and env over default."""

    def test_config_keys_take_precedence(self):
        """Config values override env vars."""
        config = {
            "aws_access_key_id": "cfg_key",
            "aws_secret_access_key": "cfg_secret",
            "aws_region": "eu-west-1",
        }
        with patch.dict(os.environ, {"AWS_ACCESS_KEY_ID": "env_key"}, clear=False):
            creds = _get_creds(config)
        assert creds["key_id"] == "cfg_key"
        assert creds["secret"] == "cfg_secret"
        assert creds["region"] == "eu-west-1"

    def test_env_var_fallback(self):
        """When config is empty, credentials are sourced from env vars."""
        with patch.dict(
            os.environ,
            {
                "AWS_ACCESS_KEY_ID": "env_k",
                "AWS_SECRET_ACCESS_KEY": "env_s",
                "AWS_DEFAULT_REGION": "ap-southeast-1",
                "S3_ENDPOINT_URL": "http://minio.test:9000",
            },
            clear=False,
        ):
            creds = _get_creds({})
        assert creds["key_id"] == "env_k"
        assert creds["secret"] == "env_s"
        assert creds["region"] == "ap-southeast-1"
        assert creds["endpoint"] == "http://minio.test:9000"

    def test_defaults_when_nothing_set(self):
        """Defaults are applied when neither config nor env has a value."""
        # Strip relevant env vars for this test.
        env_strip = {
            k: ""
            for k in [
                "AWS_ACCESS_KEY_ID", "AWS_ACCESS_KEY",
                "AWS_SECRET_ACCESS_KEY", "AWS_SECRET_KEY",
                "AWS_DEFAULT_REGION", "AWS_REGION",
                "S3_ENDPOINT_URL",
            ]
        }
        with patch.dict(os.environ, env_strip, clear=False):
            creds = _get_creds({})
        # Empty strings for key/secret (no default); region defaults to us-east-1.
        assert creds["region"] == "us-east-1"
        assert creds["endpoint"] == ""


# ---------------------------------------------------------------------------
# Group A — from_config with in-memory (no database key)
# ---------------------------------------------------------------------------


class TestFromConfigInMemory:
    """from_config without a database path creates an in-memory connector."""

    def test_no_database_key(self):
        cfg = {"connector_type": "duckdb"}
        connector = DuckDBStorageConnector.from_config(cfg)
        connector.register({"demo": _small_table()})
        result = connector.execute(_make_plan("SELECT COUNT(*) AS n FROM demo"))
        assert result.num_rows == 1
        assert result.column("n").to_pylist()[0] == 3

    def test_memory_explicit(self):
        cfg = {"connector_type": "duckdb", "database": ":memory:"}
        connector = DuckDBStorageConnector.from_config(cfg)
        assert connector._is_cloud is False

    def test_capabilities_complete(self):
        """capabilities() returns all 7 required keys with bool values."""
        connector = DuckDBStorageConnector.for_memory()
        caps = connector.capabilities()
        required = {
            "native_arrow", "predicate_pushdown", "projection_pushdown",
            "partition_pushdown", "predicate_rls", "column_masking", "streaming_cdc",
        }
        assert required == set(caps.keys())
        assert all(isinstance(v, bool) for v in caps.values())
