"""Tests for the Data Browser WRITE endpoints — PATCH/POST/DELETE /api/v1/data/*.

Strategy
--------
- Use ``InMemoryRepo`` injected via ``set_repo()`` — no live DB required.
- Seed org memberships on the repo directly (``repo.seed_org_member()``).
- A real, writable datastore is an on-disk DuckDB *file* connector seeded with
  a native table that has a PRIMARY KEY (``items``).  The file is created in a
  per-test tmp dir; the datastore ``config`` points ``database`` at it.
- A read-only datastore is an in-memory ``view_sql`` connector backed by a
  ``read_parquet(...)`` view — a VIEW, so it must report ``writable: false`` and
  reject all writes.

Coverage
--------
1.  columns endpoint surfaces writable=True + primary_key for the native table.
2.  columns endpoint reports writable=False for the parquet-view table.
3.  PATCH updates a cell via bound params and returns the updated row.
4.  POST inserts a row via bound params and returns the new row.
5.  DELETE removes the row and returns {"deleted": 1}.
6.  SQL-injection payload in a CELL VALUE is stored literally (inert).
7.  Injection in a COLUMN NAME is rejected (400) — table untouched.
8.  Writes against the parquet-view (non-writable) table → 409.
9.  Cross-org datastore → 404 (no leak).
10. PATCH with an incomplete PK → 400.
11. Viewer role → 403 (write guard).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo

# Import data_browser so the /data/* routes register ahead of the catch-all.
import app.routes.data_browser  # noqa: F401, E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(user_id: str, email: str = "alice@example.com") -> dict[str, Any]:
    return {
        "id": user_id,
        "email": email,
        "name": "Alice",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


def _auth_headers(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


def _seed_native_db(path: Path) -> None:
    """Create an on-disk DuckDB file with a native PK table ``items``."""
    conn = duckdb.connect(database=str(path), read_only=False)
    conn.execute(
        "CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT, qty INTEGER)"
    )
    conn.execute("INSERT INTO items VALUES (1, 'apple', 3), (2, 'banana', 5)")
    conn.close()


def _seed_parquet(path: Path) -> None:
    """Write a parquet file the read-only view connector reads via read_parquet."""
    tbl = pa.table({"id": [1, 2], "label": ["x", "y"]})
    pq.write_table(tbl, str(path))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def write_client(app, fake_db, tmp_path):
    """Client + seeded user/org + a writable file datastore and a read-only view."""
    repo = InMemoryRepo()
    set_repo(repo)

    alice_id = str(uuid.uuid4())
    alice_org_id = str(uuid.uuid4())
    alice = _make_user(alice_id)
    fake_db.users[alice_id] = alice
    repo.seed_org_member(org_id=alice_org_id, user_id=alice_id)

    # Writable native DuckDB file datastore.
    db_file = tmp_path / "store.duckdb"
    _seed_native_db(db_file)
    writable_ds = await repo.create(
        resource="datastores",
        org_id=alice_org_id,
        created_by=alice_id,
        name="writable",
        config={"connector_type": "duckdb", "database": str(db_file)},
    )

    # Read-only parquet-view datastore.
    pq_file = tmp_path / "ro.parquet"
    _seed_parquet(pq_file)
    view_ds = await repo.create(
        resource="datastores",
        org_id=alice_org_id,
        created_by=alice_id,
        name="readonly-view",
        config={
            "connector_type": "duckdb",
            "database": ":memory:",
            "view_sql": (
                f"CREATE OR REPLACE VIEW ro AS "
                f"SELECT * FROM read_parquet('{pq_file}')"
            ),
        },
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as ac:
        yield {
            "ac": ac,
            "user_id": alice_id,
            "org_id": alice_org_id,
            "repo": repo,
            "fake_db": fake_db,
            "writable_ds_id": writable_ds["id"],
            "view_ds_id": view_ds["id"],
            "db_file": db_file,
        }

    set_repo(None)


# ---------------------------------------------------------------------------
# Writability + PK detection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_native_table_reports_writable_and_pk(write_client):
    c = write_client
    resp = await c["ac"].get(
        f"/api/v1/data/{c['writable_ds_id']}/tables/items/columns",
        headers=_auth_headers(c["user_id"]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["writable"] is True
    assert body["primary_key"] == ["id"]
    id_col = next(col for col in body["columns"] if col["name"] == "id")
    assert id_col["pk"] is True
    assert id_col["editable"] is True


@pytest.mark.asyncio
async def test_parquet_view_reports_not_writable(write_client):
    c = write_client
    resp = await c["ac"].get(
        f"/api/v1/data/{c['view_ds_id']}/tables/ro/columns",
        headers=_auth_headers(c["user_id"]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["writable"] is False
    assert body["primary_key"] == []


# ---------------------------------------------------------------------------
# Round-trip update / insert / delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_row_roundtrip(write_client):
    c = write_client
    resp = await c["ac"].patch(
        f"/api/v1/data/{c['writable_ds_id']}/tables/items/rows",
        headers=_auth_headers(c["user_id"]),
        json={"pk": {"id": 1}, "set": {"qty": 99, "name": "apricot"}},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["updated"] == 1
    assert body["row"]["id"] == 1
    assert body["row"]["qty"] == 99
    assert body["row"]["name"] == "apricot"


@pytest.mark.asyncio
async def test_insert_row_roundtrip(write_client):
    c = write_client
    resp = await c["ac"].post(
        f"/api/v1/data/{c['writable_ds_id']}/tables/items/rows",
        headers=_auth_headers(c["user_id"]),
        json={"values": {"id": 3, "name": "cherry", "qty": 7}},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["inserted"] == 1
    assert body["row"]["id"] == 3
    assert body["row"]["name"] == "cherry"


@pytest.mark.asyncio
async def test_delete_row_roundtrip(write_client):
    c = write_client
    resp = await c["ac"].request(
        "DELETE",
        f"/api/v1/data/{c['writable_ds_id']}/tables/items/rows",
        headers=_auth_headers(c["user_id"]),
        json={"pk": {"id": 2}},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"deleted": 1}

    # Confirm it is gone from the underlying file.
    conn = duckdb.connect(database=str(c["db_file"]), read_only=True)
    remaining = conn.execute("SELECT id FROM items ORDER BY id").fetchall()
    conn.close()
    assert remaining == [(1,)]


# ---------------------------------------------------------------------------
# Security — injection via cell value (inert) + column name (rejected)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_injection_in_cell_value_is_inert(write_client):
    c = write_client
    payload = "apple'); DROP TABLE items; --"
    resp = await c["ac"].patch(
        f"/api/v1/data/{c['writable_ds_id']}/tables/items/rows",
        headers=_auth_headers(c["user_id"]),
        json={"pk": {"id": 1}, "set": {"name": payload}},
    )
    assert resp.status_code == 200, resp.text
    # The payload is stored LITERALLY, not executed.
    assert resp.json()["row"]["name"] == payload

    # Table still exists and still has both rows.
    conn = duckdb.connect(database=str(c["db_file"]), read_only=True)
    count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    stored = conn.execute("SELECT name FROM items WHERE id = 1").fetchone()[0]
    conn.close()
    assert count == 2
    assert stored == payload


@pytest.mark.asyncio
async def test_injection_in_column_name_is_rejected(write_client):
    c = write_client
    resp = await c["ac"].patch(
        f"/api/v1/data/{c['writable_ds_id']}/tables/items/rows",
        headers=_auth_headers(c["user_id"]),
        json={"pk": {"id": 1}, "set": {"qty = 1; DROP TABLE items; --": 5}},
    )
    assert resp.status_code == 400, resp.text

    # Also reject an unknown-but-syntactically-valid column.
    resp2 = await c["ac"].patch(
        f"/api/v1/data/{c['writable_ds_id']}/tables/items/rows",
        headers=_auth_headers(c["user_id"]),
        json={"pk": {"id": 1}, "set": {"nonexistent": 5}},
    )
    assert resp2.status_code == 400, resp2.text

    # Table untouched.
    conn = duckdb.connect(database=str(c["db_file"]), read_only=True)
    count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    conn.close()
    assert count == 2


# ---------------------------------------------------------------------------
# Writable gate, tenant isolation, PK completeness, role
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_writable_view_rejects_writes(write_client):
    c = write_client
    resp = await c["ac"].patch(
        f"/api/v1/data/{c['view_ds_id']}/tables/ro/rows",
        headers=_auth_headers(c["user_id"]),
        json={"pk": {"id": 1}, "set": {"label": "z"}},
    )
    assert resp.status_code == 409, resp.text

    resp2 = await c["ac"].post(
        f"/api/v1/data/{c['view_ds_id']}/tables/ro/rows",
        headers=_auth_headers(c["user_id"]),
        json={"values": {"id": 9, "label": "q"}},
    )
    assert resp2.status_code == 409, resp2.text


@pytest.mark.asyncio
async def test_cross_org_datastore_denied(write_client):
    c = write_client
    # A second user in a DIFFERENT org tries to write the first org's datastore.
    bob_id = str(uuid.uuid4())
    bob_org_id = str(uuid.uuid4())
    c["fake_db"].users[bob_id] = _make_user(bob_id, email="bob@example.com")
    c["repo"].seed_org_member(org_id=bob_org_id, user_id=bob_id)

    resp = await c["ac"].patch(
        f"/api/v1/data/{c['writable_ds_id']}/tables/items/rows",
        headers=_auth_headers(bob_id),
        json={"pk": {"id": 1}, "set": {"qty": 1}},
    )
    # Datastore is org-scoped → not found for bob (no cross-org leak).
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_update_incomplete_pk_rejected(write_client):
    c = write_client
    # 'items' PK is just (id); passing a non-PK col as pk → 400.
    resp = await c["ac"].patch(
        f"/api/v1/data/{c['writable_ds_id']}/tables/items/rows",
        headers=_auth_headers(c["user_id"]),
        json={"pk": {"name": "apple"}, "set": {"qty": 1}},
    )
    assert resp.status_code == 400, resp.text


@pytest.mark.asyncio
async def test_viewer_role_forbidden(write_client):
    c = write_client
    viewer_id = str(uuid.uuid4())
    # Same org, viewer role.
    c["fake_db"].users[viewer_id] = _make_user(viewer_id, email="viewer@example.com")
    c["repo"].seed_org_member(
        org_id=c["org_id"], user_id=viewer_id, role="viewer"
    )
    resp = await c["ac"].patch(
        f"/api/v1/data/{c['writable_ds_id']}/tables/items/rows",
        headers=_auth_headers(viewer_id),
        json={"pk": {"id": 1}, "set": {"qty": 1}},
    )
    assert resp.status_code == 403, resp.text
