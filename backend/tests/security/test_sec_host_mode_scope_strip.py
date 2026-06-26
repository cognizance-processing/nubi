"""Security tests for host-mode token write-scope stripping (Residual 1 fix).

Validates that _maybe_pin_host_mode_org strips write/admin/author:sql scopes
from host-mode embed tokens, regardless of what the external issuer embedded,
while leaving non-host-mode tokens completely unaffected.

Coverage
--------
1. Host-mode token with write:* in JWT scope -> scope is stripped after processing.
2. Host-mode token with only read:* -> read access preserved (no regression).
3. Non-host-mode (first-party access) token with write:* -> unaffected (still has write).
4. _strip_host_mode_write_scopes unit: all stripped prefixes removed, read retained.
5. author:metric is KEPT (it is allowed for embed metric authoring).
6. author:sql is STRIPPED (raw SQL is unconditionally blocked for embed anyway,
   but defence-in-depth strips it from the identity scope too).
7. admin scope (exact and admin:something) is STRIPPED.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

# ---------------------------------------------------------------------------
# Env bootstrap before any app import
# ---------------------------------------------------------------------------

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault(
    "JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef"
)
os.environ.setdefault("GOOGLE_CLIENT_ID", "fake-gid")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "fake-gsecret")
os.environ.setdefault(
    "GOOGLE_REDIRECT_URI", "http://localhost:8000/api/v1/auth/google/callback"
)
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("ENV", "test")

import jwt as pyjwt  # noqa: E402
from cryptography.hazmat.backends import default_backend  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from jwt.algorithms import RSAAlgorithm  # noqa: E402

# ---------------------------------------------------------------------------
# RSA keypair shared across this module
# ---------------------------------------------------------------------------

_PRIVATE_KEY = rsa.generate_private_key(
    public_exponent=65537,
    key_size=2048,
    backend=default_backend(),
)
_PUBLIC_KEY = _PRIVATE_KEY.public_key()

_PUBLIC_KEY_PEM: str = _PUBLIC_KEY.public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
).decode()

_JWKS_KEY: dict = json.loads(RSAAlgorithm.to_jwk(_PUBLIC_KEY))
_JWKS_KEY["kid"] = "hm-scope-strip-test"
_JWKS_KEY["use"] = "sig"
_STATIC_JWKS: dict = {"keys": [_JWKS_KEY]}

_HOST_ISS = "https://host-scopestrip-sec-test.example"
_HOST_AUD = "nubi-embed"
_USER_ID = str(uuid.uuid4())


def _make_embed_token(scopes: list[str]) -> str:
    """Mint a host-mode embed JWT with the given scope list."""
    private_pem = _PRIVATE_KEY.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    now = datetime.now(timezone.utc)
    payload = {
        "iss": _HOST_ISS,
        "aud": _HOST_AUD,
        "sub": _USER_ID,
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "scope": " ".join(scopes),
        "org": "tenant-scopestrip-test",
    }
    return pyjwt.encode(payload, private_pem, algorithm="RS256", headers={"kid": "hm-scope-strip-test"})


def _setup_registry():
    from app.auth.issuers import get_issuer_registry

    reg = get_issuer_registry()
    reg.unregister(_HOST_ISS)
    reg.register(
        _HOST_ISS,
        jwks_uri="https://host-scopestrip-sec-test.example/.well-known/jwks.json",
        aud=_HOST_AUD,
        static_jwks=_STATIC_JWKS,
        host_mode=True,
        org_claim="org",
    )
    return reg


def _teardown_registry(reg):
    reg.unregister(_HOST_ISS)


async def _process_embed_token(token_str: str) -> "VerifiedIdentity":
    """Verify the embed token and call _maybe_pin_host_mode_org; return the identity."""
    from app.auth.verify import verify_token
    from app.auth.deps import _maybe_pin_host_mode_org
    from app.routes._org import host_mode_org_pin

    identity = verify_token(token_str)
    host_mode_org_pin.set(None)
    await _maybe_pin_host_mode_org(identity)
    return identity


# ===========================================================================
# 1. Host-mode token with write:* scope -> stripped
# ===========================================================================


@pytest.mark.asyncio
async def test_host_mode_write_scope_stripped() -> None:
    """write:* in a host-mode JWT is stripped from identity.scope."""
    reg = _setup_registry()
    try:
        token = _make_embed_token(["read:*", "write:*"])
        identity = await _process_embed_token(token)
        assert "write:*" not in identity.scope, (
            f"write:* should have been stripped but scope is: {identity.scope}"
        )
        assert "read:*" in identity.scope, "read:* must be preserved"
    finally:
        _teardown_registry(reg)


@pytest.mark.asyncio
@pytest.mark.parametrize("write_scope", [
    "write:metrics",
    "write:webhooks",
    "write:*",
    "edit:metrics",
    "edit:*",
    "admin",
    "admin:org",
    "author:sql",
    "write:query:prod",
])
async def test_host_mode_write_scopes_all_stripped(write_scope: str) -> None:
    """Every write/edit/admin/author:sql scope is stripped from host-mode tokens."""
    reg = _setup_registry()
    try:
        token = _make_embed_token(["read:*", write_scope])
        identity = await _process_embed_token(token)
        assert write_scope not in identity.scope, (
            f"{write_scope!r} should have been stripped but scope is: {identity.scope}"
        )
        assert "read:*" in identity.scope, "read:* must be preserved"
    finally:
        _teardown_registry(reg)


# ===========================================================================
# 2. Host-mode token with only read:* -> no regression
# ===========================================================================


@pytest.mark.asyncio
async def test_host_mode_read_only_scope_preserved() -> None:
    """A host-mode token with only read:* retains that scope after processing."""
    reg = _setup_registry()
    try:
        token = _make_embed_token(["read:*"])
        identity = await _process_embed_token(token)
        assert "read:*" in identity.scope, "read:* must be preserved for host-mode"
        # No write scopes should be present
        for scope in identity.scope:
            assert not scope.startswith("write:"), f"Unexpected write scope: {scope}"
            assert not scope.startswith("edit:"), f"Unexpected edit scope: {scope}"
            assert scope not in ("admin", "author:sql"), f"Unexpected privileged scope: {scope}"
    finally:
        _teardown_registry(reg)


# ===========================================================================
# 3. author:metric is KEPT (it is an allowed embed scope)
# ===========================================================================


@pytest.mark.asyncio
async def test_host_mode_author_metric_kept() -> None:
    """author:metric is NOT stripped from host-mode tokens."""
    reg = _setup_registry()
    try:
        token = _make_embed_token(["read:*", "author:metric", "write:*"])
        identity = await _process_embed_token(token)
        assert "author:metric" in identity.scope, "author:metric must be preserved"
        assert "write:*" not in identity.scope, "write:* must be stripped"
    finally:
        _teardown_registry(reg)


# ===========================================================================
# 4. Non-host-mode (first-party) token with write:* -> unaffected
# ===========================================================================


def test_non_host_mode_first_party_token_unaffected() -> None:
    """A first-party (HS256) access token with write:* is NEVER modified."""
    from app.auth.jwt import mint_access_token
    from app.auth.verify import verify_token

    user_id = str(uuid.uuid4())
    token = mint_access_token(user_id)
    identity = verify_token(token)

    # First-party tokens get _FIRST_PARTY_SCOPES which includes edit:* and author:sql
    # They must never be stripped.
    assert identity.kind == "access"
    # The scope list for a first-party token contains at least read:*
    assert any(s.startswith("read:") or s == "read:*" for s in identity.scope), (
        "First-party token must retain its read scopes"
    )
    # Critically: no stripping happened — the scope is whatever was in the token.
    # We can't assert a specific write scope because first-party tokens use
    # _FIRST_PARTY_SCOPES which includes "edit:*". Let's verify it's present.
    has_write_or_edit = any(
        s.startswith("write:") or s.startswith("edit:") or s == "author:sql"
        for s in identity.scope
    )
    assert has_write_or_edit, (
        f"First-party token lost its write/edit/author scopes: {identity.scope}"
    )


# ===========================================================================
# 5. _strip_host_mode_write_scopes unit tests
# ===========================================================================


def test_strip_host_mode_write_scopes_unit() -> None:
    """Direct unit test of the scope stripping helper."""
    from app.auth.deps import _strip_host_mode_write_scopes

    input_scopes = [
        "read:*",
        "read:query",
        "author:metric",
        "write:metrics",
        "write:*",
        "edit:dashboards",
        "edit:*",
        "admin",
        "admin:org",
        "author:sql",
        "write:query:prod",
    ]
    result = _strip_host_mode_write_scopes(input_scopes)

    # These must be present
    assert "read:*" in result
    assert "read:query" in result
    assert "author:metric" in result

    # These must be stripped
    for bad in [
        "write:metrics", "write:*", "edit:dashboards", "edit:*",
        "admin", "admin:org", "author:sql", "write:query:prod",
    ]:
        assert bad not in result, f"{bad!r} should have been stripped"


def test_strip_host_mode_write_scopes_empty_input() -> None:
    """Empty scope list returns empty list."""
    from app.auth.deps import _strip_host_mode_write_scopes

    assert _strip_host_mode_write_scopes([]) == []


def test_strip_host_mode_write_scopes_all_safe() -> None:
    """A scope list with only safe scopes is returned unchanged."""
    from app.auth.deps import _strip_host_mode_write_scopes

    safe = ["read:*", "read:query", "author:metric"]
    result = _strip_host_mode_write_scopes(safe)
    assert result == safe
