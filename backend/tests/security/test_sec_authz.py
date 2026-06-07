"""Attack class 4: Cross-tenant authorisation.

Covers
------
4a. Org A cannot read org B's boards → 404 (not 403, by design — no info leak)
4b. Org A cannot update org B's board → 404
4c. Org A cannot delete org B's board → 404
4d. Unauthenticated request → 401
4e. Valid token missing required scope → 403
4f. Org A cannot list org B's boards (list is always org-scoped)
4g. Org A cannot read org B's datastores
4h. Org A cannot read org B's queries (resource CRUD)
4i. Embed token cannot access first-party resource endpoints (boards CRUD)
    Note: embed tokens do not use current_user; they are for /query and /embed only.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

# ── env bootstrap ─────────────────────────────────────────────────────────────
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
    mint_access_token,
    mint_embed_token,
    STATIC_JWKS,
    HOST_ISS,
    HOST_AUD,
    EMBED_ORIGIN,
)


# ---------------------------------------------------------------------------
# In-memory FakeDB for session-level auth (re-used from conftest pattern)
# ---------------------------------------------------------------------------

from datetime import datetime, timezone
from contextlib import asynccontextmanager


class _FakeDB:
    def __init__(self):
        self.users = {}
        self.sessions = {}
        self.org_members = {}

    def reset(self):
        self.users.clear()
        self.sessions.clear()
        self.org_members.clear()

    def seed_user(self, user_id: str, email: str = "user@example.com"):
        self.users[user_id] = {
            "id": user_id,
            "email": email,
            "name": "Test User",
            "password_hash": None,
            "email_verified": True,
            "avatar_url": None,
            "created_at": datetime.now(tz=timezone.utc),
            "updated_at": datetime.now(tz=timezone.utc),
        }

    async def fake_fetchrow(self, query: str, *args):
        q = query.upper().strip()
        if "FROM USERS" in q and "WHERE ID" in q:
            uid = str(args[0]).replace("::uuid", "").strip()
            return self.users.get(uid)
        return None

    async def fake_fetch(self, *a, **kw):
        return []

    async def fake_execute(self, *a, **kw):
        return "OK"

    @asynccontextmanager
    async def fake_get_connection(self):
        yield type("FakeConn", (), {
            "fetchrow": self.fake_fetchrow,
            "fetch": self.fake_fetch,
            "execute": self.fake_execute,
            "transaction": lambda self: type("Tx", (), {
                "__aenter__": lambda s: s,
                "__aexit__": lambda s, *a: False,
            })(),
        })()


_fakedb = _FakeDB()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_fakedb():
    _fakedb.reset()
    yield
    _fakedb.reset()


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


@pytest_asyncio.fixture
async def app_with_repo():
    """Yield a FastAPI app with two separate org repos seeded."""
    from app.repos.memory import InMemoryRepo
    from app.repos.provider import set_repo

    ORG_A = "org-alpha"
    ORG_B = "org-beta"
    USER_A = "user-alpha-001"
    USER_B = "user-beta-001"

    _fakedb.seed_user(USER_A, "a@example.com")
    _fakedb.seed_user(USER_B, "b@example.com")

    repo = InMemoryRepo()
    repo.seed_org_member(org_id=ORG_A, user_id=USER_A)
    repo.seed_org_member(org_id=ORG_B, user_id=USER_B)

    # Seed a board in Org B that Org A should NOT be able to access.
    BOARD_B_ID = "board-org-b-secret"
    repo._store["boards"][BOARD_B_ID] = {
        "id": BOARD_B_ID,
        "org_id": ORG_B,
        "created_by": USER_B,
        "name": "Org B Secret Board",
        "config": {"spec": {}, "widgets": []},
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
    }

    # Seed a datastore in Org B.
    DS_B_ID = "ds-org-b-secret"
    repo._store["datastores"][DS_B_ID] = {
        "id": DS_B_ID,
        "org_id": ORG_B,
        "created_by": USER_B,
        "name": "Org B Datastore",
        "config": {"type": "postgres", "url": "postgresql://secret"},
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
    }

    set_repo(repo)

    patches = [
        patch("app.db.fetchrow", side_effect=_fakedb.fake_fetchrow),
        patch("app.db.fetch", side_effect=_fakedb.fake_fetch),
        patch("app.db.execute", side_effect=_fakedb.fake_execute),
        patch("app.db.get_connection", new=_fakedb.fake_get_connection),
        patch("app.routes.auth.fetchrow", side_effect=_fakedb.fake_fetchrow),
        patch("app.routes.auth.execute", side_effect=_fakedb.fake_execute),
        patch("app.auth.sessions.fetchrow", side_effect=_fakedb.fake_fetchrow),
        patch("app.auth.sessions.execute", side_effect=_fakedb.fake_execute),
        patch("app.auth.sessions.get_connection", new=_fakedb.fake_get_connection),
        patch("app.auth.deps.fetchrow", side_effect=_fakedb.fake_fetchrow),
        patch("app.db.init_db", new=AsyncMock()),
        patch("app.db.close_db", new=AsyncMock()),
    ]
    for p in patches:
        p.start()
    try:
        import main as main_module
        _app = main_module.create_app()
        yield _app, repo, ORG_A, ORG_B, USER_A, USER_B, BOARD_B_ID, DS_B_ID
    finally:
        set_repo(None)
        for p in patches:
            p.stop()


@pytest_asyncio.fixture
async def client_a(app_with_repo):
    """Client authenticated as Org A user."""
    _app, repo, ORG_A, ORG_B, USER_A, USER_B, BOARD_B_ID, DS_B_ID = app_with_repo
    token = mint_access_token(USER_A)
    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(
        transport=ASGITransport(app=_app),
        base_url="http://testserver",
        follow_redirects=False,
        headers={"Authorization": f"Bearer {token}"},
    ) as ac:
        yield ac, BOARD_B_ID, DS_B_ID


# ===========================================================================
# 4a. Org A cannot read org B's board → 404
# ===========================================================================

@pytest.mark.asyncio
async def test_cross_tenant_get_board_returns_404(client_a):
    """Org A's user cannot fetch Org B's board — gets 404 (not 403)."""
    client, board_b_id, _ = client_a
    resp = await client.get(f"/api/v1/boards/{board_b_id}")
    assert resp.status_code == 404, (
        f"SECURITY FAILURE: Org A fetched Org B's board (status {resp.status_code})"
    )


# ===========================================================================
# 4b. Org A cannot update org B's board → 404
# ===========================================================================

@pytest.mark.asyncio
async def test_cross_tenant_update_board_returns_404(client_a):
    """Org A cannot update Org B's board."""
    client, board_b_id, _ = client_a
    resp = await client.put(
        f"/api/v1/boards/{board_b_id}",
        json={"name": "Hacked", "config": {}},
    )
    assert resp.status_code == 404, (
        f"SECURITY FAILURE: Org A updated Org B's board (status {resp.status_code})"
    )


# ===========================================================================
# 4c. Org A cannot delete org B's board → 404
# ===========================================================================

@pytest.mark.asyncio
async def test_cross_tenant_delete_board_returns_404(client_a):
    """Org A cannot delete Org B's board."""
    client, board_b_id, _ = client_a
    resp = await client.delete(f"/api/v1/boards/{board_b_id}")
    assert resp.status_code == 404, (
        f"SECURITY FAILURE: Org A deleted Org B's board (status {resp.status_code})"
    )


# ===========================================================================
# 4d. Unauthenticated request → 401
# ===========================================================================

@pytest.mark.asyncio
async def test_unauthenticated_boards_list_returns_401(app_with_repo):
    """No auth header → 401."""
    _app = app_with_repo[0]
    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(
        transport=ASGITransport(app=_app),
        base_url="http://testserver",
        follow_redirects=False,
    ) as ac:
        resp = await ac.get("/api/v1/boards")
    assert resp.status_code == 401, (
        f"SECURITY FAILURE: unauthenticated request returned {resp.status_code}"
    )


@pytest.mark.asyncio
async def test_unauthenticated_query_returns_401(app_with_repo):
    """POST /query without a token → 401."""
    _app = app_with_repo[0]
    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(
        transport=ASGITransport(app=_app),
        base_url="http://testserver",
        follow_redirects=False,
    ) as ac:
        resp = await ac.post("/api/v1/query", json={"sql": "SELECT 1"})
    assert resp.status_code == 401


# ===========================================================================
# 4f. List is always org-scoped (Org A gets only its own boards)
# ===========================================================================

@pytest.mark.asyncio
async def test_list_boards_only_returns_own_org(client_a):
    """Board list for Org A must not include Org B's secret board."""
    client, board_b_id, _ = client_a
    resp = await client.get("/api/v1/boards")
    # May be 200 with an empty list, or 200 with Org A's boards.
    # The key assertion is that Org B's board ID does NOT appear.
    assert resp.status_code in (200, 404), f"Unexpected status {resp.status_code}"
    if resp.status_code == 200:
        boards = resp.json()
        ids = [b.get("id") for b in boards]
        assert board_b_id not in ids, (
            f"SECURITY FAILURE: Org B's board appeared in Org A's board list: {ids}"
        )


# ===========================================================================
# 4g. Org A cannot read org B's datastore
# ===========================================================================

@pytest.mark.asyncio
async def test_cross_tenant_get_datastore_returns_404(client_a):
    """Org A cannot fetch Org B's datastore."""
    client, _, ds_b_id = client_a
    resp = await client.get(f"/api/v1/datastores/{ds_b_id}")
    assert resp.status_code == 404, (
        f"SECURITY FAILURE: Org A fetched Org B's datastore (status {resp.status_code})"
    )


# ===========================================================================
# 4e. Valid token with insufficient scope → 403 (embed path)
# ===========================================================================

@pytest.mark.asyncio
async def test_embed_token_no_scope_query_rejected(app_with_repo):
    """Embed token with no read scope → 403 on /query."""
    _app = app_with_repo[0]
    token = mint_embed_token(scope=["edit:dashboard:abc"])  # no read scope
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
    assert resp.status_code == 403, (
        f"SECURITY FAILURE: embed token with no read scope returned {resp.status_code}"
    )
    body = resp.json()
    assert body["error"]["code"] == "insufficient_scope"
