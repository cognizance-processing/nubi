"""Focused tests for the IP-keyed rate limiter (app/middleware/ratelimit.py).

The global suite runs with NUBI_RATELIMIT_ENABLED=false (conftest) because every
test shares one in-process TestClient → one client-IP bucket. These tests instead
re-enable the limiter in ISOLATION (mutating the singleton config + clearing the
bucket store under a fixture that restores afterwards) to verify the security
properties of the post-review rewrite:

  * throttling actually triggers once the per-IP cap is exceeded (not fail-open),
  * a spoofed LEFT-most X-Forwarded-For does NOT mint a fresh bucket
    (FINDING 1 — left-most XFF is attacker-controlled and must be ignored),
  * an unverified JWT ``org`` claim does NOT redirect/widen a bucket
    (FINDING 2 — key is the trusted IP, never the forgeable claim).
"""

from __future__ import annotations

import base64
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware import ratelimit


@pytest.fixture
def limited_app():
    """A tiny app with the limiter ENABLED and a low auth cap, isolated per test."""
    cfg = ratelimit._cfg
    # Snapshot whatever the singleton currently holds so we can restore it.
    saved = {
        "_loaded": getattr(cfg, "_loaded", False),
        "enabled": getattr(cfg, "enabled", False),
        "auth_rpm": getattr(cfg, "auth_rpm", 30),
        "query_rpm": getattr(cfg, "query_rpm", 120),
        "flowrun_rpm": getattr(cfg, "flowrun_rpm", 60),
        "burst_factor": getattr(cfg, "burst_factor", 1.5),
    }
    # Force a deterministic, tiny configuration: 3 auth req/min, no burst headroom.
    cfg._loaded = True
    cfg.enabled = True
    cfg.auth_rpm = 3
    cfg.query_rpm = 3
    cfg.flowrun_rpm = 3
    cfg.burst_factor = 1.0
    ratelimit._buckets.clear()

    app = FastAPI()
    ratelimit.register_ratelimit(app)

    @app.post("/api/v1/auth/login")
    async def _login() -> dict[str, str]:
        return {"ok": "yes"}

    try:
        yield app
    finally:
        ratelimit._buckets.clear()
        for k, v in saved.items():
            setattr(cfg, k, v)


def test_limiter_throttles_after_cap(limited_app):
    """The 4th auth request from one client (cap=3, burst=1.0) is rejected 429."""
    client = TestClient(limited_app)
    codes = [client.post("/api/v1/auth/login").status_code for _ in range(6)]
    assert codes[:3] == [200, 200, 200], codes
    assert 429 in codes[3:], codes


def test_leftmost_xff_spoof_does_not_grant_fresh_bucket(limited_app):
    """A unique forged left-most X-Forwarded-For per request must NOT bypass the cap.

    All requests share the same TCP peer (the TestClient host), so the key stays
    ip:testclient regardless of the attacker-supplied XFF — the brute-force bypass
    the review flagged is closed.
    """
    client = TestClient(limited_app)
    codes = []
    for i in range(6):
        codes.append(
            client.post(
                "/api/v1/auth/login",
                headers={"X-Forwarded-For": f"10.0.0.{i}"},  # spoofed, unique each time
            ).status_code
        )
    assert 429 in codes, codes


def test_forged_org_claim_does_not_redirect_bucket(limited_app):
    """An unsigned bearer token with an arbitrary ``org`` claim must not key the bucket.

    The limiter keys on the trusted IP, so rotating a forged org per request can't
    mint fresh buckets (FINDING 2). All requests still collapse to one IP bucket.
    """
    client = TestClient(limited_app)

    def forged_token(org: str) -> str:
        payload = base64.urlsafe_b64encode(json.dumps({"org": org}).encode()).decode()
        return f"header.{payload}.sig"

    codes = []
    for i in range(6):
        codes.append(
            client.post(
                "/api/v1/auth/login",
                headers={"Authorization": f"Bearer {forged_token(f'victim-{i}')}"},
            ).status_code
        )
    assert 429 in codes, codes


def test_disabled_flag_is_noop(limited_app):
    """With enabled=False the middleware passes everything through (dev/test path)."""
    ratelimit._cfg.enabled = False
    ratelimit._buckets.clear()
    client = TestClient(limited_app)
    codes = [client.post("/api/v1/auth/login").status_code for _ in range(10)]
    assert all(c == 200 for c in codes), codes


# ── Redis-backed (distributed) limiter ───────────────────────────────────────────
#
# The production path enforces the cap GLOBALLY via an atomic Lua token-bucket in
# a shared Redis store. We exercise it WITHOUT a real server using a tiny
# dict-backed fake that deterministically emulates the one script the limiter
# evaluates (`_LUA_TOKEN_BUCKET`). We monkeypatch `app.cache.redis_client.get_redis`
# so `redis_available()` returns True and `dispatch` routes through the fake.


class _FakeRedis:
    """Minimal in-memory fake emulating ONLY `eval(_LUA_TOKEN_BUCKET, ...)`.

    State is a dict of hash-key -> {"tokens", "ts"} so the counter is GLOBAL
    across every request in the test (a single store, like real Redis would be).
    The arithmetic mirrors the Lua script exactly so the fake and the server stay
    in lock-step.
    """

    def __init__(self) -> None:
        self.store: dict[str, dict[str, float]] = {}
        self.calls = 0

    def eval(self, script, numkeys, key, capacity, refill, now, ttl):  # noqa: ARG002
        self.calls += 1
        capacity = float(capacity)
        refill = float(refill)
        now = float(now)

        h = self.store.get(key)
        if h is None:
            tokens = capacity
            last = now
        else:
            tokens = h["tokens"]
            last = h["ts"]

        elapsed = max(0.0, now - last)
        tokens = min(capacity, tokens + elapsed * refill)

        if tokens >= 1.0:
            tokens -= 1.0
            allowed, retry_after = 1, 0
        else:
            import math

            retry_after = max(1, math.ceil((1.0 - tokens) / refill))
            allowed = 0

        self.store[key] = {"tokens": tokens, "ts": now}
        return [allowed, retry_after]


class _BoomRedis:
    """Fake whose eval always raises — exercises the graceful-degradation path."""

    def eval(self, *args, **kwargs):  # noqa: ARG002
        raise RuntimeError("redis exploded mid-request")


@pytest.fixture
def fake_redis(monkeypatch):
    """Patch get_redis to return a shared _FakeRedis; restore afterwards.

    Yields the fake so a test can inspect its state / swap it for _BoomRedis.
    """
    holder = {"client": _FakeRedis()}

    # Patch the symbol imported INTO ratelimit (it does `from ... import get_redis`).
    monkeypatch.setattr(ratelimit, "get_redis", lambda: holder["client"])
    yield holder


def test_redis_path_throttles_after_cap(limited_app, fake_redis):
    """The Redis (global) token-bucket throttles past the cap, just like in-process."""
    client = TestClient(limited_app)
    codes = [client.post("/api/v1/auth/login").status_code for _ in range(6)]
    assert codes[:3] == [200, 200, 200], codes
    assert 429 in codes[3:], codes
    # Confirmed the request actually went through the fake (not the in-process dict).
    assert fake_redis["client"].calls == 6
    # And nothing leaked into the in-process store.
    assert ratelimit._buckets == {}


def test_redis_path_distinct_identities_are_independent(limited_app, fake_redis):
    """Two different client identities get independent GLOBAL buckets.

    Distinct right-most XFF entries (with no TCP peer override) would normally key
    differently; here the TestClient peer is constant, so we drive distinct
    identities by going through `_consume` directly with two bucket keys and
    asserting one being capped does not throttle the other.
    """
    mw = ratelimit.RateLimitMiddleware(app=limited_app)
    rpm = ratelimit._cfg.auth_rpm  # 3, burst_factor 1.0 → capacity 3

    # Drain identity A's bucket.
    a_codes = [mw._consume(("ip:1.1.1.1", "auth"), rpm)[0] for _ in range(6)]
    assert a_codes[:3] == [True, True, True], a_codes
    assert False in a_codes[3:], a_codes

    # Identity B is untouched — its first calls are still allowed.
    b_first = mw._consume(("ip:2.2.2.2", "auth"), rpm)[0]
    assert b_first is True

    # The fake holds two independent hash keys.
    keys = set(fake_redis["client"].store.keys())
    assert keys == {"nubi:rl:ip:1.1.1.1:auth", "nubi:rl:ip:2.2.2.2:auth"}, keys


def test_redis_error_degrades_to_in_process_no_500(limited_app, fake_redis):
    """A Redis exception mid-request degrades to the in-process bucket — never 500."""
    fake_redis["client"] = _BoomRedis()
    ratelimit._buckets.clear()
    client = TestClient(limited_app)

    codes = [client.post("/api/v1/auth/login").status_code for _ in range(6)]
    # No 500s — every response is either served (200) or throttled (429).
    assert all(c in (200, 429) for c in codes), codes
    assert codes[:3] == [200, 200, 200], codes
    # Fell back to the in-process guard, so the cap is still enforced.
    assert 429 in codes[3:], codes
    # The fallback actually used the in-process store.
    assert ratelimit._buckets, "expected in-process fallback bucket to be created"
