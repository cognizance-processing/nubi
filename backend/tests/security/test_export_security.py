"""EXPORT SECURITY — adversarial tests for the bulk lake-export API.

Covers:
  - SELECT-only guard: INSERT/UPDATE/DELETE/DDL/COPY/ATTACH/multi-statement/
    CTE-write all rejected
  - FILE-ACCESS guard (cross-tenant exfiltration / arbitrary file read):
    read_parquet / read_csv / glob / postgres_scan etc. in `sql` rejected
  - dest-uri must be outside the source lake (overwrite attack)
  - table-name injection rejected
  - async path enforces the SAME guards (shared _run_export_core /
    _validate_export_sql)
  - export jobs IDOR: org A cannot enqueue-then-read org B's job
  - dest_creds_ref never returned by the status endpoint
  - worker claim CAS single-winner
"""

from __future__ import annotations

import os

import pytest

from app.errors import AppError
from app.routes.lake_export import (
    InMemoryExportJobStore,
    _validate_dest_not_in_source,
    _validate_export_sql,
    _validate_table_name,
)
from tests.security._custody_fixtures import auth_headers, custody_env  # noqa: F401


# ---------------------------------------------------------------------------
# SELECT-only guard (unit-level — shared by sync + async)
# ---------------------------------------------------------------------------

_NON_SELECT = [
    "INSERT INTO t VALUES (1)",
    "UPDATE t SET x=1",
    "DELETE FROM t",
    "DROP TABLE t",
    "CREATE TABLE x AS SELECT 1",
    "COPY t TO 'x.parquet'",
    "ATTACH 'evil.db' AS evil",
    "PRAGMA database_list",
    "SET memory_limit='1GB'",
    "INSTALL httpfs",
    "LOAD httpfs",
    "SELECT 1; DROP TABLE t",          # multi-statement
    "SELECT 1;SELECT 2",               # multi-statement
]


@pytest.mark.parametrize("sql", _NON_SELECT)
def test_non_select_rejected(sql):
    with pytest.raises(AppError) as ei:
        _validate_export_sql(sql)
    assert ei.value.code == "invalid_export_sql"


# A CTE that smuggles a file-read is caught by the file-access guard even
# though the statement legitimately starts with WITH.
def test_cte_fileaccess_rejected():
    sql = "WITH x AS (SELECT * FROM read_csv('/etc/passwd')) SELECT * FROM x"
    with pytest.raises(AppError) as ei:
        _validate_export_sql(sql)
    assert ei.value.code == "invalid_export_sql"


# ---------------------------------------------------------------------------
# FILE-ACCESS guard — the cross-tenant exfiltration / arbitrary-read fix
# ---------------------------------------------------------------------------

_FILE_ACCESS = [
    "SELECT * FROM read_csv('/etc/passwd')",
    "SELECT * FROM read_csv_auto('/etc/passwd')",
    "SELECT * FROM read_parquet('file:///managed/orgs/OTHER/lake/x/**/*.parquet')",
    "SELECT * FROM read_parquet('s3://other-tenant/secret/**')",
    "SELECT * FROM read_json('http://169.254.169.254/latest/meta-data/')",
    "SELECT * FROM read_text('/etc/hosts')",
    "SELECT * FROM glob('/**')",
    "SELECT * FROM parquet_scan('/x.parquet')",
    "SELECT * FROM postgres_scan('host=10.0.0.1','public','t')",
    "SELECT * FROM sqlite_scan('/var/db.sqlite','t')",
    "select COUNT(*) from READ_PARQUET('/x')",   # case-insensitive
]


@pytest.mark.parametrize("sql", _FILE_ACCESS)
def test_file_access_functions_rejected(sql):
    with pytest.raises(AppError) as ei:
        _validate_export_sql(sql)
    assert ei.value.code == "invalid_export_sql"


# Legitimate computed SELECTs still pass (no false positives).
_GOOD = [
    "SELECT 1 AS n, 'hello' AS greeting",
    "SELECT 1",
    "WITH x AS (SELECT 1 a) SELECT * FROM x",
    "SELECT read_count FROM x",   # column named read_count — not a function call
    "SELECT count(*) AS c, sum(v) FROM x",
]


@pytest.mark.parametrize("sql", _GOOD)
def test_safe_select_passes(sql):
    _validate_export_sql(sql)  # must not raise


@pytest.mark.asyncio
async def test_file_access_blocked_end_to_end(custody_env):
    """The fix is enforced through the real HTTP export endpoint."""
    e = custody_env
    r = await e["client"].post(
        f"/api/v1/lake/{e['alice_ds']}/export",
        json={"dest_uri": "file:///tmp/out", "sql": "SELECT * FROM read_csv('/etc/passwd')"},
        headers=auth_headers(e["alice_id"]),
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_export_sql"


# ---------------------------------------------------------------------------
# dest-uri overwrite attack
# ---------------------------------------------------------------------------


def test_dest_inside_source_rejected():
    src = "file:///managed/orgs/abc/lake/def/"
    for dest in [
        "file:///managed/orgs/abc/lake/def/",          # exact
        "file:///managed/orgs/abc/lake/def/sub",       # under source
        "file:///managed/orgs/abc/lake",               # parent of source
    ]:
        with pytest.raises(AppError) as ei:
            _validate_dest_not_in_source(dest, src)
        assert ei.value.code == "invalid_dest_uri"


def test_dest_outside_source_allowed():
    src = "file:///managed/orgs/abc/lake/def/"
    # A sibling lake or a different bucket is fine.
    _validate_dest_not_in_source("file:///exports/mybucket/", src)
    _validate_dest_not_in_source("s3://my-bucket/out/", src)


# ---------------------------------------------------------------------------
# Table-name injection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "table",
    [
        "orders'; DROP TABLE x; --",
        "../../etc/passwd",
        "a/b",
        "x';read_csv('/etc/passwd')",
        "tbl name",
        "",
        "t\x00",
    ],
)
def test_table_name_injection_rejected(table):
    with pytest.raises(AppError) as ei:
        _validate_table_name(table)
    assert ei.value.code == "invalid_table_name"


# ---------------------------------------------------------------------------
# Async enqueue enforces the same guards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_enqueue_rejects_file_access_sql(custody_env):
    e = custody_env
    r = await e["client"].post(
        f"/api/v1/lake/{e['alice_ds']}/export/jobs",
        json={"dest_uri": "file:///tmp/out", "sql": "SELECT * FROM read_parquet('/x/**')"},
        headers=auth_headers(e["alice_id"]),
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_export_sql"


@pytest.mark.asyncio
async def test_async_enqueue_rejects_non_select(custody_env):
    e = custody_env
    r = await e["client"].post(
        f"/api/v1/lake/{e['alice_ds']}/export/jobs",
        json={"dest_uri": "file:///tmp/out", "sql": "DROP TABLE orders"},
        headers=auth_headers(e["alice_id"]),
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_export_sql"


# ---------------------------------------------------------------------------
# Export-job IDOR + dest_creds_ref redaction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_job_idor_and_creds_ref_redacted(custody_env):
    e = custody_env
    # Alice enqueues a job carrying a dest_creds_ref.
    enq = await e["client"].post(
        f"/api/v1/lake/{e['alice_ds']}/export/jobs",
        json={
            "dest_uri": "file:///exports/out/",
            "dest_creds_ref": "secret-store-key-xyz",
            "table": "orders",
        },
        headers=auth_headers(e["alice_id"]),
    )
    assert enq.status_code == 202, enq.text
    job_id = enq.json()["job_id"]

    # Bob (different org) cannot read alice's job → 404.
    bob = await e["client"].get(
        f"/api/v1/lake/{e['alice_ds']}/export/jobs/{job_id}",
        headers=auth_headers(e["bob_id"]),
    )
    assert bob.status_code == 404

    # Alice can read it, but dest_creds_ref is NEVER returned.
    a = await e["client"].get(
        f"/api/v1/lake/{e['alice_ds']}/export/jobs/{job_id}",
        headers=auth_headers(e["alice_id"]),
    )
    assert a.status_code == 200, a.text
    assert "dest_creds_ref" not in a.json()


@pytest.mark.asyncio
async def test_export_job_wrong_datastore_is_404(custody_env):
    e = custody_env
    enq = await e["client"].post(
        f"/api/v1/lake/{e['alice_ds']}/export/jobs",
        json={"dest_uri": "file:///exports/out/", "table": "orders"},
        headers=auth_headers(e["alice_id"]),
    )
    job_id = enq.json()["job_id"]
    # Same org, but pointing the URL at bob's datastore id → 404 (double scope).
    r = await e["client"].get(
        f"/api/v1/lake/{e['bob_ds']}/export/jobs/{job_id}",
        headers=auth_headers(e["alice_id"]),
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Worker claim CAS single-winner
# ---------------------------------------------------------------------------


def test_worker_claim_cas_single_winner():
    store = InMemoryExportJobStore()
    job = store.create_job({"org_id": "o", "datastore_id": "d", "dest_uri": "file:///x"})
    jid = job["id"]
    first = store.claim_job(jid, "worker-1")
    second = store.claim_job(jid, "worker-2")
    assert first is not None and first["state"] == "running"
    assert second is None  # already claimed → no second winner


# ---------------------------------------------------------------------------
# Task B: engine-level FS sandbox — DuckDBStorageConnector.for_export()
#
# These tests verify that the engine-level lockdown (allowed_directories +
# enable_external_access=false + lock_configuration) blocks host-FS access
# EVEN IF the SQL denylist (_FILE_ACCESS_FUNC_RE) were bypassed.
#
# Specifically:
#   1. The engine refuses /etc/passwd at the engine level (not just denylist).
#   2. The engine refuses other-org lake paths at the engine level.
#   3. The legitimate export (read_parquet from org-pinned prefix → COPY TO
#      dest) STILL works through the same hardened connector.
#   4. A full HTTP export (path B/C, table export) succeeds end-to-end.
# ---------------------------------------------------------------------------


def test_for_export_engine_blocks_etc_passwd(tmp_path):
    """Engine-level sandbox: /etc/passwd is inaccessible even without denylist."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    from app.connectors.duckdb_storage import DuckDBStorageConnector

    lake_dir = tmp_path / "lake"
    dest_dir = tmp_path / "dest"
    lake_dir.mkdir()
    dest_dir.mkdir()

    # Write a minimal lake parquet so the connector has something legit to read.
    (lake_dir / "orders").mkdir()
    pq.write_table(
        pa.table({"id": [1, 2], "amount": [100, 200]}),
        str(lake_dir / "orders" / "data.parquet"),
    )

    connector = DuckDBStorageConnector.for_export(
        lake_prefix_dir=str(lake_dir),
        dest_dir=str(dest_dir),
    )

    # /etc/passwd must be blocked AT THE ENGINE LEVEL — this simulates a
    # denylist bypass (the denylist is not invoked here, only the engine).
    with pytest.raises(Exception) as exc_info:
        connector._inner._conn.execute(
            "SELECT * FROM read_csv('/etc/passwd')"
        ).fetchall()

    err_msg = str(exc_info.value).lower()
    # DuckDB raises PermissionException when external access is disabled.
    assert "permission" in err_msg or "disabled" in err_msg or "access" in err_msg, (
        f"Expected a DuckDB permission error, got: {exc_info.value}"
    )


def test_for_export_engine_blocks_other_org_lake(tmp_path):
    """Engine-level sandbox: another org's lake prefix is inaccessible."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    from app.connectors.duckdb_storage import DuckDBStorageConnector

    # Alice's lake (allowed).
    alice_lake = tmp_path / "orgs" / "alice" / "lake" / "ds1"
    alice_lake.mkdir(parents=True)
    (alice_lake / "orders").mkdir()
    pq.write_table(
        pa.table({"id": [1], "amount": [100]}),
        str(alice_lake / "orders" / "data.parquet"),
    )

    # Bob's lake (must be blocked — different org).
    bob_lake = tmp_path / "orgs" / "bob" / "lake" / "ds2"
    bob_lake.mkdir(parents=True)
    pq.write_table(
        pa.table({"secret": ["top-secret"]}),
        str(bob_lake / "private.parquet"),
    )

    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()

    connector = DuckDBStorageConnector.for_export(
        lake_prefix_dir=str(alice_lake),
        dest_dir=str(dest_dir),
    )

    # Bob's lake is outside the allowed_directories — must be blocked by engine.
    with pytest.raises(Exception) as exc_info:
        connector._inner._conn.execute(
            f"SELECT * FROM read_parquet('{bob_lake}/private.parquet')"
        ).fetchall()

    err_msg = str(exc_info.value).lower()
    assert "permission" in err_msg or "disabled" in err_msg or "access" in err_msg, (
        f"Expected engine permission error for cross-org access, got: {exc_info.value}"
    )


def test_for_export_legit_read_parquet_still_works(tmp_path):
    """Legitimate export (org-pinned read_parquet glob → COPY TO) still works."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    from app.connectors.duckdb_storage import DuckDBStorageConnector

    lake_dir = tmp_path / "lake"
    dest_dir = tmp_path / "dest"
    orders_dir = lake_dir / "orders"
    orders_dir.mkdir(parents=True)
    dest_dir.mkdir()

    pq.write_table(
        pa.table({"id": [1, 2, 3], "amount": [10, 20, 30]}),
        str(orders_dir / "data.parquet"),
    )

    connector = DuckDBStorageConnector.for_export(
        lake_prefix_dir=str(lake_dir),
        dest_dir=str(dest_dir),
    )

    # Legitimate glob read from the org-pinned prefix.
    src_glob = str(lake_dir / "orders" / "**" / "*.parquet")
    result = connector._inner._conn.execute(
        f"SELECT * FROM read_parquet('{src_glob}', union_by_name=true)"
    ).fetchall()
    assert sorted(result) == [(1, 10), (2, 20), (3, 30)], (
        f"Legitimate read_parquet failed — got {result}"
    )

    # COPY TO dest_dir succeeds.
    dest_file = str(dest_dir / "orders.parquet")
    sql = f"SELECT * FROM read_parquet('{src_glob}', union_by_name=true)"
    connector._inner._conn.execute(
        f"COPY ({sql}) TO '{dest_file}' (FORMAT parquet)"
    )
    written = pq.read_table(dest_file).to_pydict()
    assert written == {"id": [1, 2, 3], "amount": [10, 20, 30]}, (
        f"COPY TO produced wrong output: {written}"
    )


def test_for_export_engine_blocks_denylist_bypass_variants(tmp_path):
    """Engine blocks file-read attempts even for variants not in the regex denylist.

    This is the core defence-in-depth test: even if _FILE_ACCESS_FUNC_RE
    misses a function name (e.g. a future DuckDB alias or a case variant),
    the engine-level sandbox catches it.
    """

    from app.connectors.duckdb_storage import DuckDBStorageConnector

    lake_dir = tmp_path / "lake"
    dest_dir = tmp_path / "dest"
    lake_dir.mkdir()
    dest_dir.mkdir()

    connector = DuckDBStorageConnector.for_export(
        lake_prefix_dir=str(lake_dir),
        dest_dir=str(dest_dir),
    )

    # Simulate denylist bypass: try to read /etc/hosts using read_text
    # (which IS in the denylist, but here we're testing engine blocks it
    # independently of the denylist — so the engine catches it even if
    # the caller doesn't invoke _validate_export_sql first).
    bypass_attempts = [
        "SELECT * FROM read_text('/etc/hosts')",
        "SELECT * FROM glob('/etc/*')",
        "SELECT line FROM read_blob('/etc/passwd')",
    ]
    for sql in bypass_attempts:
        with pytest.raises(Exception) as exc_info:
            connector._inner._conn.execute(sql).fetchall()
        err_msg = str(exc_info.value).lower()
        assert "permission" in err_msg or "disabled" in err_msg or "access" in err_msg, (
            f"Engine did NOT block denylist-bypass variant {sql!r}: {exc_info.value}"
        )


@pytest.mark.asyncio
async def test_http_table_export_legit_still_works(custody_env, tmp_path):
    """Full HTTP table export (path B) succeeds end-to-end with the hardened connector."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    e = custody_env
    lake_dir = e["lake_dir"]

    # Seed the alice lake with a real parquet file.
    from app.lakehouse.managed import lake_prefix

    prefix = lake_prefix(e["alice_org"], e["alice_ds"])
    orders_dir = os.path.join(lake_dir, prefix, "orders")
    os.makedirs(orders_dir, exist_ok=True)
    pq.write_table(
        pa.table({"id": [10, 20], "val": ["a", "b"]}),
        os.path.join(orders_dir, "data.parquet"),
    )

    dest_dir = str(tmp_path / "export_out")
    os.makedirs(dest_dir, exist_ok=True)

    from tests.security._custody_fixtures import auth_headers

    r = await e["client"].post(
        f"/api/v1/lake/{e['alice_ds']}/export",
        json={"dest_uri": f"file://{dest_dir}", "table": "orders"},
        headers=auth_headers(e["alice_id"]),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tables_exported"] == 1

    # Verify the written parquet is correct.
    written_path = os.path.join(dest_dir, "orders.parquet")
    assert os.path.exists(written_path), f"output file not found: {written_path}"
    result = pq.read_table(written_path).to_pydict()
    assert result == {"id": [10, 20], "val": ["a", "b"]}, f"wrong output: {result}"
