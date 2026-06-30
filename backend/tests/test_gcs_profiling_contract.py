"""Tests for three ingestion/connector features:

A. Native GCS query connector (duckdb_storage.py)
B. Column profiling (connectors/profiling.py + GET /datasets/{id}/profile)
C. Contract gate on scheduled file-ingest (file_ingest.py)

Strategy
--------
- A: Mock duckdb.connect to capture SQL — verify TYPE gcs secret is built
  correctly for HMAC keys and for the ADC (empty creds) path.
- B: Use a real DuckDB in-memory connector with a small Parquet fixture —
  assert null_rate / distinct_count / min / max values.
- C: Monkeypatch the existing _load_table_schema from routes/ingest to
  inject a stored schema; run handle() via the patched fixture from
  test_file_ingest and verify AppError(schema_incompatible) is raised on
  incompatible schema, and succeeds on compatible schema.
"""

from __future__ import annotations

import io
import os
import tempfile
from datetime import datetime, timezone
from typing import Any, BinaryIO
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

# ---------------------------------------------------------------------------
# A. GCS connector — SQL generation (no real network)
# ---------------------------------------------------------------------------

from app.connectors.duckdb_storage import (
    DuckDBStorageConnector,
    _detect_scheme,
    _get_gcs_creds,
    _register_gcs_secret,
    _install_httpfs,
)


class TestGCSSchemeDetection:
    """gs:// and gcs:// URIs are detected as GCS schemes."""

    def test_gs_scheme(self):
        assert _detect_scheme("gs://my-bucket/data.parquet") == "gs"

    def test_gcs_scheme(self):
        assert _detect_scheme("gcs://my-bucket/data.parquet") == "gcs"

    def test_s3_unaffected(self):
        assert _detect_scheme("s3://bucket/key") == "s3"

    def test_memory_unaffected(self):
        assert _detect_scheme(":memory:") is None


class TestGetGcsCreds:
    """_get_gcs_creds extracts HMAC keys from config and env vars."""

    def test_gcs_specific_config_keys(self):
        cfg = {
            "gcs_access_key_id": "GOOGA1",
            "gcs_secret": "hmac_secret_base64=",
        }
        creds = _get_gcs_creds(cfg)
        assert creds["key_id"] == "GOOGA1"
        assert creds["secret"] == "hmac_secret_base64="

    def test_aws_compat_config_keys(self):
        cfg = {
            "aws_access_key_id": "GOOGA2",
            "aws_secret_access_key": "aws_secret=",
        }
        creds = _get_gcs_creds(cfg)
        assert creds["key_id"] == "GOOGA2"
        assert creds["secret"] == "aws_secret="

    def test_env_fallback(self, monkeypatch):
        monkeypatch.setenv("GCS_ACCESS_KEY_ID", "env_key")
        monkeypatch.setenv("GCS_SECRET", "env_secret")
        creds = _get_gcs_creds({})
        assert creds["key_id"] == "env_key"
        assert creds["secret"] == "env_secret"

    def test_empty_returns_empty_strings(self, monkeypatch):
        for k in ["GCS_ACCESS_KEY_ID", "GCS_HMAC_KEY_ID", "AWS_ACCESS_KEY_ID",
                  "AWS_ACCESS_KEY", "GCS_SECRET", "GCS_HMAC_SECRET",
                  "AWS_SECRET_ACCESS_KEY", "AWS_SECRET_KEY"]:
            monkeypatch.delenv(k, raising=False)
        creds = _get_gcs_creds({})
        assert creds["key_id"] == ""
        assert creds["secret"] == ""

    def test_scope_extracted(self):
        cfg = {"gcs_access_key_id": "k", "gcs_secret": "s",
               "gcs_scope": "gs://my-bucket/datasets/org1/"}
        creds = _get_gcs_creds(cfg)
        assert creds["scope"] == "gs://my-bucket/datasets/org1/"


class TestRegisterGcsSecret:
    """_register_gcs_secret builds the correct DuckDB TYPE gcs SQL."""

    def _make_conn(self):
        m = MagicMock()
        m.execute = MagicMock()
        return m

    def _sqls(self, conn):
        return [c.args[0] for c in conn.execute.call_args_list if c.args]

    def test_hmac_creds_produce_type_gcs(self):
        conn = self._make_conn()
        _register_gcs_secret(conn, {"key_id": "GOOGABC", "secret": "s3cr3t=", "scope": ""})
        sql = self._sqls(conn)[0]
        assert "TYPE gcs" in sql
        assert "KEY_ID 'GOOGABC'" in sql
        assert "SECRET 's3cr3t='" in sql
        assert "CREATE OR REPLACE SECRET" in sql
        # No S3 endpoint — this is native GCS, not S3-compat workaround.
        assert "storage.googleapis.com" not in sql
        assert "ENDPOINT" not in sql

    def test_scope_clause_included(self):
        conn = self._make_conn()
        _register_gcs_secret(conn, {
            "key_id": "K", "secret": "S",
            "scope": "gs://my-bucket/datasets/org-1/",
        })
        sql = self._sqls(conn)[0]
        assert "SCOPE 'gs://my-bucket/datasets/org-1/'" in sql

    def test_adc_path_uses_credential_chain(self):
        """Empty key_id/secret → PROVIDER credential_chain (ADC path)."""
        conn = self._make_conn()
        _register_gcs_secret(conn, {"key_id": "", "secret": "", "scope": ""})
        sql = self._sqls(conn)[0]
        assert "TYPE gcs" in sql
        assert "PROVIDER credential_chain" in sql
        assert "KEY_ID" not in sql
        # "SECRET" appears in the CREATE OR REPLACE SECRET statement itself;
        # verify the *credential value* SECRET clause is absent (no KEY_ID/SECRET pair).
        assert "KEY_ID" not in sql
        # The secret value lines appear as "    SECRET '...'" — check no such indented line.
        assert "\n    SECRET '" not in sql

    def test_no_scope_no_scope_clause(self):
        conn = self._make_conn()
        _register_gcs_secret(conn, {"key_id": "K", "secret": "S", "scope": ""})
        sql = self._sqls(conn)[0]
        assert "SCOPE" not in sql


class TestFromConfigGcsRouting:
    """from_config with gs:// URI routes to for_gcs (native TYPE gcs secret)."""

    def test_gs_uri_uses_gcs_secret(self):
        executed: list[str] = []

        class _MockConn:
            def execute(self, sql: str, *_args):
                executed.append(sql)
                return MagicMock()

        with patch("duckdb.connect", return_value=_MockConn()):
            cfg = {
                "connector_type": "duckdb",
                "database": "gs://my-gcs-bucket/data.parquet",
                "gcs_access_key_id": "GOOGHMACKEY123",
                "gcs_secret": "hmac_secret_base64=",
            }
            connector = DuckDBStorageConnector.from_config(cfg)

        assert connector._is_cloud is True
        # httpfs must be installed and loaded.
        assert any("INSTALL httpfs" in s for s in executed)
        assert any("LOAD httpfs" in s for s in executed)
        # Native GCS secret (TYPE gcs), NOT TYPE s3.
        secret_sql = next((s for s in executed if "CREATE OR REPLACE SECRET" in s), None)
        assert secret_sql is not None, f"No CREATE SECRET in: {executed}"
        assert "TYPE gcs" in secret_sql
        assert "GOOGHMACKEY123" in secret_sql
        # Must NOT fall through to TYPE s3 / S3-compat endpoint.
        assert "TYPE s3" not in secret_sql
        assert "storage.googleapis.com" not in secret_sql

    def test_gs_uri_adc_fallback(self):
        """gs:// URI with empty creds uses PROVIDER credential_chain."""
        executed: list[str] = []

        class _MockConn:
            def execute(self, sql: str, *_args):
                executed.append(sql)
                return MagicMock()

        env_strip = {k: "" for k in [
            "GCS_ACCESS_KEY_ID", "GCS_HMAC_KEY_ID", "GCS_SECRET", "GCS_HMAC_SECRET",
            "AWS_ACCESS_KEY_ID", "AWS_ACCESS_KEY", "AWS_SECRET_ACCESS_KEY", "AWS_SECRET_KEY",
        ]}
        with patch("duckdb.connect", return_value=_MockConn()):
            with patch.dict(os.environ, env_strip, clear=False):
                cfg = {"connector_type": "duckdb", "database": "gs://bucket/data.parquet"}
                DuckDBStorageConnector.from_config(cfg)

        secret_sql = next((s for s in executed if "CREATE OR REPLACE SECRET" in s), None)
        assert secret_sql is not None
        assert "PROVIDER credential_chain" in secret_sql

    def test_s3_uri_still_uses_type_s3(self):
        """s3:// URIs continue to use TYPE s3 (regression guard)."""
        executed: list[str] = []

        class _MockConn:
            def execute(self, sql: str, *_args):
                executed.append(sql)
                return MagicMock()

        with patch("duckdb.connect", return_value=_MockConn()):
            cfg = {
                "connector_type": "duckdb",
                "database": "s3://bucket/data.parquet",
                "aws_access_key_id": "KEY",
                "aws_secret_access_key": "SECRET",
            }
            DuckDBStorageConnector.from_config(cfg)

        secret_sql = next((s for s in executed if "CREATE OR REPLACE SECRET" in s), None)
        assert secret_sql is not None
        assert "TYPE s3" in secret_sql
        assert "TYPE gcs" not in secret_sql


# ---------------------------------------------------------------------------
# B. Column profiling
# ---------------------------------------------------------------------------

from app.connectors.profiling import profile_parquet, profile_table, _sample_cap


def _make_parquet_bytes(rows: list[dict]) -> bytes:
    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(rows), buf)
    return buf.getvalue()


class TestColumnProfiling:
    """profile_table / profile_parquet return correct stats for a known fixture."""

    @pytest.fixture()
    def connector(self):
        return DuckDBStorageConnector.for_memory()

    @pytest.fixture()
    def small_table(self, connector):
        """Register a small table with known null distribution."""
        tbl = pa.table({
            "id":    pa.array([1, 2, 3, 4, 5], type=pa.int32()),
            "name":  pa.array(["a", "b", None, "d", "a"], type=pa.string()),
            "score": pa.array([1.0, 2.0, 3.0, 4.0, 5.0], type=pa.float64()),
        })
        connector.register({"test_tbl": tbl})
        return connector

    def test_null_rate_correct(self, small_table):
        result = profile_table("test_tbl", small_table)
        name_col = next(c for c in result["columns"] if c["name"] == "name")
        # 1 NULL out of 5 rows → null_rate = 0.2
        assert abs(name_col["null_rate"] - 0.2) < 1e-6

    def test_distinct_count_correct(self, small_table):
        result = profile_table("test_tbl", small_table)
        name_col = next(c for c in result["columns"] if c["name"] == "name")
        # "a", "b", "d" are distinct (NULL excluded from approx_count_distinct).
        assert name_col["distinct_count"] >= 3

    def test_min_max_correct(self, small_table):
        result = profile_table("test_tbl", small_table)
        id_col = next(c for c in result["columns"] if c["name"] == "id")
        assert id_col["min"] == "1"
        assert id_col["max"] == "5"

    def test_row_count_correct(self, small_table):
        result = profile_table("test_tbl", small_table)
        assert result["row_count"] == 5

    def test_no_null_row_gives_zero_null_rate(self, connector):
        tbl = pa.table({"x": pa.array([10, 20, 30], type=pa.int64())})
        connector.register({"t2": tbl})
        result = profile_table("t2", connector)
        x_col = result["columns"][0]
        assert x_col["null_rate"] == 0.0

    def test_all_null_gives_one_null_rate(self, connector):
        tbl = pa.table({"x": pa.array([None, None, None], type=pa.int64())})
        connector.register({"t3": tbl})
        result = profile_table("t3", connector)
        assert result["columns"][0]["null_rate"] == 1.0

    def test_profile_parquet_local_file(self, tmp_path, connector):
        """profile_parquet reads a local Parquet file correctly."""
        rows = [{"id": i, "val": str(i)} for i in range(10)]
        pq_bytes = _make_parquet_bytes(rows)
        p = tmp_path / "data.parquet"
        p.write_bytes(pq_bytes)

        result = profile_parquet(str(p), connector)
        assert result["row_count"] == 10
        assert len(result["columns"]) == 2
        id_col = next(c for c in result["columns"] if c["name"] == "id")
        assert id_col["null_rate"] == 0.0
        assert id_col["min"] == "0"
        assert id_col["max"] == "9"

    def test_profile_parquet_file_uri(self, tmp_path, connector):
        """profile_parquet strips file:// prefix."""
        rows = [{"n": 42}]
        p = tmp_path / "x.parquet"
        p.write_bytes(_make_parquet_bytes(rows))
        result = profile_parquet(f"file://{p}", connector)
        assert result["row_count"] == 1

    def test_sampled_flag_false_for_small_table(self, small_table):
        result = profile_table("test_tbl", small_table, sample_rows=1000)
        assert result["sampled"] is False

    def test_sampled_flag_true_when_cap_exceeded(self, connector):
        """When cap < row_count, sampled=True and only cap rows are scanned."""
        rows = [{"i": i} for i in range(50)]
        connector.register({"big": pa.Table.from_pylist(rows)})
        result = profile_table("big", connector, sample_rows=10)
        assert result["sampled"] is True
        assert result["sample_rows"] == 10

    def test_type_field_populated(self, small_table):
        result = profile_table("test_tbl", small_table)
        col = next(c for c in result["columns"] if c["name"] == "id")
        assert col["type"]  # non-empty string
        assert "INT" in col["type"].upper() or "int" in col["type"].lower()

    def test_sample_cap_env(self, monkeypatch):
        monkeypatch.setenv("NUBI_PROFILE_SAMPLE_ROWS", "500")
        assert _sample_cap() == 500

    def test_sample_cap_zero_env(self, monkeypatch):
        monkeypatch.setenv("NUBI_PROFILE_SAMPLE_ROWS", "0")
        assert _sample_cap() == 0


# ---------------------------------------------------------------------------
# C. Contract gate on file-ingest
# ---------------------------------------------------------------------------

import app.flows.handlers.file_ingest as fi
from app.connectors.base import FileConnectorMixin, FileStat, file_capabilities
from app.flows.executor import TaskContext
from app.flows.handlers.file_ingest import handle
from app.flows.loaders import LoadTarget
from app.lakehouse.managed import CentralStorage
from app.lakehouse.staging import StagingArea
from app.errors import AppError


class FakeFileConnector(FileConnectorMixin):
    """Simple in-memory file connector for contract gate tests."""

    def __init__(self, files: dict[str, tuple[bytes, datetime | None]]):
        self._files = dict(files)

    def capabilities(self):
        return file_capabilities(file_interface=True)

    def list_files(self, pattern, since=None):
        out = []
        for path, (data, mtime) in self._files.items():
            out.append(FileStat(path=path, size=len(data), mtime=mtime))
        return sorted(out, key=lambda f: f.path)

    def open(self, path: str) -> BinaryIO:
        return io.BytesIO(self._files[path][0])

    def move(self, src, dst):
        pass

    def delete(self, path):
        pass


def _parquet_bytes(rows):
    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(rows), buf)
    return buf.getvalue()


@pytest.fixture()
def ingest_patched(monkeypatch, tmp_path):
    """Patch the file_ingest resolution seams; yield a run() callable.

    run(connector, schema_override=None) → runs handle() with a simple
    promote target against local staging.  schema_override lets the test
    inject a stored schema into _load_table_schema.
    """
    final_dir = tmp_path / "final"
    staging_root = tmp_path / "staging"
    final_dir.mkdir()
    staging_root.mkdir()

    def _run(connector, *, stored_schema=None, rows=None):
        if rows is None:
            rows = [{"id": 1, "name": "Alice"}]
        pq_data = _parquet_bytes(rows)
        connector._files = {"inbound/data.parquet": (pq_data, None)}

        monkeypatch.setattr(fi, "_resolve_source_connector", lambda cid, org: connector)

        central = CentralStorage(scheme="file", bucket=str(staging_root), creds={})

        def _stage(org, rid):
            return StagingArea(central=central, org_id=org, run_id=rid)

        monkeypatch.setattr(fi, "_resolve_staging", _stage)

        from app.storage.local import LocalStorageClient

        client = LocalStorageClient(root=str(final_dir))

        def _final_key(staged_rel):
            leaf = staged_rel.rsplit("/", 1)[-1]
            return f"raw/orders/{leaf}"

        def _mk_target(cid, obj, org):
            t = LoadTarget(
                object_name=obj,
                capabilities=file_capabilities(file_interface=True),
            )
            t._promote_client = client
            t._final_key = _final_key
            return t

        monkeypatch.setattr(fi, "_resolve_target", _mk_target)

        # Inject stored schema via _load_table_schema.
        monkeypatch.setattr(
            "app.routes.ingest._load_table_schema",
            lambda org, ds, tbl: stored_schema,
        )

        ctx = TaskContext(
            inputs={},
            now=datetime(2026, 1, 1, tzinfo=timezone.utc),
            org_id="orgA",
            run_id="run1",
            watermark=None,
        )
        cfg = {
            "source": {"connector_id": "src1", "path": "inbound/*"},
            "format": "parquet",
            "inner_format": "csv",
            "target": {"connector_id": "tgt1", "object": "raw.orders"},
            "mode": "append",
            "incremental": {"strategy": "none"},
            "post_action": "none",
        }
        return handle(cfg, ctx, {})

    yield _run


class TestContractGateFileIngest:
    """File-ingest fails with schema_incompatible when the stored contract is violated."""

    def test_compatible_schema_succeeds(self, ingest_patched):
        """Incoming schema matches stored schema → ingest succeeds."""
        stored = [{"name": "id", "type": "int64"}, {"name": "name", "type": "string"}]
        connector = FakeFileConnector({})
        result = ingest_patched(connector, stored_schema=stored)
        assert result["files_ingested"] == 1
        assert result["rows_ingested"] == 1

    def test_no_stored_schema_skips_check(self, ingest_patched):
        """No stored schema (first ingest) → no error."""
        connector = FakeFileConnector({})
        result = ingest_patched(connector, stored_schema=None)
        assert result["files_ingested"] == 1

    def test_removed_column_fails_run(self, ingest_patched):
        """Incoming schema drops an existing column → AppError schema_incompatible."""
        # Stored schema has 3 columns; incoming (from Parquet) has only 2.
        stored = [
            {"name": "id", "type": "int64"},
            {"name": "name", "type": "string"},
            {"name": "extra", "type": "string"},  # this column will be absent in the file
        ]
        connector = FakeFileConnector({})
        with pytest.raises(AppError) as exc_info:
            ingest_patched(connector, stored_schema=stored)
        assert exc_info.value.code == "schema_incompatible"
        assert exc_info.value.status == 409

    def test_type_change_fails_run(self, ingest_patched, monkeypatch):
        """Incoming schema changes a column type → AppError schema_incompatible."""
        # Store schema says 'id' is BIGINT; Parquet will have int64.
        # We test the check directly with an incompatible type label.
        stored = [
            {"name": "id", "type": "DOUBLE"},   # type mismatch vs int64 from Parquet
            {"name": "name", "type": "string"},
        ]
        connector = FakeFileConnector({})
        with pytest.raises(AppError) as exc_info:
            ingest_patched(connector, stored_schema=stored)
        assert exc_info.value.code == "schema_incompatible"

    def test_additive_column_succeeds(self, ingest_patched):
        """Incoming schema ADDS a new column → allowed (additive evolution)."""
        # Stored schema has 1 column; incoming Parquet has 2 (adds 'name').
        stored = [{"name": "id", "type": "int64"}]
        connector = FakeFileConnector({})
        result = ingest_patched(connector, stored_schema=stored)
        assert result["files_ingested"] == 1

    def test_empty_run_skips_check(self, ingest_patched, monkeypatch):
        """No files staged (watermark filters all) → contract check is skipped."""
        stored = [{"name": "gone_column", "type": "string"}]

        check_called = []
        original = fi._contract_check

        def _spy(*args, **kwargs):
            check_called.append(True)
            return original(*args, **kwargs)

        monkeypatch.setattr(fi, "_contract_check", _spy)

        connector = FakeFileConnector({})
        # Patch list_files to return nothing so no files are staged.
        monkeypatch.setattr(connector, "list_files", lambda *a, **kw: [])

        result = ingest_patched(connector, stored_schema=stored)
        assert result["files_ingested"] == 0
        assert not check_called  # check was never called (no entries)
