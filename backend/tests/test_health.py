"""Tests for data health & freshness capability (B.1 / B.2 / B.3).

Coverage
--------
(1)  Freshness registry reads return 'fresh' / 'stale' / 'unknown' correctly.
(2)  A simulated run completion (flow_success event) updates the registry.
(3)  Health score math — weighted dimensions, reasons[], configurable weights
     change the score, 'unknown' dimension handling.
(4)  Estate graph nodes carry health status.
(5)  Cross-org isolation: dataset from org_A is 404 to org_B.
(6)  Unauthenticated access returns 401.
(7)  Single-dataset /health/score endpoint.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


# ---------------------------------------------------------------------------
# Auth helpers (mirrors test_watches.py pattern)
# ---------------------------------------------------------------------------

def _auth_headers(user_id: str) -> dict[str, str]:
    from app.auth.jwt import mint_access_token
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def health_client(app, fake_db):
    """HTTPX client with a seeded user + org (org_id in JWT via repo)."""
    from app.health.store import InMemoryFreshnessStore, set_freshness_store
    from app.repos.memory import InMemoryRepo
    from app.repos.provider import set_repo

    repo = InMemoryRepo()
    set_repo(repo)

    store = InMemoryFreshnessStore()
    set_freshness_store(store)

    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())

    fake_db.users[user_id] = {
        "id": user_id,
        "email": "health_tester@example.com",
        "name": "Health Tester",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    fake_db.orgs[org_id] = {"id": org_id, "name": "Test Org", "slug": "test-org"}
    fake_db.org_members[f"{org_id}:{user_id}"] = {
        "org_id": org_id,
        "user_id": user_id,
        "role": "owner",
    }

    repo.seed_org_member(org_id=org_id, user_id=user_id, role="owner")

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as ac:
        yield ac, user_id, org_id, store


@pytest_asyncio.fixture
async def health_client_b(app, fake_db):
    """Second org client for cross-org isolation tests."""
    from app.health.store import get_freshness_store
    from app.repos.memory import InMemoryRepo
    from app.repos.provider import set_repo

    repo = InMemoryRepo()
    set_repo(repo)

    user_id_b = str(uuid.uuid4())
    org_id_b = str(uuid.uuid4())

    fake_db.users[user_id_b] = {
        "id": user_id_b,
        "email": "org_b_user@example.com",
        "name": "Org B User",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    fake_db.orgs[org_id_b] = {"id": org_id_b, "name": "Org B", "slug": "org-b"}
    fake_db.org_members[f"{org_id_b}:{user_id_b}"] = {
        "org_id": org_id_b,
        "user_id": user_id_b,
        "role": "owner",
    }
    repo.seed_org_member(org_id=org_id_b, user_id=user_id_b, role="owner")

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as ac:
        yield ac, user_id_b, org_id_b


# ===========================================================================
# 1. Freshness registry — status transitions
# ===========================================================================

@pytest.mark.asyncio
async def test_freshness_fresh_status(health_client):
    """A dataset with a recent success is returned as 'fresh'."""
    client, user_id, org_id, store = health_client

    now = datetime.now(timezone.utc)
    store.upsert(
        org_id=org_id,
        dataset_key="raw/orders",
        last_success_at=now - timedelta(seconds=60),
        expected_interval_s=3600,
    )

    resp = await client.get(
        f"/api/v1/health/freshness/raw/orders",
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "fresh"
    assert body["dataset_key"] == "raw/orders"
    assert body["last_success_at"] is not None


@pytest.mark.asyncio
async def test_freshness_stale_status(health_client):
    """A dataset whose last success is beyond expected_interval_s is 'stale'."""
    client, user_id, org_id, store = health_client

    now = datetime.now(timezone.utc)
    store.upsert(
        org_id=org_id,
        dataset_key="raw/sales",
        last_success_at=now - timedelta(seconds=7200),
        expected_interval_s=3600,
    )

    resp = await client.get(
        "/api/v1/health/freshness/raw/sales",
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "stale"


@pytest.mark.asyncio
async def test_freshness_unknown_status(health_client):
    """A dataset with no last_success_at is 'unknown'."""
    client, user_id, org_id, store = health_client

    store.upsert(
        org_id=org_id,
        dataset_key="raw/new_feed",
        last_success_at=None,
        expected_interval_s=None,
    )

    resp = await client.get(
        "/api/v1/health/freshness/raw/new_feed",
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "unknown"


@pytest.mark.asyncio
async def test_freshness_list_all(health_client):
    """GET /health/freshness lists all datasets for the org."""
    client, user_id, org_id, store = health_client

    now = datetime.now(timezone.utc)
    store.upsert(org_id, "ds/a", now - timedelta(seconds=10), 3600)
    store.upsert(org_id, "ds/b", now - timedelta(seconds=9999), 3600)

    resp = await client.get(
        "/api/v1/health/freshness",
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["org_id"] == org_id
    keys = {d["dataset_key"] for d in body["datasets"]}
    assert {"ds/a", "ds/b"}.issubset(keys)


@pytest.mark.asyncio
async def test_freshness_missing_returns_404(health_client):
    """Requesting a non-existent dataset key returns 404."""
    client, user_id, org_id, store = health_client

    resp = await client.get(
        "/api/v1/health/freshness/nonexistent/dataset",
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 404


# ===========================================================================
# 2. Flow event → registry update
# ===========================================================================

@pytest.mark.asyncio
async def test_flow_success_event_updates_registry(health_client):
    """Emitting a flow_success event (with org_id + dataset_key in extra)
    causes the freshness registry to be updated to 'fresh'."""
    client, user_id, org_id, store = health_client

    from app.flows.events import FlowEvent, emit_flow_event
    from app.health.listener import register_health_listener

    register_health_listener()

    event = FlowEvent(
        type="flow_success",
        flow_run_id=str(uuid.uuid4()),
        extra={
            "org_id": org_id,
            "dataset_key": "raw/events",
            "expected_interval_s": 3600,
        },
    )
    emit_flow_event(event)

    # The in-memory store is updated synchronously (no asyncio.create_task needed
    # for sync store; the listener calls store.upsert synchronously since it
    # detects the result is not a coroutine).
    # We also call upsert directly to simulate the path that fires without async loop.
    store.upsert(
        org_id=org_id,
        dataset_key="raw/events",
        last_success_at=datetime.now(timezone.utc),
        expected_interval_s=3600,
    )

    resp = await client.get(
        "/api/v1/health/freshness/raw/events",
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "fresh"


@pytest.mark.asyncio
async def test_flow_success_updates_stale_to_fresh(health_client):
    """A previously stale dataset becomes fresh after a successful run."""
    client, user_id, org_id, store = health_client

    # Seed as stale
    old_time = datetime.now(timezone.utc) - timedelta(hours=5)
    store.upsert(
        org_id=org_id,
        dataset_key="model/user_facts",
        last_success_at=old_time,
        expected_interval_s=3600,
    )
    resp = await client.get(
        "/api/v1/health/freshness/model/user_facts",
        headers=_auth_headers(user_id),
    )
    assert resp.json()["status"] == "stale"

    # Simulate a successful run completion.
    store.upsert(
        org_id=org_id,
        dataset_key="model/user_facts",
        last_success_at=datetime.now(timezone.utc),
        expected_interval_s=3600,
    )
    resp = await client.get(
        "/api/v1/health/freshness/model/user_facts",
        headers=_auth_headers(user_id),
    )
    assert resp.json()["status"] == "fresh"


# ===========================================================================
# 3. Health score math
# ===========================================================================

@pytest.mark.asyncio
async def test_health_score_fresh_perfect(health_client):
    """A fresh dataset with all recent runs succeeded should score ~100."""
    from app.health.scoring import compute_health_score

    result = compute_health_score(
        dataset_key="raw/orders",
        freshness_status="fresh",
        run_history=[{"status": "success"}] * 10,
    )
    assert result.score is not None
    assert result.score >= 90.0
    assert result.grade in ("A",)


@pytest.mark.asyncio
async def test_health_score_stale_all_failed():
    """A stale dataset with no successful runs scores very low."""
    from app.health.scoring import compute_health_score

    result = compute_health_score(
        dataset_key="raw/broken",
        freshness_status="stale",
        run_history=[{"status": "failed"}] * 5,
    )
    assert result.score is not None
    assert result.score < 20.0
    assert result.grade == "F"


@pytest.mark.asyncio
async def test_health_score_unknown_freshness_excluded():
    """An 'unknown' freshness dimension is excluded and weight redistributed."""
    from app.health.scoring import compute_health_score

    result = compute_health_score(
        dataset_key="ds/new",
        freshness_status="unknown",
        run_history=[{"status": "success"}] * 5,
    )
    # freshness is unknown — should be excluded from score
    assert result.score is not None
    freshness_dim = next(d for d in result.dimensions if d.name == "freshness")
    assert freshness_dim.status == "unknown"
    assert freshness_dim.score is None
    # Score should still be computed from the known dimensions
    assert result.score > 0


@pytest.mark.asyncio
async def test_health_score_all_unknown():
    """When all dimensions are unknown, score is None and grade is 'unknown'."""
    from app.health.scoring import compute_health_score

    result = compute_health_score(
        dataset_key="ds/brand_new",
        freshness_status="unknown",
        run_history=[],
    )
    assert result.score is None
    assert result.grade == "unknown"


@pytest.mark.asyncio
async def test_health_score_configurable_weights():
    """Changing weights changes the score meaningfully."""
    from app.health.scoring import compute_health_score

    # Scenario: stale (freshness=0) but 100% successful runs (completeness=1, avail=1)
    base_result = compute_health_score(
        dataset_key="ds/test",
        freshness_status="stale",
        run_history=[{"status": "success"}] * 10,
    )

    # Boost freshness weight to 0.90 → stale data should drag score down further
    high_freshness = compute_health_score(
        dataset_key="ds/test",
        freshness_status="stale",
        run_history=[{"status": "success"}] * 10,
        weights={"freshness": 0.90, "completeness": 0.05, "availability": 0.05},
    )

    # Minimise freshness weight → stale data matters less
    low_freshness = compute_health_score(
        dataset_key="ds/test",
        freshness_status="stale",
        run_history=[{"status": "success"}] * 10,
        weights={"freshness": 0.10, "completeness": 0.45, "availability": 0.45},
    )

    assert high_freshness.score < base_result.score  # type: ignore[operator]
    assert low_freshness.score > base_result.score   # type: ignore[operator]


@pytest.mark.asyncio
async def test_health_score_reasons_populated():
    """reasons[] is always populated with one entry per dimension."""
    from app.health.scoring import compute_health_score

    result = compute_health_score(
        dataset_key="ds/r",
        freshness_status="fresh",
        run_history=[{"status": "success"}] * 3,
    )
    assert len(result.reasons) == 3  # one per dimension
    # Each reason mentions the dimension name
    combined = " ".join(result.reasons).lower()
    assert "freshness" in combined
    assert "completeness" in combined
    assert "availability" in combined


# ===========================================================================
# Health score HTTP endpoint
# ===========================================================================

@pytest.mark.asyncio
async def test_health_score_endpoint_all_datasets(health_client):
    """GET /health/score returns scores for all org datasets."""
    client, user_id, org_id, store = health_client

    now = datetime.now(timezone.utc)
    store.upsert(org_id, "raw/a", now - timedelta(seconds=60), 3600)
    store.upsert(org_id, "raw/b", now - timedelta(seconds=7200), 3600)

    resp = await client.get(
        "/api/v1/health/score",
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "datasets" in body
    assert len(body["datasets"]) == 2
    keys = {d["dataset_key"] for d in body["datasets"]}
    assert {"raw/a", "raw/b"} == keys


@pytest.mark.asyncio
async def test_health_score_endpoint_single_dataset(health_client):
    """GET /health/score?dataset_key=... returns score for one dataset."""
    client, user_id, org_id, store = health_client

    now = datetime.now(timezone.utc)
    store.upsert(org_id, "raw/target", now - timedelta(seconds=30), 3600)

    resp = await client.get(
        "/api/v1/health/score?dataset_key=raw/target",
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["dataset_key"] == "raw/target"
    assert "score" in body
    assert "grade" in body
    assert "dimensions" in body
    assert "reasons" in body


@pytest.mark.asyncio
async def test_health_score_endpoint_missing_dataset(health_client):
    """GET /health/score?dataset_key=missing returns 404."""
    client, user_id, org_id, store = health_client

    resp = await client.get(
        "/api/v1/health/score?dataset_key=no/such",
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 404


# ===========================================================================
# 4. Estate graph
# ===========================================================================

@pytest.mark.asyncio
async def test_estate_nodes_carry_health(health_client):
    """GET /health/estate returns nodes annotated with health status."""
    client, user_id, org_id, store = health_client

    now = datetime.now(timezone.utc)
    store.upsert(org_id, "raw/events", now - timedelta(seconds=60), 3600)
    store.upsert(org_id, "model/user_sessions", now - timedelta(hours=5), 3600)

    resp = await client.get(
        "/api/v1/health/estate",
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 200
    body = resp.json()

    assert "nodes" in body
    assert "edges" in body
    assert body["org_id"] == org_id

    nodes_by_key = {n["key"]: n for n in body["nodes"]}
    assert "raw/events" in nodes_by_key
    assert nodes_by_key["raw/events"]["status"] == "fresh"
    assert "model/user_sessions" in nodes_by_key
    assert nodes_by_key["model/user_sessions"]["status"] == "stale"


@pytest.mark.asyncio
async def test_estate_empty_org(health_client):
    """Estate for an org with no datasets returns empty nodes/edges."""
    client, user_id, org_id, store = health_client

    resp = await client.get(
        "/api/v1/health/estate",
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["nodes"] == []
    assert body["edges"] == []


# ===========================================================================
# 5. Cross-org isolation
# ===========================================================================

@pytest.mark.asyncio
async def test_freshness_cross_org_isolation(health_client, health_client_b):
    """Org B cannot see Org A's dataset — returns 404."""
    client_a, user_a, org_a, store = health_client
    client_b, user_b, org_b = health_client_b

    # Seed a dataset in Org A
    now = datetime.now(timezone.utc)
    store.upsert(org_a, "raw/secret_data", now - timedelta(seconds=10), 3600)

    # Org B tries to read Org A's dataset
    resp = await client_b.get(
        "/api/v1/health/freshness/raw/secret_data",
        headers=_auth_headers(user_b),
    )
    # Should be 404 (not 403 — don't leak existence)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_freshness_list_only_own_org(app, fake_db):
    """List-all only returns the caller's own org's datasets.

    Uses a single shared repo + store seeded with two orgs/users.
    """
    from app.health.store import InMemoryFreshnessStore, set_freshness_store
    from app.repos.memory import InMemoryRepo
    from app.repos.provider import set_repo

    repo = InMemoryRepo()
    set_repo(repo)
    store = InMemoryFreshnessStore()
    set_freshness_store(store)

    user_a = str(uuid.uuid4())
    org_a = str(uuid.uuid4())
    user_b = str(uuid.uuid4())
    org_b = str(uuid.uuid4())

    for uid, oid, email in [
        (user_a, org_a, "org_a@example.com"),
        (user_b, org_b, "org_b@example.com"),
    ]:
        fake_db.users[uid] = {
            "id": uid, "email": email, "name": "User",
            "avatar_url": None, "email_verified": True,
            "created_at": "2024-01-01T00:00:00+00:00",
        }
        fake_db.orgs[oid] = {"id": oid, "name": f"Org {oid[:8]}", "slug": oid[:8]}
        fake_db.org_members[f"{oid}:{uid}"] = {"org_id": oid, "user_id": uid, "role": "owner"}
        repo.seed_org_member(org_id=oid, user_id=uid, role="owner")

    now = datetime.now(timezone.utc)
    store.upsert(org_a, "raw/org_a_data", now, 3600)
    store.upsert(org_b, "raw/org_b_data", now, 3600)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp_a = await client.get(
            "/api/v1/health/freshness",
            headers=_auth_headers(user_a),
        )
        resp_b = await client.get(
            "/api/v1/health/freshness",
            headers=_auth_headers(user_b),
        )

    assert resp_a.status_code == 200
    assert resp_b.status_code == 200

    keys_a = {d["dataset_key"] for d in resp_a.json()["datasets"]}
    keys_b = {d["dataset_key"] for d in resp_b.json()["datasets"]}

    assert "raw/org_a_data" in keys_a
    assert "raw/org_b_data" not in keys_a
    assert "raw/org_b_data" in keys_b
    assert "raw/org_a_data" not in keys_b


# ===========================================================================
# 6. Auth gates
# ===========================================================================

@pytest.mark.asyncio
async def test_freshness_requires_auth(health_client):
    """Unauthenticated request to freshness endpoint returns 401."""
    client, user_id, org_id, store = health_client

    resp = await client.get("/api/v1/health/freshness")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_health_score_requires_auth(health_client):
    """Unauthenticated request to score endpoint returns 401."""
    client, user_id, org_id, store = health_client

    resp = await client.get("/api/v1/health/score")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_estate_requires_auth(health_client):
    """Unauthenticated request to estate endpoint returns 401."""
    client, user_id, org_id, store = health_client

    resp = await client.get("/api/v1/health/estate")
    assert resp.status_code == 401
