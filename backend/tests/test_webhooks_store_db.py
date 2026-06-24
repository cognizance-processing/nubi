"""DB integration tests for PgWebhookStore — real Postgres required.

Guard
-----
Set ``RUN_PG_TESTS=1`` *and* ``DATABASE_URL`` to a live Postgres instance to
enable this module.  In the default CI/CD hermetic suite both env vars are
absent so the module-level ``pytestmark`` skips every test instantly.

Isolation
---------
Each test session creates a throwaway Postgres schema named
``nubi_wh_<random8hex>``, runs all migrations (including 0016) into it, and
drops it on teardown — reruns always start clean.

Coverage goal
-------------
Exercises every public method of ``PgWebhookStore`` so models.py coverage
goes well above 57%.  Tests cover:

- create → get_by_id → list_for_org → update → delete round-trip
- secret is stored ENCRYPTED (secret_encrypted ≠ plaintext; public rows never
  carry the field)
- cross-org isolation (get/update/delete on another org's endpoint returns
  None/False and doesn't touch the row)
- list_active_for_event: org-scoped, active-only, event_type-filtered
- event_types filtering (subscribed vs not subscribed)
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
        "PgWebhookStore integration tests."
    ),
)

# ---------------------------------------------------------------------------
# Env bootstrap — must precede any app import when PG tests DO run
# ---------------------------------------------------------------------------

if _DB_REAL:
    # Keep DATABASE_URL from conftest override (conftest.py overwrites with fake
    # when RUN_PG_TESTS is unset; when it IS set it preserves the real URL).
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
async def wh_raw_conn():
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
async def wh_schema(wh_raw_conn):
    """Create a throwaway schema, yield its name, then drop it."""
    if not _DB_REAL or wh_raw_conn is None:
        yield ""
        return

    global _TEST_SCHEMA
    schema_name = f"nubi_wh_{uuid.uuid4().hex[:8]}"
    _TEST_SCHEMA = schema_name

    await wh_raw_conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"')
    try:
        yield schema_name
    finally:
        await wh_raw_conn.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
        _TEST_SCHEMA = ""


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def wh_pool(wh_schema):
    """asyncpg pool with search_path pointing at the throwaway schema."""
    if not _DB_REAL:
        yield None
        return

    import asyncpg  # noqa: PLC0415

    async def _init_conn(conn: asyncpg.Connection) -> None:
        await conn.execute(f'SET search_path TO "{wh_schema}", public')

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
async def wh_db(wh_pool, wh_schema):
    """Run all migrations into the throwaway schema; yield the pool."""
    if not _DB_REAL or wh_pool is None:
        yield None
        return

    migrations_dir = Path(__file__).parent.parent.parent / "database" / "migrations"

    async with wh_pool.acquire() as conn:
        await conn.execute(f'SET search_path TO "{wh_schema}", public')
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

    yield wh_pool


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
# Session-level org helper (shared across tests in this file)
# ---------------------------------------------------------------------------


async def _make_org(pool: Any, label: str = "webhook-store-test") -> str:
    """Insert a minimal org row; return org_id."""
    oid = str(uuid.uuid4())
    slug = f"wh-{uuid.uuid4().hex[:8]}"
    await _execute(
        pool,
        "INSERT INTO orgs (id, name, slug) VALUES ($1, $2, $3)",
        oid,
        f"WH Test Org [{label}]",
        slug,
    )
    return oid


# ---------------------------------------------------------------------------
# Convenience: build a PgWebhookStore pointing at the test pool
# ---------------------------------------------------------------------------


def _make_store(pool: Any):
    """Return a PgWebhookStore patched to use the test schema pool."""
    import app.db as _app_db  # noqa: PLC0415
    from app.webhooks.models import PgWebhookStore  # noqa: PLC0415

    _app_db._pool = pool
    return PgWebhookStore()


# ---------------------------------------------------------------------------
# Test 1: create → get_by_id → list_for_org → update → delete round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_webhook_store_crud_roundtrip(wh_db):
    """Full CRUD round-trip through PgWebhookStore."""
    pool = wh_db
    org_id = await _make_org(pool, "crud")
    store = _make_store(pool)

    # ── CREATE ───────────────────────────────────────────────────────────────
    row = await store.create(
        org_id=org_id,
        name="My Webhook",
        url="https://example.com/hook",
        secret="s3cr3t-token",
        created_by=None,
        event_types=["flow_completed", "watch_breach"],
        active=True,
    )
    ep_id = row["id"]
    assert row["name"] == "My Webhook"
    assert row["url"] == "https://example.com/hook"
    assert row["active"] is True
    assert set(row["event_types"]) == {"flow_completed", "watch_breach"}
    assert "secret_encrypted" not in row  # public shape — no secret

    # ── GET BY ID ────────────────────────────────────────────────────────────
    fetched = await store.get_by_id(ep_id, org_id)
    assert fetched is not None
    assert fetched["id"] == ep_id
    assert fetched["name"] == "My Webhook"
    assert "secret_encrypted" not in fetched

    # ── LIST FOR ORG ─────────────────────────────────────────────────────────
    items = await store.list_for_org(org_id)
    assert any(r["id"] == ep_id for r in items)
    for r in items:
        assert "secret_encrypted" not in r  # every row in list is public

    # ── UPDATE ───────────────────────────────────────────────────────────────
    updated = await store.update(
        ep_id,
        org_id,
        name="Renamed Webhook",
        url="https://example.com/hook-v2",
        active=False,
    )
    assert updated is not None
    assert updated["name"] == "Renamed Webhook"
    assert updated["url"] == "https://example.com/hook-v2"
    assert updated["active"] is False
    assert "secret_encrypted" not in updated

    # ── GET after update ──────────────────────────────────────────────────────
    refetched = await store.get_by_id(ep_id, org_id)
    assert refetched is not None
    assert refetched["name"] == "Renamed Webhook"

    # ── DELETE ───────────────────────────────────────────────────────────────
    deleted = await store.delete(ep_id, org_id)
    assert deleted is True

    # ── Verify gone ───────────────────────────────────────────────────────────
    gone = await store.get_by_id(ep_id, org_id)
    assert gone is None

    # ── Second delete → False ─────────────────────────────────────────────────
    second_delete = await store.delete(ep_id, org_id)
    assert second_delete is False


# ---------------------------------------------------------------------------
# Test 2: secret is stored encrypted; public shape never returns secret
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_webhook_store_secret_encrypted_at_rest(wh_db):
    """secret_encrypted column is bytea; public API never exposes it; get_secret decrypts."""
    pool = wh_db
    org_id = await _make_org(pool, "secret")
    store = _make_store(pool)

    plaintext_secret = "super-secret-signing-key-abc123"
    row = await store.create(
        org_id=org_id,
        name="Secret Test EP",
        url="https://example.com/secret-hook",
        secret=plaintext_secret,
        created_by=None,
    )
    ep_id = row["id"]

    # Public row must NOT contain secret_encrypted
    assert "secret_encrypted" not in row

    # DB column must be bytes and must NOT equal the plaintext
    db_row = await _fetchrow(
        pool,
        "SELECT secret_encrypted FROM webhook_endpoints WHERE id = $1::uuid",
        ep_id,
    )
    assert db_row is not None
    raw_bytes: bytes = bytes(db_row["secret_encrypted"])
    assert isinstance(raw_bytes, bytes)
    assert raw_bytes != plaintext_secret.encode()  # never stored as plaintext

    # get_by_id must not return secret_encrypted
    fetched = await store.get_by_id(ep_id, org_id)
    assert fetched is not None
    assert "secret_encrypted" not in fetched

    # get_secret must decrypt and return the original plaintext
    recovered = await store.get_secret(ep_id, org_id)
    assert recovered == plaintext_secret

    # Cleanup
    await store.delete(ep_id, org_id)


# ---------------------------------------------------------------------------
# Test 3: cross-org isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_webhook_store_cross_org_isolation(wh_db):
    """get/update/delete from a different org returns None/False without touching the row."""
    pool = wh_db
    org_a = await _make_org(pool, "iso-a")
    org_b = await _make_org(pool, "iso-b")
    store = _make_store(pool)

    # Create an endpoint owned by org_a
    row = await store.create(
        org_id=org_a,
        name="Org A Endpoint",
        url="https://a.example.com/hook",
        secret="secret-a",
        created_by=None,
        event_types=["watch_breach"],
    )
    ep_id = row["id"]

    # org_b must not be able to GET it
    cross_get = await store.get_by_id(ep_id, org_b)
    assert cross_get is None

    # org_b must not be able to UPDATE it
    cross_update = await store.update(ep_id, org_b, name="Hacked")
    assert cross_update is None

    # org_b must not be able to get its secret
    cross_secret = await store.get_secret(ep_id, org_b)
    assert cross_secret is None

    # org_b must not be able to DELETE it
    cross_delete = await store.delete(ep_id, org_b)
    assert cross_delete is False

    # Verify the row is still intact under org_a
    still_there = await store.get_by_id(ep_id, org_a)
    assert still_there is not None
    assert still_there["name"] == "Org A Endpoint"

    # list_for_org for org_b must NOT include org_a's endpoint
    org_b_list = await store.list_for_org(org_b)
    assert not any(r["id"] == ep_id for r in org_b_list)

    # Cleanup
    await store.delete(ep_id, org_a)


# ---------------------------------------------------------------------------
# Test 4: list_active_for_event — org-scoped, active-only, event-type-filtered
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_webhook_store_list_active_for_event(wh_db):
    """list_active_for_event returns only active, subscribed endpoints for that org."""
    pool = wh_db
    org_id = await _make_org(pool, "active-event")
    other_org = await _make_org(pool, "active-event-other")
    store = _make_store(pool)

    # EP 1: active, subscribed to "flow_completed"
    ep1 = await store.create(
        org_id=org_id,
        name="Active Flow EP",
        url="https://ep1.example.com/hook",
        secret="secret-ep1",
        created_by=None,
        event_types=["flow_completed", "watch_breach"],
        active=True,
    )

    # EP 2: INACTIVE, also subscribed to "flow_completed" — should NOT appear
    ep2 = await store.create(
        org_id=org_id,
        name="Inactive Flow EP",
        url="https://ep2.example.com/hook",
        secret="secret-ep2",
        created_by=None,
        event_types=["flow_completed"],
        active=False,
    )

    # EP 3: active, but NOT subscribed to "flow_completed"
    ep3 = await store.create(
        org_id=org_id,
        name="Watch-only EP",
        url="https://ep3.example.com/hook",
        secret="secret-ep3",
        created_by=None,
        event_types=["watch_breach"],
        active=True,
    )

    # EP 4: active + subscribed, but belongs to other_org — must NOT appear
    ep4 = await store.create(
        org_id=other_org,
        name="Other Org EP",
        url="https://ep4.example.com/hook",
        secret="secret-ep4",
        created_by=None,
        event_types=["flow_completed"],
        active=True,
    )

    results = await store.list_active_for_event(org_id, "flow_completed")

    result_ids = {r["id"] for r in results}
    assert ep1["id"] in result_ids       # active + subscribed ✓
    assert ep2["id"] not in result_ids   # inactive ✗
    assert ep3["id"] not in result_ids   # not subscribed to flow_completed ✗
    assert ep4["id"] not in result_ids   # wrong org ✗

    # Each result must include the decrypted "secret" key for delivery signing
    for r in results:
        assert "secret" in r
        assert isinstance(r["secret"], str)
        # Public fields must be present
        assert "id" in r
        assert "url" in r
        # Must NOT leak the encrypted blob
        assert "secret_encrypted" not in r

    # Cleanup
    for ep in (ep1, ep2, ep3):
        await store.delete(ep["id"], org_id)
    await store.delete(ep4["id"], other_org)


# ---------------------------------------------------------------------------
# Test 5: event_types filtering — subscribing / unsubscribing via update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_webhook_store_event_types_filtering(wh_db):
    """Updating event_types changes which events the endpoint responds to."""
    pool = wh_db
    org_id = await _make_org(pool, "event-types")
    store = _make_store(pool)

    # Start subscribed to "watch_breach" only
    ep = await store.create(
        org_id=org_id,
        name="Event Types EP",
        url="https://ep-ev.example.com/hook",
        secret="secret-ev",
        created_by=None,
        event_types=["watch_breach"],
        active=True,
    )
    ep_id = ep["id"]

    # Should appear for "watch_breach"
    results_wb = await store.list_active_for_event(org_id, "watch_breach")
    assert any(r["id"] == ep_id for r in results_wb)

    # Should NOT appear for "flow_completed"
    results_fc = await store.list_active_for_event(org_id, "flow_completed")
    assert not any(r["id"] == ep_id for r in results_fc)

    # Add "flow_completed" via update
    await store.update(ep_id, org_id, event_types=["watch_breach", "flow_completed"])

    # Now should appear for both
    results_wb2 = await store.list_active_for_event(org_id, "watch_breach")
    assert any(r["id"] == ep_id for r in results_wb2)
    results_fc2 = await store.list_active_for_event(org_id, "flow_completed")
    assert any(r["id"] == ep_id for r in results_fc2)

    # Clear all event types — should no longer appear for either
    await store.update(ep_id, org_id, event_types=[])
    results_none_wb = await store.list_active_for_event(org_id, "watch_breach")
    results_none_fc = await store.list_active_for_event(org_id, "flow_completed")
    assert not any(r["id"] == ep_id for r in results_none_wb)
    assert not any(r["id"] == ep_id for r in results_none_fc)

    # Cleanup
    await store.delete(ep_id, org_id)


# ---------------------------------------------------------------------------
# Test 6: update secret re-encrypts; get_secret returns new plaintext
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_webhook_store_update_secret(wh_db):
    """Rotating the secret via update re-encrypts; get_secret returns the new value."""
    pool = wh_db
    org_id = await _make_org(pool, "secret-rotate")
    store = _make_store(pool)

    ep = await store.create(
        org_id=org_id,
        name="Rotate Secret EP",
        url="https://rotate.example.com/hook",
        secret="original-secret",
        created_by=None,
    )
    ep_id = ep["id"]

    assert await store.get_secret(ep_id, org_id) == "original-secret"

    await store.update(ep_id, org_id, secret="rotated-secret")
    assert await store.get_secret(ep_id, org_id) == "rotated-secret"

    # Cleanup
    await store.delete(ep_id, org_id)


# ---------------------------------------------------------------------------
# Test 7: list_for_org ordering (oldest first)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_webhook_store_list_ordering(wh_db):
    """list_for_org returns endpoints in ascending created_at order."""
    import asyncio  # noqa: PLC0415

    pool = wh_db
    org_id = await _make_org(pool, "ordering")
    store = _make_store(pool)

    ep_ids: list[str] = []
    for i in range(3):
        ep = await store.create(
            org_id=org_id,
            name=f"EP {i}",
            url=f"https://order-{i}.example.com/hook",
            secret=f"secret-{i}",
            created_by=None,
        )
        ep_ids.append(ep["id"])
        # Small sleep to ensure distinct timestamps (Postgres now() resolution)
        await asyncio.sleep(0.01)

    items = await store.list_for_org(org_id)
    filtered = [r for r in items if r["id"] in ep_ids]
    assert [r["id"] for r in filtered] == ep_ids  # oldest first

    # Cleanup
    for eid in ep_ids:
        await store.delete(eid, org_id)


# ---------------------------------------------------------------------------
# Test 8: get_by_id / update / delete on non-existent endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_webhook_store_missing_endpoint(wh_db):
    """Operations on a non-existent endpoint return None/False gracefully."""
    pool = wh_db
    org_id = await _make_org(pool, "missing")
    store = _make_store(pool)

    fake_id = str(uuid.uuid4())

    assert await store.get_by_id(fake_id, org_id) is None
    assert await store.get_secret(fake_id, org_id) is None
    assert await store.update(fake_id, org_id, name="Ghost") is None
    assert await store.delete(fake_id, org_id) is False
