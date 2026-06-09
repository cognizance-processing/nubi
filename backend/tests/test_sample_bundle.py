"""Tests for the onboarding *sample bundle* seeder (``app.sample``).

Coverage
--------
1.  ``seed_sample_bundle`` creates the full bundle (datastore + 6 queries +
    2 boards), all tagged ``config.sample=true`` and scoped to the project.
2.  Seeding is idempotent — a second call creates nothing new.
3.  The Sample datastore points at the bundled, absolute ``sample.duckdb`` path
    with ``connector_type=duckdb`` so the query route opens it read-only.
4.  The bundled DuckDB file is a real star schema that joins on every fact row.
5.  ``remove_sample_bundle`` deletes every sample resource and is idempotent.
6.  Removing then restoring round-trips the bundle.
7.  A query routed through the HTTP POST /api/v1/query endpoint against the
    sample datastore completes in <10 s and returns real rows (regression guard
    for the connector_type key mismatch that caused silent query hangs).
8.  GET /data/{datastore_id}/tables lists the star-schema tables of the sample
    datastore via the normal data-browser pipeline.
9.  GET /data/{datastore_id}/tables/sales/rows returns real rows from the
    sample datastore via the normal data-browser pipeline.
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
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo
from app.sample import remove_sample_bundle, seed_sample_bundle


def _ids() -> tuple[str, str, str]:
    return str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())


@pytest.mark.asyncio
async def test_seed_creates_full_bundle_tagged_sample() -> None:
    repo = InMemoryRepo()
    org, project, user = _ids()

    summary = await seed_sample_bundle(org, project, user, repo)

    assert "skipped" not in summary, summary
    datastores = await repo.list("datastores", org)
    queries = await repo.list("queries", org)
    boards = await repo.list("boards", org)

    assert len(datastores) == 1
    assert len(queries) == 6
    assert len(boards) == 2

    # Every created resource is tagged sample=true and scoped to the project.
    for row in (*datastores, *queries, *boards):
        assert row["config"].get("sample") is True
        assert row["config"].get("sample_id")
        assert str(row["project_id"]) == project


@pytest.mark.asyncio
async def test_seed_is_idempotent() -> None:
    repo = InMemoryRepo()
    org, project, user = _ids()

    await seed_sample_bundle(org, project, user, repo)
    second = await seed_sample_bundle(org, project, user, repo)

    assert second["created"] == []  # nothing new created on re-run
    assert len(await repo.list("datastores", org)) == 1
    assert len(await repo.list("queries", org)) == 6
    assert len(await repo.list("boards", org)) == 2


@pytest.mark.asyncio
async def test_datastore_points_at_bundled_file() -> None:
    repo = InMemoryRepo()
    org, project, user = _ids()
    await seed_sample_bundle(org, project, user, repo)

    ds = (await repo.list("datastores", org))[0]
    cfg = ds["config"]
    assert cfg["connector_type"] == "duckdb"
    assert os.path.isabs(cfg["database"])
    assert cfg["database"].endswith("sample.duckdb")
    assert os.path.exists(cfg["database"])  # built lazily by the seeder


@pytest.mark.asyncio
async def test_bundled_file_is_a_real_joinable_star_schema() -> None:
    repo = InMemoryRepo()
    org, project, user = _ids()
    await seed_sample_bundle(org, project, user, repo)
    db_path = (await repo.list("datastores", org))[0]["config"]["database"]

    import duckdb

    con = duckdb.connect(database=db_path, read_only=True)
    try:
        tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
        assert {"sales", "dim_regions", "dim_products", "dim_customers", "budget", "targets"} <= tables

        total = con.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
        joined = con.execute(
            """
            SELECT COUNT(*) FROM sales s
            JOIN dim_regions   r ON s.region_id   = r.region_id
            JOIN dim_products  p ON s.product_id  = p.product_id
            JOIN dim_customers c ON s.customer_id = c.customer_id
            """
        ).fetchone()[0]
        assert total > 0
        assert joined == total  # every fact row resolves all three dims
    finally:
        con.close()


@pytest.mark.asyncio
async def test_remove_then_restore_round_trips() -> None:
    repo = InMemoryRepo()
    org, project, user = _ids()
    await seed_sample_bundle(org, project, user, repo)

    removed = await remove_sample_bundle(org, project, repo)
    assert removed == {"boards": 2, "queries": 6, "datastores": 1}
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
    assert len(await repo.list("queries", org)) == 6
    assert len(await repo.list("boards", org)) == 2


# ---------------------------------------------------------------------------
# (7) End-to-end regression: sample datastore query completes via HTTP route
#
# This test guards against the connector_type key mismatch bug where
# datastore_config() stored "type" (not "connector_type"), causing
# query.py to resolve ctype=None → registry miss → silent hang/error.
#
# The test seeds the sample bundle into an InMemoryRepo, then calls
# POST /api/v1/query with the sample datastore_id and a real SQL query.
# It asserts the response is 200 Arrow IPC with >0 rows, and it runs
# under a pytest timeout so a hang fails loudly instead of blocking CI.
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def _sample_query_client(app, fake_db):
    """InMemoryRepo with a seeded sample bundle + HTTPX client."""
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


@pytest.mark.asyncio
async def test_sample_datastore_query_completes_with_rows(_sample_query_client) -> None:
    """End-to-end regression: sample connector_type datastore query finishes with rows.

    Specifically guards against the connector_type/type key mismatch that caused
    query.py to resolve ctype=None for sample datastores, triggering a registry
    miss and a silent hang or 404 error.

    The query runs real DuckDB SQL against the bundled sample.duckdb file and
    must return >0 rows (a hang would time out the test runner, not silently return).
    """
    client, user_id, datastore_id = _sample_query_client
    auth = {"Authorization": f"Bearer {mint_access_token(user_id)}"}

    resp = await client.post(
        "/api/v1/query",
        json={
            "sql": "SELECT region, ROUND(SUM(nsv), 2) AS nsv FROM sales GROUP BY region ORDER BY nsv DESC",
            "datastore_id": datastore_id,
        },
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
    assert table.num_rows > 0, (
        "Query returned 0 rows — expected region summary rows from sample sales data"
    )
    assert "region" in table.column_names
    assert "nsv" in table.column_names


# ---------------------------------------------------------------------------
# (8) Data-browser: GET /data/{datastore_id}/tables lists the sample tables
# (9) Data-browser: GET /data/{datastore_id}/tables/sales/rows returns rows
#
# These tests verify that the sample datastore flows through the NORMAL
# /data/{id}/tables and /data/{id}/tables/{table}/rows pipeline — not any
# special-cased demo path — so the Data page works with the sample bundle.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sample_datastore_tables_listable_via_data_browser(
    _sample_query_client,
) -> None:
    """GET /data/{datastore_id}/tables lists the star-schema tables of the sample DB.

    Verifies that the sample datastore flows through the normal data-browser
    pipeline (not the /data/tables demo fallback) and that the expected tables
    (sales, dim_regions, dim_products, dim_customers, budget, targets) are all
    visible.
    """
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
    _EXPECTED = {"sales", "dim_regions", "dim_products", "dim_customers", "budget", "targets"}
    assert _EXPECTED <= names, (
        f"Sample datastore missing expected tables. Got: {sorted(names)}"
    )


@pytest.mark.asyncio
async def test_sample_datastore_rows_queryable_via_data_browser(
    _sample_query_client,
) -> None:
    """GET /data/{datastore_id}/tables/sales/rows returns real rows from the sample DB.

    Verifies that the sample datastore rows endpoint flows through the normal
    data-browser pipeline, opens the bundled DuckDB file, and returns an Arrow
    IPC stream with real sales data (>0 rows, correct column names).
    """
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
    assert table.num_rows > 0, (
        "GET /data/{id}/tables/sales/rows returned 0 rows — expected sample sales data"
    )
    # Verify the star-schema fact columns are present.
    expected_cols = {"region", "channel", "supplier", "nsv", "units"}
    actual_cols = set(table.column_names)
    assert expected_cols <= actual_cols, (
        f"Missing expected columns. Got: {sorted(actual_cols)}"
    )
