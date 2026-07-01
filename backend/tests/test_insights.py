"""Tests for ``app.routes.insights`` — the ``/api/v1/_cache/stats`` endpoint.

Strategy
--------
- No real network / cloud I/O involved; the endpoint is a thin auth-gated
  wrapper around ``app.connectors.cache.get_cache().stats()``.
- HTTP-level behaviour is exercised via the shared ``client``/``app``/``fake_db``
  fixtures from ``conftest.py`` (in-memory fake DB, real JWT minting).
- The cache itself is exercised at the unit level too (real
  ``ContentAddressedCache`` via ``reset_cache_for_tests``) so the endpoint's
  numbers are verified end-to-end, not just "some dict came back".

Coverage
--------
1. GET /_cache/stats without a bearer token -> 401.
2. GET /_cache/stats with an invalid/garbage bearer token -> 401.
3. GET /_cache/stats with a valid token but unknown user -> 401.
4. GET /_cache/stats with a valid, seeded user -> 200 + the exact stats dict
   returned by ``get_cache().stats()`` (mocked to a known value).
5. End-to-end: reset the real cache, prime it with a hit and a miss, then hit
   the endpoint and assert the returned counters match.
6. The route self-registers on ``api_router`` at import time (path present).
7. ``cache_stats`` coroutine itself returns exactly ``get_cache().stats()``
   when called directly (unit-level, bypassing FastAPI).
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


def _auth_headers(user_id: str) -> dict[str, str]:
    from app.auth.jwt import mint_access_token

    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


@pytest_asyncio.fixture
async def seeded_client(app, fake_db):
    """HTTPX client + a seeded user id that ``current_user`` will resolve."""
    user_id = str(uuid.uuid4())
    fake_db.users[user_id] = {
        "id": user_id,
        "email": "insights_tester@example.com",
        "name": "Insights Tester",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as ac:
        yield ac, user_id


# ---------------------------------------------------------------------------
# Auth gating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_stats_requires_auth(client):
    """No Authorization header -> 401, cache internals are never touched."""
    resp = await client.get("/api/v1/_cache/stats")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_cache_stats_rejects_garbage_token(client):
    resp = await client.get(
        "/api/v1/_cache/stats", headers={"Authorization": "Bearer not-a-real-jwt"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_cache_stats_rejects_unknown_user(client):
    """A structurally-valid token for a user absent from the DB -> 401."""
    resp = await client.get(
        "/api/v1/_cache/stats", headers=_auth_headers(str(uuid.uuid4()))
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_stats_returns_get_cache_stats(seeded_client):
    """A seeded, authenticated user gets back exactly get_cache().stats()."""
    ac, user_id = seeded_client
    fake_stats = {
        "entries": 3,
        "hits": 7,
        "misses": 2,
        "hit_rate": 7 / 9,
        "tags": 1,
        "total_bytes": 4096,
    }
    fake_cache = type("FakeCache", (), {"stats": lambda self: fake_stats})()
    with patch("app.routes.insights.get_cache", return_value=fake_cache):
        resp = await ac.get("/api/v1/_cache/stats", headers=_auth_headers(user_id))
    assert resp.status_code == 200
    assert resp.json() == fake_stats


@pytest.mark.asyncio
async def test_cache_stats_end_to_end_with_real_cache(seeded_client):
    """Prime the real in-process cache and verify the endpoint reflects it."""
    from app.connectors.cache import get_cache, reset_cache_for_tests

    ac, user_id = seeded_client
    reset_cache_for_tests()
    try:
        with patch.dict("os.environ", {}, clear=False):
            cache = get_cache()
            cache.put("k1", b"hello world")
            assert cache.get("k1") == b"hello world"  # hit
            assert cache.get("missing-key") is None  # miss

            resp = await ac.get("/api/v1/_cache/stats", headers=_auth_headers(user_id))
        assert resp.status_code == 200
        body = resp.json()
        assert body["entries"] == 1
        assert body["hits"] == 1
        assert body["misses"] == 1
        assert body["hit_rate"] == pytest.approx(0.5)
    finally:
        reset_cache_for_tests()


# ---------------------------------------------------------------------------
# Registration + unit-level coroutine behaviour
# ---------------------------------------------------------------------------


def test_route_self_registers_on_api_router():
    import app.routes.insights  # noqa: F401 — triggers self-registration
    from app.routes import api_router

    paths = {route.path for route in api_router.routes}
    assert "/_cache/stats" in paths


@pytest.mark.asyncio
async def test_cache_stats_coroutine_direct_call():
    """Calling the handler coroutine directly returns get_cache().stats()."""
    from app.routes.insights import cache_stats

    fake_stats = {"entries": 0, "hits": 0, "misses": 0, "hit_rate": 0.0}
    fake_cache = type("FakeCache", (), {"stats": lambda self: fake_stats})()
    with patch("app.routes.insights.get_cache", return_value=fake_cache):
        result = await cache_stats(_user={"id": "irrelevant"})
    assert result == fake_stats
