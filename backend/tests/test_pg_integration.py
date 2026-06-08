"""PG-backed integration tests — SKIPPED by default.

These tests exercise real flows through the repo/db layer against a live
Postgres database.  They are guarded by two environment variables:

    RUN_PG_TESTS=1           — enable this test module (required).
    DATABASE_URL=postgres://  — connection URL for the real PG (required).

The default test suite (``pytest tests/``) is completely unaffected:
when ``RUN_PG_TESTS`` is not set, every test in this file is SKIPPED.

Isolation
---------
Each test session creates a throwaway Postgres schema named
``nubi_test_<random8hex>`` and sets the connection ``search_path`` to that
schema before running migrations.  The schema is dropped at the end of the
session so reruns always start clean.

How to run
----------
    RUN_PG_TESTS=1 \\
    DATABASE_URL=postgresql://postgres:postgres@localhost/nubi_test \\
    pytest tests/test_pg_integration.py -v

The ORCHESTRATOR will run them against a live PG.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Guard — skip everything when RUN_PG_TESTS is not set
# ---------------------------------------------------------------------------

RUN_PG_TESTS = bool(os.getenv("RUN_PG_TESTS"))

pytestmark = pytest.mark.skipif(
    not RUN_PG_TESTS,
    reason="Set RUN_PG_TESTS=1 and DATABASE_URL to run PG integration tests.",
)

# ---------------------------------------------------------------------------
# Only import asyncpg when actually running (avoids import errors on machines
# without a PG driver — though asyncpg is in requirements.txt).
# ---------------------------------------------------------------------------

if RUN_PG_TESTS:
    import asyncpg  # noqa: F401 — imported so the error is loud when missing

# ---------------------------------------------------------------------------
# Session-scoped schema fixture
# ---------------------------------------------------------------------------

_TEST_SCHEMA: str = ""  # set by pg_schema fixture; read by helpers


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def pg_raw_conn():
    """Open one raw asyncpg connection for schema management (session scope)."""
    if not RUN_PG_TESTS:
        yield None
        return

    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        pytest.skip("DATABASE_URL not set — skipping PG integration tests.")

    import asyncpg as _apg  # noqa: PLC0415

    conn = await _apg.connect(db_url)
    try:
        yield conn
    finally:
        await conn.close()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def pg_schema(pg_raw_conn):
    """Create a throwaway schema, yield its name, then drop it."""
    if not RUN_PG_TESTS or pg_raw_conn is None:
        yield ""
        return

    global _TEST_SCHEMA
    schema_name = f"nubi_test_{uuid.uuid4().hex[:8]}"
    _TEST_SCHEMA = schema_name

    await pg_raw_conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"')
    try:
        yield schema_name
    finally:
        await pg_raw_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        _TEST_SCHEMA = ""


# ---------------------------------------------------------------------------
# Session-scoped pool fixture (search_path = test schema)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def pg_pool(pg_schema):
    """asyncpg pool with search_path set to the throwaway test schema."""
    if not RUN_PG_TESTS:
        yield None
        return

    db_url = os.environ.get("DATABASE_URL", "")
    import asyncpg as _apg  # noqa: PLC0415

    async def _init_conn(conn: asyncpg.Connection) -> None:  # type: ignore[name-defined]
        await conn.execute(f'SET search_path TO "{pg_schema}", public')

    pool = await _apg.create_pool(
        dsn=db_url,
        min_size=2,
        max_size=5,
        init=_init_conn,
    )
    try:
        yield pool
    finally:
        await pool.close()


# ---------------------------------------------------------------------------
# Session-scoped migration — run once per test session
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def pg_db(pg_pool, pg_schema):
    """Run migrations into the throwaway schema, yield the pool.

    This fixture depends on pg_pool (which has search_path already set) and
    runs the app's migration SQL directly so we don't shell out.
    """
    if not RUN_PG_TESTS or pg_pool is None:
        yield None
        return

    from pathlib import Path  # noqa: PLC0415

    migrations_dir = (
        Path(__file__).parent.parent.parent / "database" / "migrations"
    )

    async with pg_pool.acquire() as conn:
        # Ensure the schema_migrations ledger lives in our test schema.
        await conn.execute(
            f'SET search_path TO "{pg_schema}", public'
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version    text        PRIMARY KEY,
                applied_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        applied = {
            r["version"]
            for r in await conn.fetch("SELECT version FROM schema_migrations")
        }
        for sql_file in sorted(migrations_dir.glob("*.sql")):
            if sql_file.name in applied:
                continue
            sql = sql_file.read_text(encoding="utf-8")
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES ($1)",
                    sql_file.name,
                )

    yield pg_pool


# ---------------------------------------------------------------------------
# Helpers — thin wrappers around the pool that mirror app.db signatures
# ---------------------------------------------------------------------------


async def _fetch(pool, query: str, *args: Any):
    async with pool.acquire() as conn:
        return await conn.fetch(query, *args)


async def _fetchrow(pool, query: str, *args: Any):
    async with pool.acquire() as conn:
        return await conn.fetchrow(query, *args)


async def _execute(pool, query: str, *args: Any) -> str:
    async with pool.acquire() as conn:
        return await conn.execute(query, *args)


# ---------------------------------------------------------------------------
# Helper — create a fresh user + personal org inside the test schema
# ---------------------------------------------------------------------------


async def _create_user_and_org(pool) -> tuple[str, str]:
    """Insert a new test user and personal org; return (user_id, org_id)."""
    from app.auth.passwords import hash_password as _hash  # noqa: PLC0415

    uid = str(uuid.uuid4())
    email = f"pgtest-{uid[:8]}@nubi.test"
    await _execute(
        pool,
        """
        INSERT INTO users (id, email, password_hash, name, email_verified)
        VALUES ($1, $2, $3, $4, true)
        """,
        uid,
        email,
        _hash("testpass123"),
        "PG Test User",
    )
    oid = str(uuid.uuid4())
    slug = f"pgtest-{uid[:8]}"
    await _execute(
        pool,
        "INSERT INTO orgs (id, name, slug) VALUES ($1, $2, $3)",
        oid,
        "PG Test Workspace",
        slug,
    )
    await _execute(
        pool,
        "INSERT INTO org_members (org_id, user_id, role) VALUES ($1, $2, 'owner')",
        oid,
        uid,
    )
    return uid, oid


# ---------------------------------------------------------------------------
# Test: migrate → seed → user + org round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_migrate_and_create_user_org(pg_db):
    """Migrations applied; can insert and retrieve a user + org."""
    pool = pg_db

    uid, oid = await _create_user_and_org(pool)

    user_row = await _fetchrow(pool, "SELECT * FROM users WHERE id = $1::uuid", uid)
    assert user_row is not None
    assert str(user_row["id"]) == uid

    org_row = await _fetchrow(pool, "SELECT * FROM orgs WHERE id = $1::uuid", oid)
    assert org_row is not None
    assert str(org_row["id"]) == oid

    member_row = await _fetchrow(
        pool,
        "SELECT * FROM org_members WHERE org_id = $1::uuid AND user_id = $2::uuid",
        oid,
        uid,
    )
    assert member_row is not None
    assert member_row["role"] == "owner"


# ---------------------------------------------------------------------------
# Test: datastore CRUD via PgRepo
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_datastore_crud(pg_db):
    """PgRepo: create, read, update, delete a datastore row."""
    pool = pg_db
    uid, oid = await _create_user_and_org(pool)

    # ── Monkey-patch the global asyncpg pool so PgRepo uses our test pool ──
    import app.db as app_db  # noqa: PLC0415

    original_pool = app_db._pool
    app_db._pool = pool
    try:
        from app.repos.pg import PgRepo  # noqa: PLC0415

        repo = PgRepo()

        # CREATE
        ds = await repo.create(
            resource="datastores",
            org_id=oid,
            created_by=uid,
            name="Test DuckDB",
            config={"type": "duckdb", "path": ":memory:"},
        )
        assert ds["name"] == "Test DuckDB"
        ds_id = ds["id"]

        # LIST
        items = await repo.list("datastores", oid)
        assert any(r["id"] == ds_id for r in items)

        # GET
        fetched = await repo.get("datastores", oid, ds_id)
        assert fetched is not None
        assert fetched["id"] == ds_id
        assert fetched["config"]["type"] == "duckdb"

        # UPDATE name
        updated = await repo.update(
            "datastores", oid, ds_id, {"name": "Renamed DuckDB"}
        )
        assert updated is not None
        assert updated["name"] == "Renamed DuckDB"

        # UPDATE config
        updated2 = await repo.update(
            "datastores", oid, ds_id, {"config": {"type": "duckdb", "path": "/tmp/demo.ddb"}}
        )
        assert updated2 is not None
        assert updated2["config"]["path"] == "/tmp/demo.ddb"

        # GET after update
        refetched = await repo.get("datastores", oid, ds_id)
        assert refetched is not None
        assert refetched["name"] == "Renamed DuckDB"

        # Cross-org isolation — different org_id should return None
        other_org_id = str(uuid.uuid4())
        cross = await repo.get("datastores", other_org_id, ds_id)
        assert cross is None

        # DELETE
        deleted = await repo.delete("datastores", oid, ds_id)
        assert deleted is True

        # Verify gone
        gone = await repo.get("datastores", oid, ds_id)
        assert gone is None

        # Double-delete returns False
        again = await repo.delete("datastores", oid, ds_id)
        assert again is False

    finally:
        app_db._pool = original_pool


# ---------------------------------------------------------------------------
# Test: register + persist a query row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_query_register_and_persist(pg_db):
    """Insert a query row (with param declarations) and retrieve it."""
    pool = pg_db
    uid, oid = await _create_user_and_org(pool)

    import app.db as app_db  # noqa: PLC0415

    original_pool = app_db._pool
    app_db._pool = pool
    try:
        from app.repos.pg import PgRepo  # noqa: PLC0415

        repo = PgRepo()

        params_decl = [
            {
                "name": "region",
                "type": "select",
                "default": "north",
                "required": False,
            }
        ]

        q = await repo.create(
            resource="queries",
            org_id=oid,
            created_by=uid,
            name="Sales by Region ({{region}})",
            config={
                "sql": "SELECT * FROM sales WHERE region = {{region}}",
                "params": params_decl,
            },
        )
        assert q["name"] == "Sales by Region ({{region}})"
        q_id = q["id"]

        fetched = await repo.get("queries", oid, q_id)
        assert fetched is not None
        assert fetched["config"]["sql"] == "SELECT * FROM sales WHERE region = {{region}}"
        assert fetched["config"]["params"][0]["name"] == "region"

    finally:
        app_db._pool = original_pool


# ---------------------------------------------------------------------------
# Test: board CRUD with a full DashboardSpec in config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_board_create_read_update_delete(pg_db):
    """Create a board with a full spec, update it, then delete it."""
    pool = pg_db
    uid, oid = await _create_user_and_org(pool)

    import app.db as app_db  # noqa: PLC0415

    original_pool = app_db._pool
    app_db._pool = pool
    try:
        from app.repos.pg import PgRepo  # noqa: PLC0415

        repo = PgRepo()

        # Insert a prerequisite query (so the spec query_id reference is realistic)
        dummy_q = await repo.create(
            resource="queries",
            org_id=oid,
            created_by=uid,
            name="Dummy Q",
            config={"sql": "SELECT 1 AS n"},
        )
        q_id = dummy_q["id"]

        spec = {
            "version": 1,
            "title": "Integration Test Board",
            "layout": {"cols": 12, "row_height": 60},
            "variables": [{"name": "region", "type": "select", "default": "north"}],
            "widgets": [
                {
                    "id": "kpi1",
                    "type": "kpi",
                    "query_id": q_id,
                    "encoding": {"value": "n"},
                    "props": {"label": "Count"},
                    "pos": {"x": 1, "y": 1, "w": 3, "h": 2},
                },
                {
                    "id": "filter1",
                    "type": "filter",
                    "query_id": "",
                    "subtype": "select",
                    "target_var": "region",
                    "encoding": {},
                    "props": {"label": "Region"},
                    "pos": {"x": 4, "y": 1, "w": 3, "h": 2},
                },
            ],
        }

        # CREATE
        board = await repo.create(
            resource="boards",
            org_id=oid,
            created_by=uid,
            name="Integration Test Board",
            config={"spec": spec},
        )
        board_id = board["id"]
        assert board["config"]["spec"]["title"] == "Integration Test Board"

        # READ
        fetched = await repo.get("boards", oid, board_id)
        assert fetched is not None
        assert fetched["config"]["spec"]["variables"][0]["name"] == "region"

        # UPDATE — rename the board and add a widget
        spec2 = dict(spec)
        spec2["title"] = "Integration Test Board (updated)"
        updated = await repo.update(
            "boards",
            oid,
            board_id,
            {"name": "Updated Board", "config": {"spec": spec2}},
        )
        assert updated is not None
        assert updated["name"] == "Updated Board"
        assert updated["config"]["spec"]["title"] == "Integration Test Board (updated)"

        # LIST — board should appear
        boards = await repo.list("boards", oid)
        assert any(b["id"] == board_id for b in boards)

        # DELETE
        deleted = await repo.delete("boards", oid, board_id)
        assert deleted is True
        assert await repo.get("boards", oid, board_id) is None

    finally:
        app_db._pool = original_pool


# ---------------------------------------------------------------------------
# Test: job CRUD via PgJobStore
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_job_create_and_retrieve(pg_db):
    """PgJobStore: create a job, read it back, update enabled flag, delete it."""
    pool = pg_db
    uid, oid = await _create_user_and_org(pool)

    import app.db as app_db  # noqa: PLC0415

    original_pool = app_db._pool
    app_db._pool = pool
    try:
        from app.jobs.store import PgJobStore  # noqa: PLC0415

        store = PgJobStore()

        # CREATE
        job = await store.create_job(
            org_id=oid,
            created_by=uid,
            name="Daily Test Sync",
            kind="query",
            target="demo_all",
            schedule="0 6 * * *",
            enabled=True,
        )
        job_id = job["id"]
        assert job["name"] == "Daily Test Sync"
        assert job["kind"] == "query"
        assert job["enabled"] is True

        # GET
        fetched = await store.get_job(job_id)
        assert fetched is not None
        assert fetched["id"] == job_id
        assert fetched["schedule"] == "0 6 * * *"

        # LIST
        jobs = await store.list_jobs(oid)
        assert any(j["id"] == job_id for j in jobs)

        # UPDATE — disable the job
        updated = await store.update_job(job_id, {"enabled": False})
        assert updated is not None
        assert updated["enabled"] is False

        # Verify update persisted
        refetched = await store.get_job(job_id)
        assert refetched is not None
        assert refetched["enabled"] is False

        # DELETE
        deleted = await store.delete_job(job_id)
        assert deleted is True
        assert await store.get_job(job_id) is None

        # Double-delete
        again = await store.delete_job(job_id)
        assert again is False

    finally:
        app_db._pool = original_pool


# ---------------------------------------------------------------------------
# Test: full seed_demo run against the test schema
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_seed_demo_idempotent(pg_db):
    """seed.py --demo creates the expected demo objects; running twice is a no-op."""
    pool = pg_db

    import app.db as app_db  # noqa: PLC0415
    import sys  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    # Ensure backend/ is on sys.path so seed.py can be imported.
    backend_dir = str(Path(__file__).parent.parent)
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)

    original_pool = app_db._pool
    app_db._pool = pool
    try:
        import seed as _sd  # noqa: PLC0415

        # Superuser + personal org, then the comprehensive demo workspace.
        uid = await _sd._ensure_superuser()
        await _sd._seed_demo(uid)

        org_row = await _fetchrow(
            pool,
            "SELECT org_id FROM org_members WHERE user_id = $1::uuid LIMIT 1",
            uid,
        )
        assert org_row is not None
        oid = str(org_row["org_id"])

        # Demo datastore + queries + 10 boards (counts may be higher if the
        # per-project sample bundle was also seeded on org creation).
        ds_rows = await _fetch(pool, "SELECT * FROM datastores WHERE org_id = $1::uuid", oid)
        assert len(ds_rows) >= 1
        q_rows = await _fetch(pool, "SELECT * FROM queries WHERE org_id = $1::uuid", oid)
        assert len(q_rows) >= 20
        b_rows = await _fetch(pool, "SELECT * FROM boards WHERE org_id = $1::uuid", oid)
        assert len(b_rows) >= 10

        # Second run — idempotent; counts must not increase.
        await _sd._seed_demo(uid)

        ds_rows2 = await _fetch(pool, "SELECT * FROM datastores WHERE org_id = $1::uuid", oid)
        assert len(ds_rows2) == len(ds_rows), "seed --demo not idempotent: datastore count grew"
        q_rows2 = await _fetch(pool, "SELECT * FROM queries WHERE org_id = $1::uuid", oid)
        assert len(q_rows2) == len(q_rows), "seed --demo not idempotent: query count grew"
        b_rows2 = await _fetch(pool, "SELECT * FROM boards WHERE org_id = $1::uuid", oid)
        assert len(b_rows2) == len(b_rows), "seed --demo not idempotent: board count grew"

    finally:
        app_db._pool = original_pool


# ---------------------------------------------------------------------------
# Test: full Flow lifecycle via PgFlowStore + async engine (regression guard)
# ---------------------------------------------------------------------------
#
# This is the canary for the sync/async store bug: the flows engine + routes
# previously called PgFlowStore methods WITHOUT ``await``, so every flow
# operation returned an un-awaited coroutine and blew up against real
# Postgres ("'coroutine' object is not subscriptable").  Running a trivial
# flow end-to-end here proves the whole async path (materialize → drain →
# advance_readiness → claim_ready_task_run) works against a live DB.


@pytest.mark.asyncio(loop_scope="session")
async def test_flow_create_and_run_end_to_end(pg_db):
    """PgFlowStore + runtime: create a 2-task noop flow and drain it to success."""
    pool = pg_db
    uid, oid = await _create_user_and_org(pool)

    import app.db as app_db  # noqa: PLC0415

    original_pool = app_db._pool
    app_db._pool = pool
    try:
        from datetime import datetime, timezone  # noqa: PLC0415

        from app.flows.runtime import (  # noqa: PLC0415
            drain_flow_run,
            materialize_flow_run,
        )
        from app.flows.store import PgFlowStore  # noqa: PLC0415

        store = PgFlowStore()

        spec = {
            "version": 1,
            "name": "pg_flow",
            "params": [],
            "tasks": [
                {"key": "step1", "kind": "noop", "needs": [], "config": {}},
                {"key": "step2", "kind": "noop", "needs": ["step1"], "config": {}},
            ],
        }

        # CREATE
        flow = await store.create_flow(
            org_id=oid,
            created_by=uid,
            name="PG Flow",
            spec=spec,
        )
        flow_id = flow["id"]
        assert flow["name"] == "PG Flow"

        # GET / LIST
        fetched = await store.get_flow(flow_id)
        assert fetched is not None and fetched["id"] == flow_id
        flows = await store.list_flows(oid)
        assert any(f["id"] == flow_id for f in flows)

        # MATERIALIZE + DRAIN (the heart of the bug)
        now = datetime.now(timezone.utc)
        claims = {
            "kind": "access",
            "sub": uid,
            "org_id": oid,
            "policies": {},
            "scope": ["read:*", "write:*"],
        }

        flow_run = await materialize_flow_run(store, fetched, {}, "manual", now)
        assert flow_run["state"] == "running"

        final = await drain_flow_run(store, flow_run["id"], now, claims=claims)
        assert final["state"] == "success", f"flow_run did not succeed: {final}"

        # Verify task_runs all reached success.
        task_runs = await store.list_task_runs(flow_run["id"])
        states = {tr["task_key"]: tr["state"] for tr in task_runs}
        assert states == {"step1": "success", "step2": "success"}, states

        # LIST RUNS
        runs = await store.list_flow_runs(flow_id)
        assert any(r["id"] == flow_run["id"] for r in runs)

        # DELETE (cascades to runs + task_runs)
        deleted = await store.delete_flow(flow_id)
        assert deleted is True
        assert await store.get_flow(flow_id) is None

    finally:
        app_db._pool = original_pool
