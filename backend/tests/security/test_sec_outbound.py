"""Requests the platform makes on a tenant's behalf go to checked, pinned targets
and never stall the event loop."""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from tests.security import conftest_helpers  # noqa: F401  (env bootstrap)

JWKS = {"keys": [{"kty": "RSA", "kid": "k", "n": "AQAB", "e": "AQAB"}]}


@pytest.fixture(autouse=True)
def _clean_cache():
    from app.auth import jwks_cache

    jwks_cache.clear_cache()
    yield
    jwks_cache.clear_cache()


class _Resp:
    def __init__(self, body=JWKS, status=200, content=None):
        self._body, self.status_code = body, status
        self.content = content if content is not None else b"{}"

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("http error")

    def json(self):
        return self._body


@pytest.mark.parametrize(
    "uri",
    ["https://127.0.0.1/jwks.json", "https://169.254.169.254/latest", "https://localhost/jwks.json",
     "http://idp.example.com/jwks.json", "file:///etc/passwd"],
)
def test_jwks_is_never_fetched_from_internal_or_plain_targets(uri):
    from app.auth import jwks_cache
    from app.errors import AppError

    with patch.object(jwks_cache, "_fetch_pinned", side_effect=AssertionError("fetched")) as fetch:
        with pytest.raises(AppError) as exc:
            jwks_cache.get_jwks(uri)
    assert exc.value.status == 401
    fetch.assert_not_called()


def test_jwks_fetch_is_pinned_cached_and_negative_cached(monkeypatch):
    from app.auth import jwks_cache
    from app.errors import AppError

    calls: list = []

    def _fetch(httpx, method, pinned, headers, timeout=None):
        calls.append(pinned)
        if len(calls) == 1:
            return _Resp()
        raise RuntimeError("unreachable")

    monkeypatch.setattr(jwks_cache, "_fetch_pinned", _fetch)
    monkeypatch.setattr(jwks_cache, "resolve_and_pin", lambda url: ("pinned", url))

    ok = "https://idp.example.com/a.json"
    assert jwks_cache.get_jwks(ok) == JWKS
    assert jwks_cache.get_jwks(ok) == JWKS  # cached
    assert len(calls) == 1 and calls[0] == ("pinned", ok)  # fetched through the pinned target

    bad = "https://idp.example.com/b.json"
    for _ in range(3):
        with pytest.raises(AppError):
            jwks_cache.get_jwks(bad)
    assert len(calls) == 2  # one failed attempt, then the failure is remembered


def test_oversized_jwks_documents_are_refused(monkeypatch):
    from app.auth import jwks_cache
    from app.errors import AppError

    monkeypatch.setattr(jwks_cache, "resolve_and_pin", lambda url: url)
    monkeypatch.setattr(jwks_cache, "_fetch_pinned", lambda *a, **k: _Resp(content=b"x" * (2 * 1024 * 1024)))
    with pytest.raises(AppError):
        jwks_cache.get_jwks("https://idp.example.com/big.json")


def test_cached_lookup_never_touches_the_network(monkeypatch):
    from app.auth import jwks_cache

    monkeypatch.setattr(jwks_cache, "_fetch_pinned", lambda *a, **k: (_ for _ in ()).throw(AssertionError("fetched")))
    assert jwks_cache.get_cached("https://idp.example.com/a.json") is None


@pytest.mark.asyncio
async def test_async_fetch_does_not_block_the_event_loop(monkeypatch):
    import time

    from app.auth import jwks_cache

    def _slow(*a, **k):
        time.sleep(0.4)
        return _Resp()

    monkeypatch.setattr(jwks_cache, "resolve_and_pin", lambda url: url)
    monkeypatch.setattr(jwks_cache, "_fetch_pinned", _slow)

    ticks = 0

    async def _ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1

    task = asyncio.create_task(_ticker())
    try:
        assert await jwks_cache.get_jwks_async("https://idp.example.com/a.json") == JWKS
    finally:
        task.cancel()
    assert ticks >= 5, "the event loop was blocked while the key set was fetched"


def test_sync_verification_only_uses_cached_keys():
    import inspect

    from app.auth import verify

    assert "allow_fetch=False" in inspect.getsource(verify._verify_embed_token)
    src = inspect.getsource(verify._verify_embed_token_async)
    assert "_resolve_jwks_async" in src and "resolve_signing_key_async" in src


# ---------------------------------------------------------------------------
# Web push
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "endpoint,ok",
    [
        ("https://fcm.googleapis.com/fcm/send/abc", True),
        ("https://updates.push.services.mozilla.com/wpush/v2/abc", True),
        ("https://web.push.apple.com/abc", True),
        ("https://wns2-par02p.notify.windows.com/w/?token=abc", True),
        ("https://evil.example/collect", False),
        ("https://fcm.googleapis.com.evil.example/x", False),
        ("http://fcm.googleapis.com/fcm/send/abc", False),
        ("https://127.0.0.1/x", False),
        ("https://169.254.169.254/latest/meta-data", False),
        ("https://user:pw@fcm.googleapis.com/x", False),
        ("", False),
    ],
)
def test_push_endpoints_must_be_a_known_push_service(endpoint, ok):
    from app.notify.push import push_endpoint_allowed

    assert push_endpoint_allowed(endpoint) is ok


def test_subscribe_refuses_unknown_endpoints():
    from fastapi.testclient import TestClient

    from app.repos.provider import set_repo
    from tests.security.conftest_helpers import as_user, make_tenant_app

    org = str(uuid.uuid4())
    app, _repo, _users = make_tenant_app({"u": (org, "member")}, modules=("app.routes.push",))
    store = AsyncMock()
    try:
        with patch("app.routes.push.get_push_store", return_value=store), TestClient(app, raise_server_exceptions=False) as c:
            resp = c.post(
                "/api/v1/push/subscribe",
                json={"endpoint": "https://evil.example/collect", "keys": {"p256dh": "a", "auth": "b"}},
                headers=as_user("u"),
            )
        assert resp.status_code == 400, resp.text
        store.upsert.assert_not_called()
    finally:
        set_repo(None)


def test_send_skips_endpoints_that_are_no_longer_allowed():
    from app.notify import push

    with patch.object(push, "push_configured", side_effect=AssertionError("went on to send")):
        result = push.send_push({"endpoint": "https://evil.example/x", "keys": {"p256dh": "a", "auth": "b"}}, {"t": 1})
    assert result == push.PUSH_GONE  # treated as a dead subscription and pruned
