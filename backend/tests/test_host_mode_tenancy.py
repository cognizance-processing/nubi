"""Tests for the "embedded host" tenancy mode (host_mode=True on an issuer).

What this suite verifies
------------------------
1. A host-mode token resolves org from the JWT claim with NO org_members row.
2. A non-host-mode issuer still requires membership (unchanged behaviour).
3. A host-mode token with a missing/empty org claim is rejected with 403.
4. host_mode_org_pin is set correctly in the contextvar after _maybe_pin_host_mode_org.
5. An X-Org-Id header matching the pinned org is accepted.
6. An X-Org-Id header conflicting with the pinned org is rejected (403).
7. The registry.register() path correctly propagates host_mode / org_claim fields.
8. The issuers store round-trips host_mode and org_claim through create/update.
9. _sync_registry passes host_mode and org_claim to the in-process registry.
10. A token with a custom org_claim name resolves from that claim.
"""

from __future__ import annotations

import json
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
_JWKS_KEY["kid"] = "test-key-host-mode"
_JWKS_KEY["use"] = "sig"
_STATIC_JWKS: dict = {"keys": [_JWKS_KEY]}

_HOST_ISS = "https://host-mode.example"
_HOST_AUD = "nubi-embed"
_KID = "test-key-host-mode"
_ORG_ID = str(uuid.uuid4())
_USER_ID = str(uuid.uuid4())


def _private_pem() -> bytes:
    return _PRIVATE_KEY.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _mint_token(
    iss: str = _HOST_ISS,
    aud: str = _HOST_AUD,
    org: str | None = _ORG_ID,
    sub: str = "embed-user",
    exp_delta: int = 300,
    kid: str = _KID,
    extra_claims: dict | None = None,
) -> str:
    """Mint a signed RS256 embed JWT."""
    now = datetime.now(tz=timezone.utc)
    payload: dict[str, Any] = {
        "iss": iss,
        "aud": aud,
        "sub": sub,
        "iat": now,
        "exp": now + timedelta(seconds=exp_delta),
    }
    if org is not None:
        payload["org"] = org
    if extra_claims:
        payload.update(extra_claims)
    return pyjwt.encode(payload, _private_pem(), algorithm="RS256", headers={"kid": kid})


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_store():
    """Reset the issuers store singleton before each test."""
    from app.security.issuers_store import InMemoryIssuersStore, set_issuers_store

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


@pytest.fixture(autouse=True)
def _clean_host_mode_pin():
    """Reset the host_mode_org_pin contextvar after each test."""
    from app.routes._org import host_mode_org_pin

    token = host_mode_org_pin.set(None)
    yield
    host_mode_org_pin.reset(token)


# ---------------------------------------------------------------------------
# Test 1: host-mode token resolves org from claim — no org_members row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_host_mode_pin_set_via_in_process_registry(_clean_registry):
    """_maybe_pin_host_mode_org sets host_mode_org_pin from JWT claim via registry."""
    from app.auth.deps import _maybe_pin_host_mode_org
    from app.auth.issuers import get_issuer_registry
    from app.auth.verify import verify_token_async
    from app.routes._org import host_mode_org_pin
    from app.security.issuers_store import InMemoryIssuersStore, set_issuers_store

    # Register a host-mode issuer in the in-process registry.
    registry = get_issuer_registry()
    registry.register(
        _HOST_ISS,
        jwks_uri="",
        aud=_HOST_AUD,
        static_jwks=_STATIC_JWKS,
        host_mode=True,
        org_claim="org",
    )

    # Also seed the store so verify_token_async can find it for DB fallback.
    store = InMemoryIssuersStore()
    set_issuers_store(store)
    await store.create(
        org_id=_ORG_ID,
        name="Host Mode Issuer",
        issuer=_HOST_ISS,
        audience=_HOST_AUD,
        created_by=_USER_ID,
        static_jwks_json=_STATIC_JWKS,
        enabled=True,
        host_mode=True,
        org_claim="org",
    )

    token = _mint_token(org=_ORG_ID)
    identity = await verify_token_async(token)
    assert identity.kind == "embed"
    assert identity.org == _ORG_ID

    # At this point host_mode_org_pin should NOT be set (only _maybe_pin sets it).
    assert host_mode_org_pin.get() is None

    # Now call the helper directly.
    await _maybe_pin_host_mode_org(identity)

    # The pin should now be set.
    assert host_mode_org_pin.get() == _ORG_ID


# ---------------------------------------------------------------------------
# Test 2: non-host-mode issuer does NOT set the pin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_host_mode_issuer_does_not_pin(_clean_registry):
    """A regular (non-host-mode) issuer leaves host_mode_org_pin unset."""
    from app.auth.deps import _maybe_pin_host_mode_org
    from app.auth.issuers import get_issuer_registry
    from app.auth.verify import verify_token_async
    from app.routes._org import host_mode_org_pin
    from app.security.issuers_store import InMemoryIssuersStore, set_issuers_store

    # Standard issuer — host_mode=False (default).
    registry = get_issuer_registry()
    registry.register(
        _HOST_ISS,
        jwks_uri="",
        aud=_HOST_AUD,
        static_jwks=_STATIC_JWKS,
        host_mode=False,
    )

    store = InMemoryIssuersStore()
    set_issuers_store(store)
    await store.create(
        org_id=_ORG_ID,
        name="Standard Issuer",
        issuer=_HOST_ISS,
        audience=_HOST_AUD,
        created_by=_USER_ID,
        static_jwks_json=_STATIC_JWKS,
        enabled=True,
    )

    token = _mint_token(org=_ORG_ID)
    identity = await verify_token_async(token)
    await _maybe_pin_host_mode_org(identity)

    # Must NOT be pinned.
    assert host_mode_org_pin.get() is None


# ---------------------------------------------------------------------------
# Test 3: missing/empty org claim on host-mode token → 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_host_mode_missing_org_claim_rejected(_clean_registry):
    """A host-mode token with no org claim raises AppError 403."""
    from app.auth.deps import _maybe_pin_host_mode_org
    from app.auth.issuers import get_issuer_registry
    from app.auth.verify import verify_token_async
    from app.errors import AppError
    from app.security.issuers_store import InMemoryIssuersStore, set_issuers_store

    registry = get_issuer_registry()
    registry.register(
        _HOST_ISS,
        jwks_uri="",
        aud=_HOST_AUD,
        static_jwks=_STATIC_JWKS,
        host_mode=True,
        org_claim="org",
    )

    store = InMemoryIssuersStore()
    set_issuers_store(store)
    await store.create(
        org_id=_ORG_ID,
        name="Host Mode Issuer",
        issuer=_HOST_ISS,
        audience=_HOST_AUD,
        created_by=_USER_ID,
        static_jwks_json=_STATIC_JWKS,
        enabled=True,
        host_mode=True,
        org_claim="org",
    )

    # Mint without an org claim.
    token = _mint_token(org=None)
    identity = await verify_token_async(token)

    with pytest.raises(AppError) as exc_info:
        await _maybe_pin_host_mode_org(identity)

    assert exc_info.value.status == 403
    assert exc_info.value.code == "forbidden"


# ---------------------------------------------------------------------------
# Test 4: DB-backed host-mode path (registry miss, store has row)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_host_mode_pin_set_via_db_store(_clean_registry):
    """_maybe_pin_host_mode_org uses the DB store when the registry misses iss."""
    from app.auth.deps import _maybe_pin_host_mode_org
    from app.auth.verify import verify_token_async
    from app.routes._org import host_mode_org_pin
    from app.security.issuers_store import InMemoryIssuersStore, set_issuers_store

    # Registry is EMPTY — only the DB store has the issuer.
    store = InMemoryIssuersStore()
    set_issuers_store(store)
    await store.create(
        org_id=_ORG_ID,
        name="DB Host Mode Issuer",
        issuer=_HOST_ISS,
        audience=_HOST_AUD,
        created_by=_USER_ID,
        static_jwks_json=_STATIC_JWKS,
        enabled=True,
        host_mode=True,
        org_claim="org",
    )

    token = _mint_token(org=_ORG_ID)
    identity = await verify_token_async(token)
    assert identity.kind == "embed"

    await _maybe_pin_host_mode_org(identity)

    assert host_mode_org_pin.get() == _ORG_ID


# ---------------------------------------------------------------------------
# Test 5: X-Org-Id matching pinned org is accepted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_org_id_matching_header_accepted(_clean_registry):
    """resolve_org_id returns the pinned org when X-Org-Id matches."""
    from unittest.mock import MagicMock

    from app.routes._org import host_mode_org_pin, resolve_org_id

    host_mode_org_pin.set(_ORG_ID)

    request = MagicMock()
    request.headers.get = lambda key, default="": _ORG_ID if key == "x-org-id" else default

    repo = MagicMock()
    result = await resolve_org_id(_USER_ID, repo, request)
    assert result == _ORG_ID


# ---------------------------------------------------------------------------
# Test 6: X-Org-Id conflicting with pinned org → 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_org_id_conflicting_header_rejected(_clean_registry):
    """resolve_org_id raises 403 when X-Org-Id conflicts with the host-mode pin."""
    from unittest.mock import MagicMock

    from app.errors import AppError
    from app.routes._org import host_mode_org_pin, resolve_org_id

    host_mode_org_pin.set(_ORG_ID)
    other_org = str(uuid.uuid4())

    request = MagicMock()
    request.headers.get = lambda key, default="": other_org if key == "x-org-id" else default

    repo = MagicMock()
    with pytest.raises(AppError) as exc_info:
        await resolve_org_id(_USER_ID, repo, request)

    assert exc_info.value.status == 403
    assert exc_info.value.code == "forbidden"


# ---------------------------------------------------------------------------
# Test 7: IssuerRegistry.register() propagates host_mode and org_claim
# ---------------------------------------------------------------------------


def test_registry_register_propagates_host_mode():
    """IssuerRegistry.register() stores host_mode and org_claim on IssuerConfig."""
    from app.auth.issuers import IssuerRegistry

    registry = IssuerRegistry()
    cfg = registry.register(
        "https://test.example",
        jwks_uri="https://test.example/.well-known/jwks.json",
        aud="test-aud",
        host_mode=True,
        org_claim="tenant",
    )
    assert cfg.host_mode is True
    assert cfg.org_claim == "tenant"


def test_registry_register_defaults_host_mode_false():
    """host_mode defaults to False for existing callers that don't pass it."""
    from app.auth.issuers import IssuerRegistry

    registry = IssuerRegistry()
    cfg = registry.register(
        "https://standard.example",
        jwks_uri="https://standard.example/.well-known/jwks.json",
        aud="std-aud",
    )
    assert cfg.host_mode is False
    assert cfg.org_claim is None


# ---------------------------------------------------------------------------
# Test 8: issuers store round-trips host_mode and org_claim
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_create_and_list_includes_host_mode():
    """InMemoryIssuersStore create/list round-trips host_mode and org_claim."""
    from app.security.issuers_store import InMemoryIssuersStore

    store = InMemoryIssuersStore()
    row = await store.create(
        org_id=_ORG_ID,
        name="Host Issuer",
        issuer=_HOST_ISS,
        audience=_HOST_AUD,
        created_by=_USER_ID,
        static_jwks_json=_STATIC_JWKS,
        host_mode=True,
        org_claim="tenant_id",
    )
    assert row["host_mode"] is True
    assert row["org_claim"] == "tenant_id"

    rows = await store.list_for_org(_ORG_ID)
    assert len(rows) == 1
    assert rows[0]["host_mode"] is True
    assert rows[0]["org_claim"] == "tenant_id"


@pytest.mark.asyncio
async def test_store_update_host_mode():
    """InMemoryIssuersStore.update() can toggle host_mode and org_claim."""
    from app.security.issuers_store import InMemoryIssuersStore

    store = InMemoryIssuersStore()
    row = await store.create(
        org_id=_ORG_ID,
        name="Initially Standard",
        issuer=_HOST_ISS,
        audience=_HOST_AUD,
        created_by=_USER_ID,
        static_jwks_json=_STATIC_JWKS,
        host_mode=False,
        org_claim=None,
    )
    assert row["host_mode"] is False

    updated = await store.update(
        row["id"],
        _ORG_ID,
        host_mode=True,
        org_claim="org_id",
    )
    assert updated is not None
    assert updated["host_mode"] is True
    assert updated["org_claim"] == "org_id"


@pytest.mark.asyncio
async def test_store_defaults_host_mode_false():
    """host_mode defaults to False in InMemoryIssuersStore.create()."""
    from app.security.issuers_store import InMemoryIssuersStore

    store = InMemoryIssuersStore()
    row = await store.create(
        org_id=_ORG_ID,
        name="Standard",
        issuer=_HOST_ISS,
        audience=_HOST_AUD,
        created_by=_USER_ID,
        static_jwks_json=_STATIC_JWKS,
    )
    assert row["host_mode"] is False
    assert row["org_claim"] is None


# ---------------------------------------------------------------------------
# Test 9: _sync_registry passes host_mode and org_claim to the in-process registry
# ---------------------------------------------------------------------------


def test_sync_registry_propagates_host_mode(_clean_registry):
    """_sync_registry passes host_mode and org_claim to registry.register()."""
    from app.auth.issuers import get_issuer_registry
    from app.routes.jwt_issuers import _sync_registry

    registry = get_issuer_registry()
    rows = [
        {
            "org_id": _ORG_ID,
            "issuer": _HOST_ISS,
            "jwks_url": None,
            "audience": _HOST_AUD,
            "static_jwks_json": _STATIC_JWKS,
            "enabled": True,
            "host_mode": True,
            "org_claim": "tenant",
        }
    ]
    _sync_registry(_ORG_ID, rows)

    cfg = registry.get(_HOST_ISS)
    assert cfg is not None
    assert cfg.host_mode is True
    assert cfg.org_claim == "tenant"


def test_sync_registry_disabled_issuer_unregisters(_clean_registry):
    """_sync_registry unregisters a disabled issuer from the registry."""
    from app.auth.issuers import get_issuer_registry
    from app.routes.jwt_issuers import _sync_registry

    registry = get_issuer_registry()
    # First register it.
    registry.register(
        _HOST_ISS,
        jwks_uri="",
        aud=_HOST_AUD,
        static_jwks=_STATIC_JWKS,
        host_mode=True,
    )
    assert registry.get(_HOST_ISS) is not None

    # Now sync with disabled=True.
    _sync_registry(
        _ORG_ID,
        [
            {
                "org_id": _ORG_ID,
                "issuer": _HOST_ISS,
                "jwks_url": None,
                "audience": _HOST_AUD,
                "static_jwks_json": _STATIC_JWKS,
                "enabled": False,
                "host_mode": True,
                "org_claim": None,
            }
        ],
    )
    assert registry.get(_HOST_ISS) is None


# ---------------------------------------------------------------------------
# Test 10: Custom org_claim name is honoured
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_custom_org_claim_name_used(_clean_registry):
    """When org_claim is 'tenant_id', the value is read from that claim."""
    from app.auth.deps import _maybe_pin_host_mode_org
    from app.auth.issuers import get_issuer_registry
    from app.auth.verify import verify_token_async
    from app.routes._org import host_mode_org_pin
    from app.security.issuers_store import InMemoryIssuersStore, set_issuers_store

    custom_org = str(uuid.uuid4())

    registry = get_issuer_registry()
    registry.register(
        _HOST_ISS,
        jwks_uri="",
        aud=_HOST_AUD,
        static_jwks=_STATIC_JWKS,
        host_mode=True,
        org_claim="tenant_id",  # custom claim name
    )

    store = InMemoryIssuersStore()
    set_issuers_store(store)
    await store.create(
        org_id=custom_org,
        name="Custom Claim Issuer",
        issuer=_HOST_ISS,
        audience=_HOST_AUD,
        created_by=_USER_ID,
        static_jwks_json=_STATIC_JWKS,
        enabled=True,
        host_mode=True,
        org_claim="tenant_id",
    )

    # Mint with org=custom_org (for DB lookup) and tenant_id=custom_org (the actual pin claim).
    token = _mint_token(
        org=custom_org,
        extra_claims={"tenant_id": custom_org},
    )
    identity = await verify_token_async(token)
    await _maybe_pin_host_mode_org(identity)

    assert host_mode_org_pin.get() == custom_org


# ---------------------------------------------------------------------------
# Test 11: get_user_org returns host_mode_org_pin when set
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_user_org_returns_host_mode_pin():
    """get_user_org returns host_mode_org_pin without touching org_members."""
    from unittest.mock import MagicMock

    from app.routes._org import get_user_org, host_mode_org_pin

    host_mode_org_pin.set(_ORG_ID)

    # repo would raise if consulted — but get_user_org should return early.
    repo = MagicMock()
    repo.get_org_for_user = MagicMock(side_effect=RuntimeError("Should not be called"))

    result = await get_user_org(_USER_ID, repo)
    assert result == _ORG_ID
    repo.get_org_for_user.assert_not_called()


# ---------------------------------------------------------------------------
# Test 12: IssuerConfig dataclass has correct defaults
# ---------------------------------------------------------------------------


def test_issuer_config_defaults():
    """IssuerConfig has host_mode=False and org_claim=None by default."""
    from app.auth.issuers import IssuerConfig

    cfg = IssuerConfig(iss="https://ex.com", jwks_uri="https://ex.com/.jwks", aud="aud")
    assert cfg.host_mode is False
    assert cfg.org_claim is None
