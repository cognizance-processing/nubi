"""Tests for the Data Browser endpoints — GET /api/v1/data/*.

Strategy
--------
- Use ``InMemoryRepo`` injected via ``set_repo()`` — no live DB required.
- Seed org memberships on the repo directly (``repo.seed_org_member()``).
- The demo connector (no datastore_id) is always available; its ``demo`` table
  has columns (id, name, value, active) and 5 rows.
- Arrow IPC responses are parsed to verify schema and row count.

Coverage
--------
1.  GET /data/tables (demo)         → 200, tables list contains "demo"
2.  GET /data/tables/demo/columns   → 200, columns list with correct names
3.  GET /data/tables/demo/rows      → 200, Arrow IPC with 5 rows
4.  GET /data/tables/demo/rows?limit=2 → 200, Arrow IPC with 2 rows
5.  GET /data/tables/missing/columns → 404
6.  GET /data/tables/missing/rows   → 404
7.  No token on /data/tables        → 401
8.  GET /data/{datastore_id}/tables with unknown id → 404
"""

from __future__ import annotations

import uuid
from typing import Any

import pyarrow as pa
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo

# Import data_browser BEFORE main / resources loads — this registers the
# /data/* routes on api_router ahead of the generic /{resource} catch-all.
import app.routes.data_browser  # noqa: F401, E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(user_id: str | None = None, email: str = "alice@example.com") -> dict[str, Any]:
    return {
        "id": user_id or str(uuid.uuid4()),
        "email": email,
        "name": "Alice",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


def _auth_headers(user_id: str) -> dict[str, str]:
    token = mint_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def browser_client(app, fake_db):
    """Async HTTPX client with InMemoryRepo, pre-seeded user + org."""
    repo = InMemoryRepo()
    set_repo(repo)

    alice_id = str(uuid.uuid4())
    alice_org_id = str(uuid.uuid4())
    alice = _make_user(user_id=alice_id, email="alice@example.com")

    # Seed user in FakeDB so current_user dependency can resolve it.
    fake_db.users[alice_id] = alice
    # Seed org membership in the InMemoryRepo.
    repo.seed_org_member(org_id=alice_org_id, user_id=alice_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as ac:
        yield ac, alice_id, alice_org_id, repo

    set_repo(None)


# ---------------------------------------------------------------------------
# Tests — demo connector (no datastore_id)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_demo_tables_returns_demo(browser_client):
    """GET /data/tables → 200 and the demo table is listed."""
    ac, user_id, _org_id, _repo = browser_client
    resp = await ac.get("/api/v1/data/tables", headers=_auth_headers(user_id))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "tables" in body
    names = {t["name"] for t in body["tables"]}
    assert "demo" in names


@pytest.mark.asyncio
async def test_list_demo_columns_returns_correct_schema(browser_client):
    """GET /data/tables/demo/columns → 200 with expected column names."""
    ac, user_id, _org_id, _repo = browser_client
    resp = await ac.get("/api/v1/data/tables/demo/columns", headers=_auth_headers(user_id))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "columns" in body
    col_names = {c["name"] for c in body["columns"]}
    assert {"id", "name", "value", "active"}.issubset(col_names)


@pytest.mark.asyncio
async def test_get_demo_rows_returns_arrow_ipc(browser_client):
    """GET /data/tables/demo/rows → 200 Arrow IPC with 5 rows."""
    ac, user_id, _org_id, _repo = browser_client
    resp = await ac.get(
        "/api/v1/data/tables/demo/rows",
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers.get("content-type", "").startswith("application/vnd.apache.arrow.stream")
    buf = pa.py_buffer(resp.content)
    reader = pa.ipc.open_stream(buf)
    tbl = reader.read_all()
    assert tbl.num_rows == 5
    assert "id" in tbl.schema.names
    assert "name" in tbl.schema.names


@pytest.mark.asyncio
async def test_get_demo_rows_with_limit(browser_client):
    """GET /data/tables/demo/rows?limit=2 → 200 Arrow IPC with 2 rows."""
    ac, user_id, _org_id, _repo = browser_client
    resp = await ac.get(
        "/api/v1/data/tables/demo/rows?limit=2",
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 200, resp.text
    buf = pa.py_buffer(resp.content)
    reader = pa.ipc.open_stream(buf)
    tbl = reader.read_all()
    assert tbl.num_rows == 2


@pytest.mark.asyncio
async def test_columns_missing_table_returns_404(browser_client):
    """GET /data/tables/nonexistent/columns → 404."""
    ac, user_id, _org_id, _repo = browser_client
    resp = await ac.get(
        "/api/v1/data/tables/nonexistent/columns",
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_rows_missing_table_returns_404(browser_client):
    """GET /data/tables/nonexistent/rows → 404."""
    ac, user_id, _org_id, _repo = browser_client
    resp = await ac.get(
        "/api/v1/data/tables/nonexistent/rows",
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# Tests — authentication
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tables_no_token_returns_401(browser_client):
    """GET /data/tables without auth → 401."""
    ac, _user_id, _org_id, _repo = browser_client
    resp = await ac.get("/api/v1/data/tables")
    assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------------------
# Tests — real connector (datastore_id path) with unknown id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tables_unknown_datastore_returns_404(browser_client):
    """GET /data/{unknown_id}/tables → 404 (datastore not found)."""
    ac, user_id, _org_id, _repo = browser_client
    fake_id = str(uuid.uuid4())
    resp = await ac.get(
        f"/api/v1/data/{fake_id}/tables",
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_list_tables_non_duckdb_connector_returns_400(browser_client):
    """GET /data/{id}/tables for a postgres connector → 400 (unsupported)."""
    ac, user_id, org_id, repo = browser_client
    # Create a postgres connector row in the InMemoryRepo
    ds_row = await repo.create(
        resource="datastores",
        org_id=org_id,
        created_by=user_id,
        name="pg-test",
        config={"connector_type": "postgres", "host": "localhost"},
    )
    ds_id = ds_row["id"]
    resp = await ac.get(
        f"/api/v1/data/{ds_id}/tables",
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 400, resp.text
