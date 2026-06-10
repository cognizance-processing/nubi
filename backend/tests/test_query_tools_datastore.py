"""Tests for the data-browser endpoints added to query_tools.py.

Covers:
  GET /api/v1/datastores/{id}/tables
  GET /api/v1/datastores/{id}/tables/{table}/preview

Strategy (mirrors test_data_browser.py)
----------------------------------------
- Inject an InMemoryRepo via set_repo() and seed an org membership.
- Create a *file-based* duckdb datastore pointing at a temp .duckdb file that
  has been pre-seeded with two tables, so introspection + preview return real
  data without a live DB.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo

# Ensure the query_tools router is imported (it is wired in main.py, but be
# explicit so the routes exist even if main hasn't been imported yet).
import app.routes.query_tools  # noqa: F401, E402


def _make_user(user_id: str, email: str = "alice@example.com") -> dict[str, Any]:
    return {
        "id": user_id,
        "email": email,
        "name": "Alice",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


def _auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


@pytest.fixture
def seeded_duckdb(tmp_path):
    """Create a duckdb file with sales + customers tables; return its path."""
    import duckdb

    db_file = tmp_path / "sample.duckdb"
    conn = duckdb.connect(str(db_file))
    conn.execute("CREATE TABLE customers (id INTEGER, name VARCHAR)")
    conn.execute("INSERT INTO customers VALUES (1, 'Acme'), (2, 'Globex')")
    conn.execute("CREATE TABLE sales (id INTEGER, amount DOUBLE, customer_id INTEGER)")
    conn.execute(
        "INSERT INTO sales VALUES (1, 10.5, 1), (2, 20.0, 2), (3, 5.25, 1)"
    )
    conn.close()
    return str(db_file)


@pytest_asyncio.fixture
async def client_with_ds(app, fake_db, seeded_duckdb):
    repo = InMemoryRepo()
    set_repo(repo)

    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    fake_db.users[user_id] = _make_user(user_id)
    repo.seed_org_member(org_id=org_id, user_id=user_id)

    ds = await repo.create(
        resource="datastores",
        org_id=org_id,
        created_by=user_id,
        name="Sample",
        config={"connector_type": "duckdb", "database": seeded_duckdb},
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as ac:
        yield ac, user_id, ds["id"]

    set_repo(None)


@pytest.mark.asyncio
async def test_list_tables_returns_seeded_tables(client_with_ds):
    ac, user_id, ds_id = client_with_ds
    resp = await ac.get(f"/api/v1/datastores/{ds_id}/tables", headers=_auth(user_id))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    names = {t["name"] for t in body["tables"]}
    assert {"customers", "sales"}.issubset(names)
    # Row counts are included and correct.
    by_name = {t["name"]: t for t in body["tables"]}
    assert by_name["customers"]["rows"] == 2
    assert by_name["sales"]["rows"] == 3


@pytest.mark.asyncio
async def test_preview_returns_columns_and_rows(client_with_ds):
    ac, user_id, ds_id = client_with_ds
    resp = await ac.get(
        f"/api/v1/datastores/{ds_id}/tables/sales/preview?limit=2",
        headers=_auth(user_id),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["table"] == "sales"
    col_names = {c["name"] for c in body["columns"]}
    assert {"id", "amount", "customer_id"}.issubset(col_names)
    assert len(body["rows"]) == 2          # limit honoured
    assert body["row_count"] == 3          # full count
    assert body["truncated"] is True       # 3 > 2


@pytest.mark.asyncio
async def test_preview_unknown_table_404(client_with_ds):
    ac, user_id, ds_id = client_with_ds
    resp = await ac.get(
        f"/api/v1/datastores/{ds_id}/tables/nope/preview",
        headers=_auth(user_id),
    )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_unknown_datastore_404(client_with_ds):
    ac, user_id, _ds_id = client_with_ds
    resp = await ac.get(
        f"/api/v1/datastores/{uuid.uuid4()}/tables", headers=_auth(user_id)
    )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_demo_sentinel_lists_and_previews(client_with_ds):
    # Regression: the virtual "Demo data" connector uses the non-UUID sentinel
    # id "__demo__".  _resolve_datastore_connector must route it to the
    # in-process demo DuckDB instead of querying datastores by UUID (which
    # raised asyncpg DataError → 500 on the data-browser "View data" page).
    ac, user_id, _ds_id = client_with_ds
    resp = await ac.get("/api/v1/datastores/__demo__/tables", headers=_auth(user_id))
    assert resp.status_code == 200, resp.text
    assert "demo" in {t["name"] for t in resp.json()["tables"]}

    prev = await ac.get(
        "/api/v1/datastores/__demo__/tables/demo/preview?limit=5",
        headers=_auth(user_id),
    )
    assert prev.status_code == 200, prev.text
    assert prev.json()["table"] == "demo"


@pytest.mark.asyncio
async def test_no_token_401(client_with_ds):
    ac, _user_id, ds_id = client_with_ds
    resp = await ac.get(f"/api/v1/datastores/{ds_id}/tables")
    assert resp.status_code == 401, resp.text
