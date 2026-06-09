"""Real MinIO round-trip tests for StorageConnectorAgent deliverables.

These tests require a live MinIO instance at http://localhost:9000 with
credentials minioadmin/minioadmin and a bucket named 'nubi'.

They are skipped automatically when:
- MinIO is not reachable (no S3_ENDPOINT_URL env var or service down), OR
- boto3/duckdb is not installed.

Run with the live MinIO env:
    S3_ACCESS_KEY=minioadmin S3_SECRET_KEY=minioadmin \\
    S3_ENDPOINT_URL=http://localhost:9000 S3_REGION=us-east-1 \\
    .venv-backend/bin/python -m pytest backend/tests/test_storage_minio.py -v

Coverage
--------
1. Multi-table S3 connector (s3_views dict): write 2 parquet files to
   s3://nubi/..., build a connector from an s3_views config, assert SHOW
   TABLES lists both tables, assert a query returns rows from each table.

2. Multi-statement view_sql connector: same parquet files, build connector
   via a semicolon-separated view_sql string, same assertions.

3. Storage→MinIO upload: use app.storage._get_storage_client() (auto-detect
   via S3_ENDPOINT_URL) to upload raw bytes, then read them back.

4. _cfg_references_s3 detects s3_views dict values.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Skip guard: only run when MinIO env is present and service is reachable
# ---------------------------------------------------------------------------

_S3_ENDPOINT = os.getenv("S3_ENDPOINT_URL", "")
_S3_KEY = os.getenv("S3_ACCESS_KEY", "") or os.getenv("AWS_ACCESS_KEY_ID", "")

_SKIP_REASON = (
    "Skipped: S3_ENDPOINT_URL and S3_ACCESS_KEY must be set and MinIO must be reachable. "
    "Run with: S3_ACCESS_KEY=minioadmin S3_SECRET_KEY=minioadmin "
    "S3_ENDPOINT_URL=http://localhost:9000 S3_REGION=us-east-1"
)


def _boto3_available() -> bool:
    """Return True when boto3 is installed."""
    try:
        import boto3  # noqa: PLC0415, F401

        return True
    except ImportError:
        return False


def _minio_reachable() -> bool:
    """Return True when MinIO is reachable at the configured endpoint (no boto3 needed).

    DuckDB httpfs talks to MinIO directly without boto3, so we only check that
    the env vars are set and the health endpoint responds.
    """
    if not _S3_ENDPOINT or not _S3_KEY:
        return False
    try:
        import urllib.request  # noqa: PLC0415

        req = urllib.request.urlopen(_S3_ENDPOINT + "/minio/health/live", timeout=2)
        return req.status == 200
    except Exception:
        # Any failure (connection refused, timeout, wrong path) → not available
        return False


requires_minio = pytest.mark.skipif(
    not _minio_reachable(),
    reason=_SKIP_REASON,
)

requires_boto3 = pytest.mark.skipif(
    not _boto3_available(),
    reason="Skipped: boto3 is not installed (needed for S3StorageClient upload tests)",
)

# ---------------------------------------------------------------------------
# Helper: build S3 creds dict from env
# ---------------------------------------------------------------------------


def _s3_cfg() -> dict[str, Any]:
    """Return a connector config dict for the live MinIO instance."""
    return {
        "s3_key_id": os.getenv("S3_ACCESS_KEY", "") or os.getenv("AWS_ACCESS_KEY_ID", ""),
        "s3_secret": os.getenv("S3_SECRET_KEY", "") or os.getenv("AWS_SECRET_ACCESS_KEY", ""),
        "s3_endpoint": os.getenv("S3_ENDPOINT_URL", ""),
        "s3_region": os.getenv("S3_REGION", "us-east-1"),
    }


# ---------------------------------------------------------------------------
# Fixture: write two Parquet files to MinIO and yield their S3 URIs
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def two_s3_parquets():
    """Write 2 real Parquet files to MinIO via DuckDB COPY and yield their URIs.

    Yields
    ------
    tuple[str, str]
        ``(uri_a, uri_b)`` — the ``s3://nubi/...`` URIs of the two test parquet
        files.  The files are cleaned up after the module finishes.
    """
    import duckdb  # noqa: PLC0415

    import sys  # noqa: PLC0415
    import os as _os  # noqa: PLC0415

    sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), ".."))
    from app.connectors.duckdb_conn import setup_s3_httpfs  # noqa: PLC0415

    run_id = str(uuid.uuid4())[:8]
    prefix = f"test/storage_minio/{run_id}"
    uri_a = f"s3://nubi/{prefix}/table_a.parquet"
    uri_b = f"s3://nubi/{prefix}/table_b.parquet"

    conn = duckdb.connect(":memory:")
    setup_s3_httpfs(conn, _s3_cfg())

    # Write table_a: id, name
    conn.execute(
        f"COPY (SELECT i AS id, 'name_' || CAST(i AS VARCHAR) AS name "
        f"FROM range(5) t(i)) "
        f"TO '{uri_a}' (FORMAT PARQUET)"
    )
    # Write table_b: product, revenue
    conn.execute(
        f"COPY (SELECT 'prod_' || CAST(i AS VARCHAR) AS product, "
        f"CAST(i * 100.0 AS DOUBLE) AS revenue "
        f"FROM range(3) t(i)) "
        f"TO '{uri_b}' (FORMAT PARQUET)"
    )

    yield uri_a, uri_b

    # Cleanup: delete from MinIO via boto3
    try:
        import boto3  # noqa: PLC0415

        creds = _s3_cfg()
        s3 = boto3.client(
            "s3",
            aws_access_key_id=creds["s3_key_id"],
            aws_secret_access_key=creds["s3_secret"],
            endpoint_url=creds["s3_endpoint"],
            region_name=creds["s3_region"],
        )
        for uri in (uri_a, uri_b):
            key = uri.replace("s3://nubi/", "")
            try:
                s3.delete_object(Bucket="nubi", Key=key)
            except Exception:
                pass
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# Test 1: Multi-table S3 connector via s3_views dict
# ---------------------------------------------------------------------------


@requires_minio
def test_multi_table_s3_connector_via_s3_views_dict(two_s3_parquets):
    """Build a connector from s3_views dict; SHOW TABLES lists both; queries return rows."""
    import sys  # noqa: PLC0415
    import os as _os  # noqa: PLC0415

    sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), ".."))
    from app.routes.data_browser import (  # noqa: PLC0415
        _build_duckdb_connector,
        _introspect_tables_duckdb,
        _make_plan,
    )

    uri_a, uri_b = two_s3_parquets

    cfg: dict[str, Any] = {
        "connector_type": "duckdb",
        "database": ":memory:",
        "s3_views": {
            "table_a": uri_a,
            "table_b": uri_b,
        },
        **_s3_cfg(),
    }

    connector = _build_duckdb_connector(cfg)

    # SHOW TABLES should list both
    tables = _introspect_tables_duckdb(connector)
    table_names = {t["name"] for t in tables}
    assert "table_a" in table_names, f"table_a missing from {table_names}"
    assert "table_b" in table_names, f"table_b missing from {table_names}"

    # Query table_a
    result_a = connector.execute(_make_plan("SELECT * FROM table_a ORDER BY id"))
    assert result_a.num_rows == 5
    assert "id" in result_a.schema.names
    assert "name" in result_a.schema.names

    # Query table_b
    result_b = connector.execute(_make_plan("SELECT * FROM table_b ORDER BY product"))
    assert result_b.num_rows == 3
    assert "product" in result_b.schema.names
    assert "revenue" in result_b.schema.names


# ---------------------------------------------------------------------------
# Test 2: Multi-table S3 connector via multi-statement view_sql
# ---------------------------------------------------------------------------


@requires_minio
def test_multi_table_s3_connector_via_multi_statement_view_sql(two_s3_parquets):
    """Build connector from semicolon-separated view_sql; both tables accessible."""
    import sys  # noqa: PLC0415
    import os as _os  # noqa: PLC0415

    sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), ".."))
    from app.routes.data_browser import (  # noqa: PLC0415
        _build_duckdb_connector,
        _introspect_tables_duckdb,
        _make_plan,
    )

    uri_a, uri_b = two_s3_parquets

    view_sql = (
        f"CREATE OR REPLACE VIEW table_a AS SELECT * FROM read_parquet('{uri_a}');\n"
        f"CREATE OR REPLACE VIEW table_b AS SELECT * FROM read_parquet('{uri_b}')"
    )

    cfg: dict[str, Any] = {
        "connector_type": "duckdb",
        "database": ":memory:",
        "view_sql": view_sql,
        **_s3_cfg(),
    }

    connector = _build_duckdb_connector(cfg)

    tables = _introspect_tables_duckdb(connector)
    table_names = {t["name"] for t in tables}
    assert "table_a" in table_names, f"table_a missing from {table_names}"
    assert "table_b" in table_names, f"table_b missing from {table_names}"

    result_a = connector.execute(_make_plan("SELECT COUNT(*) AS cnt FROM table_a"))
    assert result_a.to_pydict()["cnt"][0] == 5

    result_b = connector.execute(_make_plan("SELECT COUNT(*) AS cnt FROM table_b"))
    assert result_b.to_pydict()["cnt"][0] == 3


# ---------------------------------------------------------------------------
# Test 3: Storage→MinIO upload via app.storage auto-detect
# ---------------------------------------------------------------------------


@requires_minio
@requires_boto3
def test_storage_client_auto_detect_minio_upload_download():
    """app.storage auto-detects MinIO from S3_ENDPOINT_URL and round-trips bytes."""
    import sys  # noqa: PLC0415
    import os as _os  # noqa: PLC0415

    sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), ".."))

    # Import the factory function directly from datasets (where auto-detect lives)
    from app.routes.datasets import _get_storage_client  # noqa: PLC0415

    run_id = str(uuid.uuid4())[:8]
    key = f"test/storage_minio/{run_id}/hello.txt"
    payload = f"nubi-storage-test-{run_id}".encode()

    client = _get_storage_client()

    # Upload
    uri = client.upload_bytes(payload, key)
    assert uri.startswith("s3://"), f"Expected s3:// URI, got: {uri!r}"

    # Download and verify
    downloaded = client.download_bytes(key)
    assert downloaded == payload, f"Round-trip mismatch: {downloaded!r} != {payload!r}"

    # exists() should return True
    assert client.exists(key) is True

    # list() should include the key
    prefix = f"test/storage_minio/{run_id}/"
    listed = client.list(prefix)
    assert key in listed, f"{key!r} not in listing: {listed}"

    # Cleanup
    try:
        import boto3  # noqa: PLC0415

        creds = _s3_cfg()
        s3 = boto3.client(
            "s3",
            aws_access_key_id=creds["s3_key_id"],
            aws_secret_access_key=creds["s3_secret"],
            endpoint_url=creds["s3_endpoint"],
            region_name=creds["s3_region"],
        )
        s3.delete_object(Bucket="nubi", Key=key)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Test 4: _cfg_references_s3 detects s3_views values (no network needed)
# ---------------------------------------------------------------------------


def test_cfg_references_s3_detects_s3_views_dict():
    """_cfg_references_s3 returns True when s3_views contains an s3:// URI."""
    import sys  # noqa: PLC0415
    import os as _os  # noqa: PLC0415

    sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), ".."))
    from app.routes.data_browser import _cfg_references_s3  # noqa: PLC0415

    cfg_with_s3_views: dict[str, Any] = {
        "connector_type": "duckdb",
        "database": ":memory:",
        "s3_views": {
            "sales": "s3://nubi/projects/proj1/demo/sales.parquet",
            "budget": "s3://nubi/projects/proj1/demo/budget.parquet",
        },
    }
    assert _cfg_references_s3(cfg_with_s3_views) is True

    cfg_without_s3: dict[str, Any] = {
        "connector_type": "duckdb",
        "database": ":memory:",
        "s3_views": {},
    }
    assert _cfg_references_s3(cfg_without_s3) is False

    cfg_local_only: dict[str, Any] = {
        "connector_type": "duckdb",
        "database": ":memory:",
    }
    assert _cfg_references_s3(cfg_local_only) is False


# ---------------------------------------------------------------------------
# Test 5: _build_view_sql_from_s3_views produces correct SQL
# ---------------------------------------------------------------------------


def test_build_view_sql_from_s3_views_produces_correct_sql():
    """_build_view_sql_from_s3_views generates valid CREATE VIEW statements."""
    import sys  # noqa: PLC0415
    import os as _os  # noqa: PLC0415

    sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), ".."))
    from app.routes.data_browser import _build_view_sql_from_s3_views  # noqa: PLC0415

    views = {
        "sales": "s3://nubi/proj1/sales.parquet",
        "customers": "s3://nubi/proj1/customers.parquet",
    }
    sql = _build_view_sql_from_s3_views(views)

    # Each table should have a CREATE OR REPLACE VIEW statement
    assert "CREATE OR REPLACE VIEW sales" in sql
    assert "CREATE OR REPLACE VIEW customers" in sql
    assert "s3://nubi/proj1/sales.parquet" in sql
    assert "s3://nubi/proj1/customers.parquet" in sql

    # Statements should be separated by semicolons
    stmts = [s.strip() for s in sql.split(";") if s.strip()]
    assert len(stmts) == 2
