"""Tests for GET /embed/config/{id} — reads real boards resource (PROD).

Coverage
--------
1.  Embed token with valid board id → 200 with the board's descriptor.
2.  Embed token with unknown board id → 404 dashboard_not_found.
3.  No token → 401 unauthorized.
4.  First-party token with valid board → 200 descriptor.
5.  First-party token with unknown board → 404.
6.  Embed token with no read scope → 403 insufficient_scope.
7.  Board with a spec (EDITOR format) → descriptor includes spec + widgets.
8.  Board with html (M8 format) → descriptor includes html.
9.  Board with no widgets/spec/html → graceful fallback (widgets=[]).
10. First-party token via get_user_org (InMemoryRepo org membership).

Strategy
--------
- Use InMemoryRepo seeded via set_repo() so no live DB is needed.
- For embed tokens: use the same RSA keypair + issuer-registry pattern as
  test_embed_rls.py (avoids duplication, proves the pattern works here too).
- For first-party tokens: mint_access_token + seed org membership in repo.
- The conftest `app` fixture patches app.db.* with FakeDB (no Neon required).
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Environment bootstrap (guards for standalone runs; conftest.py sets these).
# ---------------------------------------------------------------------------

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef")
os.environ.setdefault("JWT_ACCESS_TTL_MIN", "15")
os.environ.setdefault("GOOGLE_CLIENT_ID", "fake-gid")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "fake-gsecret")
os.environ.setdefault(
    "GOOGLE_REDIRECT_URI",
    "http://localhost:8000/api/v1/auth/google/callback",
)
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("ENV", "test")

# ---------------------------------------------------------------------------
# RSA keypair (module-level, generated once — same pattern as test_embed_rls.py)
# ---------------------------------------------------------------------------

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
from jwt.algorithms import RSAAlgorithm
import jwt as pyjwt

_PRIVATE_KEY = rsa.generate_private_key(
    public_exponent=65537,
    key_size=2048,
    backend=default_backend(),
)
_PUBLIC_KEY = _PRIVATE_KEY.public_key()
_JWKS_KEY: dict = json.loads(RSAAlgorithm.to_jwk(_PUBLIC_KEY))
_JWKS_KEY["kid"] = "embed-cfg-test-key"
_JWKS_KEY["use"] = "sig"
_STATIC_JWKS: dict = {"keys": [_JWKS_KEY]}

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_HOST_ISS = "https://cfg-embed-host.example"
_HOST_AUD = "nubi"
_EMBED_ORIGIN = "https://cfg-embed-host.example"
_KID = "embed-cfg-test-key"
_EMBED_ORG = "embed-org-cfg"   # org claim in embed tokens


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------


def _mint_embed_token(
    *,
    org: str = _EMBED_ORG,
    scope: list[str] | None = None,
    embed_origin: str | None = _EMBED_ORIGIN,
    exp_delta: int = 300,
) -> str:
    """Mint a test embed JWT signed with the test RSA private key."""
    if scope is None:
        scope = ["read:query"]
    now = datetime.now(tz=timezone.utc)
    payload: dict = {
        "iss": _HOST_ISS,
        "aud": _HOST_AUD,
        "sub": "embed-cfg-user",
        "org": org,
        "roles": ["viewer"],
        "policies": {},
        "scope": scope,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=exp_delta)).timestamp()),
    }
    if embed_origin is not None:
        payload["embed_origin"] = embed_origin
    return pyjwt.encode(payload, _PRIVATE_KEY, algorithm="RS256", headers={"kid": _KID})


def _mint_first_party_token(user_id: str) -> str:
    from app.auth.jwt import mint_access_token
    return mint_access_token(user_id)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _register_embed_issuer():
    """Register the test embed issuer for this module and clean up."""
    from app.auth.issuers import get_issuer_registry
    from app.auth.jwks_cache import clear_cache
    from app.config import get_settings

    get_settings.cache_clear()
    registry = get_issuer_registry()
    registry.register(
        _HOST_ISS,
        jwks_uri=f"{_HOST_ISS}/.well-known/jwks.json",
        aud=_HOST_AUD,
        allowed_origins=[_EMBED_ORIGIN],
        static_jwks=_STATIC_JWKS,
    )
    yield
    registry.unregister(_HOST_ISS)
    clear_cache()
    get_settings.cache_clear()


def _seed_board(
    repo,
    board_id: str,
    org_id: str,
    config: dict[str, Any] | None = None,
    name: str = "My Board",
) -> dict[str, Any]:
    """Directly insert a board row into the InMemoryRepo store."""
    row: dict[str, Any] = {
        "id": board_id,
        "org_id": org_id,
        "created_by": "system",
        "name": name,
        "config": config or {},
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
    }
    repo._store["boards"][board_id] = row
    return row


@pytest_asyncio.fixture
async def embed_client(app, fake_db):
    """Async HTTPX client with an InMemoryRepo injected for embed config tests."""
    from app.repos.memory import InMemoryRepo
    from app.repos.provider import set_repo

    repo = InMemoryRepo()
    set_repo(repo)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as ac:
        yield ac, repo

    set_repo(None)


# ---------------------------------------------------------------------------
# 1. Embed token + existing board → 200 descriptor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_config_found_returns_descriptor(embed_client):
    """GET /embed/config/{id} with a valid board returns a descriptor."""
    ac, repo = embed_client
    board_id = str(uuid.uuid4())
    _seed_board(
        repo,
        board_id=board_id,
        org_id=_EMBED_ORG,
        name="Sales Dashboard",
        config={
            "widgets": [{"id": "w1", "type": "table", "query_id": "demo_all"}],
            "theme": {"accent": "#ff0000"},
        },
    )

    token = _mint_embed_token()
    resp = await ac.get(
        f"/api/v1/embed/config/{board_id}",
        headers={
            "Authorization": f"Bearer {token}",
            "Origin": _EMBED_ORIGIN,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dashboard_id"] == board_id
    assert body["title"] == "Sales Dashboard"
    assert isinstance(body["widgets"], list)
    assert len(body["widgets"]) == 1
    assert body["widgets"][0]["type"] == "table"
    assert body["theme"] == {"accent": "#ff0000"}


# ---------------------------------------------------------------------------
# 2. Embed token + unknown board → 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_config_unknown_id_returns_404(embed_client):
    """GET /embed/config/{id} with an id that doesn't exist → 404."""
    ac, repo = embed_client
    # No board seeded.

    token = _mint_embed_token()
    resp = await ac.get(
        "/api/v1/embed/config/nonexistent-board",
        headers={
            "Authorization": f"Bearer {token}",
            "Origin": _EMBED_ORIGIN,
        },
    )
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["error"]["code"] == "dashboard_not_found"


# ---------------------------------------------------------------------------
# 3. No token → 401
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_config_no_token_returns_401(embed_client):
    """GET /embed/config/{id} without Authorization header → 401."""
    ac, repo = embed_client
    resp = await ac.get("/api/v1/embed/config/any-id")
    assert resp.status_code == 401
    body = resp.json()
    assert body["error"]["code"] == "unauthorized"


# ---------------------------------------------------------------------------
# 4. First-party token + existing board → 200
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_party_token_with_board_returns_descriptor(embed_client, fake_db):
    """First-party access token with org membership → 200 descriptor."""
    ac, repo = embed_client

    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    board_id = str(uuid.uuid4())

    # Seed user in FakeDB (so current_user + verified_identity resolve it).
    fake_db.users[user_id] = {
        "id": user_id,
        "email": "fp@example.com",
        "name": "FP User",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    # Seed org membership in repo (so get_user_org works).
    repo.seed_org_member(org_id=org_id, user_id=user_id)
    # Seed board in repo.
    _seed_board(
        repo,
        board_id=board_id,
        org_id=org_id,
        name="FP Board",
        config={"widgets": [{"id": "w9", "type": "kpi", "query_id": "q1"}]},
    )

    token = _mint_first_party_token(user_id)
    resp = await ac.get(
        f"/api/v1/embed/config/{board_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dashboard_id"] == board_id
    assert body["title"] == "FP Board"
    assert body["widgets"][0]["type"] == "kpi"


# ---------------------------------------------------------------------------
# 5. First-party token + unknown board → 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_party_token_unknown_board_returns_404(embed_client, fake_db):
    """First-party token with valid org but missing board → 404."""
    ac, repo = embed_client

    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())

    fake_db.users[user_id] = {
        "id": user_id,
        "email": "fp2@example.com",
        "name": "FP2",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    repo.seed_org_member(org_id=org_id, user_id=user_id)
    # No board seeded.

    token = _mint_first_party_token(user_id)
    resp = await ac.get(
        f"/api/v1/embed/config/{str(uuid.uuid4())}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "dashboard_not_found"


# ---------------------------------------------------------------------------
# 6. Embed token with no read scope → 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_token_no_read_scope_returns_403(embed_client):
    """Embed token without read scope → 403 insufficient_scope."""
    ac, repo = embed_client
    token = _mint_embed_token(scope=["edit:boards"])
    resp = await ac.get(
        "/api/v1/embed/config/any-id",
        headers={
            "Authorization": f"Bearer {token}",
            "Origin": _EMBED_ORIGIN,
        },
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "insufficient_scope"


# ---------------------------------------------------------------------------
# 7. Board with spec (EDITOR format) → descriptor includes spec + widgets
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_board_with_spec_descriptor_includes_spec(embed_client):
    """Board with config.spec → descriptor exposes spec and widgets."""
    ac, repo = embed_client
    board_id = str(uuid.uuid4())
    spec = {
        "version": 1,
        "title": "Spec Board",
        "layout": {"cols": 12, "rowHeight": 60},
        "widgets": [{"id": "sw1", "type": "chart", "query_id": "q2"}],
    }
    _seed_board(
        repo,
        board_id=board_id,
        org_id=_EMBED_ORG,
        name="Spec Board",
        config={"spec": spec},
    )

    token = _mint_embed_token()
    resp = await ac.get(
        f"/api/v1/embed/config/{board_id}",
        headers={"Authorization": f"Bearer {token}", "Origin": _EMBED_ORIGIN},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "spec" in body
    assert body["spec"]["version"] == 1
    assert isinstance(body["widgets"], list)
    assert body["widgets"][0]["type"] == "chart"


# ---------------------------------------------------------------------------
# 8. Board with html (M8 format) → descriptor includes html
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_board_with_html_descriptor_includes_html(embed_client):
    """Board with config.html → descriptor exposes html field."""
    ac, repo = embed_client
    board_id = str(uuid.uuid4())
    html_content = "<div><nubi-kpi query-id='q1' value-col='revenue'></nubi-kpi></div>"
    _seed_board(
        repo,
        board_id=board_id,
        org_id=_EMBED_ORG,
        name="HTML Board",
        config={"html": html_content},
    )

    token = _mint_embed_token()
    resp = await ac.get(
        f"/api/v1/embed/config/{board_id}",
        headers={"Authorization": f"Bearer {token}", "Origin": _EMBED_ORIGIN},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["html"] == html_content
    assert "widgets" in body  # graceful fallback — always present


# ---------------------------------------------------------------------------
# 9. Board with empty config → graceful fallback (widgets=[])
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_board_with_empty_config_returns_minimal_descriptor(embed_client):
    """Board with no widgets/spec/html → graceful fallback with widgets=[]."""
    ac, repo = embed_client
    board_id = str(uuid.uuid4())
    _seed_board(
        repo,
        board_id=board_id,
        org_id=_EMBED_ORG,
        name="Empty Board",
        config={},  # no widgets, no spec, no html
    )

    token = _mint_embed_token()
    resp = await ac.get(
        f"/api/v1/embed/config/{board_id}",
        headers={"Authorization": f"Bearer {token}", "Origin": _EMBED_ORIGIN},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dashboard_id"] == board_id
    assert body["widgets"] == []


# ---------------------------------------------------------------------------
# 10. Cross-org: embed token for wrong org → 404 (not the other org's board)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_token_cross_org_board_returns_404(embed_client):
    """Embed token for org_A cannot access a board belonging to org_B → 404."""
    ac, repo = embed_client
    board_id = str(uuid.uuid4())
    other_org = "other-org-xyz"
    _seed_board(
        repo,
        board_id=board_id,
        org_id=other_org,  # different org from the token's org claim
        name="Other Org Board",
        config={"widgets": []},
    )

    token = _mint_embed_token(org=_EMBED_ORG)  # token claims _EMBED_ORG, board is other_org
    resp = await ac.get(
        f"/api/v1/embed/config/{board_id}",
        headers={"Authorization": f"Bearer {token}", "Origin": _EMBED_ORIGIN},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "dashboard_not_found"
