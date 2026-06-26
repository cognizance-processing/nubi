"""Integration tests for POST /api/v1/metrics/{metric_id}/explain.

These mirror the patterns in test_metrics_routes.py exactly:
- httpx AsyncClient with ASGITransport
- fake_db fixture from conftest.py
- _auth_headers / _embed_headers helpers
- seeded demo user via m_client fixture

The explain route requires a metric with a time_dimension. We create an
in-process metric (registered via the route or directly in the registry)
that uses a DuckDB VALUES subquery with a date column so the explain
endpoint can execute real queries against the demo connector.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


# ---------------------------------------------------------------------------
# Auth helpers — mirrors test_metrics_routes.py exactly
# ---------------------------------------------------------------------------


def _auth_headers(user_id: str) -> dict[str, str]:
    from app.auth.jwt import mint_access_token

    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


def _embed_headers(user_id: str, org_id: str) -> dict[str, str]:
    import time

    import jwt

    from app.config import get_settings

    settings = get_settings()
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": user_id,
            "kind": "embed",
            "scope": ["read:query"],
            "org": org_id,
            "iat": now,
            "exp": now + 900,
        },
        settings.JWT_SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def explain_client(app, fake_db):
    """HTTPX client with a seeded user for explain tests."""
    user_id = str(uuid.uuid4())
    fake_db.users[user_id] = {
        "id": user_id,
        "email": "explain_tester@example.com",
        "name": "Explain Tester",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as ac:
        yield ac, user_id


def _timed_revenue_metric_id() -> str:
    """Register (or return cached) a metric with a time_dimension against DuckDB.

    Uses a DuckDB VALUES expression as base_sql so it runs against the
    demo/built-in DuckDB connector without needing a real table with dates.
    The metric has a date column 'event_date', a text dimension 'region', and
    the measure SUM(amount).
    """
    from app.metrics.models import (
        Dimension,
        Measure,
        MetricDefinition,
        TimeDimension,
    )
    from app.metrics.registry import get_metric_registry

    metric_id = "explain_test_revenue"
    registry = get_metric_registry()

    # Only register once (safe across multiple test invocations per process).
    existing = registry.get(metric_id)
    if existing is not None:
        return metric_id

    metric = MetricDefinition(
        id=metric_id,
        name="Explain Test Revenue",
        measure=Measure(name="revenue", agg="sum", expr="amount", type="additive"),
        base_sql=(
            "SELECT * FROM (VALUES "
            "('2024-01-15'::DATE, 'North', 100.0), "
            "('2024-01-15'::DATE, 'South', 80.0), "
            "('2024-02-15'::DATE, 'North', 120.0), "
            "('2024-02-15'::DATE, 'South', 90.0)"
            ") t(event_date, region, amount)"
        ),
        dimensions=(
            Dimension(name="region", type="text"),
        ),
        time_dimension=TimeDimension(
            column="event_date",
            grains=("day", "month"),
            default_grain="day",
        ),
    )
    registry.register(metric)
    return metric_id


# ---------------------------------------------------------------------------
# (1) test_explain_basic — happy path: returns delta, drivers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explain_basic(explain_client):
    """POST /explain on a seeded metric returns delta, drivers."""
    client, user_id = explain_client
    headers = _auth_headers(user_id)
    metric_id = _timed_revenue_metric_id()

    resp = await client.post(
        f"/api/v1/metrics/{metric_id}/explain",
        json={
            "current": {"start": "2024-02-01", "end": "2024-03-01"},
            "comparison": {"start": "2024-01-01", "end": "2024-02-01"},
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "delta_total" in body
    assert "current_total" in body
    assert "comparison_total" in body
    assert "dimensions" in body
    # Should have the "region" dimension
    dim_names = [d["dimension"] for d in body["dimensions"]]
    assert "region" in dim_names
    # delta_total should be 210 - 180 = 30
    assert abs(body["delta_total"] - 30.0) < 0.1
    # summary is None when include_summary=False
    assert body["summary"] is None


# ---------------------------------------------------------------------------
# (2) test_explain_unknown_dimension_400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explain_unknown_dimension_400(explain_client):
    """Requesting a non-existent dimension returns 400."""
    client, user_id = explain_client
    headers = _auth_headers(user_id)
    metric_id = _timed_revenue_metric_id()

    resp = await client.post(
        f"/api/v1/metrics/{metric_id}/explain",
        json={
            "current": {"start": "2024-02-01", "end": "2024-03-01"},
            "comparison": {"start": "2024-01-01", "end": "2024-02-01"},
            "dimensions": ["nonexistent_dim"],
        },
        headers=headers,
    )
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert "error" in body


# ---------------------------------------------------------------------------
# (3) test_explain_no_time_dimension_400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explain_no_time_dimension_400(explain_client):
    """A metric without time_dimension → 400."""
    client, user_id = explain_client
    headers = _auth_headers(user_id)

    # demo_revenue has no time_dimension
    resp = await client.post(
        "/api/v1/metrics/demo_revenue/explain",
        json={
            "current": {"start": "2024-02-01", "end": "2024-03-01"},
            "comparison": {"start": "2024-01-01", "end": "2024-02-01"},
        },
        headers=headers,
    )
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert "error" in body
    err = body["error"]
    assert "no_time_dimension" in str(err.get("code", "")) or "no_time" in str(err).lower()


# ---------------------------------------------------------------------------
# (4) test_explain_unknown_metric_404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explain_unknown_metric_404(explain_client):
    """Requesting explain on an unknown metric returns 404."""
    client, user_id = explain_client
    headers = _auth_headers(user_id)

    resp = await client.post(
        "/api/v1/metrics/totally_nonexistent_metric_xyz/explain",
        json={
            "current": {"start": "2024-02-01", "end": "2024-03-01"},
            "comparison": {"start": "2024-01-01", "end": "2024-02-01"},
        },
        headers=headers,
    )
    assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# (5) test_explain_deterministic_summary — include_summary=True, NullProvider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explain_deterministic_summary(explain_client):
    """include_summary=True with NullProvider returns a non-None string."""
    client, user_id = explain_client
    headers = _auth_headers(user_id)
    metric_id = _timed_revenue_metric_id()

    resp = await client.post(
        f"/api/v1/metrics/{metric_id}/explain",
        json={
            "current": {"start": "2024-02-01", "end": "2024-03-01"},
            "comparison": {"start": "2024-01-01", "end": "2024-02-01"},
            "include_summary": True,
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # NullProvider returns a deterministic string (not None)
    assert "summary" in body
    assert isinstance(body["summary"], str)


# ---------------------------------------------------------------------------
# (6) test_explain_unauthenticated_401
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explain_unauthenticated_401(explain_client):
    """Unauthenticated request returns 401."""
    client, _user_id = explain_client
    metric_id = _timed_revenue_metric_id()

    resp = await client.post(
        f"/api/v1/metrics/{metric_id}/explain",
        json={
            "current": {"start": "2024-02-01", "end": "2024-03-01"},
            "comparison": {"start": "2024-01-01", "end": "2024-02-01"},
        },
    )
    assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------------------
# (7) test_explain_dimensions_filter — only requested dims returned
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explain_dimensions_filter(explain_client):
    """When dimensions list is specified, only those dims are analyzed."""
    client, user_id = explain_client
    headers = _auth_headers(user_id)
    metric_id = _timed_revenue_metric_id()

    resp = await client.post(
        f"/api/v1/metrics/{metric_id}/explain",
        json={
            "current": {"start": "2024-02-01", "end": "2024-03-01"},
            "comparison": {"start": "2024-01-01", "end": "2024-02-01"},
            "dimensions": ["region"],
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    dim_names = [d["dimension"] for d in body["dimensions"]]
    assert dim_names == ["region"]


# ---------------------------------------------------------------------------
# (8) test_explain_top_n_respected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explain_top_n_respected(explain_client):
    """top_n=1 limits members to 1 per dimension."""
    client, user_id = explain_client
    headers = _auth_headers(user_id)
    metric_id = _timed_revenue_metric_id()

    resp = await client.post(
        f"/api/v1/metrics/{metric_id}/explain",
        json={
            "current": {"start": "2024-02-01", "end": "2024-03-01"},
            "comparison": {"start": "2024-01-01", "end": "2024-02-01"},
            "top_n": 1,
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for dim in body["dimensions"]:
        assert len(dim["members"]) <= 1
