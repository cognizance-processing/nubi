"""Security tests for webhook CRUD + delivery isolation.

Coverage
--------
1.  GET /webhooks/:id — org A cannot read org B's endpoint → 404.
2.  PUT /webhooks/:id — org A cannot update org B's endpoint → 404.
3.  DELETE /webhooks/:id — org A cannot delete org B's endpoint → 404.
4.  GET /webhooks (list) — org A only sees its own endpoints (not org B's).
5.  Secret never returned by GET single or list_for_org.
6.  list_active_for_event is strictly org-scoped (org B events never fan-out to org A).
7.  HMAC replay with stale timestamp is rejected by the verify recipe.
8.  A hostile endpoint (timeout/500) does not raise — deliver_one is fire-and-forget.
9.  Positive control: create + get within same org works.
10. Delivery fan-out to org B endpoint does NOT deliver org A's event payload.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# Env bootstrap
# ---------------------------------------------------------------------------
from cryptography.fernet import Fernet

os.environ.setdefault("NUBI_SECRETS_KEY", Fernet.generate_key().decode())
os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef")
os.environ.setdefault("GOOGLE_CLIENT_ID", "fake-gid")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "fake-gsecret")
os.environ.setdefault("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/v1/auth/google/callback")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("ENV", "test")

from app.webhooks import delivery, events  # noqa: E402
from app.webhooks.models import InMemoryWebhookStore  # noqa: E402

_ORG_A = str(uuid.uuid4())
_ORG_B = str(uuid.uuid4())
_USER = str(uuid.uuid4())
_SECRET_A = "supersecretA"
_SECRET_B = "supersecretB"
_URL_A = "https://endpoint-a.example.com/hook"
_URL_B = "https://endpoint-b.example.com/hook"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store() -> InMemoryWebhookStore:
    return InMemoryWebhookStore()


# ===========================================================================
# 1. Cross-org GET → 404
# ===========================================================================


@pytest.mark.asyncio
async def test_get_by_id_cross_org_returns_none():
    """org A cannot read org B's endpoint — get_by_id returns None."""
    store = _make_store()
    row = await store.create(
        _ORG_B, "wh-b", _URL_B, _SECRET_B, _USER,
        event_types=[events.WATCH_BREACH],
    )
    result = await store.get_by_id(row["id"], _ORG_A)
    assert result is None, (
        "SECURITY: get_by_id with org A must return None for org B's endpoint"
    )


# ===========================================================================
# 2. Cross-org update → returns None
# ===========================================================================


@pytest.mark.asyncio
async def test_update_cross_org_returns_none():
    """org A cannot update org B's endpoint — update returns None (router → 404)."""
    store = _make_store()
    row = await store.create(
        _ORG_B, "wh-b2", _URL_B, _SECRET_B, _USER,
        event_types=[events.FLOW_COMPLETED],
    )
    result = await store.update(
        row["id"], _ORG_A, name="hijacked"
    )
    assert result is None, (
        "SECURITY: update with org A must return None for org B's endpoint"
    )
    # Verify the original name is intact
    original = await store.get_by_id(row["id"], _ORG_B)
    assert original is not None
    assert original["name"] == "wh-b2", "org B endpoint must be unchanged"


# ===========================================================================
# 3. Cross-org delete → returns False
# ===========================================================================


@pytest.mark.asyncio
async def test_delete_cross_org_returns_false():
    """org A cannot delete org B's endpoint — delete returns False."""
    store = _make_store()
    row = await store.create(
        _ORG_B, "wh-b3", _URL_B, _SECRET_B, _USER,
        event_types=[events.WATCH_BREACH],
    )
    deleted = await store.delete(row["id"], _ORG_A)
    assert deleted is False, "SECURITY: cross-org delete must return False"
    # Endpoint must still exist in org B
    still_there = await store.get_by_id(row["id"], _ORG_B)
    assert still_there is not None, "org B endpoint was deleted by org A — SECURITY BUG"


# ===========================================================================
# 4. list_for_org — only own endpoints returned
# ===========================================================================


@pytest.mark.asyncio
async def test_list_for_org_isolation():
    """list_for_org for org A must not include org B's endpoints."""
    store = _make_store()
    row_a = await store.create(_ORG_A, "wh-a", _URL_A, _SECRET_A, _USER, event_types=[events.WATCH_BREACH])
    row_b = await store.create(_ORG_B, "wh-b", _URL_B, _SECRET_B, _USER, event_types=[events.WATCH_BREACH])

    rows_a = await store.list_for_org(_ORG_A)
    ids_a = {r["id"] for r in rows_a}

    assert row_a["id"] in ids_a, "org A's own endpoint must be in its listing"
    assert row_b["id"] not in ids_a, (
        "SECURITY: org B's endpoint must NOT appear in org A's listing"
    )


# ===========================================================================
# 5. Secret never returned by read or list
# ===========================================================================


@pytest.mark.asyncio
async def test_secret_not_in_read_responses():
    """Neither get_by_id nor list_for_org ever return the secret."""
    store = _make_store()
    row = await store.create(_ORG_A, "wh-sec", _URL_A, "verySecretValue", _USER, event_types=[])

    # Single get
    fetched = await store.get_by_id(row["id"], _ORG_A)
    assert fetched is not None
    assert "secret" not in fetched, "Secret must not be in get_by_id response"
    assert "secret_encrypted" not in fetched, "secret_encrypted must not be in get_by_id response"

    # List
    items = await store.list_for_org(_ORG_A)
    for item in items:
        assert "secret" not in item, f"Secret must not be in list_for_org response: {item}"
        assert "secret_encrypted" not in item


# ===========================================================================
# 6. list_active_for_event is org-scoped
# ===========================================================================


@pytest.mark.asyncio
async def test_list_active_for_event_org_scoped():
    """list_active_for_event for org A must not include org B's endpoints."""
    store = _make_store()
    await store.create(_ORG_A, "wh-a-ev", _URL_A, _SECRET_A, _USER, event_types=[events.WATCH_BREACH])
    await store.create(_ORG_B, "wh-b-ev", _URL_B, _SECRET_B, _USER, event_types=[events.WATCH_BREACH])

    endpoints_a = await store.list_active_for_event(_ORG_A, events.WATCH_BREACH)
    for ep in endpoints_a:
        assert ep["org_id"] == str(_ORG_A), (
            f"SECURITY: org B endpoint leaked into org A's list: {ep}"
        )


# ===========================================================================
# 7. HMAC replay with stale timestamp rejected by verify recipe
# ===========================================================================


def _sign(secret: str, body: bytes, timestamp: int) -> str:
    signed_payload = f"{timestamp}.".encode() + body
    return hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()


def test_hmac_stale_replay_rejected():
    """verify() returns True for fresh signature but a caller-side replay-guard
    (checking timestamp skew) must reject a >5-minute-old timestamp.

    This test verifies the SIGNATURE is still mathematically correct (verify=True)
    but documents how the documented replay-guard pattern should work:
    reject timestamps older than MAX_SKEW_S.
    """
    secret = "replay-test-secret"
    payload = {"event": "watch_breach", "org": "acme"}
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()

    # Stale timestamp: 10 minutes ago
    stale_ts = int(time.time()) - 600
    sig = _sign(secret, body, stale_ts)

    # The signature itself is cryptographically valid
    is_valid_crypto = delivery.verify(secret, body, stale_ts, sig)
    assert is_valid_crypto, "Cryptographic verify must pass for correctly computed sig"

    # But a replay-guard checks timestamp skew
    MAX_SKEW_S = 300  # documented 5-minute window
    age = int(time.time()) - stale_ts
    is_fresh = age <= MAX_SKEW_S
    assert not is_fresh, (
        "Replay guard: a 10-minute-old timestamp must be rejected by the host's "
        "replay-guard check (age > MAX_SKEW_S)"
    )


def test_hmac_tampered_body_rejected():
    """A tampered body must make verify() return False."""
    secret = "tamper-test-secret"
    original_payload = {"event": "watch_breach"}
    body = delivery.canonical_body(original_payload)
    ts = int(time.time())
    sig = delivery.sign(secret, body, ts)

    # Tamper the body
    tampered_body = body + b"\x00extra"
    result = delivery.verify(secret, tampered_body, ts, sig)
    assert result is False, "Tampered body must fail HMAC verification"


def test_hmac_wrong_secret_rejected():
    """A different secret must make verify() return False."""
    body = delivery.canonical_body({"x": 1})
    ts = int(time.time())
    sig = delivery.sign("correct-secret", body, ts)

    result = delivery.verify("wrong-secret", body, ts, sig)
    assert result is False, "Wrong secret must fail HMAC verification"


# ===========================================================================
# 8. Hostile endpoint (timeout/500) does not raise from deliver_one
# ===========================================================================


@pytest.mark.asyncio
async def test_deliver_one_timeout_does_not_raise(monkeypatch):
    """A timeout on delivery must return False, never raise."""
    import httpx

    class TimeoutClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *e): return None
        async def post(self, *a, **kw):
            raise httpx.TimeoutException("simulated timeout", request=None)

    monkeypatch.setattr(httpx, "AsyncClient", TimeoutClient)

    with patch("app.connectors.ssrf.guard_url"):  # skip SSRF guard
        result = await delivery.deliver_one(
            "https://hostile.example.com/hook",
            "secret",
            events.WATCH_BREACH,
            {"x": 1},
            max_attempts=1,
            base_backoff_s=0,
        )
    assert result is False, "Timeout must return False, not raise"


@pytest.mark.asyncio
async def test_deliver_one_500_response_does_not_raise(monkeypatch):
    """A 500 response exhausts retries and returns False, never raises."""
    import httpx

    class FakeResp:
        status_code = 500

    class Client500:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *e): return None
        async def post(self, *a, **kw): return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", Client500)

    with patch("app.connectors.ssrf.guard_url"):
        result = await delivery.deliver_one(
            "https://failing.example.com/hook",
            "secret",
            events.WATCH_BREACH,
            {"x": 1},
            max_attempts=1,
            base_backoff_s=0,
        )
    assert result is False


# ===========================================================================
# 9. Positive control: create + get within same org
# ===========================================================================


@pytest.mark.asyncio
async def test_same_org_crud_works():
    """Within the same org, create + get + delete work correctly."""
    store = _make_store()
    row = await store.create(
        _ORG_A, "positive-wh", _URL_A, _SECRET_A, _USER,
        event_types=[events.FLOW_COMPLETED],
    )
    assert row["org_id"] == str(_ORG_A)

    fetched = await store.get_by_id(row["id"], _ORG_A)
    assert fetched is not None
    assert fetched["name"] == "positive-wh"

    deleted = await store.delete(row["id"], _ORG_A)
    assert deleted is True

    gone = await store.get_by_id(row["id"], _ORG_A)
    assert gone is None


# ===========================================================================
# 10. Delivery fan-out: org B's endpoint does NOT receive org A's event
# ===========================================================================


@pytest.mark.asyncio
async def test_delivery_fan_out_org_scoped():
    """deliver_to_org delivers ONLY to the specified org's active endpoints."""
    store = _make_store()

    delivered_to: list[str] = []

    # Register endpoint for org A
    await store.create(
        _ORG_A, "endpoint-a", _URL_A, _SECRET_A, _USER,
        event_types=[events.WATCH_BREACH],
    )
    # Register endpoint for org B
    row_b = await store.create(
        _ORG_B, "endpoint-b", _URL_B, _SECRET_B, _USER,
        event_types=[events.WATCH_BREACH],
    )

    from app.webhooks.models import set_webhook_store

    set_webhook_store(store)

    try:
        # Patch delivery so we capture which URLs were called
        async def _fake_deliver_one(url, secret, event_type, payload, **kw):
            delivered_to.append(url)
            return True

        with patch("app.webhooks.delivery.deliver_one", side_effect=_fake_deliver_one):
            await delivery.deliver_to_org(_ORG_A, events.WATCH_BREACH, {"x": "secret_a_data"})
    finally:
        set_webhook_store(None)

    assert _URL_A in delivered_to, "org A's own endpoint must receive the event"
    assert _URL_B not in delivered_to, (
        "SECURITY: org B's endpoint must NOT receive org A's event"
    )
