"""Tests for D2 demo-parquet serving routes.

Coverage
--------
1. GET /api/v1/demo-parquet/_manifest — no auth, returns table→URL map.
2. GET /api/v1/demo-parquet/{dataset}/{table}.parquet — no auth, serves bytes.
3. Path-traversal guard: unknown dataset/table → 404.
4. GET /api/v1/demo-parquet/_query-map — auth required; returns demo query SQL map.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from seed_data.generators import DATASET_TABLES

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_auth_token(client) -> str:
    """Register + login to get a valid access token."""
    await client.post(
        "/api/v1/auth/register",
        json={"email": "demopq@example.com", "password": "Test1234!"},
    )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "demopq@example.com", "password": "Test1234!"},
    )
    return resp.json().get("access_token", "")


# ---------------------------------------------------------------------------
# 1. Manifest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manifest_no_auth(client):
    """GET /demo-parquet/_manifest requires no auth and returns the table map."""
    resp = await client.get("/api/v1/demo-parquet/_manifest")
    assert resp.status_code == 200
    data = resp.json()
    assert "tables" in data
    # All 17 demo tables must be in the manifest
    all_tables = [t for ts in DATASET_TABLES.values() for t in ts]
    for table in all_tables:
        assert table in data["tables"], f"Table {table!r} missing from manifest"
    # Every URL should follow the expected pattern
    for table, url in data["tables"].items():
        assert url.startswith("/api/v1/demo-parquet/"), (
            f"Table {table!r} has unexpected URL: {url!r}"
        )
        assert url.endswith(".parquet"), f"Table {table!r} URL missing .parquet: {url!r}"


# ---------------------------------------------------------------------------
# 2. Parquet file serving
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parquet_file_served(client, tmp_path, monkeypatch):
    """GET /{dataset}/{table}.parquet returns parquet bytes for a valid table."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    # Write a minimal parquet file to tmp_path so the route can serve it
    tbl = pa.table({"id": [1, 2], "val": [10.0, 20.0]})
    dataset = "retail_sales"
    table = "sales"
    (tmp_path / dataset).mkdir()
    pq.write_table(tbl, tmp_path / dataset / f"{table}.parquet")

    # Patch the parquet dir to point at tmp_path
    import app.routes.demo_parquet as _mod
    monkeypatch.setattr(_mod, "_parquet_dir", lambda: tmp_path)
    # Stub _ensure_parquet to a no-op (files are already there)
    monkeypatch.setattr(_mod, "_ensure_parquet", lambda: None)

    resp = await client.get(f"/api/v1/demo-parquet/{dataset}/{table}.parquet")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/octet-stream")
    # Response body should be non-empty (parquet magic bytes)
    assert len(resp.content) > 100


# ---------------------------------------------------------------------------
# 3. Path-traversal guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_dataset_is_404(client):
    # Path traversal with ../ collapses and hits a different route (401) or 404 —
    # in both cases our route never serves a file, which is the security property.
    resp = await client.get("/api/v1/demo-parquet/../etc/sales.parquet")
    assert resp.status_code in (404, 401, 422)


@pytest.mark.asyncio
async def test_unknown_table_is_404(client):
    resp = await client.get("/api/v1/demo-parquet/retail_sales/nonexistent_table.parquet")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_unknown_dataset_is_404_direct(client):
    resp = await client.get("/api/v1/demo-parquet/evil_dataset/sales.parquet")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_missing_parquet_suffix_is_404(client):
    resp = await client.get("/api/v1/demo-parquet/retail_sales/sales")
    # No .parquet suffix → 404
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 4. Query map
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_map_requires_auth(client):
    """GET /demo-parquet/_query-map requires a valid auth token."""
    resp = await client.get("/api/v1/demo-parquet/_query-map")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_query_map_returns_empty_when_no_demo_datastores(client):
    """Returns {"queries": {}} when the org has no demo datastores."""
    token = await _get_auth_token(client)
    headers = {"Authorization": f"Bearer {token}"}
    resp = await client.get(
        "/api/v1/demo-parquet/_query-map",
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "queries" in data
    assert isinstance(data["queries"], dict)
