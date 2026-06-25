"""DB integration tests for PgIngestSessionStore — real Postgres required.

Guard
-----
Set ``RUN_PG_TESTS=1`` *and* ``DATABASE_URL`` to a live Postgres instance to
enable this module.  Without both, every test is skipped instantly.

Isolation
---------
Each test session creates a throwaway Postgres schema named
``nubi_ingest_<random8hex>``, runs all migrations (including 0017) into it,
and drops it on teardown — reruns always start clean.

Coverage
--------
1. create / get round-trip
2. get_by_idempotency_key
3. Idempotency dedup — same (org, datastore, idem_key) returns existing row
4. transition CAS — open → committing wins; second caller gets None
5. transition with result / error payloads
6. Cross-org isolation — get/transition on wrong org returns None
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Module guard — skip everything when RUN_PG_TESTS is not set or DB is fake
# ---------------------------------------------------------------------------

_RUN_PG = bool(os.getenv("RUN_PG_TESTS"))
_DB_URL = os.environ.get("DATABASE_URL", "")
_DB_REAL = _RUN_PG and _DB_URL and "fake" not in _DB_URL

pytestmark = pytest.mark.skipif(
    not _DB_REAL,
    reason=(
        "Set RUN_PG_TESTS=1 and DATABASE_URL=<real-pg-url> to run "
        "PgIngestSessionStore integration tests."
    ),
)

# ---------------------------------------------------------------------------
# Env bootstrap — must precede any app import when PG tests DO run
# ---------------------------------------------------------------------------

if _DB_REAL:
    os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef")
    os.environ.setdefault("JWT_ACCESS_TTL_MIN", "15")
    os.environ.setdefault("GOOGLE_CLIENT_ID", "fake-gid")
    os.environ.setdefault("GOOGLE_CLIENT_SECRET", "fake-gsecret")
    os.environ.setdefault("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/callback")
    os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")
    os.environ.setdefault("COOKIE_SECURE", "false")
    os.environ.setdefault("ENV", "test")

# ---------------------------------------------------------------------------
# Session-scoped fixtures: schema → pool → migrations
# ---------------------------------------------------------------------------

_TEST_SCHEMA: str = ""


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def ingest_raw_conn():
    """One raw asyncpg connection for schema lifecycle management."""
    if not _DB_REAL:
        yield None
        return

    import asyncpg  # noqa: PLC0415

    conn = await asyncpg.connect(_DB_URL)
    try:
        yield conn
    finally:
        await conn.close()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def ingest_schema(ingest_raw_conn):
    """Create a throwaway schema, yield its name, then drop it."""
    if not _DB_REAL or ingest_raw_conn is None:
        yield ""
        return

    global _TEST_SCHEMA
    schema_name = f"nubi_ingest_{uuid.uuid4().hex[:8]}"
    _TEST_SCHEMA = schema_name

    await ingest_raw_conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"')
    try:
        yield schema_name
    finally:
        await ingest_raw_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        _TEST_SCHEMA = ""


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def ingest_pool(ingest_schema):
    """asyncpg pool with search_path pointing at the throwaway schema."""
    if not _DB_REAL:
        yield None
        return

    import asyncpg  # noqa: PLC0415

    async def _init_conn(conn: asyncpg.Connection) -> None:
        await conn.execute(f'SET search_path TO "{ingest_schema}", public')

    pool = await asyncpg.create_pool(
        dsn=_DB_URL,
        min_size=2,
        max_size=5,
        init=_init_conn,
    )
    try:
        yield pool
    finally:
        await pool.close()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def ingest_db(ingest_pool, ingest_schema):
    """Run all migrations into the throwaway schema; yield the pool."""
    if not _DB_REAL or ingest_pool is None:
        yield None
        return

    migrations_dir = Path(__file__).parent.parent.parent / "database" / "migrations"

    async with ingest_pool.acquire() as conn:
        await conn.execute(f'SET search_path TO "{ingest_schema}", public')
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

    yield ingest_pool


# ---------------------------------------------------------------------------
# Pool-level helpers
# ---------------------------------------------------------------------------


async def _execute(pool: Any, sql: str, *args: Any) -> str:
    async with pool.acquire() as conn:
        return await conn.execute(sql, *args)


async def _fetchrow(pool: Any, sql: str, *args: Any) -> Any:
    async with pool.acquire() as conn:
        return await conn.fetchrow(sql, *args)


# ---------------------------------------------------------------------------
# Org helper
# ---------------------------------------------------------------------------


async def _make_org(pool: Any, label: str = "ingest-test") -> str:
    """Insert a minimal org row; return org_id."""
    oid = str(uuid.uuid4())
    slug = f"ingest-{uuid.uuid4().hex[:8]}"
    await _execute(
        pool,
        "INSERT INTO orgs (id, name, slug) VALUES ($1, $2, $3)",
        oid,
        f"Ingest Test Org [{label}]",
        slug,
    )
    return oid


# ---------------------------------------------------------------------------
# Store factory — patch app.db._pool to point at the test schema pool
# ---------------------------------------------------------------------------


def _make_store(pool: Any):
    """Return a PgIngestSessionStore patched to use the test schema pool."""
    import app.db as _app_db  # noqa: PLC0415
    from app.lakehouse.ingest_session import PgIngestSessionStore  # noqa: PLC0415

    _app_db._pool = pool
    return PgIngestSessionStore()


# ---------------------------------------------------------------------------
# Shared session fixture helpers
# ---------------------------------------------------------------------------

_SCHEMA = [{"name": "id", "type": "int64"}, {"name": "name", "type": "string"}]


def _new_session_kwargs(org_id: str, datastore_id: str, **overrides: Any) -> dict[str, Any]:
    return {
        "org_id": org_id,
        "datastore_id": datastore_id,
        "user_id": str(uuid.uuid4()),
        "mode": "full_replace",
        "idempotency_key": str(uuid.uuid4()),
        "schema": list(_SCHEMA),
        "partition": None,
        "run_id": str(uuid.uuid4()),
        "table_name": "default",
        **overrides,
    }


# ---------------------------------------------------------------------------
# Test 1: create / get round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_pg_ingest_create_and_get(ingest_db):
    """create then get returns the same record."""
    pool = ingest_db
    org_id = await _make_org(pool, "create-get")
    ds_id = str(uuid.uuid4())
    store = _make_store(pool)

    kwargs = _new_session_kwargs(org_id, ds_id)
    created = await store.create(**kwargs)

    assert created["state"] == "open"
    assert created["org_id"] == org_id
    assert created["datastore_id"] == ds_id
    assert created["mode"] == "full_replace"
    assert created["schema"] == _SCHEMA
    assert created["result"] is None
    assert created["error"] is None

    fetched = await store.get(org_id, ds_id, created["id"])
    assert fetched is not None
    assert fetched["id"] == created["id"]
    assert fetched["run_id"] == kwargs["run_id"]


# ---------------------------------------------------------------------------
# Test 2: get_by_idempotency_key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_pg_ingest_get_by_idempotency_key(ingest_db):
    """get_by_idempotency_key returns the correct session."""
    pool = ingest_db
    org_id = await _make_org(pool, "idem-lookup")
    ds_id = str(uuid.uuid4())
    store = _make_store(pool)

    idem_key = str(uuid.uuid4())
    kwargs = _new_session_kwargs(org_id, ds_id, idempotency_key=idem_key)
    created = await store.create(**kwargs)

    found = await store.get_by_idempotency_key(org_id, ds_id, idem_key)
    assert found is not None
    assert found["id"] == created["id"]

    # Different key → None
    missing = await store.get_by_idempotency_key(org_id, ds_id, str(uuid.uuid4()))
    assert missing is None


# ---------------------------------------------------------------------------
# Test 3: idempotency dedup — same key returns existing row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_pg_ingest_idempotency_dedup(ingest_db):
    """Creating with the same idempotency_key twice returns the original session."""
    pool = ingest_db
    org_id = await _make_org(pool, "idem-dedup")
    ds_id = str(uuid.uuid4())
    store = _make_store(pool)

    idem_key = str(uuid.uuid4())
    kwargs = _new_session_kwargs(org_id, ds_id, idempotency_key=idem_key)

    first = await store.create(**kwargs)

    # Second call with same key (different run_id — the new run_id is ignored)
    kwargs2 = dict(kwargs)
    kwargs2["run_id"] = str(uuid.uuid4())
    second = await store.create(**kwargs2)

    # Must be the SAME session
    assert second["id"] == first["id"]
    assert second["run_id"] == first["run_id"]  # original run_id preserved

    # Only one row in DB
    count_row = await _fetchrow(
        pool,
        "SELECT count(*) AS n FROM ingest_sessions "
        "WHERE org_id = $1::uuid AND idempotency_key = $2",
        org_id,
        idem_key,
    )
    assert count_row["n"] == 1


# ---------------------------------------------------------------------------
# Test 4: transition CAS — open → committing; second CAS attempt → None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_pg_ingest_transition_cas(ingest_db):
    """Only the first CAS transition from 'open' to 'committing' succeeds."""
    pool = ingest_db
    org_id = await _make_org(pool, "cas")
    ds_id = str(uuid.uuid4())
    store = _make_store(pool)

    created = await store.create(**_new_session_kwargs(org_id, ds_id))
    session_id = created["id"]

    # First CAS: open → committing
    locked = await store.transition(
        org_id, ds_id, session_id, "committing", from_state="open"
    )
    assert locked is not None
    assert locked["state"] == "committing"

    # Second CAS attempt on same session (simulates concurrent commit): fails
    second = await store.transition(
        org_id, ds_id, session_id, "committing", from_state="open"
    )
    assert second is None  # CAS predicate (state='open') not satisfied

    # Advance to committed with a result payload
    result_payload = {"published": True, "files": 3, "rows": 100}
    committed = await store.transition(
        org_id, ds_id, session_id, "committed",
        from_state="committing",
        result=result_payload,
    )
    assert committed is not None
    assert committed["state"] == "committed"
    assert committed["result"] == result_payload


# ---------------------------------------------------------------------------
# Test 5: transition with error payload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_pg_ingest_transition_error(ingest_db):
    """transition stores the error field on the updated row."""
    pool = ingest_db
    org_id = await _make_org(pool, "error-payload")
    ds_id = str(uuid.uuid4())
    store = _make_store(pool)

    created = await store.create(**_new_session_kwargs(org_id, ds_id))
    session_id = created["id"]

    # Transition to committing first
    await store.transition(org_id, ds_id, session_id, "committing", from_state="open")

    # Revert to open with an error note (simulates commit failure roll-back)
    reverted = await store.transition(
        org_id, ds_id, session_id, "open",
        from_state="committing",
        error="sha256 mismatch on part0.parquet",
    )
    assert reverted is not None
    assert reverted["state"] == "open"
    assert reverted["error"] == "sha256 mismatch on part0.parquet"

    # Abort with no from_state (unconditional on from_state)
    aborted = await store.transition(org_id, ds_id, session_id, "aborted")
    assert aborted is not None
    assert aborted["state"] == "aborted"


# ---------------------------------------------------------------------------
# Test 6: cross-org isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_pg_ingest_cross_org_isolation(ingest_db):
    """get and transition from a different org return None."""
    pool = ingest_db
    org_a = await _make_org(pool, "iso-a")
    org_b = await _make_org(pool, "iso-b")
    ds_id = str(uuid.uuid4())
    store = _make_store(pool)

    created = await store.create(**_new_session_kwargs(org_a, ds_id))
    session_id = created["id"]

    # org_b cannot get org_a's session
    cross_get = await store.get(org_b, ds_id, session_id)
    assert cross_get is None

    # org_b cannot get by idempotency key
    cross_idem = await store.get_by_idempotency_key(org_b, ds_id, created["idempotency_key"])
    assert cross_idem is None

    # org_b cannot transition org_a's session
    cross_trans = await store.transition(org_b, ds_id, session_id, "aborted")
    assert cross_trans is None

    # org_a's session is untouched
    still_open = await store.get(org_a, ds_id, session_id)
    assert still_open is not None
    assert still_open["state"] == "open"
