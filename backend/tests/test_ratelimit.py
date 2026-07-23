"""Focused tests for the rate limiter (app/middleware/ratelimit.py).

The global suite runs with NUBI_RATELIMIT_ENABLED=false (conftest) because every
test shares one in-process TestClient → one client-IP bucket. These tests instead
re-enable the limiter in ISOLATION (mutating the singleton config + clearing the
bucket store under a fixture that restores afterwards) to verify the security
properties of the post-review rewrite:

  * throttling actually triggers once the per-IP cap is exceeded (not fail-open),
  * a spoofed LEFT-most X-Forwarded-For does NOT mint a fresh bucket
    (FINDING 1 — left-most XFF is attacker-controlled and must be ignored),
  * an unverified JWT ``org`` claim does NOT redirect/widen a bucket
    (FINDING 2 — key is the trusted IP, never the forgeable claim),
  * FINDING 6B — org-keyed + embed exemption:
    - two users in the same org share one org bucket,
    - two orgs have independent buckets,
    - a VERIFIED embed token is NOT throttled on /metrics/{id}/query paths,
    - a FORGED org claim or forged embed token does NOT get a fresh bucket or
      exemption (falls back to IP).
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
        "auth_session_rpm": getattr(cfg, "auth_session_rpm", 120),
        "query_rpm": getattr(cfg, "query_rpm", 120),
        "flowrun_rpm": getattr(cfg, "flowrun_rpm", 60),
        "burst_factor": getattr(cfg, "burst_factor", 1.5),
    }
    # Force a deterministic, tiny configuration: 3 auth req/min, no burst headroom.
    cfg._loaded = True
    cfg.enabled = True
    cfg.auth_rpm = 3
    cfg.auth_session_rpm = 3
    cfg.query_rpm = 3
    cfg.flowrun_rpm = 3
    cfg.burst_factor = 1.0
    ratelimit._buckets.clear()

    app = FastAPI()
    ratelimit.register_ratelimit(app)

    @app.post("/api/v1/auth/login")
    async def _login() -> dict[str, str]:
        return {"ok": "yes"}

    @app.post("/api/v1/auth/refresh")
    async def _refresh() -> dict[str, str]:
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


def test_auth_refresh_has_its_own_bucket_from_login(limited_app):
    """/auth/refresh must not share /auth/login's credential-guessing bucket.

    Regression test: both used to classify as the same 'auth' route_class, so
    ordinary page-load refresh probes could exhaust the same 30 rpm budget
    meant to throttle login brute-forcing (and vice versa) — see
    AuthContext.jsx's restoreSession, which force-logs-out on ANY refresh
    failure including a transient 429 from this shared bucket.
    """
    client = TestClient(limited_app)

    # Exhaust the login bucket (cap=3) from one client.
    login_codes = [client.post("/api/v1/auth/login").status_code for _ in range(6)]
    assert 429 in login_codes, login_codes

    # /auth/refresh from the SAME client must still succeed — independent bucket.
    refresh_codes = [client.post("/api/v1/auth/refresh").status_code for _ in range(3)]
    assert refresh_codes == [200, 200, 200], refresh_codes


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


def test_board_provider_data_route_classified_as_query(limited_app):
    """POST /api/v1/boards/<id>/providers/<pid>/data is classified into the 'query'
    bucket so embed cache-busting requests face the same hard rpm ceiling as regular
    queries — closing the embed compute-amplification vector (MED finding 1a).
    """
    from app.middleware.ratelimit import _classify

    path = "/api/v1/boards/board-abc-123/providers/provider-xyz/data"
    route_class, rpm = _classify(path)
    assert route_class == "query", (
        f"Board provider data route must be classified as 'query'; got {route_class!r}. "
        "Embed tokens can otherwise cache-bust indefinitely without hitting the rpm ceiling."
    )
    assert rpm > 0, "query rpm must be positive"


def test_board_provider_data_route_throttles_after_cap(limited_app):
    """The board provider data route IS throttled once the query cap (3) is exceeded."""
    # Register a dummy endpoint on the app under test so the route exists.
    @limited_app.post("/api/v1/boards/{board_id}/providers/{pid}/data")
    async def _dummy_provider_data(board_id: str, pid: str) -> dict:
        return {"ok": True}

    client = TestClient(limited_app)
    codes = [
        client.post(f"/api/v1/boards/b1/providers/p1/data").status_code
        for _ in range(6)
    ]
    # First 3 should pass (cap=3, burst_factor=1.0); 4th onwards should be 429.
    assert codes[:3] == [200, 200, 200], codes
    assert 429 in codes[3:], codes


def test_backfill_route_classified_as_flow_run(limited_app):
    """/flows/<id>/backfill must be classified as 'flow-run' (not SKIP).

    Backfill holds a worker for up to 600 s — same resource shape as a flow run
    — so it must share the flowrun_rpm bucket to prevent worker-pool exhaustion.
    """
    from app.middleware.ratelimit import _classify

    path = "/api/v1/flows/flow-abc-123/backfill"
    route_class, rpm = _classify(path)
    assert route_class == "flow-run", (
        f"Backfill route must be classified as 'flow-run'; got {route_class!r}. "
        "An unclassified backfill route allows unlimited concurrent worker-pool exhaustion."
    )
    assert rpm > 0, "flow-run rpm must be positive"


def test_sweep_route_classified_as_flow_run(limited_app):
    """/flows/<id>/sweep must be classified as 'flow-run' (not SKIP).

    Sweep holds a worker for up to 300 s — same resource shape as a flow run
    — so it must share the flowrun_rpm bucket.
    """
    from app.middleware.ratelimit import _classify

    path = "/api/v1/flows/flow-abc-123/sweep"
    route_class, rpm = _classify(path)
    assert route_class == "flow-run", (
        f"Sweep route must be classified as 'flow-run'; got {route_class!r}. "
        "An unclassified sweep route allows unlimited concurrent worker-pool exhaustion."
    )
    assert rpm > 0, "flow-run rpm must be positive"


def test_backfill_route_throttles_after_cap(limited_app):
    """The backfill route IS throttled once the flow-run cap (3) is exceeded."""

    @limited_app.post("/api/v1/flows/{flow_id}/backfill")
    async def _dummy_backfill(flow_id: str) -> dict:
        return {"ok": True}

    client = TestClient(limited_app)
    codes = [
        client.post("/api/v1/flows/f1/backfill").status_code
        for _ in range(6)
    ]
    assert codes[:3] == [200, 200, 200], codes
    assert 429 in codes[3:], codes


def test_sweep_route_throttles_after_cap(limited_app):
    """The sweep route IS throttled once the flow-run cap (3) is exceeded."""

    @limited_app.post("/api/v1/flows/{flow_id}/sweep")
    async def _dummy_sweep(flow_id: str) -> dict:
        return {"ok": True}

    client = TestClient(limited_app)
    codes = [
        client.post("/api/v1/flows/f1/sweep").status_code
        for _ in range(6)
    ]
    assert codes[:3] == [200, 200, 200], codes
    assert 429 in codes[3:], codes


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


# ── FINDING 6B: org-keyed buckets + embed exemption ─────────────────────────
#
# These tests exercise the new cryptographic-token-keyed paths:
#   - verified first-party (HS256) tokens key by org:<org>
#   - two users in the same org share one bucket
#   - two orgs get independent buckets
#   - a VERIFIED embed (RS256) token is exempt on /metrics/{id}/query
#   - a forged org claim in an unsigned JWT falls back to IP (not a fresh bucket)
#   - a forged "embed" claim in an unsigned JWT does NOT get the exemption
#
# We use the same RSA keypair + IssuerRegistry pattern as test_embed_config.py.


import json as _json_mod
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
import jwt as pyjwt

# Generate a module-level RSA keypair (once) for embed token tests.
_RL_PRIVATE_KEY = rsa.generate_private_key(
    public_exponent=65537,
    key_size=2048,
    backend=default_backend(),
)
_RL_PUBLIC_KEY = _RL_PRIVATE_KEY.public_key()
_RL_JWKS_KEY: dict = _json_mod.loads(RSAAlgorithm.to_jwk(_RL_PUBLIC_KEY))
_RL_JWKS_KEY["kid"] = "rl-test-key"
_RL_JWKS_KEY["use"] = "sig"
_RL_STATIC_JWKS: dict = {"keys": [_RL_JWKS_KEY]}

_RL_ISS = "https://rl-test-embed-host.example"
_RL_AUD = "nubi"


def _mint_rl_embed_token(org: str = "embed-org", exp_delta: int = 300) -> str:
    """Mint a test RS256 embed JWT against the test keypair."""
    now = datetime.now(tz=timezone.utc)
    payload = {
        "iss": _RL_ISS,
        "aud": _RL_AUD,
        "sub": "embed-user",
        "org": org,
        "roles": ["viewer"],
        "policies": {},
        "scope": ["read:query"],
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=exp_delta)).timestamp()),
    }
    return pyjwt.encode(
        payload, _RL_PRIVATE_KEY, algorithm="RS256", headers={"kid": "rl-test-key"}
    )


def _mint_rl_first_party_token(org: str) -> str:
    """Mint a first-party HS256 access token carrying the given org claim."""
    from app.auth.jwt import mint_access_token
    return mint_access_token("00000000-0000-0000-0000-000000000001", extra_claims={"org": org})


@pytest.fixture
def rl_embed_issuer():
    """Register (and later unregister) the test embed issuer in the IssuerRegistry."""
    from app.auth.issuers import get_issuer_registry
    from app.auth.jwks_cache import clear_cache

    registry = get_issuer_registry()
    registry.register(
        _RL_ISS,
        jwks_uri=f"{_RL_ISS}/.well-known/jwks.json",
        aud=_RL_AUD,
        allowed_origins=[],
        static_jwks=_RL_STATIC_JWKS,
    )
    yield
    registry.unregister(_RL_ISS)
    clear_cache()


@pytest.fixture
def limited_app_6b(rl_embed_issuer):
    """An isolated limited app with low caps; registers query + metrics endpoints."""
    cfg = ratelimit._cfg
    saved = {
        "_loaded": getattr(cfg, "_loaded", False),
        "enabled": getattr(cfg, "enabled", False),
        "auth_rpm": getattr(cfg, "auth_rpm", 30),
        "query_rpm": getattr(cfg, "query_rpm", 120),
        "flowrun_rpm": getattr(cfg, "flowrun_rpm", 60),
        "burst_factor": getattr(cfg, "burst_factor", 1.5),
    }
    cfg._loaded = True
    cfg.enabled = True
    cfg.auth_rpm = 3
    cfg.query_rpm = 3
    cfg.flowrun_rpm = 3
    cfg.burst_factor = 1.0
    ratelimit._buckets.clear()

    app = FastAPI()
    ratelimit.register_ratelimit(app)

    @app.post("/api/v1/metrics/{metric_id}/query")
    async def _metrics_query(metric_id: str) -> dict:
        return {"ok": True, "metric": metric_id}

    @app.post("/api/v1/query")
    async def _query() -> dict:
        return {"ok": True}

    @app.post("/api/v1/auth/login")
    async def _login() -> dict:
        return {"ok": True}

    try:
        yield app
    finally:
        ratelimit._buckets.clear()
        for k, v in saved.items():
            setattr(cfg, k, v)


# ── org-keyed buckets ─────────────────────────────────────────────────────────

def test_same_org_users_share_one_bucket(limited_app_6b):
    """Two verified first-party tokens with the SAME org share one org bucket.

    With cap=3 and burst=1.0, 6 requests from two users in org A should
    exhaust the org A bucket (the 4th request overall is 429).
    """
    token_a = _mint_rl_first_party_token("org-alpha")
    token_b = _mint_rl_first_party_token("org-alpha")  # same org, different sub

    client = TestClient(limited_app_6b)
    codes = []
    for i in range(6):
        # Alternate between the two tokens to prove they share one bucket.
        tok = token_a if i % 2 == 0 else token_b
        codes.append(
            client.post(
                "/api/v1/auth/login",
                headers={"Authorization": f"Bearer {tok}"},
            ).status_code
        )

    assert codes[:3] == [200, 200, 200], f"first 3 must pass; got {codes}"
    assert 429 in codes[3:], f"4th+ must be throttled; got {codes}"

    # Only ONE org bucket should exist (not two per-user buckets).
    bucket_keys = list(ratelimit._buckets.keys())
    org_keys = [k for k in bucket_keys if k[0] == "org:org-alpha"]
    assert len(org_keys) == 1, (
        f"expected exactly one org:org-alpha bucket; got {bucket_keys}"
    )


def test_different_orgs_have_independent_buckets(limited_app_6b):
    """Two verified tokens from different orgs get INDEPENDENT buckets.

    Draining org-beta's budget (cap=3) must NOT throttle org-gamma requests.
    """
    token_beta = _mint_rl_first_party_token("org-beta")
    token_gamma = _mint_rl_first_party_token("org-gamma")

    client = TestClient(limited_app_6b)

    # Drain org-beta completely (4 requests → 3 pass, 1 throttled).
    beta_codes = [
        client.post(
            "/api/v1/auth/login",
            headers={"Authorization": f"Bearer {token_beta}"},
        ).status_code
        for _ in range(4)
    ]
    assert 429 in beta_codes, f"org-beta should be throttled; got {beta_codes}"

    # org-gamma is untouched — first request must be allowed.
    gamma_first = client.post(
        "/api/v1/auth/login",
        headers={"Authorization": f"Bearer {token_gamma}"},
    ).status_code
    assert gamma_first == 200, (
        f"org-gamma must not be throttled by org-beta exhaustion; got {gamma_first}"
    )


# ── embed exemption on query/metrics read paths ───────────────────────────────

def test_verified_embed_token_not_throttled_on_metrics_query(limited_app_6b):
    """A VERIFIED RS256 embed token firing many metric tile requests is never throttled.

    cap=3, burst=1.0 → without exemption the 4th request would be 429.
    With the exemption all 10 requests must be 200.
    """
    token = _mint_rl_embed_token(org="cockpit-org")
    client = TestClient(limited_app_6b)

    codes = [
        client.post(
            "/api/v1/metrics/metric-abc-123/query",
            headers={"Authorization": f"Bearer {token}"},
        ).status_code
        for _ in range(10)
    ]
    assert all(c == 200 for c in codes), (
        f"verified embed token must not be throttled on /metrics/*/query; got {codes}"
    )


def test_verified_embed_token_not_throttled_on_query(limited_app_6b):
    """A VERIFIED RS256 embed token on /api/v1/query is also exempt."""
    token = _mint_rl_embed_token(org="cockpit-org2")
    client = TestClient(limited_app_6b)

    codes = [
        client.post(
            "/api/v1/query",
            headers={"Authorization": f"Bearer {token}"},
        ).status_code
        for _ in range(10)
    ]
    assert all(c == 200 for c in codes), (
        f"verified embed token must not be throttled on /api/v1/query; got {codes}"
    )


def test_first_party_token_still_throttled_on_metrics_query(limited_app_6b):
    """A first-party (HS256) token on /metrics/{id}/query IS still throttled (per-org)."""
    token = _mint_rl_first_party_token("org-internal")
    client = TestClient(limited_app_6b)

    codes = [
        client.post(
            "/api/v1/metrics/metric-xyz/query",
            headers={"Authorization": f"Bearer {token}"},
        ).status_code
        for _ in range(6)
    ]
    assert codes[:3] == [200, 200, 200], f"first 3 must pass; got {codes}"
    assert 429 in codes[3:], (
        f"first-party token must still be throttled on /metrics/*/query; got {codes}"
    )


# ── anti-forgery: forged org + forged embed kind ──────────────────────────────

def test_forged_org_claim_does_not_mint_fresh_org_bucket(limited_app_6b):
    """An unsigned JWT with a forged ``org`` claim must NOT get an org: bucket.

    The limiter must fall back to IP key — a forged org cannot escape the IP cap
    or poison another org's bucket.
    """
    def forged_token(org: str) -> str:
        # Build a raw JWT where each part is (non-cryptographically) base64-encoded.
        # This token has no valid signature.
        header = base64.urlsafe_b64encode(
            _json_mod.dumps({"alg": "HS256", "typ": "JWT"}).encode()
        ).rstrip(b"=").decode()
        payload_b64 = base64.urlsafe_b64encode(
            _json_mod.dumps({"org": org, "sub": "hacker", "exp": 9999999999}).encode()
        ).rstrip(b"=").decode()
        return f"{header}.{payload_b64}.invalidsig"

    client = TestClient(limited_app_6b)
    codes = []
    for i in range(6):
        # Each request uses a different forged org to try to mint N fresh buckets.
        codes.append(
            client.post(
                "/api/v1/auth/login",
                headers={"Authorization": f"Bearer {forged_token(f'victim-org-{i}')}"},
            ).status_code
        )
    # The forged tokens must NOT each get a fresh bucket — all collapse to one IP
    # bucket and throttling kicks in after cap=3.
    assert 429 in codes, (
        f"forged org claims must not bypass throttling; got {codes}"
    )
    # No org: key must have been created — only IP key.
    org_keys = [k for k in ratelimit._buckets if k[0].startswith("org:")]
    assert not org_keys, (
        f"forged org tokens must NOT create org: buckets; got {org_keys}"
    )


def test_forged_embed_token_not_exempt_on_metrics_query(limited_app_6b):
    """A FORGED embed token (unsigned) must NOT get the cockpit-tile exemption.

    Without the exemption it falls back to IP key and IS throttled after cap=3.
    """
    def forged_embed_token() -> str:
        # Forge a token claiming kind/alg RS256 but with a fake signature.
        header = base64.urlsafe_b64encode(
            _json_mod.dumps({"alg": "RS256", "typ": "JWT", "kid": "rl-test-key"}).encode()
        ).rstrip(b"=").decode()
        payload_b64 = base64.urlsafe_b64encode(
            _json_mod.dumps({
                "iss": _RL_ISS,
                "aud": _RL_AUD,
                "sub": "attacker",
                "org": "attacker-org",
                "kind": "embed",
                "exp": 9999999999,
            }).encode()
        ).rstrip(b"=").decode()
        return f"{header}.{payload_b64}.FORGEDSIGNATURE"

    client = TestClient(limited_app_6b)
    codes = [
        client.post(
            "/api/v1/metrics/metric-abc/query",
            headers={"Authorization": f"Bearer {forged_embed_token()}"},
        ).status_code
        for _ in range(6)
    ]
    # With cap=3, IP-keyed bucket will be exhausted — throttle must kick in.
    assert 429 in codes, (
        f"forged embed token must NOT be exempt; expected 429 in {codes}"
    )


def test_unauthenticated_traffic_is_ip_limited(limited_app_6b):
    """Unauthenticated requests (no Bearer token) are still IP-limited as before."""
    client = TestClient(limited_app_6b)
    codes = [
        client.post("/api/v1/auth/login").status_code
        for _ in range(6)
    ]
    assert codes[:3] == [200, 200, 200], f"first 3 must pass; got {codes}"
    assert 429 in codes[3:], f"unauthenticated traffic must be throttled; got {codes}"
    # Must use an ip: key, not an org: key.
    ip_keys = [k for k in ratelimit._buckets if k[0].startswith("ip:")]
    assert ip_keys, "unauthenticated traffic must produce ip: bucket keys"
