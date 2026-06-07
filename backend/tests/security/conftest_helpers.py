"""Shared helpers for the security test suite.

Provides RSA keypair, token minting helpers, and app fixture wiring.
Each test module imports from here rather than duplicating setup.

NOT a conftest.py (to avoid pytest auto-collection conflicts with the
parent conftest); import explicitly from each security test file.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Environment bootstrap (must be done before any app imports).
# ---------------------------------------------------------------------------
os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef")
os.environ.setdefault("JWT_ACCESS_TTL_MIN", "15")
os.environ.setdefault("GOOGLE_CLIENT_ID", "fake-gid")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "fake-gsecret")
os.environ.setdefault(
    "GOOGLE_REDIRECT_URI", "http://localhost:8000/api/v1/auth/google/callback"
)
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("ENV", "test")

# ---------------------------------------------------------------------------
# RSA keypair (generated once per process).
# ---------------------------------------------------------------------------
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
import jwt as pyjwt

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

# A *second* keypair for "wrong key" / forgery tests.
_ATTACKER_PRIVATE_KEY = rsa.generate_private_key(
    public_exponent=65537,
    key_size=2048,
    backend=default_backend(),
)

_JWKS_KEY: dict = json.loads(RSAAlgorithm.to_jwk(_PUBLIC_KEY))
_JWKS_KEY["kid"] = "sec-suite-key"
_JWKS_KEY["use"] = "sig"
STATIC_JWKS: dict = {"keys": [_JWKS_KEY]}

HOST_ISS = "https://sec-suite-host.example"
HOST_AUD = "nubi"
EMBED_ORIGIN = "https://sec-suite-host.example"
KID = "sec-suite-key"


# ---------------------------------------------------------------------------
# Token minting helpers
# ---------------------------------------------------------------------------

def mint_embed_token(
    *,
    iss: str = HOST_ISS,
    aud: str = HOST_AUD,
    sub: str = "sec-user-1",
    org: str = "sec-org",
    scope: list[str] | None = None,
    policies: dict | None = None,
    embed_origin: str | None = EMBED_ORIGIN,
    exp_delta: int = 300,
    private_key=None,
    algorithm: str = "RS256",
    kid: str | None = KID,
) -> str:
    """Mint a test embed JWT."""
    if scope is None:
        scope = ["read:query"]
    if policies is None:
        policies = {}
    if private_key is None:
        private_key = _PRIVATE_KEY

    now = datetime.now(tz=timezone.utc)
    payload: dict[str, Any] = {
        "iss": iss,
        "aud": aud,
        "sub": sub,
        "org": org,
        "roles": ["viewer"],
        "policies": policies,
        "scope": scope,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=exp_delta)).timestamp()),
    }
    if embed_origin is not None:
        payload["embed_origin"] = embed_origin

    headers: dict[str, Any] = {}
    if kid is not None:
        headers["kid"] = kid

    return pyjwt.encode(payload, private_key, algorithm=algorithm, headers=headers)


def mint_access_token(user_id: str = "sec-fp-user", extra_claims: dict | None = None) -> str:
    """Mint a first-party HS256 access token via the real helper."""
    from app.auth.jwt import mint_access_token as _mint
    return _mint(user_id, extra_claims=extra_claims)


# ---------------------------------------------------------------------------
# App + client fixture factories (to be used in each test module's fixtures)
# ---------------------------------------------------------------------------

def make_db_patches():
    """Return a list of mock patches for DB I/O (mirrors conftest.py)."""
    return [
        patch("app.db.fetchrow", new=AsyncMock(return_value=None)),
        patch("app.db.fetch", new=AsyncMock(return_value=[])),
        patch("app.db.execute", new=AsyncMock(return_value="OK")),
        patch("app.db.get_connection", new=AsyncMock()),
        patch("app.routes.auth.fetchrow", new=AsyncMock(return_value=None)),
        patch("app.routes.auth.execute", new=AsyncMock(return_value="OK")),
        patch("app.auth.sessions.fetchrow", new=AsyncMock(return_value=None)),
        patch("app.auth.sessions.execute", new=AsyncMock(return_value="OK")),
        patch("app.auth.deps.fetchrow", new=AsyncMock(return_value=None)),
        patch("app.db.init_db", new=AsyncMock()),
        patch("app.db.close_db", new=AsyncMock()),
    ]
