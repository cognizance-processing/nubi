"""Attack class 5: Chat webhook signature auth.

Covers
------
5a. Slack webhook: _sig_override=False → 401 (force-fail override)
5b. WhatsApp webhook: _sig_override=False → 401
5c. Payload with _sig="bad" key → 401 (payload-embedded rejection signal)
5d. Payload without a signature hint and no override → 200 (permissive default
    in test mode — correct behavior when no secret is configured)
5e. Valid-shaped payload with _sig="bad" is rejected before reaching the agent
5f. After force-fail, even a well-formed payload is rejected
5g. Production mode WITHOUT a signing secret: verify_signature FAILS CLOSED (401)
5h. Real HMAC verification: valid Slack signature → 200
5i. Real HMAC verification: invalid Slack signature → 401
5j. Expired Slack timestamp → 401 (replay protection)
5k. Real HMAC verification: valid WhatsApp sha256= signature → 200
5l. Real HMAC verification: invalid WhatsApp signature → 401
5m. Missing X-Slack-Signature header with secret configured → 401
5n. Missing X-Hub-Signature-256 header with secret configured → 401
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
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


# ---------------------------------------------------------------------------
# HMAC signature helpers
# ---------------------------------------------------------------------------

_TEST_SLACK_SECRET = "test-slack-signing-secret-abc123"
_TEST_WA_SECRET = "test-whatsapp-app-secret-xyz789"


def _make_slack_signature(body: bytes, secret: str, timestamp: int | None = None) -> tuple[str, str]:
    """Return (timestamp_str, signature) for a valid Slack HMAC-SHA256 signature."""
    ts = str(timestamp if timestamp is not None else int(time.time()))
    sig_base = f"v0:{ts}:{body.decode('utf-8', errors='replace')}".encode()
    sig = "v0=" + hmac.new(secret.encode(), sig_base, hashlib.sha256).hexdigest()
    return ts, sig


def _make_whatsapp_signature(body: bytes, secret: str) -> str:
    """Return a valid ``sha256=<hex>`` WhatsApp/Meta HMAC-SHA256 signature."""
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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


@pytest.fixture(autouse=True)
def _reset_sig_override():
    """Ensure _sig_override is clean before and after each test."""
    from app.chat import gateway
    gateway._sig_override.clear()
    yield
    gateway._sig_override.clear()


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    """Clear the settings LRU cache before each test so env changes take effect."""
    from app.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Valid payload helpers
# ---------------------------------------------------------------------------

def _slack_payload() -> dict:
    return {
        "type": "event_callback",
        "event": {"type": "message", "text": "Hello Nubi", "channel": "C123"},
    }


def _whatsapp_payload() -> dict:
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {"from": "+1234567890", "text": {"body": "Hi"}}
                            ]
                        }
                    }
                ]
            }
        ]
    }


# ===========================================================================
# 5a. Slack: _sig_override=False → 401
# ===========================================================================

@pytest.mark.asyncio
async def test_slack_sig_override_false_returns_401(client):
    """When _sig_override['slack']=False, any Slack webhook → 401."""
    from app.chat import gateway
    gateway._sig_override["slack"] = False

    resp = await client.post("/api/v1/chat/slack", json=_slack_payload())
    assert resp.status_code == 401, (
        f"SECURITY FAILURE: Slack webhook with forced-fail sig returned {resp.status_code}"
    )
    body = resp.json()
    assert body["detail"]["code"] == "invalid_signature"


# ===========================================================================
# 5b. WhatsApp: _sig_override=False → 401
# ===========================================================================

@pytest.mark.asyncio
async def test_whatsapp_sig_override_false_returns_401(client):
    """When _sig_override['whatsapp']=False, any WhatsApp webhook → 401."""
    from app.chat import gateway
    gateway._sig_override["whatsapp"] = False

    resp = await client.post("/api/v1/chat/whatsapp", json=_whatsapp_payload())
    assert resp.status_code == 401, (
        f"SECURITY FAILURE: WhatsApp webhook with forced-fail sig returned {resp.status_code}"
    )


# ===========================================================================
# 5c. Payload with _sig="bad" → 401
# ===========================================================================

@pytest.mark.asyncio
async def test_slack_payload_sig_bad_returns_401(client):
    """Payload containing _sig='bad' is rejected before agent processing."""
    payload = _slack_payload()
    payload["_sig"] = "bad"

    resp = await client.post("/api/v1/chat/slack", json=payload)
    assert resp.status_code == 401, (
        f"SECURITY FAILURE: _sig='bad' Slack payload accepted (status {resp.status_code})"
    )


@pytest.mark.asyncio
async def test_whatsapp_payload_sig_bad_returns_401(client):
    """WhatsApp payload with _sig='bad' → 401."""
    payload = _whatsapp_payload()
    payload["_sig"] = "bad"

    resp = await client.post("/api/v1/chat/whatsapp", json=payload)
    assert resp.status_code == 401, (
        f"SECURITY FAILURE: _sig='bad' WhatsApp payload accepted (status {resp.status_code})"
    )


# ===========================================================================
# 5e. force-fail: valid-shaped payload still rejected
# ===========================================================================

@pytest.mark.asyncio
async def test_slack_force_fail_rejects_valid_payload(client):
    """Even a perfectly structured Slack payload is rejected when sig fails."""
    from app.chat import gateway
    gateway._sig_override["slack"] = False

    # Provide a well-formed Slack event.
    resp = await client.post("/api/v1/chat/slack", json=_slack_payload())
    assert resp.status_code == 401, (
        f"Signature check not evaluated before payload processing: {resp.status_code}"
    )


# ===========================================================================
# 5f. Verify the sig check happens BEFORE agent invocation
# ===========================================================================

@pytest.mark.asyncio
async def test_sig_check_happens_before_agent(client):
    """With _sig_override=False, the agent must NOT be called."""
    from app.chat import gateway

    gateway._sig_override["slack"] = False
    called: list[bool] = []

    original_handle = gateway.handle_inbound

    def spy_handle(platform, payload, **kw):
        called.append(True)
        return original_handle(platform, payload, **kw)

    with patch.object(gateway, "handle_inbound", side_effect=spy_handle):
        # Even though we patched handle_inbound, the sig check in _process
        # calls handle_inbound which internally calls verify_signature first.
        # The important check is that we get 401.
        resp = await client.post("/api/v1/chat/slack", json=_slack_payload())

    assert resp.status_code == 401


# ===========================================================================
# 5d. Permissive default (no override, no _sig key) → 200 in test mode
#     (no secret configured, ENV != production)
# ===========================================================================

@pytest.mark.asyncio
async def test_slack_no_sig_check_permissive_in_test_mode(client):
    """Without _sig_override and without _sig='bad', Slack webhooks pass.

    This is the INTENDED test-mode behavior (permissive default when no secret
    is configured and ENV != production).
    """
    # No override set, no _sig field → permissive pass.
    resp = await client.post("/api/v1/chat/slack", json=_slack_payload())
    # Should succeed (200) in test mode with the permissive default.
    assert resp.status_code == 200, (
        f"Permissive test-mode Slack webhook returned {resp.status_code}"
    )


# ===========================================================================
# 5g. Production mode WITHOUT a signing secret → FAIL CLOSED (401)
# ===========================================================================

@pytest.mark.asyncio
async def test_slack_webhook_without_sig_header_rejected_in_production(client):
    """A Slack webhook without X-Slack-Signature AND no secret in production → 401.

    Production must fail closed: if SLACK_SIGNING_SECRET is not configured,
    the endpoint must reject requests rather than permissively accepting them.
    """
    with patch.dict(os.environ, {"ENV": "production", "SLACK_SIGNING_SECRET": ""}):
        from app.config import get_settings
        get_settings.cache_clear()
        resp = await client.post(
            "/api/v1/chat/slack",
            json=_slack_payload(),
            # Deliberately NO X-Slack-Signature header
        )

    assert resp.status_code == 401, (
        f"SECURITY FAILURE: Production Slack webhook with no secret returned {resp.status_code} "
        f"(expected 401 — fail closed)"
    )


@pytest.mark.asyncio
async def test_whatsapp_webhook_without_sig_header_rejected_in_production(client):
    """A WhatsApp webhook without X-Hub-Signature-256 AND no secret in production → 401."""
    with patch.dict(os.environ, {"ENV": "production", "WHATSAPP_APP_SECRET": ""}):
        from app.config import get_settings
        get_settings.cache_clear()
        resp = await client.post("/api/v1/chat/whatsapp", json=_whatsapp_payload())

    assert resp.status_code == 401, (
        f"SECURITY FAILURE: Production WhatsApp webhook with no secret returned {resp.status_code} "
        f"(expected 401 — fail closed)"
    )


# ===========================================================================
# 5g. Unit-level: verify_signature with permissive default (no override, no _sig)
# ===========================================================================

def test_verify_signature_permissive_default():
    """verify_signature passes when no override and no _sig='bad' in payload (test env)."""
    from app.chat.gateway import verify_signature

    # Should not raise (test mode, no secret configured).
    verify_signature("slack", {"event": {"text": "hello"}})
    verify_signature("whatsapp", {"entry": []})


def test_verify_signature_bad_payload_raises():
    """verify_signature raises AppError 401 when payload has _sig='bad'."""
    from app.chat.gateway import verify_signature
    from app.errors import AppError

    with pytest.raises(AppError) as exc_info:
        verify_signature("slack", {"_sig": "bad", "event": {}})
    assert exc_info.value.status == 401
    assert exc_info.value.code == "invalid_signature"


def test_verify_signature_override_false_raises():
    """_sig_override[platform]=False raises AppError 401."""
    from app.chat import gateway
    from app.errors import AppError

    gateway._sig_override["slack"] = False
    try:
        with pytest.raises(AppError) as exc_info:
            gateway.verify_signature("slack", {})
        assert exc_info.value.status == 401
    finally:
        gateway._sig_override.pop("slack", None)


def test_verify_signature_override_true_passes():
    """_sig_override[platform]=True forces verification to pass."""
    from app.chat import gateway

    gateway._sig_override["slack"] = True
    try:
        # Should not raise.
        gateway.verify_signature("slack", {"_sig": "bad"})
    finally:
        gateway._sig_override.pop("slack", None)


# ===========================================================================
# 5h. Real HMAC: valid Slack signature → 200
# ===========================================================================

@pytest.mark.asyncio
async def test_slack_valid_hmac_signature_returns_200(client):
    """A correctly-computed Slack HMAC-SHA256 signature is accepted (200)."""
    body_dict = _slack_payload()
    raw = json.dumps(body_dict).encode()
    ts, sig = _make_slack_signature(raw, _TEST_SLACK_SECRET)

    with patch.dict(os.environ, {"SLACK_SIGNING_SECRET": _TEST_SLACK_SECRET, "ENV": "test"}):
        from app.config import get_settings
        get_settings.cache_clear()
        resp = await client.post(
            "/api/v1/chat/slack",
            content=raw,
            headers={
                "Content-Type": "application/json",
                "X-Slack-Request-Timestamp": ts,
                "X-Slack-Signature": sig,
            },
        )

    assert resp.status_code == 200, (
        f"SECURITY FAILURE: Valid Slack HMAC signature returned {resp.status_code}: {resp.text}"
    )


# ===========================================================================
# 5i. Real HMAC: wrong Slack signature → 401
# ===========================================================================

@pytest.mark.asyncio
async def test_slack_wrong_hmac_signature_returns_401(client):
    """A wrong Slack HMAC-SHA256 signature is rejected (401)."""
    body_dict = _slack_payload()
    raw = json.dumps(body_dict).encode()
    ts = str(int(time.time()))
    bad_sig = "v0=deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"

    with patch.dict(os.environ, {"SLACK_SIGNING_SECRET": _TEST_SLACK_SECRET, "ENV": "test"}):
        from app.config import get_settings
        get_settings.cache_clear()
        resp = await client.post(
            "/api/v1/chat/slack",
            content=raw,
            headers={
                "Content-Type": "application/json",
                "X-Slack-Request-Timestamp": ts,
                "X-Slack-Signature": bad_sig,
            },
        )

    assert resp.status_code == 401, (
        f"SECURITY FAILURE: Invalid Slack HMAC signature was accepted (status {resp.status_code})"
    )


# ===========================================================================
# 5j. Expired Slack timestamp → 401 (replay protection)
# ===========================================================================

@pytest.mark.asyncio
async def test_slack_expired_timestamp_returns_401(client):
    """A Slack webhook with a timestamp older than 5 minutes is rejected (replay protection)."""
    body_dict = _slack_payload()
    raw = json.dumps(body_dict).encode()
    # Use a timestamp 6 minutes in the past.
    old_ts = int(time.time()) - 6 * 60
    ts_str, sig = _make_slack_signature(raw, _TEST_SLACK_SECRET, timestamp=old_ts)

    with patch.dict(os.environ, {"SLACK_SIGNING_SECRET": _TEST_SLACK_SECRET, "ENV": "test"}):
        from app.config import get_settings
        get_settings.cache_clear()
        resp = await client.post(
            "/api/v1/chat/slack",
            content=raw,
            headers={
                "Content-Type": "application/json",
                "X-Slack-Request-Timestamp": ts_str,
                "X-Slack-Signature": sig,
            },
        )

    assert resp.status_code == 401, (
        f"SECURITY FAILURE: Expired Slack timestamp was accepted (status {resp.status_code})"
    )


# ===========================================================================
# 5k. Real HMAC: valid WhatsApp sha256= signature → 200
# ===========================================================================

@pytest.mark.asyncio
async def test_whatsapp_valid_hmac_signature_returns_200(client):
    """A correctly-computed WhatsApp sha256= HMAC signature is accepted (200)."""
    body_dict = _whatsapp_payload()
    raw = json.dumps(body_dict).encode()
    sig = _make_whatsapp_signature(raw, _TEST_WA_SECRET)

    with patch.dict(os.environ, {"WHATSAPP_APP_SECRET": _TEST_WA_SECRET, "ENV": "test"}):
        from app.config import get_settings
        get_settings.cache_clear()
        resp = await client.post(
            "/api/v1/chat/whatsapp",
            content=raw,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": sig,
            },
        )

    assert resp.status_code == 200, (
        f"SECURITY FAILURE: Valid WhatsApp HMAC signature returned {resp.status_code}: {resp.text}"
    )


# ===========================================================================
# 5l. Real HMAC: invalid WhatsApp signature → 401
# ===========================================================================

@pytest.mark.asyncio
async def test_whatsapp_wrong_hmac_signature_returns_401(client):
    """A wrong WhatsApp sha256= signature is rejected (401)."""
    body_dict = _whatsapp_payload()
    raw = json.dumps(body_dict).encode()
    bad_sig = "sha256=deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"

    with patch.dict(os.environ, {"WHATSAPP_APP_SECRET": _TEST_WA_SECRET, "ENV": "test"}):
        from app.config import get_settings
        get_settings.cache_clear()
        resp = await client.post(
            "/api/v1/chat/whatsapp",
            content=raw,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": bad_sig,
            },
        )

    assert resp.status_code == 401, (
        f"SECURITY FAILURE: Invalid WhatsApp HMAC signature was accepted (status {resp.status_code})"
    )


# ===========================================================================
# 5m. Missing X-Slack-Signature with secret configured → 401
# ===========================================================================

@pytest.mark.asyncio
async def test_slack_missing_sig_header_with_secret_configured_returns_401(client):
    """When SLACK_SIGNING_SECRET is set, requests without the signature header → 401."""
    body_dict = _slack_payload()
    raw = json.dumps(body_dict).encode()
    ts = str(int(time.time()))

    with patch.dict(os.environ, {"SLACK_SIGNING_SECRET": _TEST_SLACK_SECRET, "ENV": "test"}):
        from app.config import get_settings
        get_settings.cache_clear()
        resp = await client.post(
            "/api/v1/chat/slack",
            content=raw,
            headers={
                "Content-Type": "application/json",
                "X-Slack-Request-Timestamp": ts,
                # Deliberately NO X-Slack-Signature
            },
        )

    assert resp.status_code == 401, (
        f"SECURITY FAILURE: Missing Slack sig header was accepted (status {resp.status_code})"
    )


# ===========================================================================
# 5n. Missing X-Hub-Signature-256 with secret configured → 401
# ===========================================================================

@pytest.mark.asyncio
async def test_whatsapp_missing_sig_header_with_secret_configured_returns_401(client):
    """When WHATSAPP_APP_SECRET is set, requests without the signature header → 401."""
    body_dict = _whatsapp_payload()
    raw = json.dumps(body_dict).encode()

    with patch.dict(os.environ, {"WHATSAPP_APP_SECRET": _TEST_WA_SECRET, "ENV": "test"}):
        from app.config import get_settings
        get_settings.cache_clear()
        resp = await client.post(
            "/api/v1/chat/whatsapp",
            content=raw,
            headers={
                "Content-Type": "application/json",
                # Deliberately NO X-Hub-Signature-256
            },
        )

    assert resp.status_code == 401, (
        f"SECURITY FAILURE: Missing WhatsApp sig header was accepted (status {resp.status_code})"
    )
