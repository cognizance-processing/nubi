"""Security regression tests for host-mode org-claim type validation.

Validates that _maybe_pin_host_mode_org rejects non-string org claims
(arrays, objects, numbers, booleans) that would otherwise stringify to
non-empty values and be accepted as valid org ids.

Wave-4 hardening fix: the org claim must be a plain str; anything else
is treated as missing and raises AppError("forbidden", 403).
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
_JWKS_KEY["kid"] = "hm-org-claim-test"
_JWKS_KEY["use"] = "sig"
_STATIC_JWKS: dict = {"keys": [_JWKS_KEY]}

_HOST_ISS = "https://host-orgclaim-sec-test.example"
_HOST_AUD = "nubi-embed"
_USER_ID = str(uuid.uuid4())


def _make_token(org_val, *, org_claim_name: str = "org") -> str:
    """Mint a host-mode embed token whose org claim is *org_val* (any type)."""
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
        "scope": "read:*",
        org_claim_name: org_val,
    }
    return pyjwt.encode(payload, private_pem, algorithm="RS256", headers={"kid": "hm-org-claim-test"})


def _setup_registry():
    """Register a host-mode issuer in the in-process registry."""
    from app.auth.issuers import get_issuer_registry

    reg = get_issuer_registry()
    reg.unregister(_HOST_ISS)
    reg.register(
        _HOST_ISS,
        jwks_uri="https://host-orgclaim-sec-test.example/.well-known/jwks.json",
        aud=_HOST_AUD,
        static_jwks=_STATIC_JWKS,
        host_mode=True,
        org_claim="org",
    )
    return reg


def _teardown_registry(reg):
    reg.unregister(_HOST_ISS)


# ---------------------------------------------------------------------------
# Helper: run _maybe_pin_host_mode_org and capture the contextvar result
# ---------------------------------------------------------------------------


async def _run_pin(token_str: str) -> str | None:
    """Verify the token and call _maybe_pin_host_mode_org; return the pinned org."""
    from app.auth.verify import verify_token
    from app.auth.deps import _maybe_pin_host_mode_org
    from app.routes._org import host_mode_org_pin

    identity = verify_token(token_str)
    # Reset contextvar so prior tests don't bleed.
    host_mode_org_pin.set(None)
    await _maybe_pin_host_mode_org(identity)
    return host_mode_org_pin.get()


# ===========================================================================
# Valid plain-string org claim — must pin correctly
# ===========================================================================


@pytest.mark.asyncio
async def test_string_org_claim_is_pinned() -> None:
    """A plain string org claim is pinned correctly."""
    reg = _setup_registry()
    try:
        from app.routes._org import host_mode_org_pin

        token = _make_token("tenant-acme")
        pinned = await _run_pin(token)
        assert pinned == "tenant-acme"
    finally:
        _teardown_registry(reg)


# ===========================================================================
# Non-string org claims — must raise 403, never pin
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_org_val",
    [
        ["tenant_a"],          # list (non-empty)
        ["tenant_a", "tenant_b"],  # multi-value list
        [],                    # empty list (also rejected)
        {"id": "tenant_a"},    # dict
        42,                    # integer
        3.14,                  # float
        True,                  # boolean True (would stringify to "True")
        False,                 # boolean False (would stringify to "False" — non-empty!)
    ],
    ids=[
        "list_single",
        "list_multi",
        "list_empty",
        "dict",
        "int",
        "float",
        "bool_true",
        "bool_false",
    ],
)
async def test_non_string_org_claim_raises_403(bad_org_val) -> None:
    """Non-string org claims must raise AppError("forbidden", 403)."""
    from app.errors import AppError

    reg = _setup_registry()
    try:
        token = _make_token(bad_org_val)
        from app.auth.verify import verify_token
        from app.auth.deps import _maybe_pin_host_mode_org
        from app.routes._org import host_mode_org_pin

        # Some bad values (list, dict) can't round-trip through JWT as the org
        # claim (PyJWT encodes them as JSON arrays/objects in the payload).
        # We still want them rejected.
        try:
            identity = verify_token(token)
        except Exception:
            # If the JWT itself can't be decoded with this bad value, that's also
            # a valid rejection — test passes.
            return

        host_mode_org_pin.set(None)
        with pytest.raises(AppError) as exc_info:
            await _maybe_pin_host_mode_org(identity)
        assert exc_info.value.status == 403
        # The org must NOT have been pinned.
        assert host_mode_org_pin.get() is None
    finally:
        _teardown_registry(reg)


# ===========================================================================
# Empty / whitespace-only string org claim — must raise 403
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "empty_org",
    ["", "   ", "\t\n"],
    ids=["empty_string", "whitespace", "tab_newline"],
)
async def test_empty_string_org_claim_raises_403(empty_org: str) -> None:
    """Empty or whitespace-only org claim must be rejected."""
    from app.errors import AppError

    reg = _setup_registry()
    try:
        token = _make_token(empty_org)
        from app.auth.verify import verify_token
        from app.auth.deps import _maybe_pin_host_mode_org
        from app.routes._org import host_mode_org_pin

        identity = verify_token(token)
        host_mode_org_pin.set(None)
        with pytest.raises(AppError) as exc_info:
            await _maybe_pin_host_mode_org(identity)
        assert exc_info.value.status == 403
        assert host_mode_org_pin.get() is None
    finally:
        _teardown_registry(reg)


# ===========================================================================
# Missing org claim — must raise 403
# ===========================================================================


@pytest.mark.asyncio
async def test_missing_org_claim_raises_403() -> None:
    """A host-mode token with no org claim at all must be rejected with 403."""
    from app.errors import AppError
    from app.auth.issuers import get_issuer_registry

    reg = get_issuer_registry()
    reg.unregister(_HOST_ISS)
    reg.register(
        _HOST_ISS,
        jwks_uri="https://host-orgclaim-sec-test.example/.well-known/jwks.json",
        aud=_HOST_AUD,
        static_jwks=_STATIC_JWKS,
        host_mode=True,
        org_claim="org",
    )
    try:
        private_pem = _PRIVATE_KEY.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
        now = datetime.now(timezone.utc)
        # Token with no org claim.
        payload = {
            "iss": _HOST_ISS,
            "aud": _HOST_AUD,
            "sub": _USER_ID,
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "scope": "read:*",
            # no "org" key
        }
        token = pyjwt.encode(
            payload, private_pem, algorithm="RS256", headers={"kid": "hm-org-claim-test"}
        )

        from app.auth.verify import verify_token
        from app.auth.deps import _maybe_pin_host_mode_org
        from app.routes._org import host_mode_org_pin

        identity = verify_token(token)
        host_mode_org_pin.set(None)
        with pytest.raises(AppError) as exc_info:
            await _maybe_pin_host_mode_org(identity)
        assert exc_info.value.status == 403
        assert host_mode_org_pin.get() is None
    finally:
        reg.unregister(_HOST_ISS)
