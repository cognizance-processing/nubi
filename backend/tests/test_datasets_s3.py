"""Tests for S3/httpfs dataset browsing and query paths.

Strategy
--------
- Local parquet_path tests: write a real Parquet file to tmp_path and verify
  that _build_duckdb_connector + introspection + row-sampling work end-to-end
  (no network required).
- s3:// branch tests: use unittest.mock to intercept duckdb.connect and assert
  that setup_s3_httpfs builds the correct INSTALL/LOAD/SECRET SQL statements
  when env vars supply S3 credentials.  No actual network or MinIO is needed.

Coverage
--------
1.  Local parquet_path: _build_duckdb_connector returns a connector whose
    view_sql exposes the "dataset" table.
2.  Local parquet_path: introspecting the "dataset" table lists the right columns.
3.  Local parquet_path: row-sampling via SELECT * FROM dataset LIMIT 3 returns
    the expected data.
4.  s3:// branch: setup_s3_httpfs issues INSTALL httpfs, LOAD httpfs, and
    CREATE OR REPLACE SECRET with KEY_ID/SECRET/ENDPOINT from env vars.
5.  s3:// branch: setup_s3_httpfs skips SECRET creation when no key_id is
    resolvable (anonymous access).
6.  s3:// branch: _build_duckdb_connector calls setup_s3_httpfs when
    parquet_path starts with s3://.
7.  _register_datastore: when storage_uri is s3://, the stored parquet_path
    and view_sql use the s3:// URI, not the local /tmp path.
8.  _register_datastore: when storage_uri is file://, the local path is kept.
"""

from __future__ import annotations

import os
import uuid
from typing import Any
from unittest.mock import MagicMock, call, patch

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import pytest_asyncio

from app.connectors.duckdb_conn import setup_s3_httpfs
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo
from app.routes.data_browser import _build_duckdb_connector, _introspect_columns_duckdb, _introspect_tables_duckdb, _make_plan


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_local_parquet(tmp_path, *, rows: int = 5) -> str:
    """Write a small Parquet file to *tmp_path* and return its absolute path."""
    data = pa.table(
        {
            "id": pa.array(list(range(rows)), type=pa.int32()),
            "label": pa.array([f"item-{i}" for i in range(rows)], type=pa.string()),
            "score": pa.array([float(i) * 1.5 for i in range(rows)], type=pa.float64()),
        }
    )
    path = str(tmp_path / "test.parquet")
    pq.write_table(data, path)
    return path


# ---------------------------------------------------------------------------
# Local parquet_path tests
# ---------------------------------------------------------------------------


def test_local_parquet_build_connector_creates_dataset_view(tmp_path):
    """_build_duckdb_connector with a local parquet_path exposes 'dataset' table."""
    parquet_path = _make_local_parquet(tmp_path)
    cfg: dict[str, Any] = {
        "connector_type": "duckdb",
        "database": ":memory:",
        "view_sql": f"CREATE VIEW dataset AS SELECT * FROM read_parquet('{parquet_path}')",
        "parquet_path": parquet_path,
    }
    connector = _build_duckdb_connector(cfg)
    tables = _introspect_tables_duckdb(connector)
    names = {t["name"] for t in tables}
    assert "dataset" in names, f"Expected 'dataset' in {names}"


def test_local_parquet_columns_introspected(tmp_path):
    """Introspecting 'dataset' returns the expected column names and types."""
    parquet_path = _make_local_parquet(tmp_path)
    cfg: dict[str, Any] = {
        "connector_type": "duckdb",
        "database": ":memory:",
        "view_sql": f"CREATE VIEW dataset AS SELECT * FROM read_parquet('{parquet_path}')",
        "parquet_path": parquet_path,
    }
    connector = _build_duckdb_connector(cfg)
    columns = _introspect_columns_duckdb(connector, "dataset")
    col_names = {c["name"] for c in columns}
    assert {"id", "label", "score"}.issubset(col_names), f"Got columns: {col_names}"


def test_local_parquet_row_sampling_returns_data(tmp_path):
    """Row-sampling via SELECT * FROM dataset LIMIT 3 returns 3 rows."""
    parquet_path = _make_local_parquet(tmp_path, rows=5)
    cfg: dict[str, Any] = {
        "connector_type": "duckdb",
        "database": ":memory:",
        "view_sql": f"CREATE VIEW dataset AS SELECT * FROM read_parquet('{parquet_path}')",
        "parquet_path": parquet_path,
    }
    connector = _build_duckdb_connector(cfg)
    plan = _make_plan("SELECT * FROM dataset LIMIT 3")
    tbl = connector.execute(plan)
    assert tbl.num_rows == 3
    assert "id" in tbl.schema.names
    assert "label" in tbl.schema.names


# ---------------------------------------------------------------------------
# setup_s3_httpfs — SQL assertion tests (no network)
# ---------------------------------------------------------------------------


def test_setup_s3_httpfs_installs_and_loads_httpfs(monkeypatch):
    """setup_s3_httpfs executes INSTALL httpfs and LOAD httpfs on the connection."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "TESTKEY")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "TESTSECRET")
    monkeypatch.delenv("S3_ENDPOINT_URL", raising=False)
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)

    mock_conn = MagicMock()
    setup_s3_httpfs(mock_conn, cfg={})

    executed = [c.args[0] for c in mock_conn.execute.call_args_list]
    assert "INSTALL httpfs" in executed, f"INSTALL httpfs not called; got: {executed}"
    assert "LOAD httpfs" in executed, f"LOAD httpfs not called; got: {executed}"


def test_setup_s3_httpfs_creates_secret_with_env_creds(monkeypatch):
    """setup_s3_httpfs builds CREATE OR REPLACE SECRET with KEY_ID + SECRET from env."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "MYACCESSKEY")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "MYSECRETKEY")
    monkeypatch.delenv("S3_ENDPOINT_URL", raising=False)
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)

    mock_conn = MagicMock()
    setup_s3_httpfs(mock_conn, cfg={})

    executed = [c.args[0] for c in mock_conn.execute.call_args_list]
    secret_stmts = [s for s in executed if "CREATE OR REPLACE SECRET" in s]
    assert len(secret_stmts) == 1, f"Expected exactly one SECRET statement; got: {secret_stmts}"
    stmt = secret_stmts[0]
    assert "MYACCESSKEY" in stmt
    assert "MYSECRETKEY" in stmt


def test_setup_s3_httpfs_includes_endpoint_for_minio(monkeypatch):
    """setup_s3_httpfs includes ENDPOINT + URL_STYLE='path' for MinIO."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "minio-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "minio-secret")
    monkeypatch.setenv("S3_ENDPOINT_URL", "http://localhost:9000")

    mock_conn = MagicMock()
    setup_s3_httpfs(mock_conn, cfg={})

    executed = [c.args[0] for c in mock_conn.execute.call_args_list]
    secret_stmts = [s for s in executed if "CREATE OR REPLACE SECRET" in s]
    assert secret_stmts, "No SECRET statement created"
    stmt = secret_stmts[0]
    # Scheme should be stripped — only host:port
    assert "localhost:9000" in stmt
    assert "http://" not in stmt
    assert "URL_STYLE 'path'" in stmt


def test_setup_s3_httpfs_skips_secret_when_no_key(monkeypatch):
    """setup_s3_httpfs does NOT create a SECRET when no key_id is available."""
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    monkeypatch.delenv("S3_ACCESS_KEY", raising=False)
    monkeypatch.delenv("S3_SECRET_KEY", raising=False)
    monkeypatch.delenv("S3_ENDPOINT_URL", raising=False)
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)

    mock_conn = MagicMock()
    setup_s3_httpfs(mock_conn, cfg={})

    executed = [c.args[0] for c in mock_conn.execute.call_args_list]
    secret_stmts = [s for s in executed if "SECRET" in s.upper()]
    assert not secret_stmts, f"Expected no SECRET statement; got: {secret_stmts}"


def test_setup_s3_httpfs_prefers_cfg_over_env(monkeypatch):
    """setup_s3_httpfs prefers cfg keys over environment variables."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "env-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "env-secret")
    monkeypatch.delenv("S3_ENDPOINT_URL", raising=False)

    mock_conn = MagicMock()
    setup_s3_httpfs(mock_conn, cfg={
        "s3_key_id": "cfg-key",
        "s3_secret": "cfg-secret",
        "s3_endpoint": "http://minio.local:9000",
        "s3_region": "eu-west-1",
    })

    executed = [c.args[0] for c in mock_conn.execute.call_args_list]
    secret_stmts = [s for s in executed if "CREATE OR REPLACE SECRET" in s]
    assert secret_stmts, "No SECRET statement found"
    stmt = secret_stmts[0]
    assert "cfg-key" in stmt
    assert "cfg-secret" in stmt
    assert "minio.local:9000" in stmt
    assert "eu-west-1" in stmt
    # env-key must NOT appear
    assert "env-key" not in stmt


# ---------------------------------------------------------------------------
# _build_duckdb_connector — s3:// detection
# ---------------------------------------------------------------------------


def test_build_duckdb_connector_calls_setup_for_s3_parquet_path():
    """_build_duckdb_connector invokes setup_s3_httpfs when parquet_path is s3://."""
    cfg: dict[str, Any] = {
        "connector_type": "duckdb",
        "database": ":memory:",
        "parquet_path": "s3://mybucket/datasets/org1/ds1/data.parquet",
        "view_sql": "CREATE VIEW dataset AS SELECT * FROM read_parquet('s3://mybucket/datasets/org1/ds1/data.parquet')",
    }

    with patch("app.routes.data_browser.setup_s3_httpfs") as mock_setup:
        # view_sql will fail (no MinIO), but that's swallowed; we only care
        # that setup_s3_httpfs was called.
        try:
            _build_duckdb_connector(cfg)
        except Exception:
            pass
        mock_setup.assert_called_once()
        # The cfg dict and a duckdb connection are the two positional args
        call_args = mock_setup.call_args
        # First arg is the duckdb connection, second is cfg
        assert call_args[0][1] == cfg, (
            f"setup_s3_httpfs called with unexpected cfg: {call_args}"
        )


def test_build_duckdb_connector_no_s3_setup_for_local_path(tmp_path):
    """_build_duckdb_connector does NOT call setup_s3_httpfs for local paths."""
    parquet_path = _make_local_parquet(tmp_path)
    cfg: dict[str, Any] = {
        "connector_type": "duckdb",
        "database": ":memory:",
        "parquet_path": parquet_path,
        "view_sql": f"CREATE VIEW dataset AS SELECT * FROM read_parquet('{parquet_path}')",
    }

    with patch("app.routes.data_browser.setup_s3_httpfs") as mock_setup:
        _build_duckdb_connector(cfg)
        mock_setup.assert_not_called()


# ---------------------------------------------------------------------------
# _register_datastore — storage_uri selection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_datastore_uses_s3_uri_in_config():
    """_register_datastore stores the s3:// URI as parquet_path when provided."""
    from app.routes.datasets import _register_datastore  # noqa: PLC0415

    repo = InMemoryRepo()
    set_repo(repo)

    org_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    repo.seed_org_member(org_id=org_id, user_id=user_id)

    s3_uri = "s3://nubi-bucket/datasets/org1/ds1/data.parquet"
    local_path = "/tmp/nubi-datasets/datasets/org1/ds1/data.parquet"

    ds_id = await _register_datastore(
        org_id=org_id,
        user_id=user_id,
        name="s3-test",
        parquet_path=local_path,
        repo=repo,
        storage_uri=s3_uri,
    )

    ds = await repo.get("datastores", org_id, ds_id)
    assert ds is not None
    cfg = ds["config"]
    # The s3:// path must be stored (not the local /tmp path)
    assert cfg["parquet_path"] == s3_uri, f"Expected s3 URI, got: {cfg['parquet_path']}"
    assert s3_uri in cfg["view_sql"], f"s3 URI not in view_sql: {cfg['view_sql']}"
    assert local_path not in cfg["view_sql"], f"local_path leaked into view_sql"

    set_repo(None)


@pytest.mark.asyncio
async def test_register_datastore_keeps_local_path_when_no_s3_uri():
    """_register_datastore keeps the local path when storage_uri is file://."""
    from app.routes.datasets import _register_datastore  # noqa: PLC0415

    repo = InMemoryRepo()
    set_repo(repo)

    org_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    repo.seed_org_member(org_id=org_id, user_id=user_id)

    local_path = "/tmp/nubi-datasets/datasets/org1/ds2/data.parquet"
    file_uri = f"file://{local_path}"

    ds_id = await _register_datastore(
        org_id=org_id,
        user_id=user_id,
        name="local-test",
        parquet_path=local_path,
        repo=repo,
        storage_uri=file_uri,
    )

    ds = await repo.get("datastores", org_id, ds_id)
    assert ds is not None
    cfg = ds["config"]
    # Local path must be preserved (file:// is not an s3:// URI)
    assert cfg["parquet_path"] == local_path, f"Expected local path, got: {cfg['parquet_path']}"
    assert local_path in cfg["view_sql"]

    set_repo(None)


# ---------------------------------------------------------------------------
# Edge Case 1 — _sql_to_parquet calls setup_s3_httpfs for s3:// source SQL
# ---------------------------------------------------------------------------


def test_sql_to_parquet_calls_setup_s3_httpfs_for_s3_sql(tmp_path, monkeypatch):
    """_sql_to_parquet must invoke setup_s3_httpfs before executing SQL that
    references an s3:// path, so the httpfs extension is loaded and credentials
    are registered on the fresh in-memory DuckDB connection.

    We mock setup_s3_httpfs to avoid requiring a real MinIO/S3 endpoint and
    assert it was called with the correct arguments.  The subsequent DuckDB
    COPY will fail (no real S3), but we only need to confirm the setup call
    happens.
    """
    from unittest.mock import MagicMock, patch  # noqa: PLC0415

    from app.routes.datasets import _sql_to_parquet  # noqa: PLC0415

    parquet_out = str(tmp_path / "out.parquet")
    s3_sql = "SELECT * FROM read_parquet('s3://mybucket/data.parquet')"
    cfg = {"s3_key_id": "test-key", "s3_secret": "test-secret"}

    with patch("app.routes.datasets.setup_s3_httpfs", autospec=True) as mock_setup:  # noqa: SIM117
        # Simulate the httpfs call succeeding but COPY failing (no real S3).
        # We want to check setup was called before the error path.
        mock_setup.return_value = None
        try:
            _sql_to_parquet(
                sql=s3_sql,
                parquet_path=parquet_out,
                org_id="org1",
                user_id="user1",
                cfg=cfg,
            )
        except Exception:
            pass  # Expected — no real S3 available in tests.

    mock_setup.assert_called_once()
    call_cfg = mock_setup.call_args[0][1] if mock_setup.call_args[0] else mock_setup.call_args[1].get("cfg")
    # The cfg dict must be forwarded (not None) so org-level creds are used.
    assert call_cfg == cfg, f"Expected cfg={cfg!r} forwarded; got {call_cfg!r}"


def test_sql_to_parquet_no_s3_setup_for_local_sql(tmp_path):
    """_sql_to_parquet must NOT call setup_s3_httpfs for SQL that does not
    reference any s3:// path — the local path stays completely unchanged.
    """
    from unittest.mock import patch  # noqa: PLC0415

    import pyarrow as pa  # noqa: PLC0415
    import pyarrow.parquet as pq  # noqa: PLC0415

    from app.routes.datasets import _sql_to_parquet  # noqa: PLC0415

    # Write a small local parquet so DuckDB can actually run the query.
    src = str(tmp_path / "src.parquet")
    pq.write_table(pa.table({"x": pa.array([1, 2, 3])}), src)
    parquet_out = str(tmp_path / "out.parquet")

    with patch("app.routes.datasets.setup_s3_httpfs") as mock_setup:
        _sql_to_parquet(
            sql=f"SELECT * FROM read_parquet('{src}')",
            parquet_path=parquet_out,
            org_id="org1",
            user_id="user1",
        )
        mock_setup.assert_not_called()


def test_sql_to_parquet_s3_httpfs_failure_raises_clear_error(tmp_path, monkeypatch):
    """When httpfs fails to load for an s3:// SQL, _sql_to_parquet must raise
    a RuntimeError with a human-readable message instead of crashing with an
    obscure DuckDB exception.
    """
    from unittest.mock import patch  # noqa: PLC0415

    from app.routes.datasets import _sql_to_parquet  # noqa: PLC0415

    def _boom(conn, cfg=None):
        raise RuntimeError("IO Error: Extension httpfs not found")

    parquet_out = str(tmp_path / "out.parquet")

    with patch("app.routes.datasets.setup_s3_httpfs", side_effect=_boom):
        with pytest.raises(RuntimeError, match="Failed to load httpfs"):
            _sql_to_parquet(
                sql="SELECT * FROM read_parquet('s3://bucket/data.parquet')",
                parquet_path=parquet_out,
                org_id="org1",
                user_id="user1",
            )


# ---------------------------------------------------------------------------
# Edge Case 2 — _infer_schema_from_parquet threads cfg through to setup_s3_httpfs
# ---------------------------------------------------------------------------


def test_infer_schema_from_parquet_forwards_cfg_to_setup_s3_httpfs():
    """_infer_schema_from_parquet must forward the cfg dict to setup_s3_httpfs
    when the path is s3://, so org-level credentials are honoured instead of
    only the process environment.
    """
    from unittest.mock import MagicMock, patch  # noqa: PLC0415

    from app.routes.datasets import _infer_schema_from_parquet  # noqa: PLC0415

    cfg = {"s3_key_id": "org-key", "s3_secret": "org-secret", "s3_endpoint": "http://minio.local:9000"}

    with patch("app.routes.datasets.setup_s3_httpfs", autospec=True) as mock_setup:
        mock_setup.return_value = None
        # The DESCRIBE query will fail (no real S3) — that's fine; the function
        # returns [] on error.  We only verify setup was called with our cfg.
        _infer_schema_from_parquet("s3://mybucket/schema-test.parquet", cfg=cfg)

    mock_setup.assert_called_once()
    call_cfg = mock_setup.call_args[0][1] if mock_setup.call_args[0] else mock_setup.call_args[1].get("cfg")
    assert call_cfg == cfg, f"Expected cfg forwarded; got {call_cfg!r}"


def test_infer_schema_from_parquet_cfg_none_still_calls_setup_s3_httpfs():
    """When cfg=None (no org config), _infer_schema_from_parquet must still
    call setup_s3_httpfs for s3:// paths so env-var credentials are loaded.
    """
    from unittest.mock import patch  # noqa: PLC0415

    from app.routes.datasets import _infer_schema_from_parquet  # noqa: PLC0415

    with patch("app.routes.datasets.setup_s3_httpfs", autospec=True) as mock_setup:
        mock_setup.return_value = None
        _infer_schema_from_parquet("s3://mybucket/schema-test.parquet", cfg=None)

    mock_setup.assert_called_once()


def test_infer_schema_from_parquet_local_path_no_setup(tmp_path):
    """_infer_schema_from_parquet must NOT call setup_s3_httpfs for a local path."""
    from unittest.mock import patch  # noqa: PLC0415

    import pyarrow as pa  # noqa: PLC0415
    import pyarrow.parquet as pq  # noqa: PLC0415

    from app.routes.datasets import _infer_schema_from_parquet  # noqa: PLC0415

    src = str(tmp_path / "local.parquet")
    pq.write_table(pa.table({"a": pa.array([1, 2]), "b": pa.array(["x", "y"])}), src)

    with patch("app.routes.datasets.setup_s3_httpfs") as mock_setup:
        result = _infer_schema_from_parquet(src)
        mock_setup.assert_not_called()

    col_names = {c["name"] for c in result}
    assert {"a", "b"}.issubset(col_names), f"Unexpected schema: {result}"
