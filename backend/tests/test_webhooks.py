"""Tests for the first-class outbound webhook / event-sink subsystem.

Coverage
--------
1.  Store CRUD + secret encryption at rest (never returned by reads).
2.  Per-org isolation: org A's endpoint never serves org B (get/secret/lookup).
3.  Subscription gating: only subscribed + active event types are delivered.
4.  HMAC-SHA256 signature is correct + verifiable; tamper is rejected.
5.  Delivery: fires for subscribed events; retry/backoff on 5xx; permanent 4xx
    does not retry; a failing webhook never raises (best-effort).
6.  emit_event / dispatch_event are fire-and-forget and never raise.
7.  Admin CRUD API: create/list/get/update/delete, org-scoped, no secret leak.
"""

from __future__ import annotations

import os
import uuid

import pytest

# ---------------------------------------------------------------------------
# Environment: a valid NUBI_SECRETS_KEY must exist before crypto is imported.
# ---------------------------------------------------------------------------

from cryptography.fernet import Fernet  # noqa: E402

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
from app.webhooks.models import InMemoryWebhookStore  # noqa: E402

_ORG_A = str(uuid.uuid4())
_ORG_B = str(uuid.uuid4())
_USER = str(uuid.uuid4())


# ===========================================================================
# Store: CRUD + secret-at-rest
# ===========================================================================


@pytest.mark.asyncio
async def test_create_does_not_leak_secret():
    store = InMemoryWebhookStore()
    row = await store.create(
        _ORG_A, "wh", "https://h.example/hook", "topsecret123", _USER,
        event_types=[events.WATCH_BREACH],
    )
    assert "secret_encrypted" not in row
    assert "secret" not in row
    assert row["url"] == "https://h.example/hook"
    assert row["event_types"] == [events.WATCH_BREACH]
    assert row["active"] is True


@pytest.mark.asyncio
async def test_secret_round_trips_via_get_secret():
    store = InMemoryWebhookStore()
    row = await store.create(
        _ORG_A, "wh", "https://h.example/hook", "topsecret123", _USER,
        event_types=[events.WATCH_BREACH],
    )
    assert await store.get_secret(row["id"], _ORG_A) == "topsecret123"


@pytest.mark.asyncio
async def test_list_get_update_delete():
    store = InMemoryWebhookStore()
    row = await store.create(
        _ORG_A, "wh", "https://h.example/hook", "s3cretvalue", _USER,
        event_types=[events.QUERY_FAILED],
    )
    assert len(await store.list_for_org(_ORG_A)) == 1
    got = await store.get_by_id(row["id"], _ORG_A)
    assert got is not None and "secret_encrypted" not in got

    updated = await store.update(
        row["id"], _ORG_A, name="renamed", active=False,
        event_types=[events.FLOW_COMPLETED],
    )
    assert updated["name"] == "renamed"
    assert updated["active"] is False
    assert updated["event_types"] == [events.FLOW_COMPLETED]

    assert await store.delete(row["id"], _ORG_A) is True
    assert await store.list_for_org(_ORG_A) == []


@pytest.mark.asyncio
async def test_update_rotates_secret():
    store = InMemoryWebhookStore()
    row = await store.create(
        _ORG_A, "wh", "https://h.example/hook", "oldsecret1", _USER,
    )
    await store.update(row["id"], _ORG_A, secret="newsecret2")
    assert await store.get_secret(row["id"], _ORG_A) == "newsecret2"


# ===========================================================================
# Per-org isolation
# ===========================================================================


@pytest.mark.asyncio
async def test_cross_org_get_and_secret_isolated():
    store = InMemoryWebhookStore()
    row = await store.create(
        _ORG_A, "wh", "https://h.example/hook", "secretA1", _USER,
        event_types=[events.WATCH_BREACH],
    )
    # Org B cannot read org A's endpoint or its secret.
    assert await store.get_by_id(row["id"], _ORG_B) is None
    assert await store.get_secret(row["id"], _ORG_B) is None
    assert await store.list_for_org(_ORG_B) == []
    # Org B can't update or delete org A's endpoint.
    assert await store.update(row["id"], _ORG_B, name="hijack") is None
    assert await store.delete(row["id"], _ORG_B) is False


@pytest.mark.asyncio
async def test_active_for_event_org_scoped():
    store = InMemoryWebhookStore()
    await store.create(
        _ORG_A, "a", "https://a.example/hook", "secretA1", _USER,
        event_types=[events.WATCH_BREACH],
    )
    await store.create(
        _ORG_B, "b", "https://b.example/hook", "secretB1", _USER,
        event_types=[events.WATCH_BREACH],
    )
    a = await store.list_active_for_event(_ORG_A, events.WATCH_BREACH)
    assert len(a) == 1 and a[0]["url"] == "https://a.example/hook"
    b = await store.list_active_for_event(_ORG_B, events.WATCH_BREACH)
    assert len(b) == 1 and b[0]["url"] == "https://b.example/hook"


# ===========================================================================
# Subscription gating
# ===========================================================================


@pytest.mark.asyncio
async def test_unsubscribed_event_not_returned():
    store = InMemoryWebhookStore()
    await store.create(
        _ORG_A, "wh", "https://h.example/hook", "secretA1", _USER,
        event_types=[events.WATCH_BREACH],
    )
    # Subscribed type → 1 endpoint; unsubscribed type → none.
    assert len(await store.list_active_for_event(_ORG_A, events.WATCH_BREACH)) == 1
    assert await store.list_active_for_event(_ORG_A, events.QUERY_FAILED) == []


@pytest.mark.asyncio
async def test_inactive_endpoint_not_returned():
    store = InMemoryWebhookStore()
    await store.create(
        _ORG_A, "wh", "https://h.example/hook", "secretA1", _USER,
        event_types=[events.WATCH_BREACH], active=False,
    )
    assert await store.list_active_for_event(_ORG_A, events.WATCH_BREACH) == []


# ===========================================================================
# HMAC signing
# ===========================================================================


def test_signature_is_correct_and_verifiable():
    body = delivery.canonical_body({"type": "watch_breach", "x": 1})
    ts = 1_700_000_000
    sig = delivery.sign("supersecret", body, ts)
    assert delivery.verify("supersecret", body, ts, sig) is True


def test_signature_rejects_tamper():
    body = delivery.canonical_body({"a": 1})
    ts = 1_700_000_000
    sig = delivery.sign("supersecret", body, ts)
    # Wrong secret, wrong timestamp, or tampered body all fail.
    assert delivery.verify("othersecret", body, ts, sig) is False
    assert delivery.verify("supersecret", body, ts + 1, sig) is False
    tampered = delivery.canonical_body({"a": 2})
    assert delivery.verify("supersecret", tampered, ts, sig) is False


def test_canonical_body_is_stable():
    # Key order in the source dict must not change the bytes.
    b1 = delivery.canonical_body({"a": 1, "b": 2})
    b2 = delivery.canonical_body({"b": 2, "a": 1})
    assert b1 == b2


# ===========================================================================
# Delivery: success / retry / failure-never-raises
# ===========================================================================

import socket as _socket  # noqa: E402 — used only in helpers below


def _patch_delivery(monkeypatch, client_instance):
    """Patch deliver_one so it skips DNS resolution and uses *client_instance*.

    deliver_one now calls ``_build_pinned_client`` to get an httpx.AsyncClient
    that connects to the SSRF-checked, pinned IP.  For unit tests that only
    care about retry logic / signature / header correctness — not the SSRF
    guard — we patch two things:

    1. ``socket.getaddrinfo`` → always returns a safe public IP so that
       ``guard_url`` + ``resolve_and_pin`` pass without real DNS.
    2. ``delivery._build_pinned_client`` → returns *client_instance* directly
       so the test's recording mock captures the outbound POST.
    """

    def _fake_getaddrinfo(host, *args, **kwargs):
        return [(_socket.AF_INET, _socket.SOCK_STREAM, _socket.IPPROTO_TCP, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(_socket, "getaddrinfo", _fake_getaddrinfo)
    monkeypatch.setattr(delivery, "_build_pinned_client", lambda *a, **kw: client_instance)


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


@pytest.mark.asyncio
async def test_deliver_one_success(monkeypatch):
    class C(_RecordingClient):
        calls = []

        @classmethod
        def _next(cls):
            return _FakeResponse(200)

    _patch_delivery(monkeypatch, C())
    ok = await delivery.deliver_one(
        "https://h.example/hook", "secret1", events.WATCH_BREACH, {"x": 1}
    )
    assert ok is True
    assert len(C.calls) == 1
    hdr = C.calls[0]["headers"]
    # Signature header present + matches the body that was sent.
    assert hdr["X-Nubi-Event"] == events.WATCH_BREACH
    ts = int(hdr["X-Nubi-Timestamp"])
    assert delivery.verify("secret1", C.calls[0]["content"], ts, hdr["X-Nubi-Signature"])


@pytest.mark.asyncio
async def test_deliver_one_retries_on_5xx_then_succeeds(monkeypatch):
    seq = [500, 503, 200]

    class C(_RecordingClient):
        calls = []
        _i = 0

        @classmethod
        def _next(cls):
            code = seq[cls._i]
            cls._i += 1
            return _FakeResponse(code)

    _patch_delivery(monkeypatch, C())
    # Zero backoff so the test is fast.
    ok = await delivery.deliver_one(
        "https://h.example/hook", "s", events.QUERY_FAILED, {"x": 1},
        base_backoff_s=0.0,
    )
    assert ok is True
    assert len(C.calls) == 3  # two failures + one success


@pytest.mark.asyncio
async def test_deliver_one_permanent_4xx_no_retry(monkeypatch):
    class C(_RecordingClient):
        calls = []

        @classmethod
        def _next(cls):
            return _FakeResponse(400)

    _patch_delivery(monkeypatch, C())
    ok = await delivery.deliver_one(
        "https://h.example/hook", "s", events.QUERY_FAILED, {"x": 1},
        base_backoff_s=0.0,
    )
    assert ok is False
    assert len(C.calls) == 1  # 400 is permanent — not retried


@pytest.mark.asyncio
async def test_deliver_one_exhausts_attempts_on_transport_error(monkeypatch):
    class C(_RecordingClient):
        calls = []

        async def post(self, url, *, content, headers):
            type(self).calls.append(1)
            raise RuntimeError("connection refused")

    _patch_delivery(monkeypatch, C())
    ok = await delivery.deliver_one(
        "https://h.example/hook", "s", events.WATCH_BREACH, {"x": 1},
        max_attempts=3, base_backoff_s=0.0,
    )
    assert ok is False
    assert len(C.calls) == 3  # never raises; returns False after exhausting


@pytest.mark.asyncio
async def test_deliver_to_org_fires_for_subscribed_only(monkeypatch):
    from app.webhooks.models import set_webhook_store

    store = InMemoryWebhookStore()
    set_webhook_store(store)
    try:
        await store.create(
            _ORG_A, "subbed", "https://sub.example/hook", "s1", _USER,
            event_types=[events.WATCH_BREACH],
        )
        await store.create(
            _ORG_A, "other", "https://other.example/hook", "s2", _USER,
            event_types=[events.QUERY_FAILED],
        )

        class C(_RecordingClient):
            calls = []

            @classmethod
            def _next(cls):
                return _FakeResponse(200)

        _patch_delivery(monkeypatch, C())
        n = await delivery.deliver_to_org(_ORG_A, events.WATCH_BREACH, {"x": 1})
        assert n == 1
        assert len(C.calls) == 1
        assert C.calls[0]["url"] == "https://sub.example/hook"
    finally:
        set_webhook_store(None)


@pytest.mark.asyncio
async def test_failing_webhook_never_raises():
    from app.webhooks.models import set_webhook_store

    # A store whose lookup raises must not propagate out of deliver_to_org.
    class _BoomStore:
        async def list_active_for_event(self, org_id, event_type):
            raise RuntimeError("db down")

    set_webhook_store(_BoomStore())
    try:
        n = await delivery.deliver_to_org(_ORG_A, events.WATCH_BREACH, {"x": 1})
        assert n == 0  # swallowed, no raise
    finally:
        set_webhook_store(None)


def test_emit_event_noop_without_org():
    # No org → no delivery, no raise.
    events.emit_event(events.WATCH_BREACH, None, {"x": 1})
    events.emit_event(events.WATCH_BREACH, "", {"x": 1})


def test_emit_event_noop_for_unknown_type():
    events.emit_event("not_a_real_event", _ORG_A, {"x": 1})


@pytest.mark.asyncio
async def test_emit_event_dispatches_in_loop(monkeypatch):
    """emit_event inside a running loop schedules a background delivery task."""
    import asyncio as _asyncio

    from app.webhooks.models import set_webhook_store

    store = InMemoryWebhookStore()
    set_webhook_store(store)
    try:
        await store.create(
            _ORG_A, "wh", "https://h.example/hook", "s1", _USER,
            event_types=[events.FLOW_COMPLETED],
        )

        class C(_RecordingClient):
            calls = []

            @classmethod
            def _next(cls):
                return _FakeResponse(200)

        _patch_delivery(monkeypatch, C())
        events.emit_flow_completed(
            _ORG_A, flow_run_id="fr1", flow_id="f1", name="nightly",
            state="success",
        )
        # Let the scheduled background task run.
        await _asyncio.sleep(0.05)
        assert len(C.calls) == 1
        assert C.calls[0]["headers"]["X-Nubi-Event"] == events.FLOW_COMPLETED
    finally:
        set_webhook_store(None)


# ===========================================================================
# Admin CRUD API (org-scoped, first-party auth)
# ===========================================================================

import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

_ROUTE_ORG = str(uuid.uuid4())


@pytest_asyncio.fixture
async def api_client():
    """ASGI client with auth + org-resolution + writer-guard stubbed out."""
    from app.webhooks.models import InMemoryWebhookStore, set_webhook_store

    set_webhook_store(InMemoryWebhookStore())

    from main import app  # noqa: PLC0415
    from app.auth.deps import current_user  # noqa: PLC0415
    from app.auth.roles import require_writer_default  # noqa: PLC0415
    from app.routes import _org as org_mod  # noqa: PLC0415

    fake_user = {"id": _USER, "email": "t@nubi.dev", "name": "T"}

    async def _fake_current_user():
        return fake_user

    async def _fake_writer():
        return None

    async def _fake_get_user_org(user_id, repo):
        return _ROUTE_ORG

    app.dependency_overrides[current_user] = _fake_current_user
    app.dependency_overrides[require_writer_default] = _fake_writer
    _orig = org_mod.get_user_org
    org_mod.get_user_org = _fake_get_user_org
    # The router imported the symbol directly — patch its binding too.
    import app.webhooks.router as wh_router  # noqa: PLC0415
    wh_router._get_user_org = _fake_get_user_org
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            yield ac
    finally:
        app.dependency_overrides.pop(current_user, None)
        app.dependency_overrides.pop(require_writer_default, None)
        org_mod.get_user_org = _orig
        wh_router._get_user_org = _orig
        set_webhook_store(None)


@pytest.mark.asyncio
async def test_route_create_list_get_update_delete(api_client: AsyncClient):
    # Create
    resp = await api_client.post(
        "/api/v1/webhooks/",
        json={
            "name": "host hook",
            "url": "https://host.example/nubi",
            "secret": "supersecret",
            "event_types": [events.WATCH_BREACH, events.FLOW_COMPLETED],
        },
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert "secret" not in created and "secret_encrypted" not in created
    wid = created["id"]

    # List
    resp = await api_client.get("/api/v1/webhooks/")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1 and rows[0]["id"] == wid
    assert "secret_encrypted" not in rows[0]

    # Get
    resp = await api_client.get(f"/api/v1/webhooks/{wid}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "host hook"

    # Update
    resp = await api_client.put(
        f"/api/v1/webhooks/{wid}", json={"active": False, "name": "renamed"}
    )
    assert resp.status_code == 200
    assert resp.json()["active"] is False
    assert resp.json()["name"] == "renamed"

    # Delete
    resp = await api_client.delete(f"/api/v1/webhooks/{wid}")
    assert resp.status_code == 204
    resp = await api_client.get(f"/api/v1/webhooks/{wid}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_route_rejects_unknown_event_type(api_client: AsyncClient):
    resp = await api_client.post(
        "/api/v1/webhooks/",
        json={
            "url": "https://host.example/nubi",
            "secret": "supersecret",
            "event_types": ["not_a_real_event"],
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_event_types"


@pytest.mark.asyncio
async def test_route_get_missing_is_404(api_client: AsyncClient):
    resp = await api_client.get(f"/api/v1/webhooks/{uuid.uuid4()}")
    assert resp.status_code == 404


# ===========================================================================
# query_executed event — POPIA-safe audit webhook
# ===========================================================================


def test_query_executed_in_all_event_types():
    """QUERY_EXECUTED constant is registered in the ALL_EVENT_TYPES catalog."""
    assert events.QUERY_EXECUTED == "query_executed"
    assert events.QUERY_EXECUTED in events.ALL_EVENT_TYPES
    assert events.is_valid_event_type(events.QUERY_EXECUTED)


def test_emit_query_executed_noop_without_org():
    """emit_query_executed with no org_id must be a no-op — never raises."""
    events.emit_query_executed(
        None,
        query_id="q1",
        subject="user-abc",
        datasource_id="ds-1",
        row_count=5,
    )
    events.emit_query_executed("")


def test_emit_query_executed_payload_has_no_raw_data():
    """The envelope built by emit_query_executed must contain ONLY metadata fields."""
    from app.webhooks.events import build_envelope, QUERY_EXECUTED

    payload = {
        "query_id": "my_query",
        "subject": "user-uuid",
        "datasource_id": "ds-uuid",
        "row_count": 10,
    }
    envelope = build_envelope(QUERY_EXECUTED, _ORG_A, payload)

    assert envelope["type"] == QUERY_EXECUTED
    assert envelope["org_id"] == _ORG_A
    data = envelope["data"]

    # Required metadata fields present.
    assert data["query_id"] == "my_query"
    assert data["subject"] == "user-uuid"
    assert data["datasource_id"] == "ds-uuid"
    assert data["row_count"] == 10

    # No raw rows / result data / SQL literals / PII fields in payload.
    pii_keys = {"sql", "rows", "result", "data", "filter", "filters", "where", "params"}
    assert not pii_keys.intersection(set(data.keys())), (
        f"payload must not carry any of {pii_keys}; got keys: {set(data.keys())}"
    )


@pytest.mark.asyncio
async def test_query_executed_delivered_only_to_subscribed_endpoint(monkeypatch):
    """query_executed is delivered ONLY to endpoints that subscribed to it."""
    from app.webhooks.models import set_webhook_store

    store = InMemoryWebhookStore()
    set_webhook_store(store)
    try:
        # Endpoint subscribed to query_executed.
        await store.create(
            _ORG_A, "audit-hook", "https://audit.example/hook", "s_audit", _USER,
            event_types=[events.QUERY_EXECUTED],
        )
        # Endpoint subscribed to something else (should NOT receive query_executed).
        await store.create(
            _ORG_A, "breach-hook", "https://breach.example/hook", "s_breach", _USER,
            event_types=[events.WATCH_BREACH],
        )

        class C(_RecordingClient):
            calls = []

            @classmethod
            def _next(cls):
                return _FakeResponse(200)

        _patch_delivery(monkeypatch, C())
        n = await delivery.deliver_to_org(
            _ORG_A, events.QUERY_EXECUTED,
            build_envelope_for_test(_ORG_A),
        )
        assert n == 1  # only the subscribed endpoint received it
        assert len(C.calls) == 1
        assert C.calls[0]["url"] == "https://audit.example/hook"
        assert C.calls[0]["headers"]["X-Nubi-Event"] == events.QUERY_EXECUTED
    finally:
        set_webhook_store(None)


@pytest.mark.asyncio
async def test_query_executed_not_delivered_to_unsubscribed_endpoint(monkeypatch):
    """Endpoints not subscribed to query_executed receive zero deliveries."""
    from app.webhooks.models import set_webhook_store

    store = InMemoryWebhookStore()
    set_webhook_store(store)
    try:
        await store.create(
            _ORG_A, "other-hook", "https://other.example/hook", "s1", _USER,
            event_types=[events.QUERY_FAILED, events.FLOW_COMPLETED],
        )

        class C(_RecordingClient):
            calls = []

        import httpx

        monkeypatch.setattr(httpx, "AsyncClient", C)
        n = await delivery.deliver_to_org(
            _ORG_A, events.QUERY_EXECUTED,
            build_envelope_for_test(_ORG_A),
        )
        assert n == 0
        assert len(C.calls) == 0
    finally:
        set_webhook_store(None)


@pytest.mark.asyncio
async def test_query_executed_per_org_isolation(monkeypatch):
    """Org A's query_executed event must never reach Org B's endpoint."""
    from app.webhooks.models import set_webhook_store

    store = InMemoryWebhookStore()
    set_webhook_store(store)
    try:
        # Both orgs subscribe to query_executed.
        await store.create(
            _ORG_A, "hook-a", "https://a.example/hook", "sa", _USER,
            event_types=[events.QUERY_EXECUTED],
        )
        await store.create(
            _ORG_B, "hook-b", "https://b.example/hook", "sb", _USER,
            event_types=[events.QUERY_EXECUTED],
        )

        class C(_RecordingClient):
            calls = []

            @classmethod
            def _next(cls):
                return _FakeResponse(200)

        _patch_delivery(monkeypatch, C())

        # Emit for org A only — only org A's endpoint should be called.
        n = await delivery.deliver_to_org(
            _ORG_A, events.QUERY_EXECUTED,
            build_envelope_for_test(_ORG_A),
        )
        assert n == 1
        assert len(C.calls) == 1
        assert C.calls[0]["url"] == "https://a.example/hook"

        # Emit for org B only — only org B's endpoint should be called.
        C.calls.clear()
        n = await delivery.deliver_to_org(
            _ORG_B, events.QUERY_EXECUTED,
            build_envelope_for_test(_ORG_B),
        )
        assert n == 1
        assert len(C.calls) == 1
        assert C.calls[0]["url"] == "https://b.example/hook"
    finally:
        set_webhook_store(None)


@pytest.mark.asyncio
async def test_slow_or_failing_query_executed_never_breaks_caller(monkeypatch):
    """A failing / slow webhook for query_executed must not break the caller."""
    from app.webhooks.models import set_webhook_store

    store = InMemoryWebhookStore()
    set_webhook_store(store)
    try:
        await store.create(
            _ORG_A, "bad-hook", "https://bad.example/hook", "s_bad", _USER,
            event_types=[events.QUERY_EXECUTED],
        )

        class C(_RecordingClient):
            calls = []

            async def post(self, url, *, content, headers):
                type(self).calls.append(1)
                raise RuntimeError("connection timed out")

        _patch_delivery(monkeypatch, C())

        # deliver_to_org must return 0 (no successes) without raising.
        n = await delivery.deliver_to_org(
            _ORG_A, events.QUERY_EXECUTED,
            build_envelope_for_test(_ORG_A),
        )
        assert n == 0  # all deliveries failed — not raised

        # emit_query_executed (the fire-and-forget helper) also must not raise.
        events.emit_query_executed(
            _ORG_A,
            query_id="q1",
            subject="user-abc",
            datasource_id="ds-1",
            row_count=7,
        )
        # No exception means pass.
    finally:
        set_webhook_store(None)


@pytest.mark.asyncio
async def test_emit_query_executed_dispatches_in_loop(monkeypatch):
    """emit_query_executed inside a running event loop schedules a background task."""
    import asyncio as _asyncio

    from app.webhooks.models import set_webhook_store

    store = InMemoryWebhookStore()
    set_webhook_store(store)
    try:
        await store.create(
            _ORG_A, "audit-wh", "https://audit.example/hook", "s_a", _USER,
            event_types=[events.QUERY_EXECUTED],
        )

        class C(_RecordingClient):
            calls = []

            @classmethod
            def _next(cls):
                return _FakeResponse(200)

        _patch_delivery(monkeypatch, C())
        events.emit_query_executed(
            _ORG_A,
            query_id="my_metric",
            subject="user-uuid",
            datasource_id="ds-uuid",
            row_count=3,
        )
        await _asyncio.sleep(0.05)
        assert len(C.calls) == 1
        assert C.calls[0]["headers"]["X-Nubi-Event"] == events.QUERY_EXECUTED
    finally:
        set_webhook_store(None)


# ---------------------------------------------------------------------------
# Helper: build a minimal valid query_executed envelope for delivery tests.
# ---------------------------------------------------------------------------

def build_envelope_for_test(org_id: str) -> dict:
    from app.webhooks.events import build_envelope, QUERY_EXECUTED

    return build_envelope(
        QUERY_EXECUTED,
        org_id,
        {
            "query_id": "test_query",
            "subject": "user-test",
            "datasource_id": None,
            "row_count": 1,
        },
    )
