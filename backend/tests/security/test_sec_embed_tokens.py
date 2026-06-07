"""Attack class 1: Embed token forgery / tampering.

Covers
------
1a. Forged HMAC (wrong signing key) → 401
1b. Expired token → 401
1c. alg:none → 401
1d. HS256 claiming an embed iss (HS↔RS confusion) → 401
1e. RS256 signed with a *different* RSA key (forged signature) → 401
1f. Tampered org claim (base64 edited payload) → 401
1g. Tampered sub claim → 401
1h. embed-kind token supplying raw sql (no query_id) → 403
1i. embed-kind token: scope gate denies out-of-scope query_id
    (query with required_scope the token does NOT carry) → 403
1j. Missing required claims (no sub) → 401
1k. Token with empty alg string → 401
"""

from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
import jwt as pyjwt

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
    mint_embed_token,
    mint_access_token,
    STATIC_JWKS,
    HOST_ISS,
    HOST_AUD,
    EMBED_ORIGIN,
    KID,
    _PRIVATE_KEY,
    _ATTACKER_PRIVATE_KEY,
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


@pytest_asyncio.fixture
async def app():
    patches = [
        patch("app.db.fetchrow", new=AsyncMock(return_value=None)),
        patch("app.db.fetch", new=AsyncMock(return_value=[])),
        patch("app.db.execute", new=AsyncMock(return_value="OK")),
        patch("app.db.init_db", new=AsyncMock()),
        patch("app.db.close_db", new=AsyncMock()),
        patch("app.auth.deps.fetchrow", new=AsyncMock(return_value=None)),
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    # Re-pad
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def _tamper_payload(token: str, patch_fn) -> str:
    """Decode the middle segment of a JWT, run patch_fn on the claims dict,
    re-encode, and return a token with the *original* signature intact.
    The signature will no longer match — that's the point."""
    parts = token.split(".")
    assert len(parts) == 3, "Expected 3-part JWT"
    header, payload_b64, signature = parts
    payload_dict = json.loads(_b64url_decode(payload_b64))
    patch_fn(payload_dict)
    new_payload_b64 = _b64url_encode(json.dumps(payload_dict, separators=(",", ":")).encode())
    return f"{header}.{new_payload_b64}.{signature}"


# ===========================================================================
# 1a. Wrong signing key → 401 (forged signature)
# ===========================================================================

@pytest.mark.asyncio
async def test_embed_wrong_key_rejected(client):
    """Token signed with attacker's private key → 401 (signature mismatch)."""
    # The issuer registry has our test key; sign with a different (attacker) key.
    token = mint_embed_token(private_key=_ATTACKER_PRIVATE_KEY)
    resp = await client.post(
        "/api/v1/query",
        json={"query_id": "demo_all"},
        headers={"Authorization": f"Bearer {token}", "Origin": EMBED_ORIGIN},
    )
    assert resp.status_code == 401, (
        f"SECURITY FAILURE: forged-key embed token accepted (status {resp.status_code})"
    )


# ===========================================================================
# 1b. Expired token → 401
# ===========================================================================

@pytest.mark.asyncio
async def test_embed_expired_token_rejected(client):
    """Token with exp in the past → 401."""
    token = mint_embed_token(exp_delta=-60)  # expired 60 seconds ago
    resp = await client.post(
        "/api/v1/query",
        json={"query_id": "demo_all"},
        headers={"Authorization": f"Bearer {token}", "Origin": EMBED_ORIGIN},
    )
    assert resp.status_code == 401, (
        f"SECURITY FAILURE: expired embed token accepted (status {resp.status_code})"
    )


# ===========================================================================
# 1c. alg:none → 401
# ===========================================================================

@pytest.mark.asyncio
async def test_embed_alg_none_rejected(client):
    """Token with alg='none' must be rejected immediately."""
    now = datetime.now(tz=timezone.utc)
    header = _b64url_encode(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    payload_dict = {
        "iss": HOST_ISS,
        "aud": HOST_AUD,
        "sub": "attacker",
        "exp": int((now + timedelta(seconds=300)).timestamp()),
        "iat": int(now.timestamp()),
        "scope": ["read:query"],
        "policies": {},
    }
    payload = _b64url_encode(json.dumps(payload_dict).encode())
    token = f"{header}.{payload}."  # empty signature

    resp = await client.post(
        "/api/v1/query",
        json={"query_id": "demo_all"},
        headers={"Authorization": f"Bearer {token}", "Origin": EMBED_ORIGIN},
    )
    assert resp.status_code == 401, (
        f"SECURITY FAILURE: alg:none token accepted (status {resp.status_code})"
    )


# ===========================================================================
# 1c-variant. alg:NONE (uppercase) → 401
# ===========================================================================

@pytest.mark.asyncio
async def test_embed_alg_none_uppercase_rejected(client):
    """Token with alg='NONE' must also be rejected."""
    now = datetime.now(tz=timezone.utc)
    header = _b64url_encode(json.dumps({"alg": "NONE", "typ": "JWT"}).encode())
    payload_dict = {
        "iss": HOST_ISS,
        "aud": HOST_AUD,
        "sub": "attacker",
        "exp": int((now + timedelta(seconds=300)).timestamp()),
        "iat": int(now.timestamp()),
    }
    payload = _b64url_encode(json.dumps(payload_dict).encode())
    token = f"{header}.{payload}."

    resp = await client.post(
        "/api/v1/query",
        json={"query_id": "demo_all"},
        headers={"Authorization": f"Bearer {token}", "Origin": EMBED_ORIGIN},
    )
    assert resp.status_code == 401, (
        f"SECURITY FAILURE: alg:NONE token accepted (status {resp.status_code})"
    )


# ===========================================================================
# 1d. HS256 token claiming an embed iss (algorithm confusion) → 401
# ===========================================================================

@pytest.mark.asyncio
async def test_embed_hs256_alg_confusion_rejected(client):
    """HS256 token that claims the registered embed issuer → 401.

    An attacker who knows the JWKS public key can't use it as an HMAC secret
    to forge HS256 tokens.  The verifier must reject the alg mismatch.
    """
    from app.config import get_settings
    # Try signing with the test JWT_SECRET (first-party secret).
    settings = get_settings()
    now = datetime.now(tz=timezone.utc)
    payload = {
        "iss": HOST_ISS,  # claims to be an embed issuer
        "aud": HOST_AUD,
        "sub": "attacker",
        "exp": int((now + timedelta(seconds=300)).timestamp()),
        "iat": int(now.timestamp()),
        "typ": "access",
        "scope": ["read:query"],
        "policies": {},
    }
    # Signed with HS256 — the verifier must NOT treat this as a first-party
    # access token (because typ and iss don't match) AND must NOT allow it
    # through the embed path (because alg is HS256, not RS256/ES256).
    token = pyjwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")

    resp = await client.post(
        "/api/v1/query",
        json={"query_id": "demo_all"},
        headers={"Authorization": f"Bearer {token}", "Origin": EMBED_ORIGIN},
    )
    assert resp.status_code == 401, (
        f"SECURITY FAILURE: HS256/embed-iss confusion token accepted "
        f"(status {resp.status_code})"
    )


# ===========================================================================
# 1e. RS256 signed with a different key (forged) — unit-level
# ===========================================================================

def test_embed_forged_signature_raises_401():
    """verify_token rejects a token signed by an unknown key."""
    from app.auth.verify import verify_token
    from app.errors import AppError

    token = mint_embed_token(private_key=_ATTACKER_PRIVATE_KEY)
    with pytest.raises(AppError) as exc_info:
        verify_token(token, expected_origin=None)
    assert exc_info.value.status == 401


# ===========================================================================
# 1f. Tampered org claim (payload edited, signature kept) → 401
# ===========================================================================

def test_embed_tampered_org_rejected():
    """Changing org claim without re-signing → signature fails → 401."""
    from app.auth.verify import verify_token
    from app.errors import AppError

    token = mint_embed_token(org="acme-org")

    def _change_org(claims):
        claims["org"] = "evil-org"

    tampered = _tamper_payload(token, _change_org)

    with pytest.raises(AppError) as exc_info:
        verify_token(tampered, expected_origin=None)
    assert exc_info.value.status == 401, (
        "SECURITY FAILURE: tampered org claim accepted"
    )


# ===========================================================================
# 1g. Tampered sub claim → 401
# ===========================================================================

def test_embed_tampered_sub_rejected():
    """Changing sub claim without re-signing → signature fails → 401."""
    from app.auth.verify import verify_token
    from app.errors import AppError

    token = mint_embed_token()

    def _change_sub(claims):
        claims["sub"] = "admin-user"

    tampered = _tamper_payload(token, _change_sub)

    with pytest.raises(AppError) as exc_info:
        verify_token(tampered, expected_origin=None)
    assert exc_info.value.status == 401, (
        "SECURITY FAILURE: tampered sub accepted"
    )


# ===========================================================================
# 1h. embed-kind token with raw sql (no query_id) → 403
# ===========================================================================

@pytest.mark.asyncio
async def test_embed_raw_sql_no_query_id_rejected(client):
    """embed token supplying raw sql without a query_id → 403 query_not_registered."""
    token = mint_embed_token(scope=["read:query"])
    resp = await client.post(
        "/api/v1/query",
        json={"sql": "SELECT * FROM demo"},
        headers={"Authorization": f"Bearer {token}", "Origin": EMBED_ORIGIN},
    )
    assert resp.status_code == 403, (
        f"SECURITY FAILURE: embed raw SQL accepted (status {resp.status_code})"
    )
    body = resp.json()
    assert body["error"]["code"] == "query_not_registered"


@pytest.mark.asyncio
async def test_embed_raw_sql_with_read_star_also_rejected(client):
    """Even read:* embed token cannot execute arbitrary raw sql."""
    token = mint_embed_token(scope=["read:*"])
    resp = await client.post(
        "/api/v1/query",
        json={"sql": "SELECT * FROM demo"},
        headers={"Authorization": f"Bearer {token}", "Origin": EMBED_ORIGIN},
    )
    assert resp.status_code == 403, (
        f"SECURITY FAILURE: embed raw SQL with read:* accepted "
        f"(status {resp.status_code})"
    )


# ===========================================================================
# 1i. Scope gate: query with required_scope the token doesn't carry → 403
# ===========================================================================

@pytest.mark.asyncio
async def test_embed_required_scope_enforced(client):
    """A registered query with required_scope='read:secret' is inaccessible
    to a token that only carries 'read:query'."""
    from app.queries.registry import get_query_registry

    # Register a protected query.
    reg = get_query_registry()
    reg.register(
        id="sec_test_protected",
        sql="SELECT * FROM demo",
        name="Protected query",
        required_scope="read:secret",
    )

    token = mint_embed_token(scope=["read:query"])  # does NOT have read:secret
    resp = await client.post(
        "/api/v1/query",
        json={"query_id": "sec_test_protected"},
        headers={"Authorization": f"Bearer {token}", "Origin": EMBED_ORIGIN},
    )
    # Clean up
    reg.unregister("sec_test_protected")

    assert resp.status_code == 403, (
        f"SECURITY FAILURE: required_scope not enforced "
        f"(status {resp.status_code})"
    )
    body = resp.json()
    assert body["error"]["code"] == "insufficient_scope"


@pytest.mark.asyncio
async def test_embed_required_scope_passes_when_token_has_it(client):
    """Same query but token carries the required scope → 200."""
    from app.queries.registry import get_query_registry

    reg = get_query_registry()
    reg.register(
        id="sec_test_protected2",
        sql="SELECT * FROM demo",
        name="Protected query 2",
        required_scope="read:secret",
    )

    token = mint_embed_token(scope=["read:query", "read:secret"])
    resp = await client.post(
        "/api/v1/query",
        json={"query_id": "sec_test_protected2"},
        headers={"Authorization": f"Bearer {token}", "Origin": EMBED_ORIGIN},
    )
    reg.unregister("sec_test_protected2")

    assert resp.status_code == 200, (
        f"Token with required scope still denied (status {resp.status_code})"
    )


# ===========================================================================
# 1j. Missing required claims (no sub) → 401
# ===========================================================================

def test_embed_missing_sub_rejected():
    """Token without a sub claim is rejected."""
    from app.auth.verify import verify_token
    from app.errors import AppError

    now = datetime.now(tz=timezone.utc)
    payload = {
        "iss": HOST_ISS,
        "aud": HOST_AUD,
        # "sub" intentionally omitted
        "exp": int((now + timedelta(seconds=300)).timestamp()),
        "iat": int(now.timestamp()),
        "scope": ["read:query"],
        "policies": {},
    }
    token = pyjwt.encode(payload, _PRIVATE_KEY, algorithm="RS256", headers={"kid": KID})

    with pytest.raises(AppError) as exc_info:
        verify_token(token, expected_origin=None)
    assert exc_info.value.status == 401


# ===========================================================================
# 1k. Token with empty alg → 401
# ===========================================================================

def test_token_empty_alg_rejected():
    """A JWT whose header declares alg='' must be rejected."""
    from app.auth.verify import verify_token
    from app.errors import AppError

    now = datetime.now(tz=timezone.utc)
    header = _b64url_encode(json.dumps({"alg": "", "typ": "JWT"}).encode())
    payload_dict = {
        "iss": HOST_ISS,
        "aud": HOST_AUD,
        "sub": "attacker",
        "exp": int((now + timedelta(seconds=300)).timestamp()),
    }
    payload = _b64url_encode(json.dumps(payload_dict).encode())
    token = f"{header}.{payload}.invalidsig"

    with pytest.raises(AppError) as exc_info:
        verify_token(token, expected_origin=None)
    assert exc_info.value.status == 401
