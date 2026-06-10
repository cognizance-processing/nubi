"""Live-MinIO integration tests for the per-project S3 demo seed.

These tests verify the full round-trip:
  1. ``export_demo_to_s3`` writes one parquet per table for ALL FOUR demo
     datasets to ``s3://<bucket>/projects/<pid>/demo/<dataset>/<table>.parquet``.
  2. ``seed_sample_bundle`` registers a duckdb datastore whose config points at
     those S3 parquet files via ``view_sql``.
  3. The registered datastore's 17 tables are listable + queryable via the
     DuckDB connector class (using ``_build_duckdb_connector`` / httpfs).
  4. ``seed_sample_bundle`` is idempotent — re-running produces no duplicates
     and does not re-export files that already exist in S3.
  5. Per-project isolation: each project gets its own file set.

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
    python -m pytest tests/test_demo_s3_seed.py -v
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest

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


def _all_tables() -> dict[str, str]:
    """``{table: dataset}`` for the full 17-table inventory."""
    from seed_data.generators import DATASET_TABLES  # noqa: PLC0415

    return {t: ds for ds, tables in DATASET_TABLES.items() for t in tables}


def _build_connector_from_config(cfg: dict[str, Any]):
    """Build a DuckDB connector from a datastore config via data_browser helper.

    This reuses the same code path that GET /data/{id}/tables uses, so the test
    validates the real query pipeline (httpfs setup + view_sql execution).
    """
    from app.routes.data_browser import _build_duckdb_connector  # noqa: PLC0415

    return _build_duckdb_connector(cfg)


# ── Demo-bundle export tests ──────────────────────────────────────────────────


def test_export_demo_to_s3_writes_all_parquet_files():
    """export_demo_to_s3 writes every table of all 4 datasets to its dataset prefix."""
    from app.demo_bundle import _s3_bucket, export_demo_to_s3  # noqa: PLC0415

    pid = _pid()
    bucket = _s3_bucket()
    inventory = _all_tables()

    written = export_demo_to_s3(pid)

    assert set(written.keys()) == set(inventory), (
        f"Written tables {set(written)} != expected {set(inventory)}"
    )
    for table, uri in written.items():
        dataset = inventory[table]
        expected_uri = f"s3://{bucket}/projects/{pid}/demo/{dataset}/{table}.parquet"
        assert uri == expected_uri, f"URI mismatch for {table}: {uri!r} != {expected_uri!r}"


def test_export_one_dataset_round_trips_via_read_parquet():
    """Exported saas_metrics parquet is readable back via DuckDB + httpfs."""
    import duckdb  # noqa: PLC0415

    from app.connectors.duckdb_conn import setup_s3_httpfs  # noqa: PLC0415
    from app.demo_bundle import _s3_bucket, export_demo_to_s3  # noqa: PLC0415
    from seed_data.generators import DATASET_TABLES  # noqa: PLC0415

    pid = _pid()
    bucket = _s3_bucket()
    export_demo_to_s3(pid)

    conn = duckdb.connect(":memory:")
    setup_s3_httpfs(conn)
    try:
        for table in DATASET_TABLES["saas_metrics"]:
            uri = f"s3://{bucket}/projects/{pid}/demo/saas_metrics/{table}.parquet"
            count = conn.execute(
                f"SELECT COUNT(*) FROM read_parquet('{uri}')"
            ).fetchone()[0]
            assert count > 0, f"Table {table}: expected >0 rows in {uri}, got {count}"
        # A real aggregate over the S3-backed data returns a growing MRR series.
        uri = f"s3://{bucket}/projects/{pid}/demo/saas_metrics/saas_invoices.parquet"
        rows = conn.execute(
            f"SELECT month, SUM(amount) AS mrr FROM read_parquet('{uri}') "
            f"GROUP BY month ORDER BY month"
        ).fetchall()
        assert len(rows) == 24
        assert rows[-1][1] > rows[0][1]
    finally:
        conn.close()


def test_export_demo_to_s3_is_idempotent():
    """A second export for the same project skips everything already present."""
    from app.demo_bundle import export_demo_to_s3  # noqa: PLC0415

    pid = _pid()
    first = export_demo_to_s3(pid)
    assert len(first) == 17, f"First export should write 17 tables; got {len(first)}"

    second = export_demo_to_s3(pid)
    assert second == {}, f"Second export should skip all existing tables; got {second}"


def test_export_demo_to_s3_force_overwrites():
    """export_demo_to_s3(force=True) re-exports all tables unconditionally."""
    from app.demo_bundle import export_demo_to_s3  # noqa: PLC0415

    pid = _pid()
    export_demo_to_s3(pid)
    rewritten = export_demo_to_s3(pid, force=True)
    assert len(rewritten) == 17, (
        f"force=True should re-write all 17 tables; got {len(rewritten)}"
    )


def test_export_demo_to_s3_different_projects_are_isolated():
    """Each project_id gets its own set of S3 files (no cross-contamination)."""
    import duckdb  # noqa: PLC0415

    from app.connectors.duckdb_conn import setup_s3_httpfs  # noqa: PLC0415
    from app.demo_bundle import _s3_bucket, export_demo_to_s3  # noqa: PLC0415

    pid_a = _pid()
    pid_b = _pid()
    bucket = _s3_bucket()

    export_demo_to_s3(pid_a)
    export_demo_to_s3(pid_b)

    conn = duckdb.connect(":memory:")
    setup_s3_httpfs(conn)
    try:
        for pid in (pid_a, pid_b):
            row = conn.execute(
                f"SELECT COUNT(*) FROM read_parquet("
                f"'s3://{bucket}/projects/{pid}/demo/retail_sales/sales.parquet')"
            ).fetchone()
            assert row[0] > 0, f"project {pid} sales.parquet should exist"
    finally:
        conn.close()


# ── s3_datastore_config tests ─────────────────────────────────────────────────


def test_s3_datastore_config_shape():
    """s3_datastore_config returns a duckdb config with view_sql for all tables."""
    from app.demo_bundle import _s3_bucket, s3_datastore_config  # noqa: PLC0415

    pid = _pid()
    cfg = s3_datastore_config(pid)

    assert cfg["connector_type"] == "duckdb"
    assert cfg["database"] == ":memory:"
    assert "view_sql" in cfg

    view_sql = cfg["view_sql"]
    bucket = _s3_bucket()
    for table, dataset in _all_tables().items():
        assert f"CREATE OR REPLACE VIEW {table} " in view_sql, f"view for {table!r} missing"
        assert f"s3://{bucket}/projects/{pid}/demo/{dataset}/{table}.parquet" in view_sql, (
            f"S3 URI for {table} missing from view_sql"
        )

    assert cfg.get("demo_project_id") == pid
    assert cfg.get("demo_s3_bucket") == bucket


def test_s3_datastore_config_tables_listable_and_queryable():
    """Connector built from s3_datastore_config lists + queries all 17 tables."""
    from app.connectors.plan import PhysicalPlan  # noqa: PLC0415
    from app.demo_bundle import export_demo_to_s3, s3_datastore_config  # noqa: PLC0415
    from app.routes.data_browser import _introspect_tables_duckdb  # noqa: PLC0415

    pid = _pid()
    export_demo_to_s3(pid)
    cfg = s3_datastore_config(pid)

    connector = _build_connector_from_config(cfg)

    tables = _introspect_tables_duckdb(connector)
    table_names = {t["name"] for t in tables}
    expected = set(_all_tables())
    assert expected <= table_names, (
        f"Not all demo tables visible. Missing: {expected - table_names}"
    )

    for table in expected:
        plan = PhysicalPlan(
            sql=f"SELECT * FROM {table} LIMIT 3",
            params=[],
            cache_key="",
            rls_claims={},
        )
        result = connector.execute(plan)
        assert result.num_rows > 0, (
            f"Table {table}: expected >0 rows from S3 parquet, got {result.num_rows}"
        )


def test_s3_datastore_config_star_schema_join_works():
    """The retail star-schema join resolves on all fact rows via S3-backed views."""
    from app.connectors.plan import PhysicalPlan  # noqa: PLC0415
    from app.demo_bundle import export_demo_to_s3, s3_datastore_config  # noqa: PLC0415

    pid = _pid()
    export_demo_to_s3(pid)
    cfg = s3_datastore_config(pid)

    connector = _build_connector_from_config(cfg)

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
    assert n > 0, "Star-schema join returned 0 rows; expected all fact rows to resolve"


# ── seed_sample_bundle S3 integration tests ───────────────────────────────────


@pytest.mark.asyncio
async def test_seed_sample_bundle_uses_s3_when_configured():
    """seed_sample_bundle registers an S3-backed datastore when S3 is configured.

    The datastore config must have view_sql referencing s3:// URIs for the
    project's demo tables (NOT local file paths).
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
    assert cfg["connector_type"] == "duckdb"
    assert cfg.get("database") == ":memory:"
    assert "view_sql" in cfg
    assert f"s3://{bucket}/projects/{pid}/demo/" in cfg["view_sql"], (
        f"Expected project-scoped S3 URIs in view_sql. Got: {cfg['view_sql'][:200]!r}"
    )


@pytest.mark.asyncio
async def test_seed_sample_bundle_s3_is_idempotent():
    """Re-seeding the S3 bundle creates nothing new (idempotent)."""
    from app.demo_bundle import (  # noqa: PLC0415
        load_boards,
        load_queries,
        referenced_query_keys,
    )
    from app.repos.memory import InMemoryRepo  # noqa: PLC0415
    from app.sample import seed_sample_bundle  # noqa: PLC0415

    repo = InMemoryRepo()
    org, pid, user = _ids()

    await seed_sample_bundle(org, pid, user, repo)
    second = await seed_sample_bundle(org, pid, user, repo)

    boards = load_boards()
    queries = load_queries()
    n_queries = len([k for k in referenced_query_keys(boards) if k in queries])

    assert second["created"] == [], f"Second seed created unexpected resources: {second['created']}"
    assert len(await repo.list("datastores", org)) == 1
    assert len(await repo.list("queries", org)) == n_queries
    assert len(await repo.list("boards", org)) == len(boards)


@pytest.mark.asyncio
async def test_seed_sample_bundle_each_project_isolated():
    """Two projects in the same org each get their own S3 files and datastores."""
    from app.repos.memory import InMemoryRepo  # noqa: PLC0415
    from app.sample import remove_sample_bundle, seed_sample_bundle  # noqa: PLC0415

    repo = InMemoryRepo()
    org = str(uuid.uuid4())
    user = str(uuid.uuid4())
    pid_a = str(uuid.uuid4())
    pid_b = str(uuid.uuid4())

    sum_a = await seed_sample_bundle(org, pid_a, user, repo)
    assert "skipped" not in sum_a

    # Remove bundle A from repo (so _find_sample returns None for project B).
    await remove_sample_bundle(org, pid_a, repo)

    sum_b = await seed_sample_bundle(org, pid_b, user, repo)
    assert "skipped" not in sum_b

    ds_list = await repo.list("datastores", org)
    assert len(ds_list) == 1
    cfg_b = ds_list[0]["config"]
    assert f"projects/{pid_b}/demo/" in cfg_b.get("view_sql", ""), (
        "Project B datastore view_sql should reference pid_b path"
    )
    assert f"projects/{pid_a}/demo/" not in cfg_b.get("view_sql", ""), (
        "Project B datastore view_sql should NOT reference pid_a path"
    )
