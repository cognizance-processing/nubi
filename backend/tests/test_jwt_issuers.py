"""Tests for the JWT issuers management system.

Coverage
--------
1.  A configured RS256 issuer's token verifies successfully (DB-backed path).
2.  A token from an unknown issuer is rejected with 401.
3.  A token from a disabled issuer is rejected with 401.
4.  InMemoryIssuersStore CRUD: create / list / get / update / delete.
5.  Duplicate issuer (same org + iss) raises an error.
6.  CRUD routes: GET/POST/PUT/DELETE /security/jwt-issuers via TestClient.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Environment bootstrap (must happen before any app import)
# ---------------------------------------------------------------------------

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault(
    "JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef"
)
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
# RSA keypair helpers (generated once at module level)
# ---------------------------------------------------------------------------

import json

import jwt as pyjwt
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

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
_JWKS_KEY["kid"] = "test-key-issuers"
_JWKS_KEY["use"] = "sig"
_STATIC_JWKS: dict = {"keys": [_JWKS_KEY]}

_HOST_ISS = "https://host.managed.example"
_HOST_AUD = "nubi-embed"
_KID = "test-key-issuers"
_ORG_ID = str(uuid.uuid4())
_USER_ID = str(uuid.uuid4())


def _mint_token(
    iss: str = _HOST_ISS,
    aud: str = _HOST_AUD,
    org: str = _ORG_ID,
    sub: str = "embed-user",
    exp_delta: int = 300,
    kid: str = _KID,
) -> str:
    """Mint a signed RS256 embed JWT."""
    private_pem = _PRIVATE_KEY.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    now = datetime.now(tz=timezone.utc)
    payload = {
        "iss": iss,
        "aud": aud,
        "sub": sub,
        "org": org,
        "iat": now,
        "exp": now + timedelta(seconds=exp_delta),
    }
    return pyjwt.encode(payload, private_pem, algorithm="RS256", headers={"kid": kid})


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_store():
    """Reset the issuers store singleton before each test."""
    from app.security.issuers_store import (
        InMemoryIssuersStore,
        set_issuers_store,
    )

    store = InMemoryIssuersStore()
    set_issuers_store(store)
    yield store
    set_issuers_store(None)


@pytest.fixture(autouse=True)
def _clean_registry():
    """Clear the in-process IssuerRegistry before each test."""
    from app.auth.issuers import get_issuer_registry

    registry = get_issuer_registry()
    registry.clear()
    yield registry
    registry.clear()


# ---------------------------------------------------------------------------
# Tests — InMemoryIssuersStore CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_and_list():
    """Create an issuer row, then list it back."""
    from app.security.issuers_store import InMemoryIssuersStore

    store = InMemoryIssuersStore()
    row = await store.create(
        org_id=_ORG_ID,
        name="My Host",
        issuer=_HOST_ISS,
        audience=_HOST_AUD,
        created_by=_USER_ID,
        static_jwks_json=_STATIC_JWKS,
    )
    assert row["issuer"] == _HOST_ISS
    assert row["audience"] == _HOST_AUD
    assert row["enabled"] is True

    rows = await store.list_for_org(_ORG_ID)
    assert len(rows) == 1
    assert rows[0]["id"] == row["id"]


@pytest.mark.asyncio
async def test_get_enabled_by_iss_returns_row():
    from app.security.issuers_store import InMemoryIssuersStore

    store = InMemoryIssuersStore()
    await store.create(
        org_id=_ORG_ID,
        name="My Host",
        issuer=_HOST_ISS,
        audience=_HOST_AUD,
        created_by=_USER_ID,
        static_jwks_json=_STATIC_JWKS,
        enabled=True,
    )
    row = await store.get_enabled_by_iss(_ORG_ID, _HOST_ISS)
    assert row is not None
    assert row["issuer"] == _HOST_ISS


@pytest.mark.asyncio
async def test_disabled_issuer_not_returned_by_get_enabled():
    from app.security.issuers_store import InMemoryIssuersStore

    store = InMemoryIssuersStore()
    created = await store.create(
        org_id=_ORG_ID,
        name="Disabled Host",
        issuer=_HOST_ISS,
        audience=_HOST_AUD,
        created_by=_USER_ID,
        static_jwks_json=_STATIC_JWKS,
        enabled=False,
    )
    row = await store.get_enabled_by_iss(_ORG_ID, _HOST_ISS)
    assert row is None


@pytest.mark.asyncio
async def test_update_issuer():
    from app.security.issuers_store import InMemoryIssuersStore

    store = InMemoryIssuersStore()
    row = await store.create(
        org_id=_ORG_ID,
        name="Old Name",
        issuer=_HOST_ISS,
        audience=_HOST_AUD,
        created_by=_USER_ID,
        static_jwks_json=_STATIC_JWKS,
    )
    updated = await store.update(row["id"], _ORG_ID, name="New Name", enabled=False)
    assert updated is not None
    assert updated["name"] == "New Name"
    assert updated["enabled"] is False


@pytest.mark.asyncio
async def test_delete_issuer():
    from app.security.issuers_store import InMemoryIssuersStore

    store = InMemoryIssuersStore()
    row = await store.create(
        org_id=_ORG_ID,
        name="Delete Me",
        issuer=_HOST_ISS,
        audience=_HOST_AUD,
        created_by=_USER_ID,
        static_jwks_json=_STATIC_JWKS,
    )
    deleted = await store.delete(row["id"], _ORG_ID)
    assert deleted is True
    rows = await store.list_for_org(_ORG_ID)
    assert rows == []


@pytest.mark.asyncio
async def test_duplicate_issuer_raises():
    from app.security.issuers_store import InMemoryIssuersStore

    store = InMemoryIssuersStore()
    await store.create(
        org_id=_ORG_ID,
        name="First",
        issuer=_HOST_ISS,
        audience=_HOST_AUD,
        created_by=_USER_ID,
        static_jwks_json=_STATIC_JWKS,
    )
    with pytest.raises(ValueError, match="already configured"):
        await store.create(
            org_id=_ORG_ID,
            name="Second",
            issuer=_HOST_ISS,  # same iss, same org
            audience=_HOST_AUD,
            created_by=_USER_ID,
            static_jwks_json=_STATIC_JWKS,
        )


# ---------------------------------------------------------------------------
# Tests — async verify_token_async with DB-backed store
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_configured_issuer_rs256_token_verifies(_clean_store):
    """A token from a DB-configured issuer verifies successfully."""
    from app.auth.verify import verify_token_async

    # Seed the store (NOT the in-process registry — testing the DB path).
    await _clean_store.create(
        org_id=_ORG_ID,
        name="My Host",
        issuer=_HOST_ISS,
        audience=_HOST_AUD,
        created_by=_USER_ID,
        static_jwks_json=_STATIC_JWKS,
        enabled=True,
    )

    token = _mint_token()
    identity = await verify_token_async(token)

    assert identity.kind == "embed"
    assert identity.org == _ORG_ID
    assert identity.user_id == "embed-user"


@pytest.mark.asyncio
async def test_unknown_issuer_rejected(_clean_store):
    """A token whose iss is not in the DB is rejected with 401."""
    from app.auth.verify import verify_token_async
    from app.errors import AppError

    # Store is empty — no issuers configured.
    token = _mint_token(iss="https://unknown.example")

    with pytest.raises(AppError) as exc_info:
        await verify_token_async(token)
    assert exc_info.value.status == 401
    assert exc_info.value.code == "invalid_token"


@pytest.mark.asyncio
async def test_disabled_issuer_rejected(_clean_store):
    """A token from a disabled issuer is rejected with 401."""
    from app.auth.verify import verify_token_async
    from app.errors import AppError

    await _clean_store.create(
        org_id=_ORG_ID,
        name="Disabled Host",
        issuer=_HOST_ISS,
        audience=_HOST_AUD,
        created_by=_USER_ID,
        static_jwks_json=_STATIC_JWKS,
        enabled=False,  # <-- disabled
    )

    token = _mint_token()

    with pytest.raises(AppError) as exc_info:
        await verify_token_async(token)
    assert exc_info.value.status == 401
    assert exc_info.value.code == "invalid_token"


@pytest.mark.asyncio
async def test_token_without_org_claim_rejected(_clean_store):
    """A token with no org claim cannot be looked up in the DB → 401."""
    from app.auth.verify import verify_token_async
    from app.errors import AppError

    await _clean_store.create(
        org_id=_ORG_ID,
        name="My Host",
        issuer=_HOST_ISS,
        audience=_HOST_AUD,
        created_by=_USER_ID,
        static_jwks_json=_STATIC_JWKS,
        enabled=True,
    )

    # Mint without org claim — DB lookup requires org to scope the query.
    private_pem = _PRIVATE_KEY.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    now = datetime.now(tz=timezone.utc)
    payload = {
        "iss": _HOST_ISS,
        "aud": _HOST_AUD,
        "sub": "user",
        # no "org" claim
        "iat": now,
        "exp": now + timedelta(seconds=300),
    }
    token = pyjwt.encode(
        payload, private_pem, algorithm="RS256", headers={"kid": _KID}
    )

    with pytest.raises(AppError) as exc_info:
        await verify_token_async(token)
    assert exc_info.value.status == 401


# ---------------------------------------------------------------------------
# Tests — CRUD HTTP routes via TestClient
# ---------------------------------------------------------------------------


def _make_app_with_fake_user(org_id: str, user_id: str):
    """Build a FastAPI test app with stub auth and an InMemory store."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.auth.deps import current_user, verified_identity
    from app.repos.memory import InMemoryRepo
    from app.repos.provider import set_repo
    from app.security.issuers_store import InMemoryIssuersStore, set_issuers_store
    from app.routes import api_router
    import app.routes.jwt_issuers  # ensure routes are registered  # noqa: F401

    # Fresh store per call.
    store = InMemoryIssuersStore()
    set_issuers_store(store)

    # InMemory repo that resolves the org for our test user.  Use the public
    # seed_org_member helper so the membership has the correct dict shape that
    # get_org_for_user expects (the writer-role dependency resolves the org).
    repo = InMemoryRepo()
    repo.seed_org_member(org_id, user_id)
    set_repo(repo)

    fake_user = {
        "id": user_id,
        "email": "test@example.com",
        "name": "Test User",
        "avatar_url": None,
        "email_verified": True,
        "created_at": datetime.now(tz=timezone.utc),
    }

    # Override auth deps.
    from fastapi import FastAPI as _FastAPI
    from app.routes import api_router as _api_router
    from app.errors import register_handlers

    app = _FastAPI()
    register_handlers(app)

    app.dependency_overrides[current_user] = lambda: fake_user

    app.include_router(_api_router, prefix="/api/v1")

    return app, store


@pytest.mark.asyncio
async def test_crud_routes_roundtrip():
    """Full HTTP lifecycle: create → get → list → update → delete."""
    from fastapi.testclient import TestClient

    org_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())

    app, store = _make_app_with_fake_user(org_id, user_id)

    # Monkey-patch the org resolver so it returns our org.
    import app.routes.jwt_issuers as _jr

    original = _jr._get_user_org

    async def _fake_org(uid, repo):
        return org_id

    _jr._get_user_org = _fake_org

    try:
        with TestClient(app, raise_server_exceptions=True) as client:
            # POST — create
            payload = {
                "name": "My Host",
                "issuer": "https://host.example",
                "audience": "nubi",
                "static_jwks_json": _STATIC_JWKS,
            }
            resp = client.post("/api/v1/security/jwt-issuers/", json=payload)
            assert resp.status_code == 201, resp.text
            row = resp.json()
            assert row["issuer"] == "https://host.example"
            issuer_id = row["id"]

            # GET single
            resp = client.get(f"/api/v1/security/jwt-issuers/{issuer_id}")
            assert resp.status_code == 200
            assert resp.json()["id"] == issuer_id

            # GET list
            resp = client.get("/api/v1/security/jwt-issuers/")
            assert resp.status_code == 200
            assert len(resp.json()) == 1

            # PUT — update
            resp = client.put(
                f"/api/v1/security/jwt-issuers/{issuer_id}",
                json={"name": "Updated Host", "enabled": False},
            )
            assert resp.status_code == 200
            assert resp.json()["name"] == "Updated Host"
            assert resp.json()["enabled"] is False

            # DELETE
            resp = client.delete(f"/api/v1/security/jwt-issuers/{issuer_id}")
            assert resp.status_code == 204

            # GET after delete → 404
            resp = client.get(f"/api/v1/security/jwt-issuers/{issuer_id}")
            assert resp.status_code == 404
    finally:
        _jr._get_user_org = original

        # Cleanup singletons.
        from app.security.issuers_store import set_issuers_store
        from app.repos.provider import set_repo

        set_issuers_store(None)
        set_repo(None)
