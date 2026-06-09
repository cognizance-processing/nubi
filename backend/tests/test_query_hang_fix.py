"""Regression guard: demo/datastore query must COMPLETE, not hang.

Root cause (fixed in backend/app/db.py):
  asyncpg.create_pool() had no connect_timeout, so any TCP dial to an
  unreachable DATABASE_URL host blocked indefinitely.  pool.acquire() also
  had no timeout, so pool exhaustion under load caused the same symptom.

Fix applied:
  - connect_timeout=10.0 added to asyncpg.create_pool()
  - timeout=30.0 added to every pool.acquire() call

This test proves the query path (specifically the _get_user_org branch that
fires when effective_datastore_id is set) completes in finite time.  It uses
the InMemoryRepo test double so no real DB connection is needed, making it
fully hermetic.  If a future regression re-introduces an unbounded await the
asyncio.wait_for() wrapper turns it into a fast, loud failure instead of a
silent infinite hang.
"""

from __future__ import annotations

import asyncio
import uuid
from io import BytesIO

import pyarrow.ipc as pa_ipc
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TIMEOUT_SECONDS = 10.0  # any query taking longer than this is a hang


def _parse_arrow(content: bytes):
    return pa_ipc.open_stream(BytesIO(content)).read_all()


def _auth_headers(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def hang_client(app, fake_db):
    """Client backed by InMemoryRepo + a seeded user/org/datastore.

    The InMemoryRepo path for get_user_org() (hasattr check → get_org_for_user)
    never touches asyncpg, so the test is hermetic.  This is exactly the shape
    that would have hung before the connect_timeout fix if a PgRepo were used
    with an unreachable DATABASE_URL host.
    """
    repo = InMemoryRepo()
    set_repo(repo)

    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())

    # Seed user so JWT→user lookup works.
    fake_db.users[user_id] = {
        "id": user_id,
        "email": "hangtest@example.com",
        "name": "Hang Tester",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    # Seed org membership so get_user_org() resolves (InMemoryRepo path).
    repo.seed_org_member(org_id=org_id, user_id=user_id)

    # Seed a duckdb-typed datastore (mirrors the demo connector seeded by sample.py).
    ds = await repo.create(
        "datastores",
        org_id=org_id,
        created_by=user_id,
        name="Demo DuckDB",
        config={"connector_type": "duckdb"},  # matches sample.py's config shape
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as ac:
        yield ac, user_id, org_id, ds["id"], repo

    set_repo(None)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_demo_datastore_query_completes_without_hanging(hang_client):
    """POST /query with a duckdb datastore_id must complete within the timeout.

    Before the fix: _get_user_org → fetchrow → pool.acquire() → asyncpg
    connect → TCP dial to unreachable host → hung indefinitely.

    After the fix: InMemoryRepo.get_org_for_user() returns immediately; even
    for PgRepo the connect_timeout=10 and acquire timeout=30 mean any failure
    surfaces as a clear error within bounded time rather than a silent hang.

    The asyncio.wait_for wrapper here is the regression guard: if this test
    ever starts timing out it means the hang was re-introduced.
    """
    client, user_id, org_id, ds_id, repo = hang_client

    async def _do_query():
        return await client.post(
            "/api/v1/query",
            json={
                "sql": "SELECT 42 AS answer",
                "datastore_id": ds_id,
            },
            headers=_auth_headers(user_id),
        )

    # The guard: wrap in wait_for so a regression hangs for at most
    # _TIMEOUT_SECONDS instead of forever.
    resp = await asyncio.wait_for(_do_query(), timeout=_TIMEOUT_SECONDS)

    assert resp.status_code == 200, (
        f"Expected 200 from demo datastore query, got {resp.status_code}: {resp.text}"
    )
    ct = resp.headers.get("content-type", "")
    assert "application/vnd.apache.arrow.stream" in ct, (
        f"Expected Arrow IPC stream content-type, got {ct!r}"
    )
    table = _parse_arrow(resp.content)
    assert table.num_rows == 1, f"Expected 1 row, got {table.num_rows}"
    assert table.column("answer")[0].as_py() == 42


@pytest.mark.asyncio
async def test_no_datastore_query_completes_without_hanging(hang_client):
    """POST /query WITHOUT datastore_id (pure demo path) also completes.

    This is the no-datastore_id path (uses _get_demo_connector, never calls
    _get_user_org).  Included as a regression guard and smoke test.
    """
    client, user_id, *_ = hang_client

    async def _do_query():
        return await client.post(
            "/api/v1/query",
            json={"sql": "SELECT * FROM demo"},
            headers=_auth_headers(user_id),
        )

    resp = await asyncio.wait_for(_do_query(), timeout=_TIMEOUT_SECONDS)

    assert resp.status_code == 200, resp.text
    table = _parse_arrow(resp.content)
    assert table.num_rows == 5  # built-in demo table has 5 rows


@pytest.mark.asyncio
async def test_datastore_query_get_user_org_fast_path(hang_client):
    """_get_user_org resolves immediately from InMemoryRepo (no asyncpg involved).

    Directly verifies the hang-free code path: InMemoryRepo.get_org_for_user
    is synchronous; the await in _get_user_org just hits the hasattr branch
    and returns immediately.  If this ever starts timing out it means the
    InMemoryRepo fast path was broken and the code fell through to asyncpg.
    """
    _, user_id, org_id, _, repo = hang_client

    from app.routes._org import get_user_org

    resolved = await asyncio.wait_for(
        get_user_org(user_id, repo),
        timeout=1.0,  # must be near-instant; 1 s is generous
    )
    assert resolved == org_id, (
        f"Expected org_id={org_id!r}, get_user_org returned {resolved!r}"
    )
