"""Tests for the open-core usage surface — ``app.usage`` aggregation + the
``GET /usage`` / ``GET /usage/series`` endpoints + tenant isolation + the EE
usage-limits hook.

Aggregation runs over the in-process metering sink in tests (the conftest
``fake_db`` returns no ``usage_events`` rows, so ``app.usage`` falls back to the
sink — the same fallback used in local dev).  Events are seeded with
``app.compute.metering.record_usage`` so the test exercises the real recorder.

Auth mirrors the watches tests: a first-party access token + a seeded
``InMemoryRepo`` org membership; ``X-Org-Id`` switches orgs.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.usage.aggregate import _metric_value, period_bounds, usage_series, usage_summary
from app.usage import METRICS


def _auth_headers(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


def _seed_user(fake_db, user_id: str, email: str) -> None:
    fake_db.users[user_id] = {
        "id": user_id,
        "email": email,
        "name": "Usage Tester",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


async def _record(kind: str, org_id: str, *, units: float = 1.0) -> None:
    from app.compute.metering import record_usage

    await record_usage(kind=kind, user_id=str(uuid.uuid4()), org_id=org_id, units=units)


@pytest_asyncio.fixture
async def u_client(app, fake_db):
    """HTTPX client with a seeded user + org membership for the usage tests."""
    from app.repos.memory import InMemoryRepo
    from app.repos.provider import set_repo

    repo = InMemoryRepo()
    set_repo(repo)

    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    _seed_user(fake_db, user_id, "usage_tester@example.com")
    repo.seed_org_member(org_id=org_id, user_id=user_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as ac:
        yield ac, user_id, org_id, repo

    set_repo(None)


# ---------------------------------------------------------------------------
# 1. Aggregation unit tests
# ---------------------------------------------------------------------------


class TestAggregation:
    def test_period_bounds_buckets(self):
        assert period_bounds("day")[2] == "hour"
        assert period_bounds("week")[2] == "day"
        assert period_bounds("month")[2] == "day"
        # Unknown → month.
        assert period_bounds("decade")[2] == "day"

    def test_metric_value_count_sum_max(self):
        compute = next(m for m in METRICS if m.id == "compute_units")
        queries = next(m for m in METRICS if m.id == "queries")
        storage = next(m for m in METRICS if m.id == "storage_gb")
        events = [
            {"kind": "compute", "units": 2.0},
            {"kind": "compute", "units": 3.0},
            {"kind": "storage", "units": 5.0},
            {"kind": "storage", "units": 9.0},
        ]
        # queries = COUNT of compute events
        assert _metric_value(queries, events) == 2.0
        # compute_units = SUM of compute units
        assert _metric_value(compute, events) == 5.0
        # storage_gb = MAX of storage units (period peak, not sum)
        assert _metric_value(storage, events) == 9.0

    @pytest.mark.asyncio
    async def test_summary_aggregates_from_sink(self):
        org = str(uuid.uuid4())
        await _record("compute", org, units=4.0)
        await _record("compute", org, units=6.0)
        await _record("query_scan", org, units=1000.0)
        await _record("ai_call", org, units=120.0)

        summary = await usage_summary(org, "month")
        by_id = {m["id"]: m for m in summary["metrics"]}
        assert by_id["queries"]["used"] == 2.0          # two compute events
        assert by_id["compute_units"]["used"] == 10.0   # 4 + 6
        assert by_id["bytes_scanned"]["used"] == 1000.0
        assert by_id["ai_tokens"]["used"] == 120.0
        # No EE provider registered → every limit is unlimited (None), pct None.
        assert by_id["queries"]["limit"] is None
        assert by_id["queries"]["pct"] is None

    @pytest.mark.asyncio
    async def test_series_is_dense_and_zero_filled(self):
        org = str(uuid.uuid4())
        await _record("compute", org, units=1.0)
        series = await usage_series(org, "queries", "day")
        assert series["metric"] == "queries"
        assert series["bucket"] == "hour"
        # Dense buckets covering the last day (≈24 hourly points).
        assert len(series["points"]) >= 20
        # The recorded event contributes a non-zero bucket somewhere.
        assert any(p["value"] >= 1.0 for p in series["points"])
        # All points are zero-or-positive (zero-filled gaps).
        assert all(p["value"] >= 0.0 for p in series["points"])


# ---------------------------------------------------------------------------
# 2. Endpoint tests
# ---------------------------------------------------------------------------


class TestUsageEndpoints:
    @pytest.mark.asyncio
    async def test_get_usage_shape(self, u_client):
        client, user_id, org_id, _repo = u_client
        await _record("compute", org_id, units=2.0)

        resp = await client.get("/api/v1/usage", headers=_auth_headers(user_id))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["period"] == "month"
        assert "period_start" in body and "period_end" in body
        ids = {m["id"] for m in body["metrics"]}
        assert {"queries", "compute_units", "bytes_scanned", "storage_gb"} <= ids
        q = next(m for m in body["metrics"] if m["id"] == "queries")
        assert q["used"] == 1.0
        assert q["limit"] is None  # unlimited in OSS build

    @pytest.mark.asyncio
    async def test_get_usage_period_param(self, u_client):
        client, user_id, _org_id, _repo = u_client
        resp = await client.get(
            "/api/v1/usage?period=week", headers=_auth_headers(user_id)
        )
        assert resp.status_code == 200
        assert resp.json()["period"] == "week"

    @pytest.mark.asyncio
    async def test_series_endpoint(self, u_client):
        client, user_id, org_id, _repo = u_client
        await _record("compute", org_id, units=5.0)
        resp = await client.get(
            "/api/v1/usage/series?metric=compute_units&period=month",
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["metric"] == "compute_units"
        assert body["unit"] == "CU"
        assert isinstance(body["points"], list)
        assert sum(p["value"] for p in body["points"]) == pytest.approx(5.0)

    @pytest.mark.asyncio
    async def test_series_unknown_metric_404(self, u_client):
        client, user_id, _org_id, _repo = u_client
        resp = await client.get(
            "/api/v1/usage/series?metric=not_a_metric",
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_usage_401_unauthenticated(self, u_client):
        client, _user_id, _org_id, _repo = u_client
        resp = await client.get("/api/v1/usage")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 3. Tenant isolation — an org never sees another org's usage
# ---------------------------------------------------------------------------


class TestTenantIsolation:
    @pytest.mark.asyncio
    async def test_usage_is_org_scoped(self, u_client, fake_db):
        client, user_id, org_id, repo = u_client

        # Bob: a second user in a DIFFERENT org with his own usage.
        bob_id = str(uuid.uuid4())
        bob_org = str(uuid.uuid4())
        _seed_user(fake_db, bob_id, "bob@example.com")
        repo.seed_org_member(org_id=bob_org, user_id=bob_id)

        # Alice records 3 compute events; Bob records 7.
        for _ in range(3):
            await _record("compute", org_id)
        for _ in range(7):
            await _record("compute", bob_org)

        alice = await client.get("/api/v1/usage", headers=_auth_headers(user_id))
        bob = await client.get("/api/v1/usage", headers=_auth_headers(bob_id))
        alice_q = next(m for m in alice.json()["metrics"] if m["id"] == "queries")
        bob_q = next(m for m in bob.json()["metrics"] if m["id"] == "queries")
        # Each org sees ONLY its own events — no cross-tenant leakage.
        assert alice_q["used"] == 3.0
        assert bob_q["used"] == 7.0

    @pytest.mark.asyncio
    async def test_x_org_id_membership_enforced(self, u_client):
        client, user_id, _org_id, _repo = u_client
        # Switching to an org the caller is NOT a member of is rejected (403).
        foreign_org = str(uuid.uuid4())
        resp = await client.get(
            "/api/v1/usage",
            headers={**_auth_headers(user_id), "X-Org-Id": foreign_org},
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 4. Soft-quota hook — EE limits provider feeds used/limit/pct
# ---------------------------------------------------------------------------


class TestUsageLimitsHook:
    @pytest.mark.asyncio
    async def test_registered_provider_surfaces_limit_and_pct(self):
        from app.features import register_usage_limits_provider

        org = str(uuid.uuid4())
        await _record("compute", org, units=50.0)  # 50 CU used

        # Stand-in for the EE provider: compute_units capped at 200.
        async def _provider(_org_id: str) -> dict[str, float | None]:
            return {"compute_units": 200.0, "queries": None}

        register_usage_limits_provider(_provider)
        try:
            summary = await usage_summary(org, "month")
        finally:
            register_usage_limits_provider(None)

        cu = next(m for m in summary["metrics"] if m["id"] == "compute_units")
        assert cu["used"] == 50.0
        assert cu["limit"] == 200.0
        assert cu["pct"] == 25.0  # 50 / 200 * 100
        # An explicitly-None limit stays unlimited.
        q = next(m for m in summary["metrics"] if m["id"] == "queries")
        assert q["limit"] is None and q["pct"] is None

    @pytest.mark.asyncio
    async def test_provider_failure_is_fail_open(self):
        from app.features import register_usage_limits_provider

        org = str(uuid.uuid4())

        def _boom(_org_id: str):
            raise RuntimeError("provider down")

        register_usage_limits_provider(_boom)
        try:
            summary = await usage_summary(org, "month")
        finally:
            register_usage_limits_provider(None)
        # A broken provider degrades to unlimited rather than erroring.
        assert all(m["limit"] is None for m in summary["metrics"])
