"""Tests for the per-warehouse bulk loaders (``app.flows.bulk_loaders``) — §4.

Coverage
--------
- ``choose_strategy`` picks ``bulk`` when the target's ``bulk_load_from``
  intersects the staging scheme, and falls back to ``stream`` on a cross-cloud
  mismatch (covered here from the loader's perspective + in test_loaders).
- Each warehouse's COPY / load-job / s3() statement is constructed correctly
  (pure builders — no client, no creds).
- The dispatch path (``make_bulk_callable`` → executor) drives a MOCKED client
  and asserts the statement reaches it, with NO live warehouse round-trip.
- ``resolve_bulk_target`` / ``bind_bulk`` wire a bulk LoadTarget end-to-end and
  ``load_staged`` runs it.

What needs LIVE creds (NOT covered, stated in the deliverable)
--------------------------------------------------------------
A real BigQuery load job / Snowflake COPY / Redshift COPY / ClickHouse s3()
round-trip against a live warehouse + live cloud staging.  The client seam
(``client=`` injection) is exactly the boundary those would cross; everything
up to issuing the statement is tested with a mock.
"""

from __future__ import annotations

import io
import tempfile

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.errors import AppError
from app.flows import bulk_loaders as bl
from app.flows.bulk_loaders import (
    WAREHOUSE_BULK_LOAD_FROM,
    bigquery_source_uris,
    clickhouse_insert_statement,
    make_bulk_callable,
    redshift_copy_statement,
    resolve_bulk_target,
    snowflake_copy_statement,
)
from app.flows.loaders import choose_strategy, load_staged
from app.lakehouse.managed import CentralStorage
from app.lakehouse.staging import StagingArea


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parquet_bytes(rows):
    table = pa.Table.from_pylist(rows)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


def _staging(tmp, org="org1", run="run1"):
    return StagingArea(central=CentralStorage(scheme="file", bucket=tmp, creds={}),
                       org_id=org, run_id=run)


def _staged(tmp, rel="orders.parquet", rows=None):
    area = _staging(tmp)
    rows = rows or [{"id": 1}, {"id": 2}, {"id": 3}]
    entry = area.write_bytes(_parquet_bytes(rows), rel)
    manifest = area.build_manifest([entry], {rel: len(rows)})
    return area, manifest


class _FakeCursor:
    def __init__(self, rowcount=3):
        self.executed = []
        self.rowcount = rowcount

    def execute(self, sql):
        self.executed.append(sql)

    def close(self):
        pass


class _FakeDBClient:
    """A psycopg/snowflake-shaped client capturing executed SQL."""

    def __init__(self, rowcount=3):
        self._cur = _FakeCursor(rowcount)
        self.committed = False

    def cursor(self):
        return self._cur

    def commit(self):
        self.committed = True

    # context-manager cursor (psycopg style)
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _CtxCursorClient:
    """psycopg-style client whose .cursor() is a context manager."""

    def __init__(self, rowcount=2):
        self.cur = _FakeCursor(rowcount)
        self.committed = False

    def cursor(self):
        client = self

        class _CM:
            def __enter__(self_inner):
                return client.cur

            def __exit__(self_inner, *a):
                return False

        return _CM()

    def commit(self):
        self.committed = True


class _FakeCHClient:
    def __init__(self):
        self.commands = []

    def command(self, sql):
        self.commands.append(sql)


class _FakeBQJob:
    def __init__(self, output_rows=3):
        self.output_rows = output_rows

    def result(self):
        return self


class _FakeBQClient:
    def __init__(self):
        self.calls = []

    def load_table_from_uri(self, sources, table, job_config=None):
        self.calls.append((list(sources), table, job_config))
        return _FakeBQJob(output_rows=3)


# ---------------------------------------------------------------------------
# Capability table / choose_strategy gating
# ---------------------------------------------------------------------------


def test_capability_table_schemes():
    assert WAREHOUSE_BULK_LOAD_FROM["bigquery"] == ["gcs"]
    assert "s3" in WAREHOUSE_BULK_LOAD_FROM["snowflake"]
    assert WAREHOUSE_BULK_LOAD_FROM["redshift"] == ["s3"]
    assert "s3" in WAREHOUSE_BULK_LOAD_FROM["clickhouse"]


def test_choose_strategy_bigquery_s3_staging_falls_back_to_stream():
    # BigQuery only bulk-loads from gcs; staging on s3 → stream (cross-cloud).
    from app.connectors.base import file_capabilities

    caps = file_capabilities(bulk_load_from=WAREHOUSE_BULK_LOAD_FROM["bigquery"])
    assert choose_strategy(caps, "s3") == "stream"
    assert choose_strategy(caps, "gcs") == "bulk"


def test_choose_strategy_snowflake_s3_is_bulk():
    from app.connectors.base import file_capabilities

    caps = file_capabilities(bulk_load_from=WAREHOUSE_BULK_LOAD_FROM["snowflake"])
    assert choose_strategy(caps, "s3") == "bulk"


# ---------------------------------------------------------------------------
# Pure statement builders (no client / no creds)
# ---------------------------------------------------------------------------


def test_bigquery_source_uris_normalises_gcs_scheme():
    assert bigquery_source_uris(["gcs://b/orgs/o/staging/r/x.parquet"]) == [
        "gs://b/orgs/o/staging/r/x.parquet"
    ]
    # s3 left unchanged (the cross-cloud gate prevents it reaching BQ in practice)
    assert bigquery_source_uris(["gs://b/x.parquet"]) == ["gs://b/x.parquet"]


def test_snowflake_copy_statement():
    sql = snowflake_copy_statement("raw.orders", "s3://b/orgs/o/staging/r/")
    assert sql.startswith("COPY INTO raw.orders FROM 's3://b/orgs/o/staging/r/'")
    assert "FILE_FORMAT = (TYPE = PARQUET)" in sql
    assert "MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE" in sql


def test_redshift_copy_statement_iam_role_preferred():
    sql = redshift_copy_statement(
        "raw.orders",
        "s3://b/orgs/o/staging/r/",
        iam_role="arn:aws:iam::123:role/redshift",
        access_key_id="AK",
        secret_access_key="SK",
        region="us-east-1",
    )
    assert "COPY raw.orders" in sql
    assert "FROM 's3://b/orgs/o/staging/r/'" in sql
    assert "IAM_ROLE 'arn:aws:iam::123:role/redshift'" in sql
    assert "ACCESS_KEY_ID" not in sql  # IAM role wins
    assert "FORMAT AS PARQUET" in sql
    assert "REGION 'us-east-1'" in sql


def test_redshift_copy_statement_static_keys():
    sql = redshift_copy_statement(
        "t", "s3://b/p/", access_key_id="AK", secret_access_key="SK"
    )
    assert "ACCESS_KEY_ID 'AK'" in sql
    assert "SECRET_ACCESS_KEY 'SK'" in sql


def test_clickhouse_insert_statement_with_and_without_keys():
    with_keys = clickhouse_insert_statement(
        "raw.orders", "s3://b/p/*.parquet", access_key_id="AK", secret_access_key="SK"
    )
    assert with_keys == (
        "INSERT INTO raw.orders SELECT * FROM "
        "s3('s3://b/p/*.parquet', 'AK', 'SK', 'Parquet')"
    )
    no_keys = clickhouse_insert_statement("raw.orders", "s3://b/p/*.parquet")
    assert no_keys == (
        "INSERT INTO raw.orders SELECT * FROM s3('s3://b/p/*.parquet', 'Parquet')"
    )


# ---------------------------------------------------------------------------
# Dispatch with a MOCKED client (no live warehouse)
# ---------------------------------------------------------------------------


def test_bigquery_dispatch_with_mock_client():
    with tempfile.TemporaryDirectory() as tmp:
        area, manifest = _staged(tmp)
        client = _FakeBQClient()
        bulk = make_bulk_callable("bigquery", {}, area, manifest, client=client)
        rows = bulk("proj.ds.orders")
        assert rows == 3
        sources, table, job_config = client.calls[0]
        assert table == "proj.ds.orders"
        # The staged file:// URI is passed through (gcs normalisation is a no-op
        # for file://); the important assertion is the call reached the client.
        assert sources and sources[0].endswith("orders.parquet")


def test_snowflake_dispatch_with_mock_client():
    with tempfile.TemporaryDirectory() as tmp:
        area, manifest = _staged(tmp)
        client = _FakeDBClient(rowcount=3)
        bulk = make_bulk_callable("snowflake", {}, area, manifest, client=client)
        rows = bulk("raw.orders")
        assert rows == 3
        sql = client._cur.executed[0]
        assert sql.startswith("COPY INTO raw.orders FROM '")
        assert "FILE_FORMAT = (TYPE = PARQUET)" in sql


def test_redshift_dispatch_with_mock_client():
    with tempfile.TemporaryDirectory() as tmp:
        area, manifest = _staged(tmp)
        client = _CtxCursorClient(rowcount=2)
        cfg = {"iam_role": "arn:aws:iam::1:role/r"}
        bulk = make_bulk_callable("redshift", cfg, area, manifest, client=client)
        rows = bulk("raw.orders")
        assert rows == 2
        sql = client.cur.executed[0]
        assert sql.startswith("COPY raw.orders FROM '")
        assert "IAM_ROLE 'arn:aws:iam::1:role/r'" in sql
        assert client.committed is True


def test_clickhouse_dispatch_with_mock_client():
    with tempfile.TemporaryDirectory() as tmp:
        area, manifest = _staged(tmp, rows=[{"id": 1}, {"id": 2}])
        client = _FakeCHClient()
        cfg = {"aws_access_key_id": "AK", "aws_secret_access_key": "SK"}
        bulk = make_bulk_callable("clickhouse", cfg, area, manifest, client=client)
        rows = bulk("raw.orders")
        assert rows == 2  # manifest rows (ClickHouse command has no rowcount)
        sql = client.commands[0]
        assert sql.startswith("INSERT INTO raw.orders SELECT * FROM s3(")
        assert "'AK', 'SK'" in sql


# ---------------------------------------------------------------------------
# resolve_bulk_target / bind_bulk / load_staged end-to-end (mocked client)
# ---------------------------------------------------------------------------


def test_resolve_bulk_target_unknown_type_returns_none():
    assert resolve_bulk_target("t", "postgres", {}) is None
    assert resolve_bulk_target("t", "", {}) is None


def test_bulk_target_load_staged_end_to_end():
    with tempfile.TemporaryDirectory() as tmp:
        area, manifest = _staged(tmp)
        target = resolve_bulk_target("raw.orders", "snowflake", {})
        assert target is not None
        assert target.capabilities["bulk_load_from"] == ["s3", "gcs", "az"]

        # Bind with a MOCK client by monkeypatching make_bulk_callable's client.
        client = _FakeDBClient(rowcount=3)
        target.bulk = make_bulk_callable("snowflake", {}, area, manifest, client=client)

        # Staging is file://, but force the bulk strategy to prove dispatch works
        # (choose_strategy would pick stream for file:// — bulk path is exercised
        # directly here; the scheme gating itself is covered by choose_strategy).
        from app.flows.loaders import _bulk_load

        result = _bulk_load(area, manifest, target)
        assert result["strategy"] == "bulk"
        assert result["rows_loaded"] == 3
        assert client._cur.executed  # COPY reached the client


# ---------------------------------------------------------------------------
# SECURITY: target-table identifier validation (SQL injection via target.object)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "evil",
    [
        "orders; DROP TABLE secrets; --",
        "orders'; DELETE FROM users; --",
        "raw.orders FROM 's3://attacker/'; --",
        'raw."orders"; SELECT 1',
        "raw.orders WHERE 1=1",
        "a b",
        "raw.orders) ; --",
        "",
        "x" * 600,
    ],
)
def test_validate_table_identifier_rejects_injection(evil):
    with pytest.raises(AppError) as ei:
        bl.validate_table_identifier(evil)
    assert ei.value.code == "invalid_identifier"


@pytest.mark.parametrize(
    "ok",
    ["orders", "raw.orders", "db.raw.orders", "proj.ds.tbl", '"My Table"', 'raw."Order-Items"'],
)
def test_validate_table_identifier_accepts_clean(ok):
    assert bl.validate_table_identifier(ok) == ok


@pytest.mark.parametrize(
    "builder",
    [
        lambda t: bl.snowflake_copy_statement(t, "s3://b/p/"),
        lambda t: bl.redshift_copy_statement(t, "s3://b/p/", iam_role="arn:x"),
        lambda t: bl.clickhouse_insert_statement(t, "s3://b/p/"),
    ],
)
def test_statement_builders_reject_injected_table(builder):
    # Every warehouse statement builder must refuse an injected table name —
    # the table is the only user-controlled token that reaches the statement.
    with pytest.raises(AppError):
        builder("orders; DROP TABLE secrets; --")
    # And produce a clean statement for a legitimate identifier.
    assert "raw.orders" in builder("raw.orders")


def test_bind_bulk_noop_for_non_bulk_target():
    # bind_bulk on a target that wasn't produced by resolve_bulk_target is a no-op.
    from app.flows.loaders import LoadTarget

    with tempfile.TemporaryDirectory() as tmp:
        area, manifest = _staged(tmp)
        target = LoadTarget(object_name="t", capabilities={})
        bl.bind_bulk(target, area, manifest)
        assert target.bulk is None
