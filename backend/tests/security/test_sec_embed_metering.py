"""Metering invariant: EMBED tokens are NEVER metered.

CORE INVARIANT
--------------
First-party viewer-role users AND embed tokens are NEVER metered — neither the
pre-flight quota gate NOR the post-execution ``usage_events`` writes. A cache
MISS executed under an embed token must write ZERO usage_events (compute AND
query_scan); otherwise ``ee.billing.reconcile.aggregate_usage_for_org`` sums
those rows into the org's billable overage and inflates the bill for traffic
that, by design, is free.

Coverage
--------
- An embed-token /query on a cache MISS writes ZERO usage_events
  (no kind="compute", no kind="query_scan").
- Control: a first-party WRITER/owner running the SAME query DOES write both
  events — proving the sink is wired and the query path actually meters.
"""

from __future__ import annotations

import os
import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

# ── env bootstrap (mirrors the rest of the security suite) ──────────────────
os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef")
os.environ.setdefault("JWT_ACCESS_TTL_MIN", "15")
os.environ.setdefault("GOOGLE_CLIENT_ID", "fake-gid")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "fake-gsecret")
os.environ.setdefault("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/v1/auth/google/callback")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("ENV", "test")

from tests.security.conftest_helpers import (  # noqa: E402
    mint_embed_token,
    mint_access_token,
    STATIC_JWKS,
    HOST_ISS,
    HOST_AUD,
    EMBED_ORIGIN,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _register_issuer():
    from app.auth.issuers import get_issuer_registry
    from app.auth.jwks_cache import clear_cache
    from app.config import get_settings

    get_settings.cache_clear()
    reg = get_issuer_registry()
    reg.register(
        HOST_ISS,
        jwks_uri=f"{HOST_ISS}/.well-known/jwks.json",
        aud=HOST_AUD,
        allowed_origins=[EMBED_ORIGIN],
        static_jwks=STATIC_JWKS,
    )
    yield
    reg.unregister(HOST_ISS)
    clear_cache()
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _clear_cache():
    from app.connectors.cache import get_cache

    get_cache().clear()
    yield
    get_cache().clear()


@pytest.fixture(autouse=True)
def _fresh_sink():
    """Inject a fresh in-memory metering sink per test; reset afterwards."""
    from app.compute.metering import InMemorySink, set_sink

    sink = InMemorySink()
    set_sink(sink)
    yield sink
    set_sink(None)


@pytest.fixture(autouse=True)
def _no_quota_checker():
    """Ensure no quota checker leaks in (the gate is bypassed for embed anyway)."""
    from app.features import register_quota_checker

    register_quota_checker(None)
    yield
    register_quota_checker(None)


class _FakeDB:
    def __init__(self):
        self.users = {}

    def reset(self):
        self.users.clear()

    def seed_user(self, user_id: str, email: str = "writer@example.com"):
        self.users[user_id] = {
            "id": user_id,
            "email": email,
            "name": "Writer",
            "password_hash": None,
            "email_verified": True,
            "avatar_url": None,
            "created_at": "2024-01-01T00:00:00+00:00",
            "updated_at": "2024-01-01T00:00:00+00:00",
        }

    async def fake_fetchrow(self, query: str, *args):
        q = query.upper().strip()
        if "FROM USERS" in q and "WHERE ID" in q:
            uid = str(args[0]).replace("::uuid", "").strip()
            return self.users.get(uid)
        return None


_fakedb = _FakeDB()


@pytest.fixture(autouse=True)
def _reset_fakedb():
    _fakedb.reset()
    yield
    _fakedb.reset()


@pytest_asyncio.fixture
async def app_with_repo():
    """FastAPI app with an InMemoryRepo seeded with one writer/owner user."""
    from app.repos.memory import InMemoryRepo
    from app.repos.provider import set_repo

    writer_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    _fakedb.seed_user(writer_id, "writer@example.com")

    repo = InMemoryRepo()
    repo.seed_org_member(org_id=org_id, user_id=writer_id, role="owner")
    set_repo(repo)

    patches = [
        patch("app.db.fetchrow", side_effect=_fakedb.fake_fetchrow),
        patch("app.db.fetch", new=AsyncMock(return_value=[])),
        patch("app.db.execute", new=AsyncMock(return_value="OK")),
        patch("app.db.init_db", new=AsyncMock()),
        patch("app.db.close_db", new=AsyncMock()),
        patch("app.auth.deps.fetchrow", side_effect=_fakedb.fake_fetchrow),
    ]
    for p in patches:
        p.start()
    try:
        import main as main_module

        _app = main_module.create_app()
        yield _app, writer_id, org_id
    finally:
        set_repo(None)
        for p in patches:
            p.stop()


def _compute_events(sink) -> list[dict]:
    return [e for e in sink.get_events() if e["kind"] == "compute"]


def _scan_events(sink) -> list[dict]:
    return [e for e in sink.get_events() if e["kind"] == "query_scan"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_embed_token_cache_miss_writes_no_usage_events(app_with_repo, _fresh_sink):
    """An embed-token query on a cache MISS must write ZERO usage_events."""
    _app, _writer_id, _org_id = app_with_repo
    token = mint_embed_token(scope=["read:query"])

    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=_app),
        base_url="http://testserver",
        follow_redirects=False,
    ) as ac:
        resp = await ac.post(
            "/api/v1/query",
            json={"query_id": "demo_all"},
            headers={"Authorization": f"Bearer {token}", "Origin": EMBED_ORIGIN},
        )

    assert resp.status_code == 200, resp.text
    # Cache MISS — the query actually executed …
    assert resp.headers.get("x-nubi-cache") == "MISS"
    # … but embed tokens are NEVER metered: zero compute AND zero query_scan.
    assert _compute_events(_fresh_sink) == [], (
        "INVARIANT VIOLATION: embed token wrote a compute usage_event"
    )
    assert _scan_events(_fresh_sink) == [], (
        "INVARIANT VIOLATION: embed token wrote a query_scan usage_event"
    )


@pytest.mark.asyncio
async def test_writer_cache_miss_writes_both_usage_events(app_with_repo, _fresh_sink):
    """Control: a first-party writer/owner on the SAME query DOES meter both."""
    _app, writer_id, org_id = app_with_repo
    token = mint_access_token(writer_id)

    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=_app),
        base_url="http://testserver",
        follow_redirects=False,
    ) as ac:
        resp = await ac.post(
            "/api/v1/query",
            json={"query_id": "demo_all"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200, resp.text
    assert resp.headers.get("x-nubi-cache") == "MISS"
    compute = _compute_events(_fresh_sink)
    scan = _scan_events(_fresh_sink)
    assert len(compute) == 1, f"expected 1 compute event, got {compute}"
    assert len(scan) == 1, f"expected 1 query_scan event, got {scan}"
    assert compute[0]["org_id"] == org_id
    assert scan[0]["org_id"] == org_id
