"""Security regression tests for webhook SSRF guard (wave-4 hardening).

Coverage
--------
1.  deliver_one() blocks cloud-metadata URL (169.254.169.254) — no HTTP call.
2.  deliver_one() blocks loopback URL (http://127.0.0.1).
3.  deliver_one() blocks RFC1918 URL (http://10.0.0.5).
4.  deliver_one() blocks a hostname that DNS-resolves to a private IP.
5.  deliver_one() returns False (not raises) so it never breaks the caller.
6.  deliver_one() allows and attempts a public URL.
7.  POST /webhooks create rejects a metadata IP URL at registration time (400).
8.  POST /webhooks create rejects a localhost URL at registration time.
9.  POST /webhooks create rejects a file:// URL at registration time.
10. PUT  /webhooks update rejects a private IP URL during rotation.
11. deliver_one() blocks non-http(s) scheme (file://).
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
from app.webhooks.models import InMemoryWebhookStore  # noqa: E402

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


class _TrackingClient:
    """Minimal async httpx.AsyncClient stand-in that records calls."""

    called = False

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def post(self, url, *, content, headers):
        type(self).called = True
        return _FakeResponse(200)


# ===========================================================================
# deliver_one — SSRF guard fires before any HTTP call
# ===========================================================================


@pytest.mark.asyncio
async def test_deliver_one_blocks_metadata_ip(monkeypatch) -> None:
    """Cloud metadata IP is blocked; no HTTP request is made and no exception raised."""
    import httpx

    class C(_TrackingClient):
        called = False

    monkeypatch.setattr(httpx, "AsyncClient", C)
    result = await delivery.deliver_one(
        "http://169.254.169.254/latest/meta-data/",
        "secret",
        events.WATCH_BREACH,
        {"x": 1},
    )
    assert result is False, "deliver_one must return False for blocked URL"
    assert C.called is False, "no HTTP request must be attempted to blocked URL"


@pytest.mark.asyncio
async def test_deliver_one_blocks_loopback(monkeypatch) -> None:
    """Loopback URL is blocked before any HTTP call."""
    import httpx

    class C(_TrackingClient):
        called = False

    monkeypatch.setattr(httpx, "AsyncClient", C)
    result = await delivery.deliver_one(
        "http://127.0.0.1:8080/internal",
        "secret",
        events.WATCH_BREACH,
        {"x": 1},
    )
    assert result is False
    assert C.called is False


@pytest.mark.asyncio
async def test_deliver_one_blocks_rfc1918(monkeypatch) -> None:
    """RFC1918 private IP is blocked before any HTTP call."""
    import httpx

    class C(_TrackingClient):
        called = False

    monkeypatch.setattr(httpx, "AsyncClient", C)
    result = await delivery.deliver_one(
        "http://10.0.0.5/webhook-sink",
        "secret",
        events.QUERY_FAILED,
        {"x": 1},
    )
    assert result is False
    assert C.called is False


@pytest.mark.asyncio
async def test_deliver_one_blocks_hostname_resolving_to_private(monkeypatch) -> None:
    """A hostname that resolves to a private IP is blocked (DNS-rebind guard)."""
    import httpx

    class C(_TrackingClient):
        called = False

    monkeypatch.setattr(httpx, "AsyncClient", C)
    fake_resolve = _getaddrinfo_returning("192.168.1.1")
    with patch.object(socket, "getaddrinfo", fake_resolve):
        result = await delivery.deliver_one(
            "http://internal.attacker.example/sink",
            "secret",
            events.WATCH_BREACH,
            {"x": 1},
        )
    assert result is False
    assert C.called is False


@pytest.mark.asyncio
async def test_deliver_one_blocks_file_scheme(monkeypatch) -> None:
    """Non-http(s) scheme is blocked."""
    import httpx

    class C(_TrackingClient):
        called = False

    monkeypatch.setattr(httpx, "AsyncClient", C)
    result = await delivery.deliver_one(
        "file:///etc/passwd",
        "secret",
        events.WATCH_BREACH,
        {"x": 1},
    )
    assert result is False
    assert C.called is False


@pytest.mark.asyncio
async def test_deliver_one_never_raises_on_ssrf_block(monkeypatch) -> None:
    """deliver_one is fire-and-forget: SSRF block must never raise."""
    import httpx

    class C(_TrackingClient):
        called = False

    monkeypatch.setattr(httpx, "AsyncClient", C)
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
    """A public URL passes the SSRF guard and the HTTP call is attempted."""
    import httpx

    class C(_TrackingClient):
        called = False

    monkeypatch.setattr(httpx, "AsyncClient", C)
    fake_resolve = _getaddrinfo_returning("93.184.216.34")
    with patch.object(socket, "getaddrinfo", fake_resolve):
        result = await delivery.deliver_one(
            "https://api.example.com/webhook",
            "secret",
            events.FLOW_COMPLETED,
            {"x": 1},
        )
    assert result is True
    assert C.called is True, "HTTP call must be attempted for a public URL"


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
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "ssrf_blocked"


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
