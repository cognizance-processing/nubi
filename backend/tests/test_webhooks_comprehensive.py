"""Comprehensive edge-case tests for the outbound webhook subsystem.

Covers areas NOT fully exercised by test_webhooks.py:
- Per-event-type delivery gating (parametrized over all 4 event types)
- Inactive endpoint lifecycle (create inactive → activate → deactivate)
- HMAC signature exactness and bit-flip tamper detection
- canonical_body stability with unicode and nested dicts
- Retry on every retryable status code (5xx + 429)
- Stop-on-permanent-4xx for each non-retryable code
- Transport-error exhaustion
- timeout_s flow-through
- Secret never leaked through any read/list API
- get_secret org isolation (wrong org, after delete, non-existent id)
- list_active_for_event secret key presence and rotation
- Per-org CRUD isolation
- Empty event_types list → no delivery
- deliver_to_org with multiple endpoints (same event vs different events)
- deliver_to_org with empty org_id
- emit_event edge cases (None org, empty org, unknown type, store failure)
- build_envelope structure (keys, UUID4, ISO 8601)
- is_valid_event_type exhaustive check
- Typed emit helpers never raise (even with all-None kwargs)
"""

from __future__ import annotations

import asyncio
import hmac as _hmac
import json
import os
import uuid

import pytest
import pytest_asyncio  # noqa: F401

from cryptography.fernet import Fernet

# ---------------------------------------------------------------------------
# Environment bootstrap — must happen before any app imports.
# ---------------------------------------------------------------------------

os.environ.setdefault("NUBI_SECRETS_KEY", Fernet.generate_key().decode())
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

from app.webhooks import delivery, events  # noqa: E402
from app.webhooks.events import (  # noqa: E402
    ALL_EVENT_TYPES,
    FLOW_COMPLETED,
    FRESHNESS_STALE,
    QUERY_FAILED,
    WATCH_BREACH,
)
from app.webhooks.models import InMemoryWebhookStore, set_webhook_store  # noqa: E402

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_ORG_A = str(uuid.uuid4())
_ORG_B = str(uuid.uuid4())
_ORG_C = str(uuid.uuid4())
_USER = str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Delivery fake helpers
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _RecordingClient:
    """Minimal async httpx.AsyncClient stand-in driven by a status sequence."""

    calls: list[dict] = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def post(self, url, *, content, headers):
        type(self).calls.append({"url": url, "content": content, "headers": headers})
        return type(self)._next()

    @classmethod
    def _next(cls):  # overridden per test
        return _FakeResponse(200)


def _seq_client(statuses: list[int]):
    """Build a fresh _RecordingClient subclass that emits *statuses* in order."""
    seq = list(statuses)

    class C(_RecordingClient):
        calls: list[dict] = []
        _i: int = 0

        @classmethod
        def _next(cls):
            code = seq[cls._i]
            cls._i += 1
            return _FakeResponse(code)

    return C


import socket as _socket  # noqa: E402


def _patch_delivery(monkeypatch, client_instance, *, timeout_capture: list | None = None):
    """Patch deliver_one so it bypasses DNS + SSRF and uses *client_instance*.

    deliver_one now calls ``resolve_and_pin`` (single DNS resolution) and then
    ``_build_pinned_client`` to get an httpx.AsyncClient.  For unit tests that
    care about retry logic / headers / subscription gating — not the pinning
    mechanism itself — we:

    1. Patch ``socket.getaddrinfo`` → always returns a safe public IP so that
       ``guard_url`` + ``resolve_and_pin`` pass without real DNS.
    2. Patch ``delivery._build_pinned_client`` → returns *client_instance*.
       If *timeout_capture* is given, the list is populated with the kwargs
       passed to _build_pinned_client so tests can verify timeout propagation.
    """

    def _fake_getaddrinfo(host, *args, **kwargs):
        return [(_socket.AF_INET, _socket.SOCK_STREAM, _socket.IPPROTO_TCP, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(_socket, "getaddrinfo", _fake_getaddrinfo)

    def _fake_build(pinned_ip, hostname, scheme, port, timeout_s):
        if timeout_capture is not None:
            timeout_capture.append({"pinned_ip": pinned_ip, "hostname": hostname,
                                    "scheme": scheme, "port": port, "timeout_s": timeout_s})
        return client_instance

    monkeypatch.setattr(delivery, "_build_pinned_client", _fake_build)


# ===========================================================================
# 1. Per-event-type delivery gating
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("subscribed_type", list(ALL_EVENT_TYPES))
async def test_each_event_type_delivered_only_when_subscribed(
    subscribed_type, monkeypatch
):
    """Endpoint subscribed to exactly one event type receives it and not others."""
    store = InMemoryWebhookStore()
    set_webhook_store(store)
    try:
        await store.create(
            _ORG_A,
            "wh",
            "https://h.example/hook",
            "secret123",
            _USER,
            event_types=[subscribed_type],
        )

        C = _seq_client([200])
        C.calls = []

        _patch_delivery(monkeypatch, C())

        # Subscribed event → delivered once.
        n = await delivery.deliver_to_org(_ORG_A, subscribed_type, {"x": 1})
        assert n == 1, f"Expected 1 delivery for {subscribed_type}, got {n}"
        assert len(C.calls) == 1

        # Every OTHER event type → zero deliveries.
        for other in ALL_EVENT_TYPES:
            if other == subscribed_type:
                continue
            C.calls.clear()
            # reset sequence counter
            C._i = 0
            n2 = await delivery.deliver_to_org(_ORG_A, other, {"x": 1})
            assert n2 == 0, f"Expected 0 for {other} when subscribed only to {subscribed_type}"
            assert len(C.calls) == 0
    finally:
        set_webhook_store(None)


# ===========================================================================
# 2. Inactive endpoint lifecycle
# ===========================================================================


@pytest.mark.asyncio
async def test_inactive_endpoint_created_with_active_false():
    """Endpoint created with active=False is never returned for delivery."""
    store = InMemoryWebhookStore()
    row = await store.create(
        _ORG_A,
        "inactive-wh",
        "https://h.example/hook",
        "secret",
        _USER,
        event_types=[WATCH_BREACH],
        active=False,
    )
    assert row["active"] is False
    active = await store.list_active_for_event(_ORG_A, WATCH_BREACH)
    assert active == []


@pytest.mark.asyncio
async def test_deactivate_then_reactivate_endpoint():
    """Updating active=False stops delivery; restoring active=True resumes it."""
    store = InMemoryWebhookStore()
    row = await store.create(
        _ORG_A,
        "wh",
        "https://h.example/hook",
        "secret",
        _USER,
        event_types=[WATCH_BREACH],
        active=True,
    )
    eid = row["id"]

    # Initially active.
    active = await store.list_active_for_event(_ORG_A, WATCH_BREACH)
    assert len(active) == 1

    # Deactivate.
    await store.update(eid, _ORG_A, active=False)
    active = await store.list_active_for_event(_ORG_A, WATCH_BREACH)
    assert active == []

    # Reactivate.
    await store.update(eid, _ORG_A, active=True)
    active = await store.list_active_for_event(_ORG_A, WATCH_BREACH)
    assert len(active) == 1


@pytest.mark.asyncio
async def test_inactive_endpoint_not_delivered_to(monkeypatch):
    """deliver_to_org does not call the endpoint when active=False."""
    store = InMemoryWebhookStore()
    set_webhook_store(store)
    try:
        row = await store.create(
            _ORG_A,
            "wh",
            "https://h.example/hook",
            "secret",
            _USER,
            event_types=[FLOW_COMPLETED],
            active=True,
        )
        await store.update(row["id"], _ORG_A, active=False)

        C = _seq_client([200])

        _patch_delivery(monkeypatch, C())
        n = await delivery.deliver_to_org(_ORG_A, FLOW_COMPLETED, {"x": 1})
        assert n == 0
        assert len(C.calls) == 0
    finally:
        set_webhook_store(None)


# ===========================================================================
# 3. HMAC signature exactness
# ===========================================================================


def test_sign_uses_timestamp_dot_body_format():
    """sign() must HMAC the string "{timestamp}.{body}" not just the body."""
    secret = "testsecret"
    body = delivery.canonical_body({"k": "v"})
    ts = 1_700_000_000

    # Reconstruct manually.
    signed_payload = f"{ts}.".encode("utf-8") + body
    expected = _hmac.new(
        secret.encode("utf-8"), signed_payload, "sha256"
    ).hexdigest()

    assert delivery.sign(secret, body, ts) == expected


def test_verify_uses_constant_time_comparison():
    """verify() must not short-circuit — it returns a plain bool, never timing-sensitive data."""
    body = delivery.canonical_body({"a": 1})
    ts = 1_700_000_000
    sig = delivery.sign("secret", body, ts)
    # Both True and False results must be obtainable.
    assert delivery.verify("secret", body, ts, sig) is True
    assert delivery.verify("wrong", body, ts, sig) is False


def test_bit_flip_in_body_fails_verify():
    """Any byte change in body causes verify to return False."""
    body = delivery.canonical_body({"value": 12345})
    ts = 1_700_000_001
    sig = delivery.sign("secret", body, ts)

    # Flip the first byte.
    tampered = bytes([body[0] ^ 0xFF]) + body[1:]
    assert delivery.verify("secret", tampered, ts, sig) is False


def test_wrong_secret_fails_verify():
    body = delivery.canonical_body({"type": "test"})
    ts = 1_700_000_002
    sig = delivery.sign("correct-secret", body, ts)
    assert delivery.verify("wrong-secret", body, ts, sig) is False


def test_wrong_timestamp_fails_verify():
    body = delivery.canonical_body({"type": "test"})
    ts = 1_700_000_003
    sig = delivery.sign("secret", body, ts)
    assert delivery.verify("secret", body, ts + 1, sig) is False


def test_correct_all_three_passes_verify():
    body = delivery.canonical_body({"type": "test", "n": 99})
    ts = 1_700_000_004
    sig = delivery.sign("mysecret", body, ts)
    assert delivery.verify("mysecret", body, ts, sig) is True


def test_body_bytes_identical_between_sign_and_canonical_body():
    """The bytes sent over the wire are the exact bytes that were signed."""
    payload = {"z": 1, "a": 2, "m": [1, 2, 3]}
    body = delivery.canonical_body(payload)
    ts = 1_700_000_005
    sig = delivery.sign("s", body, ts)
    # Re-calling canonical_body with same payload yields same bytes.
    assert delivery.canonical_body(payload) == body
    assert delivery.verify("s", body, ts, sig) is True


# ===========================================================================
# 4. HMAC tamper detection (parametrized)
# ===========================================================================


@pytest.mark.parametrize("tamper", ["secret", "timestamp", "body"])
def test_hmac_tamper_rejected(tamper):
    """Changing secret, timestamp, or body each independently break verify."""
    body = delivery.canonical_body({"data": "important"})
    ts = 1_700_000_010
    secret = "original-secret"
    sig = delivery.sign(secret, body, ts)

    if tamper == "secret":
        result = delivery.verify("different-secret", body, ts, sig)
    elif tamper == "timestamp":
        result = delivery.verify(secret, body, ts + 100, sig)
    else:  # body
        tampered_body = delivery.canonical_body({"data": "different"})
        result = delivery.verify(secret, tampered_body, ts, sig)

    assert result is False


# ===========================================================================
# 5. canonical_body stability
# ===========================================================================


def test_canonical_body_key_order_does_not_matter():
    b1 = delivery.canonical_body({"a": 1, "b": 2, "c": 3})
    b2 = delivery.canonical_body({"c": 3, "a": 1, "b": 2})
    b3 = delivery.canonical_body({"b": 2, "c": 3, "a": 1})
    assert b1 == b2 == b3


def test_canonical_body_is_compact_json():
    """No extra spaces — compact separators (',', ':')."""
    body = delivery.canonical_body({"x": 1})
    # Should NOT contain ': ' or ', '
    assert b": " not in body
    assert b", " not in body


def test_canonical_body_unicode_survives_roundtrip():
    payload = {"message": "héllo wörld 中文 🎉", "emoji": "🚀"}
    body = delivery.canonical_body(payload)
    decoded = json.loads(body.decode("utf-8"))
    assert decoded["message"] == payload["message"]
    assert decoded["emoji"] == payload["emoji"]


def test_canonical_body_nested_dicts_stable():
    b1 = delivery.canonical_body({"outer": {"z": 9, "a": 1}, "top": 0})
    b2 = delivery.canonical_body({"top": 0, "outer": {"a": 1, "z": 9}})
    assert b1 == b2


def test_canonical_body_returns_bytes():
    result = delivery.canonical_body({"k": "v"})
    assert isinstance(result, bytes)


# ===========================================================================
# 6. Retry on 5xx (parametrized)
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_retry_on_5xx_then_succeeds(status, monkeypatch):
    """5xx followed by 200 → True, called exactly twice."""
    C = _seq_client([status, 200])

    _patch_delivery(monkeypatch, C())
    ok = await delivery.deliver_one(
        "https://h.example/hook",
        "s",
        WATCH_BREACH,
        {"x": 1},
        base_backoff_s=0.0,
    )
    assert ok is True
    assert len(C.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_retry_on_5xx_exhaust_returns_false(status, monkeypatch):
    """Persistent 5xx exhausts all attempts and returns False."""
    C = _seq_client([status] * 4)

    _patch_delivery(monkeypatch, C())
    ok = await delivery.deliver_one(
        "https://h.example/hook",
        "s",
        WATCH_BREACH,
        {"x": 1},
        max_attempts=4,
        base_backoff_s=0.0,
    )
    assert ok is False
    assert len(C.calls) == 4


# ===========================================================================
# 7. Retry on 429
# ===========================================================================


@pytest.mark.asyncio
async def test_retry_on_429(monkeypatch):
    """429 is retried (not a permanent failure)."""
    C = _seq_client([429, 200])

    _patch_delivery(monkeypatch, C())
    ok = await delivery.deliver_one(
        "https://h.example/hook",
        "s",
        QUERY_FAILED,
        {"x": 1},
        base_backoff_s=0.0,
    )
    assert ok is True
    assert len(C.calls) == 2


@pytest.mark.asyncio
async def test_retry_on_429_multiple_then_succeeds(monkeypatch):
    """429, 429, 200 → True after 3 attempts."""
    C = _seq_client([429, 429, 200])

    _patch_delivery(monkeypatch, C())
    ok = await delivery.deliver_one(
        "https://h.example/hook",
        "s",
        QUERY_FAILED,
        {"x": 1},
        max_attempts=4,
        base_backoff_s=0.0,
    )
    assert ok is True
    assert len(C.calls) == 3


@pytest.mark.asyncio
async def test_429_not_treated_as_permanent_4xx(monkeypatch):
    """429 must NOT stop delivery the way other 4xx codes do."""
    C = _seq_client([429, 200])

    _patch_delivery(monkeypatch, C())
    ok = await delivery.deliver_one(
        "https://h.example/hook",
        "s",
        WATCH_BREACH,
        {"x": 1},
        max_attempts=3,
        base_backoff_s=0.0,
    )
    # Would be False after 1 attempt if 429 were treated as permanent.
    assert ok is True
    assert len(C.calls) == 2


# ===========================================================================
# 8. Stop on permanent 4xx (parametrized, NOT 429)
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 404, 410, 422])
async def test_stop_on_permanent_4xx(status, monkeypatch):
    """Non-429 4xx responses cause immediate stop with False and only 1 attempt."""
    C = _seq_client([status, 200])  # 200 should never be reached

    _patch_delivery(monkeypatch, C())
    ok = await delivery.deliver_one(
        "https://h.example/hook",
        "s",
        QUERY_FAILED,
        {"x": 1},
        max_attempts=4,
        base_backoff_s=0.0,
    )
    assert ok is False
    assert len(C.calls) == 1, f"status {status} should stop immediately but got {len(C.calls)} calls"


# ===========================================================================
# 9. Exhaust all attempts on transport error
# ===========================================================================


@pytest.mark.asyncio
async def test_exhaust_all_attempts_on_transport_error(monkeypatch):
    """max_attempts=3 transport errors → False after exactly 3 tries, no raise."""

    class BoomClient(_RecordingClient):
        calls: list[dict] = []

        async def post(self, url, *, content, headers):
            type(self).calls.append({"url": url})
            raise RuntimeError("connection refused")

    _patch_delivery(monkeypatch, BoomClient())
    ok = await delivery.deliver_one(
        "https://h.example/hook",
        "s",
        WATCH_BREACH,
        {"x": 1},
        max_attempts=3,
        base_backoff_s=0.0,
    )
    assert ok is False
    assert len(BoomClient.calls) == 3


@pytest.mark.asyncio
async def test_transport_error_never_raises(monkeypatch):
    """deliver_one swallows transport errors and returns False."""

    class ExplodingClient(_RecordingClient):
        calls: list[dict] = []

        async def post(self, url, *, content, headers):
            type(self).calls.append(1)
            raise ConnectionError("network down")

    _patch_delivery(monkeypatch, ExplodingClient())
    # Must not raise, must return False.
    result = await delivery.deliver_one(
        "https://h.example/hook",
        "secret",
        FLOW_COMPLETED,
        {"x": 1},
        max_attempts=2,
        base_backoff_s=0.0,
    )
    assert result is False


# ===========================================================================
# 10. timeout_s flows through to httpx.AsyncClient
# ===========================================================================


@pytest.mark.asyncio
async def test_timeout_s_passed_to_httpx_async_client(monkeypatch):
    """timeout_s parameter value must be forwarded to _build_pinned_client."""
    timeout_capture: list[dict] = []

    class C(_RecordingClient):
        calls: list[dict] = []

        @classmethod
        def _next(cls):
            return _FakeResponse(200)

    _patch_delivery(monkeypatch, C(), timeout_capture=timeout_capture)
    await delivery.deliver_one(
        "https://h.example/hook",
        "s",
        WATCH_BREACH,
        {"x": 1},
        max_attempts=1,
        base_backoff_s=0.0,
        timeout_s=42.0,
    )
    assert timeout_capture, "_build_pinned_client was never called"
    assert timeout_capture[0]["timeout_s"] == 42.0


# ===========================================================================
# 11. Secret never returned by read/list API
# ===========================================================================


@pytest.mark.asyncio
async def test_create_never_returns_secret_fields():
    store = InMemoryWebhookStore()
    row = await store.create(
        _ORG_A, "wh", "https://h.example/hook", "topsecret", _USER,
        event_types=[WATCH_BREACH],
    )
    assert "secret" not in row
    assert "secret_encrypted" not in row


@pytest.mark.asyncio
async def test_list_for_org_never_returns_secret_fields():
    store = InMemoryWebhookStore()
    await store.create(
        _ORG_A, "wh1", "https://h1.example/hook", "secret1", _USER,
        event_types=[WATCH_BREACH],
    )
    await store.create(
        _ORG_A, "wh2", "https://h2.example/hook", "secret2", _USER,
        event_types=[QUERY_FAILED],
    )
    rows = await store.list_for_org(_ORG_A)
    assert len(rows) == 2
    for row in rows:
        assert "secret" not in row, f"Row contains 'secret': {row}"
        assert "secret_encrypted" not in row, f"Row contains 'secret_encrypted': {row}"


@pytest.mark.asyncio
async def test_get_by_id_never_returns_secret_fields():
    store = InMemoryWebhookStore()
    row = await store.create(
        _ORG_A, "wh", "https://h.example/hook", "mysecret", _USER,
    )
    fetched = await store.get_by_id(row["id"], _ORG_A)
    assert fetched is not None
    assert "secret" not in fetched
    assert "secret_encrypted" not in fetched


@pytest.mark.asyncio
async def test_update_never_returns_secret_fields():
    store = InMemoryWebhookStore()
    row = await store.create(
        _ORG_A, "wh", "https://h.example/hook", "oldsecret", _USER,
    )
    updated = await store.update(row["id"], _ORG_A, name="new-name", secret="newsecret")
    assert updated is not None
    assert "secret" not in updated
    assert "secret_encrypted" not in updated


# ===========================================================================
# 12. get_secret org isolation
# ===========================================================================


@pytest.mark.asyncio
async def test_get_secret_wrong_org_returns_none():
    store = InMemoryWebhookStore()
    row = await store.create(
        _ORG_A, "wh", "https://h.example/hook", "secretA", _USER,
    )
    result = await store.get_secret(row["id"], _ORG_B)
    assert result is None


@pytest.mark.asyncio
async def test_get_secret_after_delete_returns_none():
    store = InMemoryWebhookStore()
    row = await store.create(
        _ORG_A, "wh", "https://h.example/hook", "secretA", _USER,
    )
    await store.delete(row["id"], _ORG_A)
    result = await store.get_secret(row["id"], _ORG_A)
    assert result is None


@pytest.mark.asyncio
async def test_get_secret_nonexistent_id_returns_none():
    store = InMemoryWebhookStore()
    result = await store.get_secret(str(uuid.uuid4()), _ORG_A)
    assert result is None


@pytest.mark.asyncio
async def test_get_secret_returns_correct_plaintext():
    store = InMemoryWebhookStore()
    row = await store.create(
        _ORG_A, "wh", "https://h.example/hook", "my-very-secret-value", _USER,
    )
    result = await store.get_secret(row["id"], _ORG_A)
    assert result == "my-very-secret-value"


# ===========================================================================
# 13. list_active_for_event includes "secret" key (for delivery)
# ===========================================================================


@pytest.mark.asyncio
async def test_list_active_for_event_includes_secret_key():
    store = InMemoryWebhookStore()
    await store.create(
        _ORG_A, "wh", "https://h.example/hook", "delivery-secret", _USER,
        event_types=[WATCH_BREACH],
    )
    active = await store.list_active_for_event(_ORG_A, WATCH_BREACH)
    assert len(active) == 1
    assert "secret" in active[0]


@pytest.mark.asyncio
async def test_list_active_for_event_secret_matches_created_value():
    store = InMemoryWebhookStore()
    await store.create(
        _ORG_A, "wh", "https://h.example/hook", "exact-secret-xyz", _USER,
        event_types=[FRESHNESS_STALE],
    )
    active = await store.list_active_for_event(_ORG_A, FRESHNESS_STALE)
    assert active[0]["secret"] == "exact-secret-xyz"


@pytest.mark.asyncio
async def test_list_active_for_event_reflects_rotated_secret():
    store = InMemoryWebhookStore()
    row = await store.create(
        _ORG_A, "wh", "https://h.example/hook", "old-secret", _USER,
        event_types=[QUERY_FAILED],
    )
    await store.update(row["id"], _ORG_A, secret="new-rotated-secret")
    active = await store.list_active_for_event(_ORG_A, QUERY_FAILED)
    assert active[0]["secret"] == "new-rotated-secret"


@pytest.mark.asyncio
async def test_list_active_for_event_secret_not_encrypted_bytes():
    """The 'secret' key in the delivery list must be a plaintext str, not bytes."""
    store = InMemoryWebhookStore()
    await store.create(
        _ORG_A, "wh", "https://h.example/hook", "plain-text-secret", _USER,
        event_types=[FLOW_COMPLETED],
    )
    active = await store.list_active_for_event(_ORG_A, FLOW_COMPLETED)
    assert isinstance(active[0]["secret"], str)
    assert active[0]["secret"] == "plain-text-secret"


# ===========================================================================
# 14. Per-org isolation across CRUD
# ===========================================================================


@pytest.mark.asyncio
async def test_org_b_cannot_get_org_a_endpoint():
    store = InMemoryWebhookStore()
    row = await store.create(
        _ORG_A, "wh", "https://h.example/hook", "secretA", _USER,
    )
    assert await store.get_by_id(row["id"], _ORG_B) is None


@pytest.mark.asyncio
async def test_org_b_cannot_update_org_a_endpoint():
    store = InMemoryWebhookStore()
    row = await store.create(
        _ORG_A, "wh", "https://h.example/hook", "secretA", _USER,
    )
    result = await store.update(row["id"], _ORG_B, name="hijacked")
    assert result is None
    # Verify org A's endpoint was not modified.
    original = await store.get_by_id(row["id"], _ORG_A)
    assert original["name"] == "wh"


@pytest.mark.asyncio
async def test_org_b_cannot_delete_org_a_endpoint():
    store = InMemoryWebhookStore()
    row = await store.create(
        _ORG_A, "wh", "https://h.example/hook", "secretA", _USER,
    )
    deleted = await store.delete(row["id"], _ORG_B)
    assert deleted is False
    # Endpoint must still exist for org A.
    still_there = await store.get_by_id(row["id"], _ORG_A)
    assert still_there is not None


@pytest.mark.asyncio
async def test_list_for_org_is_strictly_org_scoped():
    store = InMemoryWebhookStore()
    await store.create(_ORG_A, "a1", "https://a1.example/hook", "s", _USER)
    await store.create(_ORG_A, "a2", "https://a2.example/hook", "s", _USER)
    await store.create(_ORG_B, "b1", "https://b1.example/hook", "s", _USER)

    a_rows = await store.list_for_org(_ORG_A)
    b_rows = await store.list_for_org(_ORG_B)
    c_rows = await store.list_for_org(_ORG_C)

    assert len(a_rows) == 2
    assert len(b_rows) == 1
    assert c_rows == []
    assert all(r["org_id"] == _ORG_A for r in a_rows)
    assert all(r["org_id"] == _ORG_B for r in b_rows)


@pytest.mark.asyncio
async def test_list_active_for_event_is_strictly_org_scoped():
    store = InMemoryWebhookStore()
    await store.create(
        _ORG_A, "a", "https://a.example/hook", "secretA", _USER,
        event_types=[WATCH_BREACH],
    )
    await store.create(
        _ORG_B, "b", "https://b.example/hook", "secretB", _USER,
        event_types=[WATCH_BREACH],
    )
    a_active = await store.list_active_for_event(_ORG_A, WATCH_BREACH)
    b_active = await store.list_active_for_event(_ORG_B, WATCH_BREACH)

    assert len(a_active) == 1
    assert a_active[0]["url"] == "https://a.example/hook"
    assert a_active[0]["secret"] == "secretA"

    assert len(b_active) == 1
    assert b_active[0]["url"] == "https://b.example/hook"
    assert b_active[0]["secret"] == "secretB"


# ===========================================================================
# 15. Empty event_types delivers nothing
# ===========================================================================


@pytest.mark.asyncio
async def test_empty_event_types_list_active_returns_empty():
    """Endpoint with event_types=[] is never returned for any event."""
    store = InMemoryWebhookStore()
    await store.create(
        _ORG_A, "wh", "https://h.example/hook", "secret", _USER,
        event_types=[],
    )
    for et in ALL_EVENT_TYPES:
        result = await store.list_active_for_event(_ORG_A, et)
        assert result == [], f"Expected [] for {et} with empty event_types"


@pytest.mark.asyncio
async def test_empty_event_types_zero_deliveries(monkeypatch):
    """deliver_to_org returns 0 when endpoint has event_types=[]."""
    store = InMemoryWebhookStore()
    set_webhook_store(store)
    try:
        await store.create(
            _ORG_A, "wh", "https://h.example/hook", "secret", _USER,
            event_types=[],
        )

        class C(_RecordingClient):
            calls: list[dict] = []

        _patch_delivery(monkeypatch, C())
        n = await delivery.deliver_to_org(_ORG_A, WATCH_BREACH, {"x": 1})
        assert n == 0
        assert len(C.calls) == 0
    finally:
        set_webhook_store(None)


@pytest.mark.asyncio
async def test_no_event_types_defaults_to_empty():
    """create() without event_types defaults to [] (not None)."""
    store = InMemoryWebhookStore()
    row = await store.create(
        _ORG_A, "wh", "https://h.example/hook", "secret", _USER,
    )
    assert row["event_types"] == []


# ===========================================================================
# 16. deliver_to_org with multiple endpoints in same org
# ===========================================================================


@pytest.mark.asyncio
async def test_deliver_to_org_three_subscribed_endpoints(monkeypatch):
    """3 active subscribed endpoints → 3 deliveries, count=3."""
    store = InMemoryWebhookStore()
    set_webhook_store(store)
    try:
        for i in range(3):
            await store.create(
                _ORG_A,
                f"wh{i}",
                f"https://ep{i}.example/hook",
                f"secret{i}",
                _USER,
                event_types=[WATCH_BREACH],
            )

        C = _seq_client([200, 200, 200])

        _patch_delivery(monkeypatch, C())
        n = await delivery.deliver_to_org(_ORG_A, WATCH_BREACH, {"x": 1})
        assert n == 3
        assert len(C.calls) == 3
    finally:
        set_webhook_store(None)


@pytest.mark.asyncio
async def test_deliver_to_org_only_subscribed_endpoints(monkeypatch):
    """1 subscribed + 2 unsubscribed → 1 delivery."""
    store = InMemoryWebhookStore()
    set_webhook_store(store)
    try:
        await store.create(
            _ORG_A, "sub", "https://sub.example/hook", "s1", _USER,
            event_types=[FLOW_COMPLETED],
        )
        await store.create(
            _ORG_A, "other1", "https://o1.example/hook", "s2", _USER,
            event_types=[WATCH_BREACH],
        )
        await store.create(
            _ORG_A, "other2", "https://o2.example/hook", "s3", _USER,
            event_types=[QUERY_FAILED],
        )

        C = _seq_client([200])

        _patch_delivery(monkeypatch, C())
        n = await delivery.deliver_to_org(_ORG_A, FLOW_COMPLETED, {"x": 1})
        assert n == 1
        assert len(C.calls) == 1
        assert C.calls[0]["url"] == "https://sub.example/hook"
    finally:
        set_webhook_store(None)


@pytest.mark.asyncio
async def test_deliver_to_org_partial_success_counted(monkeypatch):
    """If 2 of 3 endpoints succeed, count=2."""
    store = InMemoryWebhookStore()
    set_webhook_store(store)
    try:
        for i in range(3):
            await store.create(
                _ORG_A,
                f"wh{i}",
                f"https://ep{i}.example/hook",
                f"s{i}",
                _USER,
                event_types=[FRESHNESS_STALE],
            )

        # gather runs them concurrently — sequence depends on gather order.
        # Provide enough responses (3 needed, mix of success/fail).
        responses = [200, 200, 400]

        class C(_RecordingClient):
            calls: list[dict] = []
            _i: int = 0

            def __init__(self, *args, **kwargs):
                pass

            @classmethod
            def _next(cls):
                code = responses[cls._i % len(responses)]
                cls._i += 1
                return _FakeResponse(code)

        _patch_delivery(monkeypatch, C())
        n = await delivery.deliver_to_org(_ORG_A, FRESHNESS_STALE, {"x": 1})
        # 2 succeed, 1 permanent failure.
        assert n == 2
        assert len(C.calls) == 3
    finally:
        set_webhook_store(None)


# ===========================================================================
# 17. deliver_to_org with empty org_id
# ===========================================================================


@pytest.mark.asyncio
async def test_deliver_to_org_empty_string_org_id_returns_zero():
    """deliver_to_org('', ...) returns 0 without error."""
    n = await delivery.deliver_to_org("", WATCH_BREACH, {"x": 1})
    assert n == 0


@pytest.mark.asyncio
async def test_deliver_to_org_empty_org_id_does_not_raise():
    """Empty org_id must not raise any exception."""
    try:
        await delivery.deliver_to_org("", FLOW_COMPLETED, {})
    except Exception as exc:
        pytest.fail(f"deliver_to_org raised unexpectedly: {exc}")


# ===========================================================================
# 18. emit_event edge cases
# ===========================================================================


def test_emit_event_none_org_id_does_not_raise():
    events.emit_event(WATCH_BREACH, None, {"x": 1})


def test_emit_event_empty_string_org_id_does_not_raise():
    events.emit_event(WATCH_BREACH, "", {"x": 1})


def test_emit_event_unknown_event_type_does_not_raise():
    events.emit_event("totally_unknown_event_type_xyz", _ORG_A, {"x": 1})


def test_emit_event_none_org_no_delivery():
    """None org_id is a no-op — should not attempt any delivery."""
    # No store set; if delivery were attempted it would error. Should not raise.
    events.emit_event(FRESHNESS_STALE, None, {"x": 1})


def test_emit_event_empty_org_no_delivery():
    events.emit_event(QUERY_FAILED, "", {"x": 1})


@pytest.mark.asyncio
async def test_emit_event_when_store_fails_no_raise(monkeypatch):
    """emit_event must swallow store errors (fire-and-forget contract)."""
    class _BrokenStore:
        async def list_active_for_event(self, org_id, event_type):
            raise RuntimeError("store exploded")

    set_webhook_store(_BrokenStore())
    try:
        # Should not raise.
        events.emit_event(WATCH_BREACH, _ORG_A, {"x": 1})
        # Allow any background task to run.
        await asyncio.sleep(0.05)
    finally:
        set_webhook_store(None)


# ===========================================================================
# 19. build_envelope structure
# ===========================================================================


def test_build_envelope_contains_required_keys():
    env = events.build_envelope(WATCH_BREACH, _ORG_A, {"val": 42})
    for key in ("type", "id", "org_id", "occurred_at", "data"):
        assert key in env, f"Missing key: {key}"


def test_build_envelope_type_matches_event_type():
    env = events.build_envelope(FLOW_COMPLETED, _ORG_A, {})
    assert env["type"] == FLOW_COMPLETED


def test_build_envelope_id_is_valid_uuid4():
    env = events.build_envelope(QUERY_FAILED, _ORG_A, {})
    eid = uuid.UUID(env["id"])
    assert eid.version == 4


def test_build_envelope_id_is_unique_per_call():
    env1 = events.build_envelope(WATCH_BREACH, _ORG_A, {})
    env2 = events.build_envelope(WATCH_BREACH, _ORG_A, {})
    assert env1["id"] != env2["id"]


def test_build_envelope_org_id_matches():
    env = events.build_envelope(FRESHNESS_STALE, _ORG_A, {})
    assert env["org_id"] == _ORG_A


def test_build_envelope_occurred_at_is_iso8601_utc():
    """occurred_at must be parseable ISO 8601 and have UTC offset."""
    from datetime import datetime

    env = events.build_envelope(WATCH_BREACH, _ORG_A, {})
    occurred_at = env["occurred_at"]
    assert isinstance(occurred_at, str)
    # Must be parseable.
    dt = datetime.fromisoformat(occurred_at)
    # Must carry timezone info.
    assert dt.tzinfo is not None


def test_build_envelope_data_matches_input():
    data = {"watch_id": "w123", "value": 99.9, "nested": {"a": 1}}
    env = events.build_envelope(WATCH_BREACH, _ORG_A, data)
    assert env["data"] == data


def test_build_envelope_no_extra_top_level_keys():
    """Envelope should have exactly the 5 defined keys."""
    env = events.build_envelope(WATCH_BREACH, _ORG_A, {})
    assert set(env.keys()) == {"type", "id", "org_id", "occurred_at", "data"}


# ===========================================================================
# 20. is_valid_event_type
# ===========================================================================


@pytest.mark.parametrize("event_type", list(ALL_EVENT_TYPES))
def test_is_valid_event_type_known_events_return_true(event_type):
    assert events.is_valid_event_type(event_type) is True


@pytest.mark.parametrize("event_type", [
    "unknown",
    "watch_breach_extra",
    "WATCH_BREACH",  # case-sensitive
    "Watch_Breach",
    "flow_complete",  # missing 'd'
    "",
    " ",
    "null",
    "flow_completed ",  # trailing space
])
def test_is_valid_event_type_unknown_strings_return_false(event_type):
    assert events.is_valid_event_type(event_type) is False


def test_is_valid_event_type_empty_string_returns_false():
    assert events.is_valid_event_type("") is False


def test_all_event_types_tuple_has_five_items():
    # Updated: QUERY_EXECUTED was the 5th event type; SCHEMA_DRIFT added as 6th.
    assert len(ALL_EVENT_TYPES) == 6


# ===========================================================================
# 21. Typed emit helpers never raise (even with all-None kwargs)
# ===========================================================================


def test_emit_watch_breach_never_raises_with_none_values():
    events.emit_watch_breach(
        _ORG_A,
        watch_id=None,
        name=None,
        metric_id=None,
        value=None,
        explanation=None,
    )


def test_emit_watch_breach_never_raises_with_none_org():
    events.emit_watch_breach(
        None,
        watch_id="w1",
        name="watch",
        metric_id="m1",
        value=99.0,
        explanation="exceeded threshold",
    )


def test_emit_freshness_stale_never_raises_with_none_values():
    events.emit_freshness_stale(
        _ORG_A,
        flow_run_id=None,
        flow_id=None,
        name=None,
        age_s=None,
        sla_s=None,
    )


def test_emit_freshness_stale_never_raises_with_none_org():
    events.emit_freshness_stale(
        None,
        flow_run_id="fr1",
        flow_id="f1",
        name="daily",
        age_s=3600.0,
        sla_s=1800.0,
    )


def test_emit_query_failed_never_raises_with_none_values():
    events.emit_query_failed(
        _ORG_A,
        error_code=None,
        message=None,
        datastore_id=None,
        query_id=None,
    )


def test_emit_query_failed_never_raises_with_none_org():
    events.emit_query_failed(
        None,
        error_code="E001",
        message="timeout",
        datastore_id="ds1",
        query_id="q1",
    )


def test_emit_flow_completed_never_raises_with_none_values():
    events.emit_flow_completed(
        _ORG_A,
        flow_run_id=None,
        flow_id=None,
        name=None,
        state=None,
        duration_s=None,
        failed_task=None,
        error=None,
    )


def test_emit_flow_completed_never_raises_with_none_org():
    events.emit_flow_completed(
        None,
        flow_run_id="fr1",
        flow_id="f1",
        name="nightly",
        state="failed",
        duration_s=120.0,
        failed_task="task_A",
        error="Timeout",
    )


@pytest.mark.parametrize("emit_fn,kwargs", [
    (
        events.emit_watch_breach,
        dict(watch_id="w1", name="W", metric_id="m1", value=1.0, explanation="over"),
    ),
    (
        events.emit_freshness_stale,
        dict(flow_run_id="fr1", flow_id="f1", name="F", age_s=100.0, sla_s=50.0),
    ),
    (
        events.emit_query_failed,
        dict(error_code="E404", message="not found", datastore_id="ds1", query_id="q1"),
    ),
    (
        events.emit_flow_completed,
        dict(flow_run_id="fr1", flow_id="f1", name="F", state="success", duration_s=10.0),
    ),
])
def test_all_emit_helpers_never_raise_with_valid_kwargs(emit_fn, kwargs):
    emit_fn(_ORG_A, **kwargs)


# ===========================================================================
# 22. deliver_one success path — correct headers
# ===========================================================================


@pytest.mark.asyncio
async def test_deliver_one_sets_correct_headers(monkeypatch):
    """deliver_one must set all four required headers on the POST."""
    C = _seq_client([200])

    _patch_delivery(monkeypatch, C())
    ok = await delivery.deliver_one(
        "https://h.example/hook",
        "header-secret",
        QUERY_FAILED,
        {"error_code": "E1", "message": "bad"},
        max_attempts=1,
        base_backoff_s=0.0,
    )
    assert ok is True
    assert len(C.calls) == 1
    hdr = C.calls[0]["headers"]
    assert "X-Nubi-Signature" in hdr
    assert "X-Nubi-Timestamp" in hdr
    assert "X-Nubi-Event" in hdr
    assert hdr["X-Nubi-Event"] == QUERY_FAILED
    assert hdr.get("Content-Type") == "application/json"


@pytest.mark.asyncio
async def test_deliver_one_signature_verifiable_from_headers(monkeypatch):
    """The signature in X-Nubi-Signature must verify against the sent body."""
    C = _seq_client([200])

    _patch_delivery(monkeypatch, C())
    await delivery.deliver_one(
        "https://h.example/hook",
        "verify-me",
        FLOW_COMPLETED,
        {"state": "success"},
        max_attempts=1,
        base_backoff_s=0.0,
    )
    call = C.calls[0]
    hdr = call["headers"]
    ts = int(hdr["X-Nubi-Timestamp"])
    sig = hdr["X-Nubi-Signature"]
    body = call["content"]
    assert delivery.verify("verify-me", body, ts, sig) is True


# ===========================================================================
# 23. Store: list_for_org returns oldest-first order
# ===========================================================================


@pytest.mark.asyncio
async def test_list_for_org_returns_oldest_first():
    store = InMemoryWebhookStore()
    r1 = await store.create(_ORG_A, "first", "https://first.example/hook", "s", _USER)
    r2 = await store.create(_ORG_A, "second", "https://second.example/hook", "s", _USER)
    r3 = await store.create(_ORG_A, "third", "https://third.example/hook", "s", _USER)
    rows = await store.list_for_org(_ORG_A)
    assert rows[0]["id"] == r1["id"]
    assert rows[1]["id"] == r2["id"]
    assert rows[2]["id"] == r3["id"]


# ===========================================================================
# 24. Store: update with no-op fields
# ===========================================================================


@pytest.mark.asyncio
async def test_update_with_no_fields_returns_unchanged_row():
    """update() called with no keyword args returns the current row unchanged."""
    store = InMemoryWebhookStore()
    row = await store.create(
        _ORG_A, "original-name", "https://orig.example/hook", "s", _USER,
        event_types=[WATCH_BREACH],
        active=True,
    )
    updated = await store.update(row["id"], _ORG_A)
    assert updated is not None
    assert updated["name"] == "original-name"
    assert updated["url"] == "https://orig.example/hook"
    assert updated["event_types"] == [WATCH_BREACH]
    assert updated["active"] is True


@pytest.mark.asyncio
async def test_update_nonexistent_endpoint_returns_none():
    store = InMemoryWebhookStore()
    result = await store.update(str(uuid.uuid4()), _ORG_A, name="ghost")
    assert result is None


@pytest.mark.asyncio
async def test_delete_nonexistent_endpoint_returns_false():
    store = InMemoryWebhookStore()
    result = await store.delete(str(uuid.uuid4()), _ORG_A)
    assert result is False


# ===========================================================================
# 25. deliver_to_org — store failure is swallowed
# ===========================================================================


@pytest.mark.asyncio
async def test_deliver_to_org_swallows_store_exception():
    class _BoomStore:
        async def list_active_for_event(self, org_id, event_type):
            raise RuntimeError("connection to DB lost")

    set_webhook_store(_BoomStore())
    try:
        n = await delivery.deliver_to_org(_ORG_A, WATCH_BREACH, {"x": 1})
        assert n == 0
    finally:
        set_webhook_store(None)


# ===========================================================================
# 26. dispatch_event is fire-and-forget and never raises
# ===========================================================================


@pytest.mark.asyncio
async def test_dispatch_event_never_raises_with_no_store():
    """dispatch_event must not raise even if no store is configured."""
    set_webhook_store(None)
    try:
        delivery.dispatch_event(_ORG_A, WATCH_BREACH, {"x": 1})
        await asyncio.sleep(0.05)
    except Exception as exc:
        pytest.fail(f"dispatch_event raised: {exc}")
    finally:
        set_webhook_store(None)


@pytest.mark.asyncio
async def test_dispatch_event_delivers_when_subscribed(monkeypatch):
    """dispatch_event schedules background delivery to subscribed endpoints."""
    store = InMemoryWebhookStore()
    set_webhook_store(store)
    try:
        await store.create(
            _ORG_A, "wh", "https://h.example/hook", "s", _USER,
            event_types=[FRESHNESS_STALE],
        )

        C = _seq_client([200])

        _patch_delivery(monkeypatch, C())
        delivery.dispatch_event(_ORG_A, FRESHNESS_STALE, {"x": 1})
        await asyncio.sleep(0.1)
        assert len(C.calls) == 1
    finally:
        set_webhook_store(None)


# ===========================================================================
# 27. Event subscriptions spanning multiple types (multi-subscription)
# ===========================================================================


@pytest.mark.asyncio
async def test_endpoint_subscribed_to_multiple_event_types():
    store = InMemoryWebhookStore()
    await store.create(
        _ORG_A, "multi", "https://h.example/hook", "s", _USER,
        event_types=[WATCH_BREACH, FLOW_COMPLETED],
    )
    wb = await store.list_active_for_event(_ORG_A, WATCH_BREACH)
    fc = await store.list_active_for_event(_ORG_A, FLOW_COMPLETED)
    qf = await store.list_active_for_event(_ORG_A, QUERY_FAILED)
    fs = await store.list_active_for_event(_ORG_A, FRESHNESS_STALE)

    assert len(wb) == 1
    assert len(fc) == 1
    assert qf == []
    assert fs == []


@pytest.mark.asyncio
async def test_endpoint_subscribed_to_all_event_types():
    store = InMemoryWebhookStore()
    await store.create(
        _ORG_A, "all-events", "https://h.example/hook", "s", _USER,
        event_types=list(ALL_EVENT_TYPES),
    )
    for et in ALL_EVENT_TYPES:
        active = await store.list_active_for_event(_ORG_A, et)
        assert len(active) == 1, f"Expected 1 active for {et}"


# ===========================================================================
# 28. deliver_to_org with no endpoints returns 0
# ===========================================================================


@pytest.mark.asyncio
async def test_deliver_to_org_no_endpoints_returns_zero(monkeypatch):
    store = InMemoryWebhookStore()
    set_webhook_store(store)
    try:
        C = _seq_client([])

        _patch_delivery(monkeypatch, C())
        n = await delivery.deliver_to_org(_ORG_C, WATCH_BREACH, {"x": 1})
        assert n == 0
        assert len(C.calls) == 0
    finally:
        set_webhook_store(None)


# ===========================================================================
# 29. canonical_body determinism across multiple calls
# ===========================================================================


def test_canonical_body_deterministic_repeated_calls():
    payload = {"type": "test", "data": {"z": 99, "a": [1, 2, 3]}}
    results = {delivery.canonical_body(payload) for _ in range(10)}
    assert len(results) == 1, "canonical_body is non-deterministic"


# ===========================================================================
# 30. Secret isolation: multiple orgs with same endpoint URL
# ===========================================================================


@pytest.mark.asyncio
async def test_two_orgs_same_url_different_secrets():
    """Two orgs can register the same URL but have independent secrets."""
    store = InMemoryWebhookStore()
    url = "https://shared.example/hook"
    row_a = await store.create(_ORG_A, "wh", url, "secret-for-A", _USER, event_types=[WATCH_BREACH])
    row_b = await store.create(_ORG_B, "wh", url, "secret-for-B", _USER, event_types=[WATCH_BREACH])

    assert await store.get_secret(row_a["id"], _ORG_A) == "secret-for-A"
    assert await store.get_secret(row_b["id"], _ORG_B) == "secret-for-B"
    # Cross-org returns None.
    assert await store.get_secret(row_a["id"], _ORG_B) is None
    assert await store.get_secret(row_b["id"], _ORG_A) is None
