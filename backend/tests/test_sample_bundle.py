"""Tests for the onboarding *sample bundle* seeder (``app.sample``).

Coverage
--------
1.  ``seed_sample_bundle`` creates the FULL bundle (1 datastore + every demo
    query + all 10 boards), all tagged ``config.sample=true`` and scoped to the
    project.
2.  Seeding is idempotent — a second call creates nothing new.
3.  In local (no-S3) mode the datastore is a ``:memory:`` DuckDB whose
    ``view_sql`` reads LOCAL parquet files via ``read_parquet`` — the same
    connector shape as the S3 mode, no special-cased local .duckdb file.
4.  The local parquet files exist for all 17 tables across the 4 datasets.
5.  ``remove_sample_bundle`` deletes every sample resource and is idempotent;
    remove → restore round-trips the bundle.
6.  One representative query per dataset routed through the HTTP POST
    /api/v1/query endpoint returns real rows (regression guard for silent
    query hangs / view registration failures).
7.  GET /data/{datastore_id}/tables lists every table of all four datasets via
    the normal data-browser pipeline (no demo special-casing).
8.  GET /data/{datastore_id}/tables/sales/rows returns real rows.
"""

from __future__ import annotations

import os
import uuid
from io import BytesIO

import pyarrow.ipc as pa_ipc
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.demo_bundle import (
    ALL_DEMO_TABLES,
    load_boards,
    load_queries,
    referenced_query_keys,
)
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo
from app.sample import remove_sample_bundle, seed_sample_bundle

# Env vars that would flip seed_sample_bundle into S3 mode — cleared in these
# tests so the local-parquet path is exercised deterministically.
_S3_ENV_VARS = ("S3_ACCESS_KEY", "AWS_ACCESS_KEY_ID")

# Env vars that would flip seed_sample_bundle into the EDITABLE on-disk DuckDB
# mode — cleared here so these tests deterministically exercise the read-only
# parquet-view (cloud/offline) fallback documented above.  The editable path is
# covered by test_demo_lakehouse.py.
_LAKE_DIR_ENV_VARS = ("NUBI_MANAGED_LAKE_DIR", "NUBI_LOCAL_LAKE_DIR", "NUBI_DEMO_LAKE_DIR")


def _ids() -> tuple[str, str, str]:
    return str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())


def _clear_s3_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (*_S3_ENV_VARS, *_LAKE_DIR_ENV_VARS):
        monkeypatch.delenv(var, raising=False)


def _expected_counts() -> tuple[int, int]:
    """(expected query count, expected board count) from the demo fixtures."""
    boards = load_boards()
    queries = load_queries()
    needed = [k for k in referenced_query_keys(boards) if k in queries]
    return len(needed), len(boards)


@pytest.mark.asyncio
async def test_seed_creates_full_bundle_tagged_sample(monkeypatch) -> None:
    _clear_s3_env(monkeypatch)
    repo = InMemoryRepo()
    org, project, user = _ids()

    summary = await seed_sample_bundle(org, project, user, repo)

    assert "skipped" not in summary, summary
    datastores = await repo.list("datastores", org)
    queries = await repo.list("queries", org)
    boards = await repo.list("boards", org)

    n_queries, n_boards = _expected_counts()
    assert len(datastores) == 1
    assert len(queries) == n_queries
    assert len(boards) == n_boards
    assert n_boards == 10  # the demo ships exactly 10 dashboards

    # Every created resource is tagged sample=true and scoped to the project.
    for row in (*datastores, *queries, *boards):
        assert row["config"].get("sample") is True
        assert row["config"].get("sample_id")
        assert str(row["project_id"]) == project


@pytest.mark.asyncio
async def test_seed_is_idempotent(monkeypatch) -> None:
    _clear_s3_env(monkeypatch)
    repo = InMemoryRepo()
    org, project, user = _ids()

    await seed_sample_bundle(org, project, user, repo)
    second = await seed_sample_bundle(org, project, user, repo)

    n_queries, n_boards = _expected_counts()
    assert second["created"] == []  # nothing new created on re-run
    assert len(await repo.list("datastores", org)) == 1
    assert len(await repo.list("queries", org)) == n_queries
    assert len(await repo.list("boards", org)) == n_boards


@pytest.mark.asyncio
async def test_local_datastore_is_parquet_backed_memory_duckdb(monkeypatch) -> None:
    """Local mode uses :memory: + read_parquet views — same shape as S3 mode."""
    _clear_s3_env(monkeypatch)
    repo = InMemoryRepo()
    org, project, user = _ids()
    await seed_sample_bundle(org, project, user, repo)

    ds = (await repo.list("datastores", org))[0]
    cfg = ds["config"]
    assert cfg["connector_type"] == "duckdb"
    assert cfg["database"] == ":memory:"
    view_sql = cfg["view_sql"]
    assert "read_parquet(" in view_sql
    assert "s3://" not in view_sql  # local mode reads local files
    for table in ALL_DEMO_TABLES:
        assert f"CREATE OR REPLACE VIEW {table} " in view_sql, f"missing view for {table}"


@pytest.mark.asyncio
async def test_local_parquet_files_exist_for_all_tables(monkeypatch) -> None:
    _clear_s3_env(monkeypatch)
    repo = InMemoryRepo()
    org, project, user = _ids()
    await seed_sample_bundle(org, project, user, repo)

    from app.demo_bundle import export_demo_parquet_local

    paths = export_demo_parquet_local()  # idempotent — returns existing paths
    assert set(paths) == set(ALL_DEMO_TABLES)
    for table, path in paths.items():
        assert os.path.isabs(path)
        assert os.path.exists(path), f"missing parquet for {table}: {path}"
        assert os.path.getsize(path) > 0


@pytest.mark.asyncio
async def test_remove_then_restore_round_trips(monkeypatch) -> None:
    _clear_s3_env(monkeypatch)
    repo = InMemoryRepo()
    org, project, user = _ids()
    await seed_sample_bundle(org, project, user, repo)

    n_queries, n_boards = _expected_counts()
    removed = await remove_sample_bundle(org, project, repo)
    assert removed == {"boards": n_boards, "queries": n_queries, "datastores": 1}
    assert await repo.list("datastores", org) == []
    assert await repo.list("queries", org) == []
    assert await repo.list("boards", org) == []

    # Removing again is a no-op (idempotent).
    assert await remove_sample_bundle(org, project, repo) == {
        "boards": 0,
        "queries": 0,
        "datastores": 0,
    }

    # Restore re-creates the whole bundle.
    await seed_sample_bundle(org, project, user, repo)
    assert len(await repo.list("datastores", org)) == 1
    assert len(await repo.list("queries", org)) == n_queries
    assert len(await repo.list("boards", org)) == n_boards


# ---------------------------------------------------------------------------
# End-to-end: representative query per dataset via the HTTP /query route
# against the parquet-backed DuckDB datastore (local mode).
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def _sample_query_client(app, fake_db, monkeypatch):
    """InMemoryRepo with a seeded sample bundle + HTTPX client (local mode)."""
    _clear_s3_env(monkeypatch)
    repo = InMemoryRepo()
    set_repo(repo)

    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())

    # Seed the user in FakeDB so current_user dep resolves.
    fake_db.users[user_id] = {
        "id": user_id,
        "email": "sample-test@example.com",
        "name": "Sample Tester",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }

    # Seed org membership in InMemoryRepo for get_user_org().
    repo.seed_org_member(org_id=org_id, user_id=user_id)

    # Seed the full sample bundle.
    summary = await seed_sample_bundle(org_id, project_id, user_id, repo)
    assert "skipped" not in summary, f"sample bundle skipped: {summary}"

    datastore_id = summary["datastore_id"]

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as client:
        yield client, user_id, datastore_id

    set_repo(None)


# One representative aggregate query per dataset: (label, sql, expected columns)
_DATASET_QUERIES = [
    (
        "retail_sales",
        "SELECT region, ROUND(SUM(nsv), 2) AS nsv FROM sales GROUP BY region ORDER BY nsv DESC",
        {"region", "nsv"},
    ),
    (
        "saas_metrics",
        "SELECT month, ROUND(SUM(amount), 2) AS mrr FROM saas_invoices GROUP BY month ORDER BY month",
        {"month", "mrr"},
    ),
    (
        "web_analytics",
        "SELECT utm_source, COUNT(*) AS sessions, SUM(converted) AS conversions "
        "FROM web_sessions GROUP BY utm_source ORDER BY sessions DESC",
        {"utm_source", "sessions", "conversions"},
    ),
    (
        "finance_ops",
        "SELECT month, ROUND(SUM(amount), 2) AS collected FROM fin_payments GROUP BY month ORDER BY month",
        {"month", "collected"},
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("dataset", "sql", "cols"), _DATASET_QUERIES, ids=[d[0] for d in _DATASET_QUERIES])
async def test_representative_query_per_dataset_returns_rows(
    _sample_query_client, dataset, sql, cols
) -> None:
    """A real aggregate per dataset completes through POST /api/v1/query with rows."""
    client, user_id, datastore_id = _sample_query_client
    auth = {"Authorization": f"Bearer {mint_access_token(user_id)}"}

    resp = await client.post(
        "/api/v1/query",
        json={"sql": sql, "datastore_id": datastore_id},
        headers=auth,
    )
    assert resp.status_code == 200, (
        f"[{dataset}] expected 200 but got {resp.status_code}. Body: {resp.text[:500]}"
    )
    ct = resp.headers.get("content-type", "")
    assert "application/vnd.apache.arrow.stream" in ct, (
        f"[{dataset}] expected Arrow IPC content-type, got: {ct!r}"
    )

    table = pa_ipc.open_stream(BytesIO(resp.content)).read_all()
    assert table.num_rows > 0, f"[{dataset}] query returned 0 rows"
    assert cols <= set(table.column_names), (
        f"[{dataset}] missing columns; got {table.column_names}"
    )


@pytest.mark.asyncio
async def test_sample_datastore_tables_listable_via_data_browser(
    _sample_query_client,
) -> None:
    """GET /data/{datastore_id}/tables lists every table of all four datasets."""
    client, user_id, datastore_id = _sample_query_client
    auth = {"Authorization": f"Bearer {mint_access_token(user_id)}"}

    resp = await client.get(
        f"/api/v1/data/{datastore_id}/tables",
        headers=auth,
    )
    assert resp.status_code == 200, (
        f"Expected 200 but got {resp.status_code}. Body: {resp.text[:500]}"
    )
    body = resp.json()
    assert "tables" in body, f"Response missing 'tables' key: {body}"
    assert body["datastore_id"] == datastore_id

    names = {t["name"] for t in body["tables"]}
    assert set(ALL_DEMO_TABLES) <= names, (
        f"Sample datastore missing expected tables: {set(ALL_DEMO_TABLES) - names}"
    )


@pytest.mark.asyncio
async def test_sample_datastore_rows_queryable_via_data_browser(
    _sample_query_client,
) -> None:
    """GET /data/{datastore_id}/tables/sales/rows returns real parquet-backed rows."""
    client, user_id, datastore_id = _sample_query_client
    auth = {"Authorization": f"Bearer {mint_access_token(user_id)}"}

    resp = await client.get(
        f"/api/v1/data/{datastore_id}/tables/sales/rows?limit=10",
        headers=auth,
    )
    assert resp.status_code == 200, (
        f"Expected 200 but got {resp.status_code}. Body: {resp.text[:500]}"
    )
    ct = resp.headers.get("content-type", "")
    assert "application/vnd.apache.arrow.stream" in ct, (
        f"Expected Arrow IPC content-type, got: {ct!r}"
    )

    table = pa_ipc.open_stream(BytesIO(resp.content)).read_all()
    assert table.num_rows > 0, "GET /data/{id}/tables/sales/rows returned 0 rows"
    expected_cols = {"region", "channel", "supplier", "nsv", "units"}
    assert expected_cols <= set(table.column_names), (
        f"Missing expected columns. Got: {sorted(table.column_names)}"
    )
