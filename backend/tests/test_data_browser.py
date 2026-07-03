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

from app.connectors.base import Connector  # noqa: E402
from app.connectors.plan import PhysicalPlan  # noqa: E402
from app.connectors.registry import get_connector_registry  # noqa: E402


# ---------------------------------------------------------------------------
# Fake non-DuckDB SQL connector (for the connector-uniform introspection path)
# ---------------------------------------------------------------------------

_FAKE_SQL_TYPE = "fake_probe_sql"

# Zero-row Arrow schema returned by the LIMIT-0 probe for table "widgets".
_WIDGETS_SCHEMA = pa.schema(
    [
        pa.field("id", pa.int64()),
        pa.field("name", pa.utf8()),
        pa.field("price", pa.float64()),
        pa.field("created_at", pa.timestamp("us")),
        pa.field("active", pa.bool_()),
    ]
)


class _FakeSQLConnector(Connector):
    """Minimal non-DuckDB connector used to exercise the probe path.

    Answers exactly two SQL shapes the data browser issues: the
    information_schema.tables introspection and the ``SELECT * FROM t LIMIT 0``
    zero-row probe.  Everything else returns an empty table.
    """

    def __init__(self, config: dict) -> None:  # noqa: ARG002
        self.validate_capabilities()

    def capabilities(self) -> dict[str, bool]:
        return {
            "native_arrow": True,
            "predicate_pushdown": True,
            "projection_pushdown": True,
            "partition_pushdown": False,
            "predicate_rls": True,
            "column_masking": False,
            "streaming_cdc": False,
        }

    def execute(self, plan: PhysicalPlan) -> pa.Table:
        sql = (plan.sql or "").lower()
        if "information_schema.tables" in sql:
            return pa.table({"table_schema": ["main"], "table_name": ["widgets"]})
        if "widgets" in sql:  # the LIMIT 0 probe
            return _WIDGETS_SCHEMA.empty_table()
        return pa.table({})

    def execute_stream(self, plan: PhysicalPlan):  # noqa: ARG002
        yield from []  # pragma: no cover


get_connector_registry().register(
    _FAKE_SQL_TYPE, lambda config: _FakeSQLConnector(config)
)


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
async def test_demo_columns_include_portable_type(browser_client):
    """DuckDB (demo) columns carry a portable_type from the portable vocab.

    Backward-compatible: the native ``type`` field is still present; only
    ``portable_type`` is added.
    """
    ac, user_id, _org_id, _repo = browser_client
    resp = await ac.get("/api/v1/data/tables/demo/columns", headers=_auth_headers(user_id))
    assert resp.status_code == 200, resp.text
    vocab = {"text", "number", "bool", "date", "timestamp", "json"}
    for col in resp.json()["columns"]:
        assert "type" in col  # native type preserved (backward compatible)
        assert col["portable_type"] in vocab
    by_name = {c["name"]: c for c in resp.json()["columns"]}
    assert by_name["id"]["portable_type"] == "number"
    assert by_name["name"]["portable_type"] == "text"
    assert by_name["active"]["portable_type"] == "bool"


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
async def test_non_duckdb_connector_columns_via_probe(browser_client):
    """GET /data/{id}/tables/{t}/columns for a non-DuckDB connector.

    A fake SQL connector answers the information_schema.tables introspection and
    the zero-row ``SELECT * FROM t LIMIT 0`` probe.  The columns response is
    derived from the probe's Arrow schema and carries both the native ``type``
    and the normalised ``portable_type`` for every column.
    """
    ac, user_id, org_id, repo = browser_client
    ds_row = await repo.create(
        resource="datastores",
        org_id=org_id,
        created_by=user_id,
        name="fake-sql",
        config={"connector_type": _FAKE_SQL_TYPE},
    )
    ds_id = ds_row["id"]

    # Tables endpoint now works for a non-DuckDB connector (no more 400).
    resp = await ac.get(
        f"/api/v1/data/{ds_id}/tables",
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 200, resp.text
    assert "widgets" in {t["name"] for t in resp.json()["tables"]}

    # Columns endpoint derives schema + portable types from the probe.
    resp = await ac.get(
        f"/api/v1/data/{ds_id}/tables/widgets/columns",
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["writable"] is False
    by_name = {c["name"]: c for c in body["columns"]}
    assert set(by_name) == {"id", "name", "price", "created_at", "active"}
    # Native Arrow type strings are preserved alongside the portable type.
    assert by_name["id"]["type"] == "int64"
    assert by_name["id"]["portable_type"] == "number"
    assert by_name["price"]["portable_type"] == "number"
    assert by_name["name"]["portable_type"] == "text"
    assert by_name["active"]["portable_type"] == "bool"
    assert by_name["created_at"]["portable_type"] == "timestamp"
    # Every column exposes the full contract shape.
    for col in body["columns"]:
        assert set(col) >= {"name", "type", "portable_type", "nullable", "pk", "editable"}


@pytest.mark.asyncio
async def test_non_duckdb_unknown_table_returns_404(browser_client):
    """A table not present in the connector's introspected tables → 404."""
    ac, user_id, org_id, repo = browser_client
    ds_row = await repo.create(
        resource="datastores",
        org_id=org_id,
        created_by=user_id,
        name="fake-sql-2",
        config={"connector_type": _FAKE_SQL_TYPE},
    )
    resp = await ac.get(
        f"/api/v1/data/{ds_row['id']}/tables/not_a_table/columns",
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# Unit tests: _build_view_sql_from_s3_views — SQL injection prevention
# ---------------------------------------------------------------------------


from app.routes.data_browser import _build_view_sql_from_s3_views  # noqa: E402


def test_build_view_sql_valid():
    """Well-formed s3_views dict produces correct CREATE VIEW SQL."""
    sql = _build_view_sql_from_s3_views({"sales": "s3://bucket/sales.parquet"})
    assert "CREATE OR REPLACE VIEW sales" in sql
    assert "read_parquet('s3://bucket/sales.parquet')" in sql


def test_build_view_sql_escapes_single_quote_in_uri():
    """A single-quote in the URI path is doubled (SQL-escaped), not injected."""
    sql = _build_view_sql_from_s3_views({"tbl": "s3://bucket/it's.parquet"})
    assert "it''s.parquet" in sql
    # Must NOT contain an unescaped literal single-quote after the opening one
    # i.e. no pattern  '...it's...  with a naked apostrophe breaking the string
    assert "it's" not in sql


def test_build_view_sql_rejects_invalid_table_name():
    """Table name containing SQL metacharacters raises ValueError."""
    with pytest.raises(ValueError, match="Invalid s3_views table name"):
        _build_view_sql_from_s3_views({"bad-name; DROP TABLE users--": "s3://b/f.parquet"})


def test_build_view_sql_rejects_table_name_with_space():
    with pytest.raises(ValueError, match="Invalid s3_views table name"):
        _build_view_sql_from_s3_views({"bad name": "s3://b/f.parquet"})


def test_build_view_sql_rejects_non_s3_non_local_uri():
    """URIs that are neither s3:// nor local paths raise ValueError."""
    with pytest.raises(ValueError, match="Invalid s3_views URI"):
        _build_view_sql_from_s3_views({"tbl": "http://evil.example.com/x.parquet"})


def test_build_view_sql_rejects_injection_via_uri():
    """Injection attempt in URI is caught by prefix validation."""
    with pytest.raises(ValueError, match="Invalid s3_views URI"):
        _build_view_sql_from_s3_views(
            {"tbl": "'); COPY (SELECT 1) TO '/tmp/pwned'; --"}
        )


def test_build_view_sql_multiple_tables():
    """Multiple entries produce multiple semicolon-separated statements."""
    sql = _build_view_sql_from_s3_views({
        "orders": "s3://b/orders.parquet",
        "items": "s3://b/items.parquet",
    })
    stmts = [s.strip() for s in sql.split(";") if s.strip()]
    assert len(stmts) == 2
