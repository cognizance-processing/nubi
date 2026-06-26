"""Security regression tests for webhook SSRF guard + DNS-rebinding pin.

Coverage
--------
1.  deliver_one() blocks cloud-metadata URL (169.254.169.254) — no HTTP call.
2.  deliver_one() blocks loopback URL (http://127.0.0.1).
3.  deliver_one() blocks RFC1918 URL (http://10.0.0.5).
4.  deliver_one() blocks a hostname that DNS-resolves to a private IP.
5.  deliver_one() returns False (not raises) so it never breaks the caller.
6.  deliver_one() allows and attempts a public URL (with pinned transport).
7.  POST /webhooks create rejects a metadata IP URL at registration time (400).
8.  POST /webhooks create rejects a localhost URL at registration time.
9.  POST /webhooks create rejects a file:// URL at registration time.
10. PUT  /webhooks update rejects a private IP URL during rotation.
11. deliver_one() blocks non-http(s) scheme (file://).
12. DNS-rebinding: resolver returns public IP for the guard check but the
    transport connects to the *pinned* IP, not a re-resolved one.  Even if
    an attacker could make a second lookup return a private IP, the connection
    always targets the pinned public IP — verified via captured connect args.
13. URL scheme validation rejects non-http(s) at the pydantic schema layer
    (ftp://, file://, javascript://, etc.) with a 422 before SSRF guard runs.
14. Pydantic also rejects a URL with no host component.
"""

from __future__ import annotations

import os
import socket
import uuid
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Env bootstrap before any app import
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

_ORG = str(uuid.uuid4())
_USER = str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _getaddrinfo_returning(*ips: str):
    """Fake socket.getaddrinfo that resolves any host to *ips*."""

    def _fake(host, *args, **kwargs):
        infos = []
        for ip in ips:
            if ":" in ip:
                family = socket.AF_INET6
                sockaddr = (ip, 0, 0, 0)
            else:
                family = socket.AF_INET
                sockaddr = (ip, 0)
            infos.append((family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr))
        return infos

    return _fake


class _FakeResponse:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code


class _FakeTransportResponse:
    """Minimal httpx.Response-like object for transport-level mocking."""

    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.headers = {}
        self.extensions = {}

    async def aread(self):
        return b""

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


# ===========================================================================
# deliver_one — SSRF guard fires before any HTTP call
# ===========================================================================


@pytest.mark.asyncio
async def test_deliver_one_blocks_metadata_ip(monkeypatch) -> None:
    """Cloud metadata IP is blocked; no HTTP request is made and no exception raised."""
    # _build_pinned_client should never be reached — the SSRF guard blocks first.
    _build_called = []

    orig_build = delivery._build_pinned_client

    def _spy_build(*args, **kwargs):
        _build_called.append(True)
        return orig_build(*args, **kwargs)

    monkeypatch.setattr(delivery, "_build_pinned_client", _spy_build)

    result = await delivery.deliver_one(
        "http://169.254.169.254/latest/meta-data/",
        "secret",
        events.WATCH_BREACH,
        {"x": 1},
    )
    assert result is False, "deliver_one must return False for blocked URL"
    assert not _build_called, "no transport should be built for a blocked URL"


@pytest.mark.asyncio
async def test_deliver_one_blocks_loopback(monkeypatch) -> None:
    """Loopback URL is blocked before any HTTP call."""
    result = await delivery.deliver_one(
        "http://127.0.0.1:8080/internal",
        "secret",
        events.WATCH_BREACH,
        {"x": 1},
    )
    assert result is False


@pytest.mark.asyncio
async def test_deliver_one_blocks_rfc1918(monkeypatch) -> None:
    """RFC1918 private IP is blocked before any HTTP call."""
    result = await delivery.deliver_one(
        "http://10.0.0.5/webhook-sink",
        "secret",
        events.QUERY_FAILED,
        {"x": 1},
    )
    assert result is False


@pytest.mark.asyncio
async def test_deliver_one_blocks_hostname_resolving_to_private(monkeypatch) -> None:
    """A hostname that resolves to a private IP is blocked (DNS-rebind guard)."""
    fake_resolve = _getaddrinfo_returning("192.168.1.1")
    with patch.object(socket, "getaddrinfo", fake_resolve):
        result = await delivery.deliver_one(
            "http://internal.attacker.example/sink",
            "secret",
            events.WATCH_BREACH,
            {"x": 1},
        )
    assert result is False


@pytest.mark.asyncio
async def test_deliver_one_blocks_file_scheme(monkeypatch) -> None:
    """Non-http(s) scheme is blocked."""
    result = await delivery.deliver_one(
        "file:///etc/passwd",
        "secret",
        events.WATCH_BREACH,
        {"x": 1},
    )
    assert result is False


@pytest.mark.asyncio
async def test_deliver_one_never_raises_on_ssrf_block(monkeypatch) -> None:
    """deliver_one is fire-and-forget: SSRF block must never raise."""
    # This should NOT raise — must return False only.
    result = await delivery.deliver_one(
        "http://169.254.169.254/",
        "secret",
        events.WATCH_BREACH,
        {},
    )
    assert result is False  # returned, not raised


@pytest.mark.asyncio
async def test_deliver_one_allows_public_url(monkeypatch) -> None:
    """A public URL passes the SSRF guard and the HTTP call is attempted.

    We mock _build_pinned_client to return an AsyncClient whose transport
    captures the POST and returns 200, proving the full happy path executes.
    """
    import httpx

    _post_urls: list[str] = []

    class _FakeTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            _post_urls.append(str(request.url))
            return httpx.Response(200, content=b"ok")

    def _fake_build_pinned_client(pinned_ip, hostname, scheme, port, timeout_s):
        return httpx.AsyncClient(transport=_FakeTransport(), follow_redirects=False)

    monkeypatch.setattr(delivery, "_build_pinned_client", _fake_build_pinned_client)

    fake_resolve = _getaddrinfo_returning("93.184.216.34")
    with patch.object(socket, "getaddrinfo", fake_resolve):
        result = await delivery.deliver_one(
            "https://api.example.com/webhook",
            "secret",
            events.FLOW_COMPLETED,
            {"x": 1},
        )
    assert result is True
    assert _post_urls, "HTTP call must be attempted for a public URL"


# ===========================================================================
# DNS-rebinding pin test (the main fix)
# ===========================================================================


@pytest.mark.asyncio
async def test_deliver_one_pins_connection_to_resolved_ip(monkeypatch) -> None:
    """DNS-rebinding fix: the transport always connects to the pinned (resolved) IP.

    Scenario
    --------
    The attacker controls ``evil.example`` with a zero-TTL record.

    * DNS lookups #1 and #2 (guard_url + resolve_and_pin, both synchronous):
      return 93.184.216.34 (public, safe).  This is realistic: an attacker with
      a zero-TTL record makes the host appear public at check time.
    * Any THIRD lookup — which the old httpx.AsyncClient would make at
      socket-connect time — would return 127.0.0.1 (loopback, private).

    Because deliver_one now passes the PINNED IP (from resolve_and_pin) to
    _build_pinned_client, the transport connects directly to 93.184.216.34
    without any further getaddrinfo call.  We prove this by:

    1. Capturing the ``pinned_ip`` argument that _build_pinned_client receives.
    2. Asserting it equals the public IP from the pin step.
    3. Asserting delivery succeeds (returns True) — the pinned transport works.
    4. Asserting the transport made ZERO additional getaddrinfo calls (only the
       two pre-flight calls from guard_url and resolve_and_pin happened).
    """
    import httpx

    _captured_pin: list[str] = []

    class _FakeTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"ok")

    def _spy_build_pinned_client(pinned_ip, hostname, scheme, port, timeout_s):
        _captured_pin.append(pinned_ip)
        # Return a fake transport — no real TCP so no getaddrinfo at connect time.
        return httpx.AsyncClient(transport=_FakeTransport(), follow_redirects=False)

    monkeypatch.setattr(delivery, "_build_pinned_client", _spy_build_pinned_client)

    _PUBLIC_IP = "93.184.216.34"
    _call_count = [0]

    def _rebinding_resolver(host, *args, **kwargs):
        """Calls 1-2 (guard_url + resolve_and_pin): return the public IP.
        Call 3+ would be a third lookup made by httpx at connect time — the bug
        we fixed.  In the old code call #3 could rebind to a private IP.
        In the new (pinned) code there must be no call #3 at all.
        """
        _call_count[0] += 1
        if _call_count[0] <= 2:
            # Pre-flight guard + pin resolution: public IP, safe.
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (_PUBLIC_IP, 0))]
        # Third call = httpx re-resolving at connect time (the old vulnerability).
        # Return loopback to make the rebinding clearly visible in test output.
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 0))]

    with patch.object(socket, "getaddrinfo", _rebinding_resolver):
        result = await delivery.deliver_one(
            "https://evil.example/webhook",
            "secret",
            events.WATCH_BREACH,
            {"event": "test"},
        )

    # The delivery must succeed (pinned public IP was used, no rebind).
    assert result is True, "Delivery to pinned public IP should succeed"

    # The IP passed to _build_pinned_client must be the public one from pin step.
    assert _captured_pin, "_build_pinned_client must have been called"
    assert _captured_pin[0] == _PUBLIC_IP, (
        f"Transport must connect to the pinned public IP {_PUBLIC_IP!r}, "
        f"not a re-resolved address. Got: {_captured_pin[0]!r}"
    )

    # guard_url calls getaddrinfo once; resolve_and_pin calls it once.
    # The transport must NOT make a third call.
    assert _call_count[0] <= 2, (
        f"Expected at most 2 DNS resolutions (guard_url + resolve_and_pin), "
        f"but got {_call_count[0]}.  A third call means the transport re-resolved "
        f"the hostname — the DNS-rebinding vulnerability is not fixed."
    )


@pytest.mark.asyncio
async def test_deliver_one_rebind_to_private_fails_closed(monkeypatch) -> None:
    """Fail-closed: if somehow a private IP reaches _build_pinned_client it is rejected.

    This covers the defence-in-depth secondary check inside _build_pinned_client.
    We bypass resolve_and_pin and inject a private IP directly to prove the
    transport itself refuses to connect to it.
    """
    from app.connectors.ssrf import PinnedTarget

    # Patch resolve_and_pin to return a 'pinned' private IP (simulating a logic bug).
    _private_ip = "192.168.1.100"

    def _fake_resolve_and_pin(url: str) -> PinnedTarget:
        return PinnedTarget(url=url, ip=_private_ip, host="evil.example", port=443)

    with patch("app.connectors.ssrf.resolve_and_pin", _fake_resolve_and_pin):
        # Also patch guard_url to pass (so only the secondary check fires).
        with patch("app.connectors.ssrf.guard_url"):
            result = await delivery.deliver_one(
                "https://evil.example/webhook",
                "secret",
                events.WATCH_BREACH,
                {},
            )

    # _build_pinned_client's fail-closed secondary check should have blocked it.
    assert result is False, (
        "deliver_one must return False when the pinned IP is private "
        "(secondary fail-closed check in _build_pinned_client)"
    )


# ===========================================================================
# Registration-time SSRF guard (POST /webhooks and PUT /webhooks/:id)
# ===========================================================================


import pytest_asyncio  # noqa: E402


@pytest_asyncio.fixture
async def webhook_api_client():
    """ASGI test client with auth / writer guard / org-resolution stubbed."""
    from app.webhooks.models import InMemoryWebhookStore, set_webhook_store

    set_webhook_store(InMemoryWebhookStore())

    from main import app  # noqa: PLC0415
    from app.auth.deps import current_user  # noqa: PLC0415
    from app.auth.roles import require_writer_default  # noqa: PLC0415
    from app.routes import _org as org_mod  # noqa: PLC0415
    from httpx import ASGITransport, AsyncClient  # noqa: PLC0415

    _org_id = str(uuid.uuid4())
    fake_user = {"id": _USER, "email": "sec@nubi.dev", "name": "Sec"}

    async def _fake_user():
        return fake_user

    async def _fake_writer():
        return None

    async def _fake_get_org(user_id, repo):
        return _org_id

    app.dependency_overrides[current_user] = _fake_user
    app.dependency_overrides[require_writer_default] = _fake_writer
    _orig = org_mod.get_user_org
    org_mod.get_user_org = _fake_get_org
    import app.webhooks.router as wh_router  # noqa: PLC0415

    wh_router._get_user_org = _fake_get_org
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
async def test_create_webhook_rejects_metadata_ip(webhook_api_client) -> None:
    """POST /webhooks with 169.254.169.254 must be rejected at registration time."""
    resp = await webhook_api_client.post(
        "/api/v1/webhooks/",
        json={
            "url": "http://169.254.169.254/latest/meta-data/",
            "secret": "supersecret",
            "event_types": [events.WATCH_BREACH],
        },
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "ssrf_blocked"


@pytest.mark.asyncio
async def test_create_webhook_rejects_localhost(webhook_api_client) -> None:
    """POST /webhooks with localhost must be rejected at registration time."""
    resp = await webhook_api_client.post(
        "/api/v1/webhooks/",
        json={
            "url": "http://localhost:9000/internal",
            "secret": "supersecret",
            "event_types": [],
        },
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "ssrf_blocked"


@pytest.mark.asyncio
async def test_create_webhook_rejects_file_scheme(webhook_api_client) -> None:
    """POST /webhooks with file:// scheme must be rejected at registration time."""
    resp = await webhook_api_client.post(
        "/api/v1/webhooks/",
        json={
            "url": "file:///etc/passwd",
            "secret": "supersecret",
            "event_types": [],
        },
    )
    # Schema-layer validation (pydantic) fires first → 422; SSRF guard gives 400.
    # Either is a rejection — the URL must not be accepted.
    assert resp.status_code in (400, 422), resp.text


@pytest.mark.asyncio
async def test_update_webhook_rejects_private_ip(webhook_api_client) -> None:
    """PUT /webhooks with a private IP URL must be rejected during rotation."""
    # First create a valid endpoint.
    fake_resolve = _getaddrinfo_returning("93.184.216.34")
    with patch.object(socket, "getaddrinfo", fake_resolve):
        create_resp = await webhook_api_client.post(
            "/api/v1/webhooks/",
            json={
                "url": "https://api.example.com/webhook",
                "secret": "supersecret1",
                "event_types": [events.FLOW_COMPLETED],
            },
        )
    assert create_resp.status_code == 201, create_resp.text
    wid = create_resp.json()["id"]

    # Attempt to update to a private IP URL.
    resp = await webhook_api_client.put(
        f"/api/v1/webhooks/{wid}",
        json={"url": "http://192.168.1.1/steal-data"},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "ssrf_blocked"


# ===========================================================================
# Pydantic schema-layer URL validation (strict, before SSRF guard)
# ===========================================================================


@pytest.mark.asyncio
async def test_create_webhook_rejects_ftp_scheme_at_schema_layer(webhook_api_client) -> None:
    """ftp:// scheme must be rejected by pydantic schema validation (422)."""
    resp = await webhook_api_client.post(
        "/api/v1/webhooks/",
        json={
            "url": "ftp://files.example.com/data",
            "secret": "supersecret",
            "event_types": [],
        },
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_create_webhook_rejects_javascript_scheme_at_schema_layer(webhook_api_client) -> None:
    """javascript:// scheme must be rejected by pydantic schema validation (422)."""
    resp = await webhook_api_client.post(
        "/api/v1/webhooks/",
        json={
            "url": "javascript:alert(1)",
            "secret": "supersecret",
            "event_types": [],
        },
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_create_webhook_rejects_url_with_no_host(webhook_api_client) -> None:
    """A URL with no host component must be rejected at the schema layer (422)."""
    resp = await webhook_api_client.post(
        "/api/v1/webhooks/",
        json={
            "url": "https:///path/only",
            "secret": "supersecret",
            "event_types": [],
        },
    )
    assert resp.status_code == 422, resp.text


# ===========================================================================
# Pydantic WebhookCreate schema unit tests (no HTTP round-trip needed)
# ===========================================================================


def test_webhook_create_rejects_file_scheme() -> None:
    """WebhookCreate pydantic model rejects file:// at parse time."""
    import pytest
    from pydantic import ValidationError
    from app.webhooks.schemas import WebhookCreate

    with pytest.raises(ValidationError, match="scheme"):
        WebhookCreate(url="file:///etc/passwd", secret="longsecret", event_types=[])


def test_webhook_create_rejects_ftp_scheme() -> None:
    """WebhookCreate pydantic model rejects ftp:// at parse time."""
    import pytest
    from pydantic import ValidationError
    from app.webhooks.schemas import WebhookCreate

    with pytest.raises(ValidationError, match="scheme"):
        WebhookCreate(url="ftp://files.example.com/data", secret="longsecret", event_types=[])


def test_webhook_create_accepts_https() -> None:
    """WebhookCreate pydantic model accepts a valid https:// URL."""
    from app.webhooks.schemas import WebhookCreate

    wh = WebhookCreate(url="https://hook.example.com/recv", secret="longsecret", event_types=[])
    assert wh.url == "https://hook.example.com/recv"


def test_webhook_create_accepts_http() -> None:
    """WebhookCreate pydantic model accepts a valid http:// URL."""
    from app.webhooks.schemas import WebhookCreate

    wh = WebhookCreate(url="http://hook.example.com/recv", secret="longsecret", event_types=[])
    assert wh.url == "http://hook.example.com/recv"
