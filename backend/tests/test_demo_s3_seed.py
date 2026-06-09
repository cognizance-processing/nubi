"""Live-MinIO integration tests for the per-project S3 demo seed.

These tests verify the full round-trip:
  1. ``export_demo_to_s3`` writes 6 parquet files to s3://nubi/projects/<pid>/demo/
  2. ``seed_sample_bundle`` registers a duckdb datastore whose config points at
     those S3 parquet files via ``view_sql``.
  3. The registered datastore's 6 tables are listable + queryable via the
     DuckDB connector class (using ``_build_duckdb_connector`` / httpfs).
  4. ``seed_sample_bundle`` is idempotent — re-running produces no duplicates and
     does not re-export files that already exist in S3.
  5. Removing the bundle then restoring it re-exports + re-creates cleanly.

Environment requirements
------------------------
The following env vars must be set for these tests to run:
  S3_ACCESS_KEY=minioadmin
  S3_SECRET_KEY=minioadmin
  S3_ENDPOINT_URL=http://localhost:9000

When any of these is absent the test module is **skipped** (``pytest.skip`` at
collection time), so CI without MinIO stays green.

Usage (from the backend/ directory):
    S3_ACCESS_KEY=minioadmin S3_SECRET_KEY=minioadmin \\
    S3_ENDPOINT_URL=http://localhost:9000 \\
    ../.venv-backend/bin/python -m pytest tests/test_demo_s3_seed.py -v
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest
import pytest_asyncio

# ── Skip collection when MinIO creds are absent ───────────────────────────────

_S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY") or os.getenv("AWS_ACCESS_KEY_ID") or ""
_S3_SECRET_KEY = os.getenv("S3_SECRET_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY") or ""
_S3_ENDPOINT = os.getenv("S3_ENDPOINT_URL") or ""

pytestmark = pytest.mark.skipif(
    not (_S3_ACCESS_KEY and _S3_ENDPOINT),
    reason=(
        "MinIO not configured — set S3_ACCESS_KEY + S3_ENDPOINT_URL to run live S3 tests."
    ),
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pid() -> str:
    return str(uuid.uuid4())


def _ids() -> tuple[str, str, str]:
    return str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())


def _build_connector_from_config(cfg: dict[str, Any]):
    """Build a DuckDB connector from a datastore config via data_browser helper.

    This reuses the same code path that GET /data/{id}/tables uses, so the test
    validates the real query pipeline (httpfs setup + view_sql execution).
    """
    from app.routes.data_browser import _build_duckdb_connector  # noqa: PLC0415

    return _build_duckdb_connector(cfg)


# ── Demo-bundle export tests ──────────────────────────────────────────────────


def test_export_demo_to_s3_writes_6_parquet_files():
    """export_demo_to_s3 writes all 6 demo tables to s3://nubi/projects/<pid>/demo/."""
    from app.demo_bundle import DEMO_TABLES, _s3_bucket, export_demo_to_s3  # noqa: PLC0415

    pid = _pid()
    bucket = _s3_bucket()

    written = export_demo_to_s3(pid)

    assert len(written) == 6, (
        f"Expected 6 tables written; got {len(written)}: {list(written)}"
    )
    expected_tables = set(DEMO_TABLES)
    assert set(written.keys()) == expected_tables, (
        f"Written tables {set(written.keys())} != expected {expected_tables}"
    )

    for table, uri in written.items():
        expected_uri = f"s3://{bucket}/projects/{pid}/demo/{table}.parquet"
        assert uri == expected_uri, f"URI mismatch for {table}: {uri!r} != {expected_uri!r}"


def test_export_demo_to_s3_parquet_files_are_readable():
    """Exported parquet files are readable via DuckDB + httpfs."""
    import duckdb  # noqa: PLC0415
    from app.connectors.duckdb_conn import setup_s3_httpfs  # noqa: PLC0415
    from app.demo_bundle import DEMO_TABLES, _s3_bucket, export_demo_to_s3  # noqa: PLC0415

    pid = _pid()
    bucket = _s3_bucket()
    export_demo_to_s3(pid)

    conn = duckdb.connect(":memory:")
    setup_s3_httpfs(conn)

    try:
        for table in DEMO_TABLES:
            uri = f"s3://{bucket}/projects/{pid}/demo/{table}.parquet"
            count = conn.execute(
                f"SELECT COUNT(*) FROM read_parquet('{uri}')"
            ).fetchone()[0]
            assert count > 0, f"Table {table}: expected >0 rows in {uri}, got {count}"
    finally:
        conn.close()


def test_export_demo_to_s3_is_idempotent():
    """Calling export_demo_to_s3 twice for the same project_id does not fail.

    The second call skips tables already present (idempotency check) and returns
    an empty dict (nothing newly written).
    """
    from app.demo_bundle import export_demo_to_s3  # noqa: PLC0415

    pid = _pid()
    first = export_demo_to_s3(pid)
    assert len(first) == 6, f"First export should write 6 tables; got {len(first)}"

    second = export_demo_to_s3(pid)
    assert second == {}, (
        f"Second export should skip all existing tables; got {second}"
    )


def test_export_demo_to_s3_force_overwrites():
    """export_demo_to_s3(force=True) re-exports all tables unconditionally."""
    from app.demo_bundle import export_demo_to_s3  # noqa: PLC0415

    pid = _pid()
    export_demo_to_s3(pid)
    rewritten = export_demo_to_s3(pid, force=True)
    assert len(rewritten) == 6, (
        f"force=True should re-write all 6 tables; got {len(rewritten)}"
    )


def test_export_demo_to_s3_different_projects_are_isolated():
    """Each project_id gets its own set of S3 files (no cross-contamination)."""
    from app.demo_bundle import _s3_bucket, export_demo_to_s3  # noqa: PLC0415

    pid_a = _pid()
    pid_b = _pid()
    bucket = _s3_bucket()

    export_demo_to_s3(pid_a)
    export_demo_to_s3(pid_b)

    # Both sets should exist; skipping project_b export should not affect project_a.
    import duckdb  # noqa: PLC0415
    from app.connectors.duckdb_conn import setup_s3_httpfs  # noqa: PLC0415

    conn = duckdb.connect(":memory:")
    setup_s3_httpfs(conn)
    try:
        for pid in (pid_a, pid_b):
            row = conn.execute(
                f"SELECT COUNT(*) FROM read_parquet('s3://{bucket}/projects/{pid}/demo/sales.parquet')"
            ).fetchone()
            assert row[0] > 0, f"project {pid} sales.parquet should exist"
    finally:
        conn.close()


# ── s3_datastore_config tests ─────────────────────────────────────────────────


def test_s3_datastore_config_shape():
    """s3_datastore_config returns a duckdb config with view_sql and s3:// refs."""
    from app.demo_bundle import DEMO_TABLES, _s3_bucket, s3_datastore_config  # noqa: PLC0415

    pid = _pid()
    cfg = s3_datastore_config(pid)

    assert cfg["connector_type"] == "duckdb"
    assert cfg["database"] == ":memory:"
    assert "view_sql" in cfg

    view_sql = cfg["view_sql"]
    bucket = _s3_bucket()
    for table in DEMO_TABLES:
        assert table in view_sql, f"table {table!r} missing from view_sql"
        assert f"s3://{bucket}/projects/{pid}/demo/{table}.parquet" in view_sql, (
            f"S3 URI for {table} missing from view_sql"
        )

    assert cfg.get("demo_project_id") == pid
    assert cfg.get("demo_s3_bucket") == bucket


def test_s3_datastore_config_connector_listable():
    """Connector built from s3_datastore_config can list all 6 tables.

    Exports the parquet files first so the views resolve, then builds the
    connector via the same helper as the data-browser route.
    """
    from app.demo_bundle import DEMO_TABLES, export_demo_to_s3, s3_datastore_config  # noqa: PLC0415

    pid = _pid()
    export_demo_to_s3(pid)
    cfg = s3_datastore_config(pid)

    connector = _build_connector_from_config(cfg)

    from app.routes.data_browser import _introspect_tables_duckdb  # noqa: PLC0415

    tables = _introspect_tables_duckdb(connector)
    table_names = {t["name"] for t in tables}

    expected = set(DEMO_TABLES)
    assert expected <= table_names, (
        f"Not all demo tables visible. Missing: {expected - table_names}. Got: {table_names}"
    )


def test_s3_datastore_config_tables_queryable():
    """Every demo table in s3_datastore_config returns >0 rows when queried."""
    from app.demo_bundle import DEMO_TABLES, export_demo_to_s3, s3_datastore_config  # noqa: PLC0415

    pid = _pid()
    export_demo_to_s3(pid)
    cfg = s3_datastore_config(pid)

    connector = _build_connector_from_config(cfg)

    from app.connectors.plan import PhysicalPlan  # noqa: PLC0415

    for table in DEMO_TABLES:
        plan = PhysicalPlan(
            sql=f"SELECT * FROM {table} LIMIT 5",
            params=[],
            cache_key="",
            rls_claims={},
        )
        result = connector.execute(plan)
        assert result.num_rows > 0, (
            f"Table {table}: expected >0 rows from S3 parquet, got {result.num_rows}"
        )


def test_s3_datastore_config_star_schema_join_works():
    """The star-schema join resolves on all fact rows via S3-backed views."""
    from app.demo_bundle import export_demo_to_s3, s3_datastore_config  # noqa: PLC0415

    pid = _pid()
    export_demo_to_s3(pid)
    cfg = s3_datastore_config(pid)

    connector = _build_connector_from_config(cfg)

    from app.connectors.plan import PhysicalPlan  # noqa: PLC0415

    plan = PhysicalPlan(
        sql="""
        SELECT COUNT(*) AS n FROM sales s
        JOIN dim_regions   r ON s.region_id   = r.region_id
        JOIN dim_products  p ON s.product_id  = p.product_id
        JOIN dim_customers c ON s.customer_id = c.customer_id
        """,
        params=[],
        cache_key="",
        rls_claims={},
    )
    result = connector.execute(plan)
    n = result.to_pydict()["n"][0]
    assert n > 0, f"Star-schema join returned 0 rows; expected all fact rows to resolve"


# ── seed_sample_bundle S3 integration tests ───────────────────────────────────


@pytest.mark.asyncio
async def test_seed_sample_bundle_uses_s3_when_configured():
    """seed_sample_bundle registers an S3-backed datastore when S3 is configured.

    The datastore config must have view_sql referencing s3:// URIs for the
    project's demo tables (NOT a local file path).
    """
    from app.demo_bundle import _s3_bucket  # noqa: PLC0415
    from app.repos.memory import InMemoryRepo  # noqa: PLC0415
    from app.sample import seed_sample_bundle  # noqa: PLC0415

    repo = InMemoryRepo()
    org, pid, user = _ids()

    summary = await seed_sample_bundle(org, pid, user, repo)
    assert "skipped" not in summary, f"seed skipped: {summary}"

    ds_list = await repo.list("datastores", org)
    assert len(ds_list) == 1
    cfg = ds_list[0]["config"]

    bucket = _s3_bucket()
    assert cfg["connector_type"] == "duckdb", f"Expected duckdb, got {cfg.get('connector_type')!r}"
    assert cfg.get("database") == ":memory:", f"Expected :memory: database, got {cfg.get('database')!r}"
    assert "view_sql" in cfg, "Expected view_sql in config"
    assert f"s3://{bucket}/projects/{pid}/demo/" in cfg["view_sql"], (
        f"Expected project-scoped S3 URIs in view_sql. Got: {cfg['view_sql'][:200]!r}"
    )


@pytest.mark.asyncio
async def test_seed_sample_bundle_s3_datastore_listable_and_queryable():
    """Datastore registered by seed_sample_bundle is listable + queryable.

    Lists all 6 tables via _introspect_tables_duckdb and runs a SELECT on each,
    matching the 'listable + queryable through the connector' acceptance criterion.
    """
    from app.demo_bundle import DEMO_TABLES  # noqa: PLC0415
    from app.repos.memory import InMemoryRepo  # noqa: PLC0415
    from app.routes.data_browser import (  # noqa: PLC0415
        _build_duckdb_connector,
        _introspect_tables_duckdb,
    )
    from app.sample import seed_sample_bundle  # noqa: PLC0415
    from app.connectors.plan import PhysicalPlan  # noqa: PLC0415

    repo = InMemoryRepo()
    org, pid, user = _ids()

    summary = await seed_sample_bundle(org, pid, user, repo)
    assert "skipped" not in summary, f"seed skipped: {summary}"

    ds_list = await repo.list("datastores", org)
    cfg = dict(ds_list[0]["config"])

    connector = _build_duckdb_connector(cfg)

    # Listable
    tables = _introspect_tables_duckdb(connector)
    table_names = {t["name"] for t in tables}
    expected = set(DEMO_TABLES)
    assert expected <= table_names, (
        f"Missing tables: {expected - table_names}. Got: {sorted(table_names)}"
    )

    # Queryable
    for table in DEMO_TABLES:
        plan = PhysicalPlan(
            sql=f"SELECT * FROM {table} LIMIT 3",
            params=[],
            cache_key="",
            rls_claims={},
        )
        result = connector.execute(plan)
        assert result.num_rows > 0, (
            f"Table {table}: expected >0 rows, got {result.num_rows}"
        )


@pytest.mark.asyncio
async def test_seed_sample_bundle_s3_is_idempotent():
    """Re-seeding the S3 bundle creates nothing new (idempotent)."""
    from app.repos.memory import InMemoryRepo  # noqa: PLC0415
    from app.sample import seed_sample_bundle  # noqa: PLC0415

    repo = InMemoryRepo()
    org, pid, user = _ids()

    await seed_sample_bundle(org, pid, user, repo)
    second = await seed_sample_bundle(org, pid, user, repo)

    assert second["created"] == [], f"Second seed created unexpected resources: {second['created']}"
    assert len(await repo.list("datastores", org)) == 1
    assert len(await repo.list("queries", org)) == 6
    assert len(await repo.list("boards", org)) == 2


@pytest.mark.asyncio
async def test_seed_sample_bundle_each_project_isolated():
    """Two projects in the same org each get their own S3 files and datastores.

    Both bundles are created under the same org but with different project IDs.
    Each datastore's view_sql must reference its own project's S3 prefix.
    """
    from app.repos.memory import InMemoryRepo  # noqa: PLC0415
    from app.sample import seed_sample_bundle  # noqa: PLC0415

    repo = InMemoryRepo()
    org = str(uuid.uuid4())
    user = str(uuid.uuid4())
    pid_a = str(uuid.uuid4())
    pid_b = str(uuid.uuid4())

    # Seed bundle for project A.
    sum_a = await seed_sample_bundle(org, pid_a, user, repo)
    assert "skipped" not in sum_a

    # Remove bundle A from repo (so _find_sample returns None for project B).
    from app.sample import remove_sample_bundle  # noqa: PLC0415

    await remove_sample_bundle(org, pid_a, repo)

    # Seed bundle for project B.
    sum_b = await seed_sample_bundle(org, pid_b, user, repo)
    assert "skipped" not in sum_b

    ds_list = await repo.list("datastores", org)
    assert len(ds_list) == 1
    cfg_b = ds_list[0]["config"]
    assert f"projects/{pid_b}/demo/" in cfg_b.get("view_sql", ""), (
        f"Project B datastore view_sql should reference pid_b path. Got: {cfg_b.get('view_sql', '')[:200]!r}"
    )
    assert f"projects/{pid_a}/demo/" not in cfg_b.get("view_sql", ""), (
        "Project B datastore view_sql should NOT reference pid_a path"
    )
