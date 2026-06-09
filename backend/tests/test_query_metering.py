"""Query-route metering + quota enforcement (billing chokepoint).

POST /query is the primary compute workload, so every cache-MISS execution
must produce one org-attributed ``usage_events`` row (kind="compute") and the
EE quota checker must be able to gate it up front.

Coverage
--------
(1) Demo-path query (no datastore_id) records ONE compute event with the
    caller's org_id, elapsed_ms >= 0, output_bytes > 0, tier="demo".
(2) An identical second query is a cache HIT and records NO new event.
(3) A registered quota checker that denies "compute_units" → 402
    quota_exceeded BEFORE any execution (no event recorded).
(4) Datastore path (duckdb type) records the event with tier="duckdb".
(5) A caller with no org membership still gets a 200 on the demo path
    (org_id=None → quota allows; event recorded unattributed).

Test strategy mirrors test_query_connectors.py: InMemoryRepo via set_repo(),
first-party JWT, fake_db user seeding, cache cleared between tests.  The
metering sink is swapped for a fresh InMemorySink per test.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.compute.metering import InMemorySink, set_sink
from app.features import register_quota_checker
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo


def _auth_headers(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def conn_app(app):
    """Inject an InMemoryRepo alongside the conftest fake-DB app."""
    repo = InMemoryRepo()
    set_repo(repo)
    yield app, repo
    set_repo(None)


@pytest_asyncio.fixture
async def conn_client(conn_app, fake_db):
    """HTTPX client with InMemoryRepo + a seeded user/org."""
    app, repo = conn_app

    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())

    fake_db.users[user_id] = {
        "id": user_id,
        "email": "meter-tester@example.com",
        "name": "Meter Tester",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    repo.seed_org_member(org_id=org_id, user_id=user_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as ac:
        yield ac, user_id, org_id, repo


@pytest.fixture(autouse=True)
def _fresh_sink():
    """Inject a fresh InMemorySink per test; reset afterwards."""
    sink = InMemorySink()
    set_sink(sink)
    yield sink
    set_sink(None)


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear the query result cache before and after each test."""
    from app.connectors.cache import get_cache

    get_cache().clear()
    yield
    get_cache().clear()


@pytest.fixture(autouse=True)
def _clear_quota_checker():
    """Ensure no quota checker leaks across tests."""
    register_quota_checker(None)
    yield
    register_quota_checker(None)


def _compute_events(sink: InMemorySink) -> list[dict]:
    return [e for e in sink.get_events() if e["kind"] == "compute"]


# ---------------------------------------------------------------------------
# (1) Demo path records one org-attributed compute event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_demo_query_records_compute_event(conn_client, _fresh_sink):
    client, user_id, org_id, _repo = conn_client

    resp = await client.post(
        "/api/v1/query",
        json={"sql": "SELECT * FROM demo"},
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers.get("x-nubi-cache") == "MISS"

    events = _compute_events(_fresh_sink)
    assert len(events) == 1
    evt = events[0]
    assert evt["org_id"] == org_id
    assert evt["user_id"] == user_id
    assert evt["tier"] == "demo"
    assert evt["elapsed_ms"] >= 0
    assert evt["output_bytes"] > 0
    # units are compute-seconds derived from elapsed_ms
    assert evt["units"] == pytest.approx(evt["elapsed_ms"] / 1000.0)


# ---------------------------------------------------------------------------
# (2) Cache HIT records no new event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_hit_is_not_metered(conn_client, _fresh_sink):
    client, user_id, _org_id, _repo = conn_client
    body = {"sql": "SELECT * FROM demo"}

    first = await client.post("/api/v1/query", json=body, headers=_auth_headers(user_id))
    assert first.status_code == 200
    assert first.headers.get("x-nubi-cache") == "MISS"

    second = await client.post("/api/v1/query", json=body, headers=_auth_headers(user_id))
    assert second.status_code == 200
    assert second.headers.get("x-nubi-cache") == "HIT"

    assert len(_compute_events(_fresh_sink)) == 1


# ---------------------------------------------------------------------------
# (3) Quota denial → 402 before execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_quota_denied_returns_402_and_no_event(conn_client, _fresh_sink):
    client, user_id, org_id, _repo = conn_client

    seen: list[tuple[str, str, float]] = []

    def _deny(*, org_id: str, dimension: str, amount: float = 1.0):
        seen.append((org_id, dimension, amount))
        return False, "Compute quota exhausted for this billing period."

    register_quota_checker(_deny)

    resp = await client.post(
        "/api/v1/query",
        json={"sql": "SELECT * FROM demo"},
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 402, resp.text
    detail = resp.json()
    assert "quota" in str(detail).lower()

    # The checker saw the caller's org + the compute dimension …
    assert seen == [(org_id, "compute_units", 1.0)]
    # … and nothing was executed or metered.
    assert _compute_events(_fresh_sink) == []


@pytest.mark.asyncio
async def test_quota_allowed_passes_through(conn_client, _fresh_sink):
    client, user_id, _org_id, _repo = conn_client

    register_quota_checker(lambda **_kw: (True, ""))

    resp = await client.post(
        "/api/v1/query",
        json={"sql": "SELECT * FROM demo"},
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 200, resp.text
    assert len(_compute_events(_fresh_sink)) == 1


# ---------------------------------------------------------------------------
# (4) Datastore path: event tier reflects the connector type
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_datastore_query_metered_with_connector_tier(conn_client, _fresh_sink):
    client, user_id, org_id, repo = conn_client

    ds = await repo.create(
        "datastores",
        org_id=org_id,
        created_by=user_id,
        name="DuckDB local",
        config={"type": "duckdb"},
    )

    resp = await client.post(
        "/api/v1/query",
        json={"query_id": "demo_points_10k", "datastore_id": ds["id"]},
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 200, resp.text

    events = _compute_events(_fresh_sink)
    assert len(events) == 1
    assert events[0]["tier"] == "duckdb"
    assert events[0]["org_id"] == org_id


# ---------------------------------------------------------------------------
# (5) No org membership → demo path still works, event unattributed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_org_caller_still_queries_demo_path(conn_app, fake_db, _fresh_sink):
    app, _repo = conn_app
    user_id = str(uuid.uuid4())
    fake_db.users[user_id] = {
        "id": user_id,
        "email": "no-org@example.com",
        "name": "No Org",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    # NOTE: no repo.seed_org_member — the user has no org.

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as client:
        resp = await client.post(
            "/api/v1/query",
            json={"sql": "SELECT * FROM demo"},
            headers=_auth_headers(user_id),
        )
    assert resp.status_code == 200, resp.text

    events = _compute_events(_fresh_sink)
    assert len(events) == 1
    assert events[0]["org_id"] is None
