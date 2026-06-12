"""Tests for the EDITABLE demo lakehouse (``app.demo_lakehouse`` + ``app.sample``).

The product goal: when a user opts into demo data, it should be their OWN,
OWNED, EDITABLE copy in the lakehouse — a real connector backed by native
DuckDB BASE TABLEs (so the data grid can edit cells), not the shared read-only
``read_parquet`` views.

Coverage
--------
1.  ``materialize_demo_duckdb`` writes all 17 demo datasets as native BASE
    TABLEs with a synthetic ``_row_id`` PRIMARY KEY, idempotently.
2.  ``_writable_meta_duckdb`` reports those tables writable with the ``_row_id``
    identity.
3.  ``seed_sample_bundle`` (local lake root configured) provisions the editable
    connector — ``config.database`` is the abs ``.duckdb`` path (not ``:memory:``,
    no ``view_sql``) and it is NOT marked managed/system.
4.  A demo query runs against the editable connector via POST /api/v1/query.
5.  Editing a cell via the data-browser PATCH path persists on a demo table.
6.  The cloud/read-only fallback (no local lake root) still produces the
    view-based ``:memory:`` demo.
"""

from __future__ import annotations

import os
import uuid
from io import BytesIO

import duckdb
import pyarrow.ipc as pa_ipc
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.demo_lakehouse import (
    ROW_ID_COL,
    editable_demo_datastore_config,
    editable_demo_supported,
    materialize_demo_duckdb,
)
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo
from app.routes.data_browser import (
    _build_writable_duckdb_connector,
    _writable_meta_duckdb,
)
from app.sample import seed_sample_bundle
from seed_data.generators import ALL_TABLES

_S3_ENV_VARS = ("S3_ACCESS_KEY", "AWS_ACCESS_KEY_ID")
_LAKE_DIR_ENV_VARS = ("NUBI_MANAGED_LAKE_DIR", "NUBI_LOCAL_LAKE_DIR", "NUBI_DEMO_LAKE_DIR")


def _ids() -> tuple[str, str, str]:
    return str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())


def _local_lake_env(monkeypatch: pytest.MonkeyPatch, lake_dir) -> None:
    """Configure a LOCAL lake root (editable mode), with no S3 creds."""
    for var in _S3_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    for var in _LAKE_DIR_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NUBI_DEMO_LAKE_DIR", str(lake_dir))


def _no_storage_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """No S3, no local lake root → read-only view fallback."""
    for var in (*_S3_ENV_VARS, *_LAKE_DIR_ENV_VARS):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# 1+2. Materialisation → native, writable BASE TABLEs
# ---------------------------------------------------------------------------


def test_materialize_writes_native_writable_tables(monkeypatch, tmp_path) -> None:
    _local_lake_env(monkeypatch, tmp_path)
    org, project, _ = _ids()

    path = materialize_demo_duckdb(org, project)
    assert path is not None
    assert os.path.isabs(path) and path.endswith(".duckdb")
    # Tenant-scoped, server-pinned layout.
    assert f"orgs{os.sep}{org}{os.sep}demo{os.sep}" in path

    con = duckdb.connect(path, read_only=True)
    try:
        rows = con.execute(
            "SELECT table_name, table_type FROM information_schema.tables "
            "WHERE table_schema NOT IN ('information_schema', 'pg_catalog')"
        ).fetchall()
    finally:
        con.close()
    by_name = {r[0]: r[1] for r in rows}
    for table in ALL_TABLES:
        assert by_name.get(table) == "BASE TABLE", f"{table} is not a native BASE TABLE"

    # Each table is writable via the data-browser introspection with _row_id PK.
    cfg = editable_demo_datastore_config(path)
    conn = _build_writable_duckdb_connector(cfg)
    for table in ALL_TABLES:
        writable, pk = _writable_meta_duckdb(conn, table)
        assert writable is True, f"{table} not writable"
        assert pk == [ROW_ID_COL], f"{table} pk = {pk}"


def test_materialize_is_idempotent(monkeypatch, tmp_path) -> None:
    _local_lake_env(monkeypatch, tmp_path)
    org, project, _ = _ids()

    path = materialize_demo_duckdb(org, project)
    mtime = os.path.getmtime(path)
    path2 = materialize_demo_duckdb(org, project)
    assert path2 == path
    assert os.path.getmtime(path2) == mtime  # not rebuilt


# ---------------------------------------------------------------------------
# 3. seed_sample_bundle provisions the editable connector
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seed_provisions_editable_connector(monkeypatch, tmp_path) -> None:
    _local_lake_env(monkeypatch, tmp_path)
    repo = InMemoryRepo()
    org, project, user = _ids()

    summary = await seed_sample_bundle(org, project, user, repo)
    assert "skipped" not in summary, summary

    ds = (await repo.list("datastores", org))[0]
    cfg = ds["config"]
    assert cfg["connector_type"] == "duckdb"
    assert cfg["database"].endswith(".duckdb")
    assert cfg["database"] != ":memory:"
    assert "view_sql" not in cfg  # native tables, not views
    # User OWNS it: not hidden/managed/system.
    assert cfg.get("system") is not True
    assert cfg.get("managed_lake") is not True
    # Still tagged sample so remove/restore works.
    assert cfg.get("sample") is True
    assert ds["name"] == "Demo Lakehouse"


# ---------------------------------------------------------------------------
# 4+5. Query + edit a cell through the HTTP surface
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def _editable_client(app, fake_db, monkeypatch, tmp_path):
    _local_lake_env(monkeypatch, tmp_path)
    repo = InMemoryRepo()
    set_repo(repo)

    user_id, org_id, project_id = _ids()
    fake_db.users[user_id] = {
        "id": user_id,
        "email": "editable-demo@example.com",
        "name": "Editor",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    repo.seed_org_member(org_id=org_id, user_id=user_id)

    summary = await seed_sample_bundle(org_id, project_id, user_id, repo)
    assert "skipped" not in summary, summary
    datastore_id = summary["datastore_id"]

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as client:
        yield client, user_id, datastore_id

    set_repo(None)


@pytest.mark.asyncio
async def test_demo_query_runs_against_editable_connector(_editable_client) -> None:
    client, user_id, datastore_id = _editable_client
    auth = {"Authorization": f"Bearer {mint_access_token(user_id)}"}

    resp = await client.post(
        "/api/v1/query",
        json={
            "sql": "SELECT region, ROUND(SUM(nsv), 2) AS nsv FROM sales "
            "GROUP BY region ORDER BY nsv DESC",
            "datastore_id": datastore_id,
        },
        headers=auth,
    )
    assert resp.status_code == 200, resp.text[:500]
    table = pa_ipc.open_stream(BytesIO(resp.content)).read_all()
    assert table.num_rows > 0
    assert {"region", "nsv"} <= set(table.column_names)


@pytest.mark.asyncio
async def test_columns_report_writable_and_edit_cell_persists(_editable_client) -> None:
    client, user_id, datastore_id = _editable_client
    auth = {"Authorization": f"Bearer {mint_access_token(user_id)}"}

    # columns endpoint reports the demo table writable with the _row_id identity.
    resp = await client.get(
        f"/api/v1/data/{datastore_id}/tables/budget/columns", headers=auth
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["writable"] is True
    assert body["primary_key"] == [ROW_ID_COL]

    # Read a row's current value via DML PK = 1.
    patch = await client.patch(
        f"/api/v1/data/{datastore_id}/tables/budget/rows",
        headers=auth,
        json={"pk": {ROW_ID_COL: 1}, "set": {"budget_nsv": 123456.78}},
    )
    assert patch.status_code == 200, patch.text
    out = patch.json()
    assert out["updated"] == 1
    assert out["row"][ROW_ID_COL] == 1
    assert float(out["row"]["budget_nsv"]) == 123456.78


# ---------------------------------------------------------------------------
# 6. Cloud / read-only fallback still produces the view-based demo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_storage_falls_back_to_view_demo(monkeypatch) -> None:
    _no_storage_env(monkeypatch)
    assert editable_demo_supported() is False

    repo = InMemoryRepo()
    org, project, user = _ids()
    summary = await seed_sample_bundle(org, project, user, repo)
    assert "skipped" not in summary, summary

    ds = (await repo.list("datastores", org))[0]
    cfg = ds["config"]
    # Read-only view demo: :memory: + read_parquet views, no on-disk .duckdb file.
    assert cfg["database"] == ":memory:"
    assert "read_parquet(" in cfg["view_sql"]
    assert ds["name"] == "Sample"
