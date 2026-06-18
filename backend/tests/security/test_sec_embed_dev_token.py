"""Security tests for POST /embed/embed-token (dev helper).

Covers the three fixes applied to embed.py:
  A. Endpoint requires a valid bearer token (current_user) — no unauthenticated access.
  B. Forbidden scope prefixes (write:, edit:, admin:, delete:) are rejected with 400.
  C. ENV=production guard returns 503 even when EMBED_DEV_TOKEN_ENABLED=true.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Environment bootstrap
# ---------------------------------------------------------------------------
os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef")
os.environ.setdefault("JWT_ACCESS_TTL_MIN", "15")
os.environ.setdefault("GOOGLE_CLIENT_ID", "fake-gid")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "fake-gsecret")
os.environ.setdefault("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/v1/auth/google/callback")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("ENV", "test")

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_USER_ID = "00000000-0000-0000-0000-000000000001"
_FAKE_USER_ROW = {
    "id": _USER_ID,
    "email": "dev@example.com",
    "name": "Dev User",
    "avatar_url": None,
    "email_verified": True,
    "created_at": "2024-01-01T00:00:00+00:00",
}


async def _fake_fetchrow(query: str, *args):
    """Route-aware fetchrow stub.

    - Users lookup (FROM users WHERE id) → returns the fake user row.
    - Denylist lookup (revoked_tokens) → returns None (token not revoked).
    - Everything else → None.
    """
    q = query.upper()
    if "FROM USERS" in q:
        return _FAKE_USER_ROW
    return None


@pytest_asyncio.fixture
async def app():
    """Create the FastAPI app with DB patched out and an in-memory denylist."""
    from app.auth.denylist import InMemoryTokenDenylist, set_token_denylist_for_tests

    denylist = InMemoryTokenDenylist()
    set_token_denylist_for_tests(denylist)

    patches = [
        patch("app.db.fetchrow", new=_fake_fetchrow),
        patch("app.db.fetch", new=AsyncMock(return_value=[])),
        patch("app.db.execute", new=AsyncMock(return_value="OK")),
        patch("app.db.init_db", new=AsyncMock()),
        patch("app.db.close_db", new=AsyncMock()),
        # current_user uses this direct import
        patch("app.auth.deps.fetchrow", new=_fake_fetchrow),
    ]
    for p in patches:
        p.start()
    try:
        import main as main_module
        _app = main_module.create_app()
        yield _app
    finally:
        for p in patches:
            p.stop()
        set_token_denylist_for_tests(None)


@pytest_asyncio.fixture
async def client(app):
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as ac:
        yield ac


def _valid_access_token() -> str:
    """Mint a valid first-party HS256 access token for the fake user."""
    from app.auth.jwt import mint_access_token

    return mint_access_token(_USER_ID)


# ===========================================================================
# A. Authentication guard
# ===========================================================================


@pytest.mark.asyncio
async def test_embed_token_unauthenticated_rejected(client):
    """No bearer token → 401; endpoint is no longer openly accessible."""
    resp = await client.post(
        "/api/v1/embed/embed-token",
        json={"sub": "attacker"},
        headers={"EMBED_DEV_TOKEN_ENABLED": "true"},  # header, not the env — sanity
    )
    assert resp.status_code == 401, (
        f"SECURITY FAILURE: unauthenticated request accepted (status {resp.status_code})"
    )


@pytest.mark.asyncio
async def test_embed_token_invalid_bearer_rejected(client):
    """Garbage bearer token → 401."""
    resp = await client.post(
        "/api/v1/embed/embed-token",
        json={"sub": "attacker"},
        headers={"Authorization": "Bearer not.a.real.jwt"},
    )
    assert resp.status_code == 401, (
        f"SECURITY FAILURE: invalid bearer accepted (status {resp.status_code})"
    )


@pytest.mark.asyncio
async def test_embed_token_authenticated_enabled_succeeds(client):
    """Valid bearer + EMBED_DEV_TOKEN_ENABLED=true + ENV=test → 200."""
    token = _valid_access_token()
    with patch.dict(os.environ, {"EMBED_DEV_TOKEN_ENABLED": "true", "ENV": "test"}):
        from app.config import get_settings

        get_settings.cache_clear()
        try:
            resp = await client.post(
                "/api/v1/embed/embed-token",
                json={"sub": "dev-user"},
                headers={"Authorization": f"Bearer {token}"},
            )
        finally:
            get_settings.cache_clear()
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "token" in body
    assert "expires_in" in body


# ===========================================================================
# B. Forbidden scope prefix filtering
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_scope",
    [
        "write:*",
        "edit:dashboard",
        "admin:org",
        "delete:resource",
        "write:query",
    ],
)
async def test_embed_token_forbidden_scope_rejected(client, bad_scope):
    """Forbidden scope prefix → 400 regardless of other scopes."""
    token = _valid_access_token()
    with patch.dict(os.environ, {"EMBED_DEV_TOKEN_ENABLED": "true", "ENV": "test"}):
        from app.config import get_settings

        get_settings.cache_clear()
        try:
            resp = await client.post(
                "/api/v1/embed/embed-token",
                json={"sub": "dev-user", "scope": [bad_scope]},
                headers={"Authorization": f"Bearer {token}"},
            )
        finally:
            get_settings.cache_clear()
    assert resp.status_code == 400, (
        f"SECURITY FAILURE: forbidden scope '{bad_scope}' accepted "
        f"(status {resp.status_code})"
    )
    assert bad_scope in resp.json()["detail"]


@pytest.mark.asyncio
async def test_embed_token_mixed_scopes_rejected_on_any_forbidden(client):
    """A mix of allowed + forbidden scopes is still rejected."""
    token = _valid_access_token()
    with patch.dict(os.environ, {"EMBED_DEV_TOKEN_ENABLED": "true", "ENV": "test"}):
        from app.config import get_settings

        get_settings.cache_clear()
        try:
            resp = await client.post(
                "/api/v1/embed/embed-token",
                json={"sub": "dev-user", "scope": ["read:query", "admin:*"]},
                headers={"Authorization": f"Bearer {token}"},
            )
        finally:
            get_settings.cache_clear()
    assert resp.status_code == 400, (
        f"SECURITY FAILURE: mixed scopes with forbidden prefix accepted "
        f"(status {resp.status_code})"
    )


@pytest.mark.asyncio
async def test_embed_token_read_scope_accepted(client):
    """read:* scope is allowed — just a smoke-test to confirm the allowlist isn't over-broad."""
    token = _valid_access_token()
    with patch.dict(os.environ, {"EMBED_DEV_TOKEN_ENABLED": "true", "ENV": "test"}):
        from app.config import get_settings

        get_settings.cache_clear()
        try:
            resp = await client.post(
                "/api/v1/embed/embed-token",
                json={"sub": "dev-user", "scope": ["read:*"]},
                headers={"Authorization": f"Bearer {token}"},
            )
        finally:
            get_settings.cache_clear()
    assert resp.status_code == 200, f"read:* scope should be accepted, got {resp.status_code}"


# ===========================================================================
# C. ENV=production guard
# ===========================================================================


@pytest.mark.asyncio
async def test_embed_token_production_env_rejected(client):
    """ENV=production + EMBED_DEV_TOKEN_ENABLED=true → 503 (production guard)."""
    token = _valid_access_token()
    with patch.dict(os.environ, {"EMBED_DEV_TOKEN_ENABLED": "true", "ENV": "production"}):
        from app.config import get_settings

        get_settings.cache_clear()
        try:
            resp = await client.post(
                "/api/v1/embed/embed-token",
                json={"sub": "dev-user"},
                headers={"Authorization": f"Bearer {token}"},
            )
        finally:
            get_settings.cache_clear()
    assert resp.status_code == 503, (
        f"SECURITY FAILURE: production ENV guard bypassed (status {resp.status_code})"
    )
    assert "production" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_embed_token_production_env_disabled_also_rejected(client):
    """ENV=production + EMBED_DEV_TOKEN_ENABLED not set → also 503 (disabled check fires first)."""
    token = _valid_access_token()
    with patch.dict(os.environ, {"ENV": "production"}, clear=False):
        os.environ.pop("EMBED_DEV_TOKEN_ENABLED", None)
        from app.config import get_settings

        get_settings.cache_clear()
        try:
            resp = await client.post(
                "/api/v1/embed/embed-token",
                json={"sub": "dev-user"},
                headers={"Authorization": f"Bearer {token}"},
            )
        finally:
            get_settings.cache_clear()
    assert resp.status_code == 503
