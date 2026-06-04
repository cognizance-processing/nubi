"""Tests for the unified token verifier (M3-A).

Security properties verified by this suite
-------------------------------------------
1.  Valid embed RS256 JWT  → VerifiedIdentity(kind='embed') with correct claims.
2.  Expired embed token    → AppError 401.
3.  Wrong audience         → AppError 401.
4.  Unregistered issuer    → AppError 401.
5.  alg 'none'             → AppError 401 (always blocked).
6.  HS256 claiming embed iss (alg-confusion) → AppError 401.
7.  Origin mismatch        → AppError 403.
8.  Missing scope          → AppError 403 (via require_scope).
9.  Valid first-party HS256 access token → VerifiedIdentity(kind='access').
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

import pytest

# ---------------------------------------------------------------------------
# Environment bootstrap (must happen before any app import)
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
# Generate an RSA keypair for the "host" issuer (done once at module import).
# ---------------------------------------------------------------------------

from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.backends import default_backend

import jwt as pyjwt
from jwt.algorithms import RSAAlgorithm
import json

# Generate a fresh 2048-bit RSA keypair.
_PRIVATE_KEY = rsa.generate_private_key(
    public_exponent=65537,
    key_size=2048,
    backend=default_backend(),
)
_PUBLIC_KEY = _PRIVATE_KEY.public_key()

# Serialise the public key to PEM for use in verify.py static_public_key path.
_PUBLIC_KEY_PEM: str = _PUBLIC_KEY.public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
).decode()

# Build a minimal JWKS from the public key using PyJWT's RSAAlgorithm helper.
_JWKS: dict = json.loads(RSAAlgorithm.to_jwk(_PUBLIC_KEY))
_JWKS["kid"] = "test-key-1"
_JWKS["use"] = "sig"
_STATIC_JWKS: dict = {"keys": [_JWKS]}

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_HOST_ISS = "https://host.example"
_HOST_AUD = "nubi"
_HOST_ORIGIN = "https://host.example"
_EMBED_ORIGIN = "https://host.example"
_KID = "test-key-1"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mint_embed_jwt(
    *,
    iss: str = _HOST_ISS,
    aud: str = _HOST_AUD,
    sub: str = "user-abc",
    exp_delta: int = 300,  # seconds from now (negative = expired)
    scope: list[str] | None = None,
    policies: dict | None = None,
    embed_origin: str | None = _EMBED_ORIGIN,
    alg: str = "RS256",
    private_key=None,
) -> str:
    """Mint a test embed JWT signed with the test RSA private key."""
    if scope is None:
        scope = ["read:dashboard:abc"]
    if policies is None:
        policies = {"tenant_id": "acme"}
    if private_key is None:
        private_key = _PRIVATE_KEY

    now = datetime.now(tz=timezone.utc)
    payload: dict = {
        "iss": iss,
        "aud": aud,
        "sub": sub,
        "org": "acme-org",
        "project": "acme-project",
        "roles": ["viewer"],
        "policies": policies,
        "scope": scope,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=exp_delta)).timestamp()),
    }
    if embed_origin is not None:
        payload["embed_origin"] = embed_origin

    headers = {"kid": _KID}
    return pyjwt.encode(payload, private_key, algorithm=alg, headers=headers)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _register_test_issuer():
    """Register the test RSA issuer and clean up after each test."""
    from app.auth.issuers import get_issuer_registry
    from app.auth.jwks_cache import clear_cache
    from app.config import get_settings

    get_settings.cache_clear()
    registry = get_issuer_registry()
    registry.register(
        _HOST_ISS,
        jwks_uri=f"{_HOST_ISS}/.well-known/jwks.json",
        aud=_HOST_AUD,
        allowed_origins=[_HOST_ORIGIN],
        static_jwks=_STATIC_JWKS,
    )
    yield
    registry.unregister(_HOST_ISS)
    clear_cache()
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Tests — embed path (RS256)
# ---------------------------------------------------------------------------


def test_valid_embed_token_returns_correct_identity():
    """A well-formed RS256 embed JWT is accepted and mapped to VerifiedIdentity."""
    from app.auth.verify import verify_token

    token = _mint_embed_jwt()
    identity = verify_token(token, expected_origin=_EMBED_ORIGIN)

    assert identity.kind == "embed"
    assert identity.user_id == "user-abc"
    assert identity.org == "acme-org"
    assert identity.project == "acme-project"
    assert identity.roles == ["viewer"]
    assert identity.policies == {"tenant_id": "acme"}
    assert "read:dashboard:abc" in identity.scope
    assert identity.embed_origin == _EMBED_ORIGIN


def test_expired_embed_token_raises_401():
    """An expired embed token must raise AppError(401)."""
    from app.auth.verify import verify_token
    from app.errors import AppError

    token = _mint_embed_jwt(exp_delta=-1)  # already expired
    with pytest.raises(AppError) as exc_info:
        verify_token(token)
    assert exc_info.value.status == 401


def test_wrong_audience_raises_401():
    """A token whose aud does not match the registered issuer's aud → 401."""
    from app.auth.verify import verify_token
    from app.errors import AppError

    token = _mint_embed_jwt(aud="wrong-audience")
    with pytest.raises(AppError) as exc_info:
        verify_token(token)
    assert exc_info.value.status == 401


def test_unregistered_issuer_raises_401():
    """A token from an unknown iss is rejected without leaking information."""
    from app.auth.verify import verify_token
    from app.errors import AppError

    token = _mint_embed_jwt(iss="https://evil.example")
    with pytest.raises(AppError) as exc_info:
        verify_token(token)
    assert exc_info.value.status == 401


def test_alg_none_raises_401():
    """A token with alg='none' must always be rejected."""
    from app.auth.verify import verify_token
    from app.errors import AppError

    # Craft a token with alg=none manually.
    # pyjwt won't sign with none; build the parts directly.
    import base64

    def _b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    header = _b64url(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    payload_dict = {
        "iss": _HOST_ISS,
        "aud": _HOST_AUD,
        "sub": "user-abc",
        "exp": int((datetime.now(tz=timezone.utc) + timedelta(seconds=300)).timestamp()),
    }
    payload = _b64url(json.dumps(payload_dict).encode())
    token = f"{header}.{payload}."  # empty signature

    with pytest.raises(AppError) as exc_info:
        verify_token(token)
    assert exc_info.value.status == 401


def test_hs256_claiming_embed_iss_raises_401():
    """HS256 token that claims an embed iss is rejected (alg-confusion attack)."""
    from app.auth.verify import verify_token
    from app.errors import AppError

    # Mint an HS256 token pretending to come from the registered embed issuer.
    # Even though the iss is registered, the alg ≠ RS256 → embed path should reject it.
    now = datetime.now(tz=timezone.utc)
    payload = {
        "iss": _HOST_ISS,
        "aud": _HOST_AUD,
        "sub": "attacker",
        "exp": int((now + timedelta(seconds=300)).timestamp()),
        "iat": int(now.timestamp()),
        "typ": "access",  # not a valid access token either
    }
    # Use a *different* HS256 secret so it won't pass first-party decode
    # AND has the wrong alg for the embed path.
    different_secret = "different-secret-that-is-at-least-32-bytes-long!"
    token = pyjwt.encode(payload, different_secret, algorithm="HS256")

    with pytest.raises(AppError) as exc_info:
        verify_token(token)
    # Should be 401 — alg is HS256 but it doesn't have a valid 'typ': access
    # and doesn't match JWT_SECRET from settings.
    assert exc_info.value.status == 401


def test_origin_mismatch_raises_403():
    """When embed_origin is in the token but doesn't match expected_origin → 403."""
    from app.auth.verify import verify_token
    from app.errors import AppError

    token = _mint_embed_jwt(embed_origin="https://host.example")
    with pytest.raises(AppError) as exc_info:
        verify_token(token, expected_origin="https://evil.example")
    assert exc_info.value.status == 403
    assert exc_info.value.code == "origin_mismatch"


def test_missing_scope_via_require_scope_raises_403():
    """require_scope() raises 403 when the required scope is absent."""
    from app.auth.scopes import require_scope
    from app.errors import AppError

    claims = {"scope": ["read:dashboard:abc"]}

    # This scope IS present (should not raise)
    require_scope(claims, "read:dashboard:abc")

    # This scope is NOT present
    with pytest.raises(AppError) as exc_info:
        require_scope(claims, "edit:dashboard:abc")
    assert exc_info.value.status == 403
    assert exc_info.value.code == "insufficient_scope"


# ---------------------------------------------------------------------------
# Tests — first-party HS256 path
# ---------------------------------------------------------------------------


def test_valid_first_party_access_token_verifies_as_access():
    """A Nubi-minted HS256 access token returns VerifiedIdentity(kind='access')."""
    from app.auth.jwt import mint_access_token
    from app.auth.verify import verify_token

    token = mint_access_token("user-123")
    identity = verify_token(token)

    assert identity.kind == "access"
    assert identity.user_id == "user-123"
    assert "read:*" in identity.scope
    assert "edit:*" in identity.scope
    assert identity.embed_origin is None


# ---------------------------------------------------------------------------
# Tests — scope helpers
# ---------------------------------------------------------------------------


def test_parse_scopes_list():
    """parse_scopes handles a list value in the claims."""
    from app.auth.scopes import parse_scopes

    claims = {"scope": ["read:dashboard:abc", "read:widget:xyz"]}
    assert parse_scopes(claims) == ["read:dashboard:abc", "read:widget:xyz"]


def test_parse_scopes_space_delimited():
    """parse_scopes handles a space-delimited string (RFC 6749)."""
    from app.auth.scopes import parse_scopes

    claims = {"scope": "read:dashboard:abc edit:widget"}
    assert parse_scopes(claims) == ["read:dashboard:abc", "edit:widget"]


def test_parse_scopes_missing_key():
    """parse_scopes returns [] when no scope claim is present."""
    from app.auth.scopes import parse_scopes

    assert parse_scopes({}) == []


def test_has_scope_exact_match():
    from app.auth.scopes import has_scope

    assert has_scope(["read:dashboard:abc"], "read:dashboard:abc") is True


def test_has_scope_trailing_wildcard():
    from app.auth.scopes import has_scope

    assert has_scope(["read:dashboard:*"], "read:dashboard:xyz") is True
    assert has_scope(["read:dashboard:*"], "read:dashboard:abc") is True
    assert has_scope(["read:dashboard:*"], "edit:dashboard:abc") is False


def test_has_scope_read_star():
    from app.auth.scopes import has_scope

    assert has_scope(["read:*"], "read:dashboard:xyz") is True
    assert has_scope(["read:*"], "read:anything") is True
    assert has_scope(["read:*"], "edit:something") is False


def test_has_scope_super_wildcard():
    from app.auth.scopes import has_scope

    assert has_scope(["*"], "read:dashboard:xyz") is True
    assert has_scope(["*"], "edit:everything") is True


def test_has_scope_no_match():
    from app.auth.scopes import has_scope

    assert has_scope(["read:dashboard:abc"], "read:widget:xyz") is False
    assert has_scope([], "read:anything") is False


# ---------------------------------------------------------------------------
# Tests — issuer registry
# ---------------------------------------------------------------------------


def test_get_unregistered_issuer_returns_none():
    from app.auth.issuers import get_issuer_registry

    registry = get_issuer_registry()
    assert registry.get("https://never-registered.example") is None


def test_register_and_get_roundtrip():
    from app.auth.issuers import get_issuer_registry

    registry = get_issuer_registry()
    registry.register(
        "https://extra.example",
        jwks_uri="https://extra.example/.well-known/jwks.json",
        aud="nubi-extra",
        allowed_origins=["https://extra.example"],
    )
    cfg = registry.get("https://extra.example")
    assert cfg is not None
    assert cfg.aud == "nubi-extra"
    registry.unregister("https://extra.example")
    assert registry.get("https://extra.example") is None


# ---------------------------------------------------------------------------
# Tests — verify_token with no origin check (embed_origin absent in token)
# ---------------------------------------------------------------------------


def test_embed_token_without_embed_origin_skips_origin_check():
    """If the token has no embed_origin claim, origin check is skipped."""
    from app.auth.verify import verify_token

    token = _mint_embed_jwt(embed_origin=None)
    # Providing expected_origin should NOT cause a rejection since
    # there is nothing to compare against.
    identity = verify_token(token, expected_origin="https://any.example")
    assert identity.kind == "embed"
    assert identity.embed_origin is None


def test_embed_token_origin_present_but_no_expected_origin_raises_403():
    """SECURITY: embed_origin in token + missing request Origin → 403 origin_mismatch.

    A missing Origin header (server-to-server, CLI) must NOT bypass the
    embed_origin check.  If the token was bound to a specific origin, any
    request that cannot prove it comes from that origin must be rejected.
    """
    from app.auth.verify import verify_token
    from app.errors import AppError

    token = _mint_embed_jwt(embed_origin="https://host.example")
    with pytest.raises(AppError) as exc_info:
        verify_token(token, expected_origin=None)
    assert exc_info.value.status == 403
    assert exc_info.value.code == "origin_mismatch"
