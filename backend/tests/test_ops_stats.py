"""Tests for the in-process latency recorder + the GET /ops/stats endpoint.

Two halves:

1. Unit tests for ``app.observability.latency.LatencyRecorder`` — percentile
   sanity, ring-buffer bound, all-time ``count`` accuracy, the synthetic ``all``
   bucket, bucket-cap overflow into ``other``, and ``reset()``.
2. Endpoint tests for ``GET /ops/stats`` — shape (latency/cache/rate_limits/
   uptime/version keys) with first-party auth; ``401`` for unauthenticated and
   for embed-style (non first-party) tokens.

Uses the conftest ``app`` / ``client`` / ``fake_db`` fixtures and the
``mint_access_token`` auth pattern from test_admin.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest

from app.auth.jwt import mint_access_token
from app.observability.latency import (
    ALL_BUCKET,
    OTHER_BUCKET,
    LatencyRecorder,
)

# Self-registering router (idempotent; main.py imports it too).
import app.routes.ops  # noqa: F401, E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auth_headers(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


def _make_user(user_id: str, email: str) -> dict[str, Any]:
    return {
        "id": user_id,
        "email": email,
        "name": "Ops User",
        "avatar_url": None,
        "email_verified": True,
        "is_superadmin": False,
        "created_at": datetime.now(tz=timezone.utc),
    }


# ---------------------------------------------------------------------------
# 1. Recorder unit tests
# ---------------------------------------------------------------------------


class TestLatencyRecorder:
    def test_percentiles_are_sane(self):
        rec = LatencyRecorder()
        # 1..100 ms into a single bucket.
        for i in range(1, 101):
            rec.record("query", float(i))
        snap = rec.snapshot()
        q = snap["query"]
        assert q["count"] == 100
        # Nearest-rank over 1..100: p50→50, p95→95, p99→99, max→100.
        assert q["p50"] == 50.0
        assert q["p95"] == 95.0
        assert q["p99"] == 99.0
        assert q["max"] == 100.0
        assert q["mean"] == pytest.approx(50.5)

    def test_all_bucket_aggregates_every_sample(self):
        rec = LatencyRecorder()
        rec.record("auth", 10.0)
        rec.record("query", 20.0)
        rec.record("flow-run", 30.0)
        snap = rec.snapshot()
        assert snap[ALL_BUCKET]["count"] == 3
        assert snap[ALL_BUCKET]["max"] == 30.0
        # Each keyed bucket counted once.
        assert snap["auth"]["count"] == 1
        assert snap["query"]["count"] == 1
        assert snap["flow-run"]["count"] == 1

    def test_count_is_all_time_not_ring_size(self):
        rec = LatencyRecorder(ring_size=10)
        for i in range(100):
            rec.record("query", float(i))
        snap = rec.snapshot()
        # count tracks all 100 observed samples even though the ring holds 10.
        assert snap["query"]["count"] == 100
        # Percentiles are computed over the retained window (last 10: 90..99).
        assert snap["query"]["max"] == 99.0
        assert snap["query"]["p50"] >= 90.0

    def test_ring_bound_respected(self):
        rec = LatencyRecorder(ring_size=5)
        for i in range(50):
            rec.record("query", float(i))
        # Internal ring never exceeds the bound.
        with rec._lock:  # noqa: SLF001 — white-box check of the bound
            assert len(rec._buckets["query"].ring) == 5
            assert len(rec._buckets[ALL_BUCKET].ring) == 5

    def test_bucket_cap_folds_into_other(self):
        rec = LatencyRecorder(max_buckets=3)
        # 5 distinct keyed buckets, cap is 3 → 2 overflow into `other`.
        for n in range(5):
            rec.record(f"k{n}", 1.0)
        snap = rec.snapshot()
        keyed = [k for k in snap if k != ALL_BUCKET]
        # 3 created keys + the synthetic `other` overflow bucket.
        assert OTHER_BUCKET in snap
        assert len([k for k in keyed if k != OTHER_BUCKET]) == 3
        # All 5 samples are still accounted for in `all`.
        assert snap[ALL_BUCKET]["count"] == 5
        # The 2 folded samples live in `other`.
        assert snap[OTHER_BUCKET]["count"] == 2

    def test_reset_clears_everything(self):
        rec = LatencyRecorder()
        rec.record("query", 5.0)
        assert rec.snapshot() != {}
        rec.reset()
        assert rec.snapshot() == {}

    def test_empty_buckets_omitted(self):
        rec = LatencyRecorder()
        assert rec.snapshot() == {}

    def test_single_sample_percentiles(self):
        rec = LatencyRecorder()
        rec.record("auth", 7.0)
        a = rec.snapshot()["auth"]
        assert a["p50"] == a["p95"] == a["p99"] == a["max"] == 7.0
        assert a["count"] == 1


# ---------------------------------------------------------------------------
# 2. Endpoint tests
# ---------------------------------------------------------------------------


class TestOpsStatsEndpoint:
    @pytest.mark.asyncio
    async def test_stats_shape_with_auth(self, client, fake_db):
        uid = str(uuid.uuid4())
        fake_db.users[uid] = _make_user(uid, "ops@example.com")
        resp = await client.get("/api/v1/ops/stats", headers=_auth_headers(uid))
        assert resp.status_code == 200
        body = resp.json()
        # Top-level keys.
        assert {"latency", "cache", "uptime_s", "version", "rate_limits"} <= set(body)
        # cache carries a backend name.
        assert body["cache"]["backend"] in ("memory", "redis")
        assert "hit_rate" in body["cache"]
        # rate_limits carries the three caps + enabled flag.
        assert {"auth_rpm", "query_rpm", "flowrun_rpm", "enabled"} <= set(
            body["rate_limits"]
        )
        # latency is a dict (may be empty if no timed traffic yet).
        assert isinstance(body["latency"], dict)
        assert isinstance(body["uptime_s"], (int, float))

    @pytest.mark.asyncio
    async def test_stats_reflects_recorded_traffic(self, client, fake_db):
        from app.observability.latency import get_recorder

        uid = str(uuid.uuid4())
        fake_db.users[uid] = _make_user(uid, "ops2@example.com")
        get_recorder().reset()
        get_recorder().record("query", 42.0)

        resp = await client.get("/api/v1/ops/stats", headers=_auth_headers(uid))
        assert resp.status_code == 200
        latency = resp.json()["latency"]
        assert "query" in latency
        assert latency["query"]["count"] >= 1
        assert ALL_BUCKET in latency

    @pytest.mark.asyncio
    async def test_stats_401_unauthenticated(self, client):
        resp = await client.get("/api/v1/ops/stats")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_stats_401_for_non_first_party_token(self, client):
        # A non first-party (e.g. embed-style / opaque) bearer token cannot be
        # decoded as a first-party HS256 access token → current_user rejects 401.
        resp = await client.get(
            "/api/v1/ops/stats",
            headers={"Authorization": "Bearer not-a-first-party-access-token"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_ops_health_public(self, client):
        resp = await client.get("/api/v1/ops/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "uptime_s" in body
