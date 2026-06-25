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

import uuid

import pytest

from app.errors import AppError
from app.lakehouse.managed import lake_prefix
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
