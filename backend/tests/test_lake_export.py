"""Tests for the lake-export bulk-export endpoints.

Strategy
--------
- Use LOCAL storage only: set ``NUBI_MANAGED_LAKE_DIR`` to a tmp dir and
  ``NUBI_CUSTODY_ENABLED=true`` so no cloud creds are required.
- ``get_settings.cache_clear()`` is called after monkeypatching env vars to
  flush the pydantic-settings singleton.
- Provision a managed lake via ``InMemoryRepo`` (seed a datastore row with
  ``config.managed_lake: true`` and the server-pinned ``database`` path).
- Seed the lake with a small Parquet file via pyarrow + ``LocalStorageClient``.
- Import ``app.routes.lake_export`` at the top so the router self-registers
  on ``api_router`` before the app is created.

Coverage
--------
1. ``GET /lake/{id}/tables``       — lists seeded table
2. ``POST /lake/{id}/export``      — all-tables export writes parquet + rows match
3. ``POST /lake/{id}/export``      — named-table export
4. ``POST /lake/{id}/export``      — format=csv writes CSV
5. Custody disabled               → 403
6. Cross-org datastore            → 404
7. Non-SELECT sql                 → 400  (invalid_export_sql)
8. Bad dest_uri                   → 400  (invalid_dest_uri)
9. table + sql conflict           → 400  (export_conflict)
10. Explicit SELECT sql export      — single file written
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Self-register the lake_export router on api_router BEFORE the app is created.
import app.routes.lake_export  # noqa: F401

from app.auth.jwt import mint_access_token
from app.lakehouse.managed import lake_prefix, MANAGED_MARKER
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(user_id: str | None = None, email: str = "alice@example.com") -> dict[str, Any]:
    """Return a minimal user dict matching the shape returned by ``current_user``."""
    return {
        "id": user_id or str(uuid.uuid4()),
        "email": email,
        "name": "Alice",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


def _auth_headers(user_id: str) -> dict[str, str]:
    """Return a Bearer Authorization header for *user_id*."""
    token = mint_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


def _managed_ds_row(
    ds_id: str,
    org_id: str,
    db_uri: str,
    prefix: str,
    user_id: str = "user-x",
) -> dict[str, Any]:
    """Build a managed-lake datastore row for seeding into ``InMemoryRepo``."""
    return {
        "id": ds_id,
        "org_id": org_id,
        "name": "Test managed lake",
        "created_by": user_id,
        "config": {
            "connector_type": "duckdb",
            "database": db_uri,
            MANAGED_MARKER: True,
            "managed_prefix": prefix,
            "managed_scheme": "file",
        },
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def lake_client(tmp_path, monkeypatch, app, fake_db):
    """Yield ``(client, alice_id, alice_org_id, repo, ds_id, dest_dir, table_name)``.

    Sets up:
    - A local managed-lake dir under ``tmp_path``.
    - ``NUBI_CUSTODY_ENABLED=true``.
    - An ``InMemoryRepo`` with one user + org + managed-lake datastore.
    - A seeded Parquet file (10 rows) under the lake prefix.
    - A ``dest_dir`` path for export output.
    """

    lake_dir = tmp_path / "managed-lake"
    lake_dir.mkdir()
    dest_dir = tmp_path / "export-dest"
    dest_dir.mkdir()

    # Patch env BEFORE get_settings() is called.
    monkeypatch.setenv("NUBI_MANAGED_LAKE_DIR", str(lake_dir))
    monkeypatch.setenv("NUBI_CUSTODY_ENABLED", "true")
    # Clear the pydantic-settings singleton so the new env vars take effect.
    from app.config import get_settings
    get_settings.cache_clear()

    # Provision repo + user.
    repo = InMemoryRepo()
    set_repo(repo)

    alice_id = str(uuid.uuid4())
    alice_org_id = str(uuid.uuid4())
    alice = _make_user(user_id=alice_id)
    fake_db.users[alice_id] = alice
    repo.seed_org_member(org_id=alice_org_id, user_id=alice_id)

    # Create the managed datastore row.
    ds_id = str(uuid.uuid4())
    prefix = lake_prefix(alice_org_id, ds_id)
    db_uri = f"file://{lake_dir}/{prefix}"

    ds_row = _managed_ds_row(
        ds_id=ds_id,
        org_id=alice_org_id,
        db_uri=db_uri,
        prefix=prefix,
        user_id=alice_id,
    )
    # Seed directly into the InMemoryRepo store.
    repo._store["datastores"][ds_id] = {
        **ds_row,
        "created_at": "2025-01-01T00:00:00+00:00",
        "updated_at": "2025-01-01T00:00:00+00:00",
        "project_id": None,
    }

    # Seed a Parquet file under the lake prefix.
    table_name = "orders"
    parquet_key = f"{prefix}{table_name}/part-0.parquet"
    parquet_abs = lake_dir / parquet_key

    arrow_table = pa.table({
        "id": pa.array(list(range(1, 11)), type=pa.int32()),
        "amount": pa.array([float(i * 10) for i in range(1, 11)], type=pa.float64()),
        "label": pa.array([f"row_{i}" for i in range(1, 11)], type=pa.string()),
    })
    parquet_abs.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(arrow_table, str(parquet_abs))

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as ac:
        yield ac, alice_id, alice_org_id, repo, ds_id, dest_dir, table_name

    # Teardown.
    set_repo(None)
    from app.config import get_settings as _gs
    _gs.cache_clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestListLakeTables:
    """GET /lake/{id}/tables"""

    @pytest.mark.asyncio
    async def test_lists_seeded_table(self, lake_client):
        ac, alice_id, org_id, repo, ds_id, dest_dir, table_name = lake_client
        resp = await ac.get(
            f"/api/v1/lake/{ds_id}/tables",
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["datastore_id"] == ds_id
        assert table_name in data["tables"]
        assert data["count"] >= 1

    @pytest.mark.asyncio
    async def test_custody_disabled_returns_403(self, lake_client, monkeypatch):
        ac, alice_id, org_id, repo, ds_id, dest_dir, table_name = lake_client
        monkeypatch.setenv("NUBI_CUSTODY_ENABLED", "false")
        from app.config import get_settings
        get_settings.cache_clear()

        resp = await ac.get(
            f"/api/v1/lake/{ds_id}/tables",
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "custody_disabled"

    @pytest.mark.asyncio
    async def test_cross_org_returns_404(self, lake_client):
        """A user in a different org cannot see Alice's managed lake."""
        ac, alice_id, org_id, repo, ds_id, dest_dir, table_name = lake_client
        bob_id = str(uuid.uuid4())
        bob_org_id = str(uuid.uuid4())
        from tests.conftest import _fake_db
        _fake_db.users[bob_id] = _make_user(user_id=bob_id, email="bob@example.com")
        repo.seed_org_member(org_id=bob_org_id, user_id=bob_id)

        # Bob queries Alice's datastore (which lives in Alice's org → 404).
        resp = await ac.get(
            f"/api/v1/lake/{ds_id}/tables",
            headers=_auth_headers(bob_id),
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "lake_not_found"


class TestExportLake:
    """POST /lake/{id}/export"""

    @pytest.mark.asyncio
    async def test_all_tables_export_writes_parquet(self, lake_client):
        """All-tables export: file written to dest_dir, rows match seeded data."""
        ac, alice_id, org_id, repo, ds_id, dest_dir, table_name = lake_client
        resp = await ac.post(
            f"/api/v1/lake/{ds_id}/export",
            json={"dest_uri": f"file://{dest_dir}"},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["tables_exported"] >= 1
        assert data["format"] == "parquet"

        # Find the written file and verify rows.
        written_uri = data["exported"][0]["dest_uri"]
        # Strip file:// prefix.
        written_path = written_uri.removeprefix("file://")
        assert os.path.exists(written_path), f"Expected file at {written_path}"
        result = pq.read_table(written_path)
        assert result.num_rows == 10
        assert "id" in result.schema.names
        assert "amount" in result.schema.names

    @pytest.mark.asyncio
    async def test_named_table_export(self, lake_client):
        """Named-table export: single table written."""
        ac, alice_id, org_id, repo, ds_id, dest_dir, table_name = lake_client
        resp = await ac.post(
            f"/api/v1/lake/{ds_id}/export",
            json={"dest_uri": f"file://{dest_dir}", "table": table_name},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["tables_exported"] == 1
        assert data["exported"][0]["table"] == table_name

        written_uri = data["exported"][0]["dest_uri"]
        written_path = written_uri.removeprefix("file://")
        assert os.path.exists(written_path)
        result = pq.read_table(written_path)
        assert result.num_rows == 10

    @pytest.mark.asyncio
    async def test_csv_format_export(self, lake_client, tmp_path):
        """format=csv writes a CSV file that can be read back."""
        ac, alice_id, org_id, repo, ds_id, dest_dir, table_name = lake_client
        csv_dest = tmp_path / "csv-out"
        csv_dest.mkdir()
        resp = await ac.post(
            f"/api/v1/lake/{ds_id}/export",
            json={
                "dest_uri": f"file://{csv_dest}",
                "table": table_name,
                "format": "csv",
            },
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["format"] == "csv"
        written_uri = data["exported"][0]["dest_uri"]
        written_path = written_uri.removeprefix("file://")
        assert os.path.exists(written_path)
        # CSV should be a text file with headers + 10 data rows.
        with open(written_path) as f:
            lines = f.read().strip().splitlines()
        # Header + 10 rows = 11 lines.
        assert len(lines) == 11, f"Expected 11 lines (header+10 rows), got {len(lines)}"

    @pytest.mark.asyncio
    async def test_explicit_sql_export(self, lake_client, tmp_path):
        """Explicit SELECT sql: single output file written with the right rows."""
        ac, alice_id, org_id, repo, ds_id, dest_dir, table_name = lake_client
        sql_dest = tmp_path / "sql-out"
        sql_dest.mkdir()

        # A pure-SQL SELECT (no table reference) exercises the sql export path
        # without requiring the connector to reach the source lake files.
        select_sql = "SELECT 1 AS n, 'hello' AS greeting"
        resp = await ac.post(
            f"/api/v1/lake/{ds_id}/export",
            json={
                "dest_uri": f"file://{sql_dest}",
                "sql": select_sql,
                "format": "parquet",
            },
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["tables_exported"] == 1
        written_uri = data["exported"][0]["dest_uri"]
        written_path = written_uri.removeprefix("file://")
        assert os.path.exists(written_path)
        result = pq.read_table(written_path)
        assert result.num_rows == 1
        assert "n" in result.schema.names
        assert "greeting" in result.schema.names

    @pytest.mark.asyncio
    async def test_custody_disabled_returns_403(self, lake_client, monkeypatch):
        ac, alice_id, org_id, repo, ds_id, dest_dir, table_name = lake_client
        monkeypatch.setenv("NUBI_CUSTODY_ENABLED", "false")
        from app.config import get_settings
        get_settings.cache_clear()

        resp = await ac.post(
            f"/api/v1/lake/{ds_id}/export",
            json={"dest_uri": f"file://{dest_dir}"},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "custody_disabled"

    @pytest.mark.asyncio
    async def test_cross_org_returns_404(self, lake_client):
        ac, alice_id, org_id, repo, ds_id, dest_dir, table_name = lake_client
        bob_id = str(uuid.uuid4())
        bob_org_id = str(uuid.uuid4())
        from tests.conftest import _fake_db
        _fake_db.users[bob_id] = _make_user(user_id=bob_id, email="bob@example.com")
        repo.seed_org_member(org_id=bob_org_id, user_id=bob_id)

        resp = await ac.post(
            f"/api/v1/lake/{ds_id}/export",
            json={"dest_uri": f"file://{dest_dir}"},
            headers=_auth_headers(bob_id),
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "lake_not_found"

    @pytest.mark.asyncio
    async def test_non_select_sql_returns_400(self, lake_client):
        ac, alice_id, org_id, repo, ds_id, dest_dir, table_name = lake_client
        for bad_sql in [
            "DROP TABLE orders",
            "INSERT INTO orders VALUES (1)",
            "COPY orders TO 'out.parquet'",
            "ATTACH 'evil.db' AS evil",
            "CREATE TABLE x AS SELECT 1",
            "SELECT 1; DROP TABLE orders",
        ]:
            resp = await ac.post(
                f"/api/v1/lake/{ds_id}/export",
                json={"dest_uri": f"file://{dest_dir}", "sql": bad_sql},
                headers=_auth_headers(alice_id),
            )
            assert resp.status_code == 400, f"Expected 400 for sql={bad_sql!r}, got {resp.status_code}: {resp.text}"
            assert resp.json()["error"]["code"] == "invalid_export_sql", \
                f"Expected invalid_export_sql for sql={bad_sql!r}"

    @pytest.mark.asyncio
    async def test_bad_dest_uri_returns_400(self, lake_client):
        ac, alice_id, org_id, repo, ds_id, dest_dir, table_name = lake_client
        resp = await ac.post(
            f"/api/v1/lake/{ds_id}/export",
            json={"dest_uri": "/just/a/path/no-scheme"},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "invalid_dest_uri"

    @pytest.mark.asyncio
    async def test_table_and_sql_conflict_returns_400(self, lake_client):
        ac, alice_id, org_id, repo, ds_id, dest_dir, table_name = lake_client
        resp = await ac.post(
            f"/api/v1/lake/{ds_id}/export",
            json={
                "dest_uri": f"file://{dest_dir}",
                "table": table_name,
                "sql": "SELECT 1",
            },
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "export_conflict"

    @pytest.mark.asyncio
    async def test_invalid_format_returns_400(self, lake_client):
        ac, alice_id, org_id, repo, ds_id, dest_dir, table_name = lake_client
        resp = await ac.post(
            f"/api/v1/lake/{ds_id}/export",
            json={"dest_uri": f"file://{dest_dir}", "format": "xlsx"},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "invalid_format"


# ---------------------------------------------------------------------------
# Regression tests — Bug 1: table-name SQL injection (CRITICAL)
# ---------------------------------------------------------------------------


class TestTableNameValidation:
    """Regression tests for Bug 1: unvalidated `table` parameter SQL injection.

    A single quote, semicolon, read_csv payload, path traversal, or other
    dangerous characters in `table` must be rejected with 400/404 BEFORE any
    DuckDB call — the injected string must never touch the SQL engine.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad_table", [
        "orders'--",                          # single quote breaks string literal
        "orders; DROP TABLE x",               # semicolon + DDL injection
        "../../../etc/passwd",                # path traversal via ..
        "read_csv('/etc/passwd')",            # DuckDB file-read function payload
        "orders\x00null",                     # null byte
        "orders orders",                      # space (path-splittable)
        "orders/subdir",                      # slash (path traversal)
        "or'ders",                            # embedded quote
    ])
    async def test_invalid_table_name_rejected(self, lake_client, bad_table):
        """Table names with unsafe chars are rejected with 400 (invalid_table_name)."""
        ac, alice_id, org_id, repo, ds_id, dest_dir, table_name = lake_client
        resp = await ac.post(
            f"/api/v1/lake/{ds_id}/export",
            json={"dest_uri": f"file://{dest_dir}", "table": bad_table},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code in (400, 422), (
            f"Expected 400/422 for table={bad_table!r}, got {resp.status_code}: {resp.text}"
        )

    @pytest.mark.asyncio
    async def test_table_not_in_lake_returns_404(self, lake_client):
        """A syntactically valid but non-existent table name returns 404."""
        ac, alice_id, org_id, repo, ds_id, dest_dir, table_name = lake_client
        resp = await ac.post(
            f"/api/v1/lake/{ds_id}/export",
            json={"dest_uri": f"file://{dest_dir}", "table": "nonexistent_table"},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 404, resp.text
        assert resp.json()["error"]["code"] == "table_not_found"

    @pytest.mark.asyncio
    async def test_valid_table_name_accepted(self, lake_client):
        """A safe table name that exists in the lake exports successfully."""
        ac, alice_id, org_id, repo, ds_id, dest_dir, table_name = lake_client
        resp = await ac.post(
            f"/api/v1/lake/{ds_id}/export",
            json={"dest_uri": f"file://{dest_dir}", "table": table_name},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["tables_exported"] == 1


# ---------------------------------------------------------------------------
# Regression tests — Bug 2: dest_uri overlap with source lake
# ---------------------------------------------------------------------------


class TestDestUriOverlapValidation:
    """Regression tests for Bug 2: dest_uri pointing into the managed-lake prefix."""

    @pytest.mark.asyncio
    async def test_dest_inside_source_lake_rejected(self, lake_client, monkeypatch):
        """dest_uri that resolves to/under the source lake prefix → 400."""
        ac, alice_id, org_id, repo, ds_id, dest_dir, table_name = lake_client

        from app.lakehouse.managed import lake_prefix, resolve_central_storage
        from app.config import get_settings
        get_settings.cache_clear()
        central = resolve_central_storage()
        if central is None:
            pytest.skip("Central storage not configured — skipping overlap test")

        prefix = lake_prefix(org_id, ds_id)
        # Build a dest_uri that points directly into the source lake prefix.
        source_lake_uri = f"{central.base_uri()}/{prefix.lstrip('/')}"
        resp = await ac.post(
            f"/api/v1/lake/{ds_id}/export",
            json={"dest_uri": source_lake_uri},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 400, (
            f"Expected 400 for dest inside source lake, got {resp.status_code}: {resp.text}"
        )
        assert resp.json()["error"]["code"] == "invalid_dest_uri"

    @pytest.mark.asyncio
    async def test_dest_outside_source_lake_accepted(self, lake_client):
        """dest_uri outside the source lake prefix is allowed (normal export)."""
        ac, alice_id, org_id, repo, ds_id, dest_dir, table_name = lake_client
        resp = await ac.post(
            f"/api/v1/lake/{ds_id}/export",
            json={"dest_uri": f"file://{dest_dir}"},
            headers=_auth_headers(alice_id),
        )
        assert resp.status_code == 200, resp.text
