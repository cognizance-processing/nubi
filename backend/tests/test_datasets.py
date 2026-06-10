"""Tests for the Datasets lakehouse endpoints — POST /api/v1/datasets/*.

Strategy
--------
- InMemoryRepo injected via set_repo().
- InMemoryDatasetsCatalog injected via set_catalog().
- Local file:// storage using a pytest tmp_path (NUBI_BUCKET_ROOT env var).
- No network, no MinIO, no external deps — fully hermetic.
- DuckDB runs in-process for CSV→Parquet and SQL→Parquet conversions.

Coverage
--------
1.  POST /datasets/upload with a small CSV → 200, dataset row returned with schema.
2.  The upload creates a Parquet file on disk at the expected path.
3.  The upload registers a linked 'datastores' row (datastore_id is set).
4.  GET /datasets → lists the uploaded dataset.
5.  GET /datasets/{id} → returns the single dataset row.
6.  GET /datasets/{bad_id} → 404.
7.  POST /datasets/materialize with a valid SELECT SQL → 200, dataset row returned.
8.  The materialised Parquet is readable by a second DuckDB query (round-trip).
9.  No token → 401 on upload.
10. No token → 401 on materialize.
"""

from __future__ import annotations

import io
import os
import uuid
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.datasets.catalog import InMemoryDatasetsCatalog
from app.datasets import set_catalog
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo

# Import datasets routes so they register on api_router before the test client runs.
import app.routes.datasets  # noqa: F401, E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(user_id: str | None = None) -> dict[str, Any]:
    return {
        "id": user_id or str(uuid.uuid4()),
        "email": "test@example.com",
        "name": "Test User",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2025-01-01T00:00:00+00:00",
    }


def _auth_headers(user_id: str) -> dict[str, str]:
    token = mint_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


_SMALL_CSV = b"id,name,value\n1,alpha,1.1\n2,beta,2.2\n3,gamma,3.3\n"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def ds_client(app, fake_db, tmp_path, monkeypatch):
    """Async HTTPX client with InMemoryRepo + InMemoryDatasetsCatalog, pre-seeded user/org."""
    # Point storage at tmp_path so we never write to /tmp during tests
    monkeypatch.setenv("NUBI_BUCKET_ROOT", str(tmp_path / "nubi-datasets"))
    monkeypatch.delenv("NUBI_BUCKET_URI", raising=False)

    repo = InMemoryRepo()
    set_repo(repo)

    catalog = InMemoryDatasetsCatalog()
    set_catalog(catalog)

    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    user = _make_user(user_id=user_id)

    fake_db.users[user_id] = user
    repo.seed_org_member(org_id=org_id, user_id=user_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as ac:
        yield ac, user_id, org_id, repo, catalog

    set_repo(None)
    set_catalog(None)


# ---------------------------------------------------------------------------
# Upload tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_csv_returns_dataset_row(ds_client):
    """POST /datasets/upload → 200 with dataset row containing schema."""
    ac, user_id, _org_id, _repo, _catalog = ds_client
    resp = await ac.post(
        "/api/v1/datasets/upload",
        headers=_auth_headers(user_id),
        files={"file": ("data.csv", io.BytesIO(_SMALL_CSV), "text/csv")},
        data={"name": "test-upload"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "test-upload"
    assert body["source"] == "upload"
    assert body["format"] == "parquet"
    assert body["id"] is not None
    assert body["storage_uri"].endswith("data.parquet")
    # Schema should have been inferred
    schema = body.get("schema_json") or []
    col_names = {c["name"] for c in schema}
    assert {"id", "name", "value"}.issubset(col_names), f"Got schema: {schema}"


@pytest.mark.asyncio
async def test_upload_parquet_written_to_disk(ds_client, tmp_path):
    """POST /datasets/upload → Parquet file exists at expected path."""
    ac, user_id, org_id, _repo, _catalog = ds_client
    resp = await ac.post(
        "/api/v1/datasets/upload",
        headers=_auth_headers(user_id),
        files={"file": ("data.csv", io.BytesIO(_SMALL_CSV), "text/csv")},
        data={"name": "disk-test"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    dataset_id = body["id"]
    # The parquet should be at <tmp_path>/nubi-datasets/datasets/<org_id>/<dataset_id>/data.parquet
    expected = (
        tmp_path / "nubi-datasets" / "datasets" / org_id / dataset_id / "data.parquet"
    )
    assert expected.is_file(), f"Parquet not found at {expected}"


@pytest.mark.asyncio
async def test_upload_registers_datastore(ds_client):
    """POST /datasets/upload → datastore_id is set and datastore exists in repo."""
    ac, user_id, org_id, repo, _catalog = ds_client
    resp = await ac.post(
        "/api/v1/datasets/upload",
        headers=_auth_headers(user_id),
        files={"file": ("data.csv", io.BytesIO(_SMALL_CSV), "text/csv")},
        data={"name": "ds-link-test"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    datastore_id = body.get("datastore_id")
    assert datastore_id is not None, "datastore_id not set in response"

    # Verify the datastore exists in the repo
    ds = await repo.get("datastores", org_id, datastore_id)
    assert ds is not None, f"Datastore {datastore_id} not found in repo"
    cfg = ds["config"]
    assert cfg.get("connector_type") == "duckdb"
    assert "parquet_path" in cfg


# ---------------------------------------------------------------------------
# List / Get tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_datasets_returns_uploaded(ds_client):
    """GET /datasets → lists the uploaded dataset."""
    ac, user_id, _org_id, _repo, _catalog = ds_client
    # Upload first
    await ac.post(
        "/api/v1/datasets/upload",
        headers=_auth_headers(user_id),
        files={"file": ("data.csv", io.BytesIO(_SMALL_CSV), "text/csv")},
        data={"name": "list-test"},
    )
    resp = await ac.get("/api/v1/datasets", headers=_auth_headers(user_id))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "datasets" in body
    assert len(body["datasets"]) == 1
    assert body["datasets"][0]["name"] == "list-test"


@pytest.mark.asyncio
async def test_get_dataset_returns_correct_row(ds_client):
    """GET /datasets/{id} → returns the dataset row by id."""
    ac, user_id, _org_id, _repo, _catalog = ds_client
    upload_resp = await ac.post(
        "/api/v1/datasets/upload",
        headers=_auth_headers(user_id),
        files={"file": ("data.csv", io.BytesIO(_SMALL_CSV), "text/csv")},
        data={"name": "get-test"},
    )
    assert upload_resp.status_code == 200
    dataset_id = upload_resp.json()["id"]

    resp = await ac.get(f"/api/v1/datasets/{dataset_id}", headers=_auth_headers(user_id))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == dataset_id
    assert body["name"] == "get-test"


@pytest.mark.asyncio
async def test_get_dataset_unknown_id_returns_404(ds_client):
    """GET /datasets/{bad_id} → 404."""
    ac, user_id, _org_id, _repo, _catalog = ds_client
    fake_id = str(uuid.uuid4())
    resp = await ac.get(f"/api/v1/datasets/{fake_id}", headers=_auth_headers(user_id))
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# Materialize tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_materialize_returns_dataset_row(ds_client):
    """POST /datasets/materialize → 200 with dataset row (source=materialized)."""
    ac, user_id, _org_id, _repo, _catalog = ds_client
    resp = await ac.post(
        "/api/v1/datasets/materialize",
        headers=_auth_headers(user_id),
        json={"sql": "SELECT 1 AS n, 'hello' AS msg", "name": "mat-test"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "mat-test"
    assert body["source"] == "materialized"
    assert body["id"] is not None
    assert body["datastore_id"] is not None


@pytest.mark.asyncio
async def test_materialize_output_is_queryable(ds_client, tmp_path):
    """Materialised Parquet is readable by a second DuckDB query (round-trip)."""
    ac, user_id, org_id, _repo, _catalog = ds_client
    resp = await ac.post(
        "/api/v1/datasets/materialize",
        headers=_auth_headers(user_id),
        json={"sql": "SELECT 42 AS answer, 'world' AS word", "name": "roundtrip-test"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    dataset_id = body["id"]

    # Find the parquet file
    parquet_path = (
        tmp_path / "nubi-datasets" / "datasets" / org_id / dataset_id / "data.parquet"
    )
    assert parquet_path.is_file(), f"Parquet not found at {parquet_path}"

    # Re-query the Parquet with a fresh DuckDB connection
    import duckdb  # noqa: PLC0415

    conn = duckdb.connect(":memory:")
    result = conn.execute(f"SELECT * FROM read_parquet('{parquet_path}')").fetchall()
    assert len(result) == 1
    row = result[0]
    assert row[0] == 42   # answer
    assert row[1] == "world"  # word


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_no_token_returns_401(ds_client):
    """POST /datasets/upload without auth → 401."""
    ac, _user_id, _org_id, _repo, _catalog = ds_client
    resp = await ac.post(
        "/api/v1/datasets/upload",
        files={"file": ("data.csv", io.BytesIO(_SMALL_CSV), "text/csv")},
        data={"name": "no-auth"},
    )
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_materialize_no_token_returns_401(ds_client):
    """POST /datasets/materialize without auth → 401."""
    ac, _user_id, _org_id, _repo, _catalog = ds_client
    resp = await ac.post(
        "/api/v1/datasets/materialize",
        json={"sql": "SELECT 1", "name": "no-auth"},
    )
    assert resp.status_code == 401, resp.text
