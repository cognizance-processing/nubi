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


# ---------------------------------------------------------------------------
# Each rule on its own
# ---------------------------------------------------------------------------


def test_a_successful_fetch_forgets_an_earlier_failure(monkeypatch):
    from app.auth import jwks_cache
    from app.errors import AppError

    clock = {"now": 1000.0}
    outcomes = [RuntimeError("down"), _Resp()]

    def _fetch(*_a, **_k):
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(jwks_cache.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(jwks_cache, "resolve_and_pin", lambda url: url)
    monkeypatch.setattr(jwks_cache, "_fetch_pinned", _fetch)
    uri = "https://idp.example.com/a.json"

    with pytest.raises(AppError):
        jwks_cache.get_jwks(uri)
    assert uri in jwks_cache._failures
    clock["now"] += jwks_cache._NEGATIVE_TTL_SECONDS + 1  # the remembered failure has lapsed
    assert jwks_cache.get_jwks(uri) == JWKS
    assert uri not in jwks_cache._failures  # nothing left behind for a healthy key set


def test_clearing_the_cache_also_forgets_failures(monkeypatch):
    from app.auth import jwks_cache
    from app.errors import AppError

    calls: list = []

    def _fetch(*_a, **_k):
        calls.append(1)
        raise RuntimeError("down")

    monkeypatch.setattr(jwks_cache, "resolve_and_pin", lambda url: url)
    monkeypatch.setattr(jwks_cache, "_fetch_pinned", _fetch)
    uri = "https://idp.example.com/a.json"
    with pytest.raises(AppError):
        jwks_cache.get_jwks(uri)
    jwks_cache.clear_cache()
    with pytest.raises(AppError):
        jwks_cache.get_jwks(uri)
    assert len(calls) == 2  # tried again: the failure was not kept across a clear


def test_jwks_fetch_gives_up_quickly(monkeypatch):
    from app.auth import jwks_cache

    seen: dict = {}

    def _fetch(httpx, method, pinned, headers, timeout=None):
        seen["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr(jwks_cache, "resolve_and_pin", lambda url: url)
    monkeypatch.setattr(jwks_cache, "_fetch_pinned", _fetch)
    jwks_cache.get_jwks("https://idp.example.com/a.json")
    timeout = seen["timeout"]
    limits = [timeout.connect, timeout.read, timeout.write, timeout.pool]
    assert all(limit is not None and limit <= 10 for limit in limits), limits


@pytest.mark.parametrize("body", [{"keys": "nope"}, {"keys": {"kid": "k"}}, {"keys": None}, {"nokeys": []}, ["keys"], "keys"])
def test_a_document_without_a_key_list_is_refused_and_not_cached(monkeypatch, body):
    from app.auth import jwks_cache
    from app.errors import AppError

    monkeypatch.setattr(jwks_cache, "resolve_and_pin", lambda url: url)
    monkeypatch.setattr(jwks_cache, "_fetch_pinned", lambda *a, **k: _Resp(body=body))
    uri = "https://idp.example.com/odd.json"
    with pytest.raises(AppError) as exc:
        jwks_cache.get_jwks(uri)
    assert exc.value.status == 401
    assert jwks_cache.get_cached(uri) is None


@pytest.mark.asyncio
async def test_stored_issuer_keys_are_fetched_off_the_event_loop(monkeypatch):
    from app.security import jwks as jwks_mod

    fetch = AsyncMock(return_value=JWKS)
    monkeypatch.setattr("app.auth.jwks_cache.get_jwks_async", fetch)
    monkeypatch.setattr(jwks_mod, "resolve_signing_key", lambda row, kid, alg: "KEY")

    row = {"jwks_url": "https://idp.example.com/a.json", "static_jwks_json": None}
    assert await jwks_mod.resolve_signing_key_async(row, "k", "RS256") == "KEY"
    fetch.assert_awaited_once_with("https://idp.example.com/a.json")

    fetch.reset_mock()
    static = {"jwks_url": "https://idp.example.com/a.json", "static_jwks_json": JWKS}
    assert await jwks_mod.resolve_signing_key_async(static, "k", "RS256") == "KEY"
    fetch.assert_not_awaited()  # a configured key set needs no request at all


@pytest.mark.asyncio
async def test_async_verifier_resolves_a_stored_issuers_key_asynchronously(monkeypatch):
    from app.auth import verify
    from app.security import jwks as jwks_mod

    org = str(uuid.uuid4())
    row = {"org_id": org, "jwks_url": "https://idp.example.com/a.json", "audience": "aud", "allowed_origins": []}
    store = AsyncMock()
    store.get_enabled_by_iss = AsyncMock(return_value=row)
    resolve_async = AsyncMock(return_value="KEY")

    def _sync_resolver(*_a, **_k):
        raise AssertionError("the blocking key resolver ran on the event loop")

    monkeypatch.setattr(verify, "_peek_embed_token", lambda token, alg: ("https://idp.example.com", {"org": org}))
    monkeypatch.setattr(verify, "_registry_issuer_for", lambda iss, org_id: None)
    monkeypatch.setattr("app.security.issuers_store.get_issuers_store", lambda: store)
    monkeypatch.setattr(jwks_mod, "resolve_signing_key_async", resolve_async)
    monkeypatch.setattr(jwks_mod, "resolve_signing_key", _sync_resolver)
    monkeypatch.setattr(verify, "_finish_embed_verification", lambda token, alg, iss, key, *a, **k: ("verified-with", key))

    out = await verify._verify_embed_token_async("tok", {"kid": "k"}, "RS256", None)
    assert out == ("verified-with", "KEY")
    resolve_async.assert_awaited_once_with(row, "k", "RS256")


@pytest.mark.parametrize(
    "endpoint,ok",
    [
        ("https://fcm.googleapis.com:443/fcm/send/abc", True),
        ("https://fcm.googleapis.com:8443/fcm/send/abc", False),
        ("https://fcm.googleapis.com:80/fcm/send/abc", False),
        ("https://api.push.apple.com/3/device/x", True),
        ("https://evilpush.apple.com/3/device/x", False),
        ("https://notpush.services.mozilla.com/wpush/v2/x", False),
        ("https://evilnotify.windows.com/w/?token=x", False),
        ("https://db5.notify.windows.com/w/?token=x", True),
    ],
)
def test_push_endpoint_port_and_host_boundaries(endpoint, ok):
    from app.notify.push import push_endpoint_allowed

    assert push_endpoint_allowed(endpoint) is ok


def test_operator_can_allow_additional_push_hosts(monkeypatch):
    from app.notify.push import push_endpoint_allowed

    monkeypatch.delenv("NUBI_PUSH_ALLOWED_HOSTS", raising=False)
    assert push_endpoint_allowed("https://push.example.org/send/x") is False
    monkeypatch.setenv("NUBI_PUSH_ALLOWED_HOSTS", "other.example.org, Push.Example.org")
    assert push_endpoint_allowed("https://push.example.org/send/x") is True
    assert push_endpoint_allowed("https://sub.push.example.org/send/x") is False  # exact hosts only
    assert push_endpoint_allowed("http://push.example.org/send/x") is False  # still https only
