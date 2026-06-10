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
