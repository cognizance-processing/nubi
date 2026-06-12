"""Tests for the EDITABLE per-project demo lakehouse.

Product directive: when a user gets demo data it must be their OWN, per-PROJECT
isolated FILES in the managed lakehouse object storage (S3 in cloud, the
local-file backend in dev) — NOT a server-local ``.duckdb`` file — and the cells
must still be EDITABLE via rewrite-on-edit.

Coverage
--------
1.  ``provision_demo_parquet`` writes one parquet per demo table under the
    per-project prefix ``orgs/<org>/projects/<project>/demo/...`` (NOT a local
    ``.duckdb``), each carrying a synthetic ``_row_id``; idempotent.
2.  ``seed_sample_bundle`` (local-file lake root) provisions the editable
    connector — ``s3_views``/parquet, ``editable_parquet=true``, NOT
    managed/system, name "Demo Lakehouse".
3.  Columns endpoint reports the demo table writable with the ``_row_id``
    identity; a PATCH edits a cell and the change PERSISTS (re-read shows it).
4.  INSERT and DELETE rewrite correctly + persist.
5.  A demo query runs against the editable connector via POST /api/v1/query.
6.  Cross-project / cross-org prefixes are isolated.
7.  The no-storage fallback still produces the view-based read-only demo.
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
from app.demo_lakehouse import (
    ROW_ID_COL,
    demo_table_uris,
    editable_demo_datastore_config,
    editable_demo_supported,
    provision_demo_parquet,
)
from app.lakehouse.managed import project_demo_prefix
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo
from app.routes.data_browser import (
    _build_writable_duckdb_connector,
    _parquet_writable_meta,
)
from app.sample import seed_sample_bundle
from seed_data.generators import ALL_TABLES

_S3_ENV_VARS = ("S3_ACCESS_KEY", "AWS_ACCESS_KEY_ID")
_LAKE_DIR_ENV_VARS = ("NUBI_MANAGED_LAKE_DIR", "NUBI_LOCAL_LAKE_DIR", "NUBI_DEMO_LAKE_DIR")


def _ids() -> tuple[str, str, str]:
    return str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())


def _local_lake_env(monkeypatch: pytest.MonkeyPatch, lake_dir) -> None:
    """Configure a LOCAL-FILE lake root (editable mode), with no S3 creds."""
    for var in _S3_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    for var in _LAKE_DIR_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("NUBI_BUCKET_URI", raising=False)
    monkeypatch.setenv("NUBI_MANAGED_LAKE_DIR", str(lake_dir))


def _no_storage_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """No S3, no local lake root → read-only view fallback."""
    for var in (*_S3_ENV_VARS, *_LAKE_DIR_ENV_VARS):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("NUBI_BUCKET_URI", raising=False)


# ---------------------------------------------------------------------------
# 1. Per-project parquet provisioning (NOT a local .duckdb)
# ---------------------------------------------------------------------------


def test_provision_writes_per_project_parquet_with_row_id(monkeypatch, tmp_path) -> None:
    import pyarrow.parquet as pq  # noqa: PLC0415

    _local_lake_env(monkeypatch, tmp_path)
    org, project, _ = _ids()

    uris = provision_demo_parquet(org, project)
    assert uris is not None
    assert set(uris) >= set(ALL_TABLES)

    # No on-disk .duckdb anywhere under the lake root.
    for dirpath, _dirs, files in os.walk(tmp_path):
        for f in files:
            assert not f.endswith(".duckdb"), f"unexpected .duckdb file {f}"

    # Per-project, server-pinned layout: every parquet lives under the prefix.
    prefix = project_demo_prefix(org, project)
    for table, uri in uris.items():
        assert prefix.rstrip("/") in uri, f"{table} uri {uri} not under {prefix}"
        assert uri.endswith(".parquet")
        # The file exists on disk (local-file backend) and has _row_id.
        assert os.path.isfile(uri), uri
        cols = pq.read_table(uri).column_names
        assert ROW_ID_COL in cols, f"{table} missing {ROW_ID_COL}"


def test_provision_is_idempotent(monkeypatch, tmp_path) -> None:
    _local_lake_env(monkeypatch, tmp_path)
    org, project, _ = _ids()

    uris = provision_demo_parquet(org, project)
    one = uris["budget"]
    mtime = os.path.getmtime(one)
    uris2 = provision_demo_parquet(org, project)
    assert uris2["budget"] == one
    assert os.path.getmtime(one) == mtime  # not rewritten


def test_parquet_meta_reports_writable_row_id(monkeypatch, tmp_path) -> None:
    _local_lake_env(monkeypatch, tmp_path)
    org, project, _ = _ids()
    uris = provision_demo_parquet(org, project)
    cfg = editable_demo_datastore_config(uris)
    conn = _build_writable_duckdb_connector(cfg)
    for table in ALL_TABLES:
        writable, pk = _parquet_writable_meta(conn, table)
        assert writable is True, f"{table} not writable"
        assert pk == [ROW_ID_COL], f"{table} pk = {pk}"


def test_cross_project_and_cross_org_prefixes_isolated(monkeypatch, tmp_path) -> None:
    _local_lake_env(monkeypatch, tmp_path)
    org_a, proj_a, _ = _ids()
    org_b, proj_b, _ = _ids()

    a = demo_table_uris(org_a, proj_a)
    b_same_org = demo_table_uris(org_a, proj_b)
    b_other_org = demo_table_uris(org_b, proj_a)

    assert a["budget"] != b_same_org["budget"]   # per-project isolation
    assert a["budget"] != b_other_org["budget"]  # per-org isolation
    assert project_demo_prefix(org_a, proj_a) in a["budget"]
    assert project_demo_prefix(org_b, proj_a) in b_other_org["budget"]


# ---------------------------------------------------------------------------
# 2. seed_sample_bundle provisions the editable per-project connector
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seed_provisions_editable_parquet_connector(monkeypatch, tmp_path) -> None:
    _local_lake_env(monkeypatch, tmp_path)
    repo = InMemoryRepo()
    org, project, user = _ids()

    summary = await seed_sample_bundle(org, project, user, repo)
    assert "skipped" not in summary, summary

    ds = (await repo.list("datastores", org))[0]
    cfg = ds["config"]
    assert cfg["connector_type"] == "duckdb"
    assert cfg["database"] == ":memory:"
    assert cfg.get("editable_parquet") is True
    assert isinstance(cfg.get("s3_views"), dict) and cfg["s3_views"]
    # User OWNS it: not hidden/managed/system.
    assert cfg.get("system") is not True
    assert cfg.get("managed_lake") is not True
    assert cfg.get("sample") is True
    assert ds["name"] == "Demo Lakehouse"


# ---------------------------------------------------------------------------
# 3-5. Query + edit cells through the HTTP surface (rewrite-on-edit)
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

    resp = await client.get(
        f"/api/v1/data/{datastore_id}/tables/budget/columns", headers=auth
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["writable"] is True
    assert body["primary_key"] == [ROW_ID_COL]
    # _row_id surfaced as non-editable / hidden.
    rid = next(c for c in body["columns"] if c["name"] == ROW_ID_COL)
    assert rid["editable"] is False

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

    # Persisted: a fresh read of the rows reflects the new value.
    rows = await client.get(
        f"/api/v1/data/{datastore_id}/tables/budget/rows?limit=5000", headers=auth
    )
    assert rows.status_code == 200, rows.text
    tbl = pa_ipc.open_stream(BytesIO(rows.content)).read_all().to_pylist()
    edited = next(r for r in tbl if r[ROW_ID_COL] == 1)
    assert float(edited["budget_nsv"]) == 123456.78


@pytest.mark.asyncio
async def test_insert_and_delete_rewrite_persist(_editable_client) -> None:
    client, user_id, datastore_id = _editable_client
    auth = {"Authorization": f"Bearer {mint_access_token(user_id)}"}

    # Baseline row count.
    base = await client.get(
        f"/api/v1/data/{datastore_id}/tables/budget/rows?limit=5000", headers=auth
    )
    base_rows = pa_ipc.open_stream(BytesIO(base.content)).read_all().to_pylist()
    base_n = len(base_rows)
    sample_cols = {k for k in base_rows[0] if k != ROW_ID_COL}

    # INSERT a row (without supplying _row_id — server assigns it).
    values = {c: base_rows[0][c] for c in sample_cols}
    ins = await client.post(
        f"/api/v1/data/{datastore_id}/tables/budget/rows",
        headers=auth,
        json={"values": values},
    )
    assert ins.status_code == 201, ins.text
    new_rid = ins.json()["row"][ROW_ID_COL]
    assert new_rid == base_n + 1  # max+1

    after_ins = await client.get(
        f"/api/v1/data/{datastore_id}/tables/budget/rows?limit=5000", headers=auth
    )
    after_ins_rows = pa_ipc.open_stream(BytesIO(after_ins.content)).read_all().to_pylist()
    assert len(after_ins_rows) == base_n + 1

    # DELETE the inserted row.
    dele = await client.request(
        "DELETE",
        f"/api/v1/data/{datastore_id}/tables/budget/rows",
        headers=auth,
        json={"pk": {ROW_ID_COL: new_rid}},
    )
    assert dele.status_code == 200, dele.text
    assert dele.json() == {"deleted": 1}

    after_del = await client.get(
        f"/api/v1/data/{datastore_id}/tables/budget/rows?limit=5000", headers=auth
    )
    after_del_rows = pa_ipc.open_stream(BytesIO(after_del.content)).read_all().to_pylist()
    assert len(after_del_rows) == base_n
    assert all(r[ROW_ID_COL] != new_rid for r in after_del_rows)


@pytest.mark.asyncio
async def test_injection_in_cell_value_is_inert(_editable_client) -> None:
    client, user_id, datastore_id = _editable_client
    auth = {"Authorization": f"Bearer {mint_access_token(user_id)}"}

    # Injection in a CELL VALUE must be stored literally (bound param), inert.
    payload = "x'); DROP TABLE t; --"
    patch = await client.patch(
        f"/api/v1/data/{datastore_id}/tables/dim_customers/rows",
        headers=auth,
        json={"pk": {ROW_ID_COL: 1}, "set": {"customer": payload}},
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["row"]["customer"] == payload

    # Table still intact + payload stored literally on re-read.
    rows = await client.get(
        f"/api/v1/data/{datastore_id}/tables/dim_customers/rows?limit=5000",
        headers=auth,
    )
    assert rows.status_code == 200, rows.text
    tbl = pa_ipc.open_stream(BytesIO(rows.content)).read_all().to_pylist()
    edited = next(r for r in tbl if r[ROW_ID_COL] == 1)
    assert edited["customer"] == payload


# ---------------------------------------------------------------------------
# 7. No-storage fallback still produces the view-based read-only demo
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
    assert cfg["database"] == ":memory:"
    assert "read_parquet(" in cfg["view_sql"]
    assert cfg.get("editable_parquet") is not True
    assert ds["name"] == "Sample"
