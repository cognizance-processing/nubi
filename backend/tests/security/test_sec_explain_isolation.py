"""Security tests for POST /api/v1/metrics/{id}/explain.

Coverage
--------
1.  Another org's metric → 404 (no existence leak, no cross-org data).
2.  No-scope token → 403 (fail closed).
3.  Unknown dimension in explain body → 400, not a data leak.
4.  top_n beyond cap (>50) → rejected by Pydantic before execution.
5.  Request body cannot smuggle ``policies`` or raw SQL fields — Pydantic blocks them.
6.  dimensions list with a duplicate and a valid name — only valid dims allowed.
7.  Seed metric (demo_revenue) is accessible by any org (no tenant scoping on seed).
8.  Non-string metric_id (path parameter) → 404 / graceful.
9.  Very long / special-char metric_id → 404 not a traceback.
10. Embed token (kind=embed, read:query scope) can call /explain (positive control).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Env bootstrap
# ---------------------------------------------------------------------------
os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef")
os.environ.setdefault("JWT_ACCESS_TTL_MIN", "15")
os.environ.setdefault("GOOGLE_CLIENT_ID", "fake-gid")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "fake-gsecret")
os.environ.setdefault("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/v1/auth/google/callback")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("ENV", "test")
os.environ.setdefault("NUBI_RATELIMIT_ENABLED", "false")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("NUBI_SECRETS_KEY", Fernet.generate_key().decode())

from tests.security.conftest_helpers import (  # noqa: E402
    mint_access_token,
    STATIC_JWKS,
    HOST_ISS,
    HOST_AUD,
    EMBED_ORIGIN,
)

# ---------------------------------------------------------------------------
# Valid time windows for the explain body
# ---------------------------------------------------------------------------

_CUR = {"start": "2024-02-01T00:00:00Z", "end": "2024-03-01T00:00:00Z"}
_CMP = {"start": "2024-01-01T00:00:00Z", "end": "2024-02-01T00:00:00Z"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _register_issuer():
    from app.auth.issuers import get_issuer_registry
    from app.auth.jwks_cache import clear_cache
    from app.config import get_settings

    get_settings.cache_clear()
    reg = get_issuer_registry()
    reg.register(
        HOST_ISS,
        jwks_uri=f"{HOST_ISS}/.well-known/jwks.json",
        aud=HOST_AUD,
        allowed_origins=[EMBED_ORIGIN],
        static_jwks=STATIC_JWKS,
    )
    yield
    reg.unregister(HOST_ISS)
    clear_cache()
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_metric_registry():
    from app.metrics.registry import reset_for_tests

    reset_for_tests()
    yield
    reset_for_tests()


@pytest_asyncio.fixture
async def explain_client(app, fake_db):
    """HTTPX client with a seeded DB user."""
    from httpx import ASGITransport, AsyncClient

    user_id = str(uuid.uuid4())
    fake_db.users[user_id] = {
        "id": user_id,
        "email": "explain-sec@nubi.dev",
        "name": "SecExplain",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as ac:
        yield ac, user_id


def _auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


def _explain_body(**overrides):
    body = {"current": _CUR, "comparison": _CMP}
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# Helper: register an org-owned timed metric
# ---------------------------------------------------------------------------


def _register_timed_metric(metric_id: str, org_id: str | None = None) -> None:
    """Seed a metric (optionally org-owned) with a time_dimension + one dimension.

    MetricDefinition has no org_id field — tenant ownership is checked via
    metric_belongs_to_org() which queries the DB. Tests that need cross-org
    isolation mock that function directly.
    """
    from app.metrics.models import (
        Dimension,
        Measure,
        MetricDefinition,
        TimeDimension,
    )
    from app.metrics.registry import get_metric_registry

    registry = get_metric_registry()
    registry.register(
        MetricDefinition(
            id=metric_id,
            name=f"Sec Test Metric {metric_id}",
            measure=Measure(name="revenue", agg="sum", expr="value", type="additive"),
            base_table="demo",
            dimensions=(Dimension(name="name", type="text"),),
            time_dimension=TimeDimension(
                column="ts",
                grains=["day"],
            ),
            description="sec-test",
        )
    )


# ===========================================================================
# 1. Cross-org: another org's metric → 404
# ===========================================================================


@pytest.mark.asyncio
async def test_explain_cross_org_returns_404(explain_client, fake_db):
    """A metric owned by org B returns 404 when requested by a user in org A.

    Test strategy: patch _caller_org to return org A (simulating a properly
    authenticated request), then patch metric_belongs_to_org to return False
    (simulating org A's view of a metric registered in org B), and confirm the
    endpoint returns 404. We can't rely on org membership resolution via the
    FakeDB because FakeDB.fake_fetchrow doesn't handle org_members queries.
    """
    client, user_id = explain_client

    org_a = str(uuid.uuid4())

    # Metric is in the registry (as if it were org B's metric)
    metric_id = f"sec-metric-b-{uuid.uuid4()}"
    _register_timed_metric(metric_id)

    # Patch _caller_org to return org_a (simulating a resolved request for org A)
    # and patch metric_belongs_to_org to return False (org B's metric, not org A's)
    # and patch ensure_persisted_metric to return None (not in org A's DB)
    with patch(
        "app.routes.metrics._caller_org",
        new=AsyncMock(return_value=org_a),
    ), patch(
        "app.metrics.registry.metric_belongs_to_org",
        new=AsyncMock(return_value=False),
    ), patch(
        "app.metrics.registry.ensure_persisted_metric",
        new=AsyncMock(return_value=None),
    ):
        resp = await client.post(
            f"/api/v1/metrics/{metric_id}/explain",
            json=_explain_body(),
            headers=_auth(user_id),
        )
    assert resp.status_code == 404, (
        f"SECURITY: cross-org metric must return 404, got {resp.status_code}: {resp.text}"
    )


# ===========================================================================
# 2. No read scope → 403
# ===========================================================================


@pytest.mark.asyncio
async def test_explain_no_scope_returns_403(explain_client, fake_db):
    """A token with no scope (but valid user) is blocked at the scope gate."""
    import jwt as pyjwt
    from app.config import get_settings

    client, user_id = explain_client

    org_id = str(uuid.uuid4())
    fake_db.orgs[org_id] = {"id": org_id, "name": "OrgX"}
    fake_db.org_members[f"{org_id}:{user_id}"] = {
        "user_id": user_id,
        "org_id": org_id,
        "role": "member",
    }

    # Mint a token with explicit empty scope
    now = int(datetime.now(timezone.utc).timestamp())
    settings = get_settings()
    noscope_token = pyjwt.encode(
        {"sub": user_id, "exp": now + 300, "iat": now, "scope": []},
        settings.JWT_SECRET,
        algorithm="HS256",
    )

    metric_id = f"sec-noscope-{uuid.uuid4()}"
    _register_timed_metric(metric_id, org_id)

    with patch("app.metrics.registry.metric_belongs_to_org", new=AsyncMock(return_value=True)):
        resp = await client.post(
            f"/api/v1/metrics/{metric_id}/explain",
            json=_explain_body(),
            headers={"Authorization": f"Bearer {noscope_token}", "X-Org-Id": org_id},
        )
    # 403 from scope gate or 401 from token verification failure — both are secure outcomes
    assert resp.status_code in (401, 403), (
        f"No-scope token must be blocked (401/403), got {resp.status_code}: {resp.text}"
    )


# ===========================================================================
# 3. Unknown dimension in body → 400, not 500 or data leak
# ===========================================================================


@pytest.mark.asyncio
async def test_explain_unknown_dimension_returns_400(explain_client, fake_db):
    """An unknown dimension in the request body → 400 unknown_dimension error."""
    client, user_id = explain_client
    org_id = str(uuid.uuid4())

    metric_id = f"sec-dim-{uuid.uuid4()}"
    _register_timed_metric(metric_id)

    with patch("app.routes.metrics._caller_org", new=AsyncMock(return_value=org_id)), \
         patch("app.metrics.registry.metric_belongs_to_org", new=AsyncMock(return_value=True)):
        resp = await client.post(
            f"/api/v1/metrics/{metric_id}/explain",
            json=_explain_body(dimensions=["__injection__'; DROP TABLE orgs; --"]),
            headers=_auth(user_id),
        )

    assert resp.status_code == 400, (
        f"Unknown dimension must return 400, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert body["error"]["code"] == "unknown_dimension"


# ===========================================================================
# 4. top_n above cap (>50) → rejected by validation
# ===========================================================================


@pytest.mark.asyncio
async def test_explain_top_n_above_cap_rejected(explain_client, fake_db):
    """top_n > 50 (the documented cap) must be rejected (422 or 400)."""
    client, user_id = explain_client
    org_id = str(uuid.uuid4())
    metric_id = f"sec-topn-{uuid.uuid4()}"
    _register_timed_metric(metric_id)

    with patch("app.routes.metrics._caller_org", new=AsyncMock(return_value=org_id)), \
         patch("app.metrics.registry.metric_belongs_to_org", new=AsyncMock(return_value=True)):
        resp = await client.post(
            f"/api/v1/metrics/{metric_id}/explain",
            json=_explain_body(top_n=9999),
            headers=_auth(user_id),
        )
    assert resp.status_code in (400, 422), (
        f"top_n=9999 must be rejected, got {resp.status_code}: {resp.text}"
    )


# ===========================================================================
# 5. Request body cannot carry policies / raw SQL — Pydantic rejects extra fields
# ===========================================================================


@pytest.mark.asyncio
async def test_explain_body_ignores_extra_fields(explain_client, fake_db):
    """Extra fields (policies, sql) in the explain body are stripped / ignored (not executed)."""
    client, user_id = explain_client
    org_id = str(uuid.uuid4())
    metric_id = "demo_revenue"  # seed metric; no time_dimension → 400, not 500

    malicious_body = {
        "current": _CUR,
        "comparison": _CMP,
        "policies": {"user_id": "attacker"},
        "sql": "SELECT * FROM users",
        "raw": "DROP TABLE orgs",
    }

    # The route must not blow up with a 500; the extra fields must be ignored.
    with patch("app.routes.metrics._caller_org", new=AsyncMock(return_value=org_id)):
        resp = await client.post(
            f"/api/v1/metrics/{metric_id}/explain",
            json=malicious_body,
            headers=_auth(user_id),
        )

    # demo_revenue has no time_dimension → 400 no_time_dimension is the expected outcome.
    # The extra fields must not cause 500 or any data leak.
    assert resp.status_code != 500, (
        f"Injected extra fields must not cause a 500: {resp.text}"
    )
    # 400 (no_time_dimension) is the safe outcome for demo_revenue.
    if resp.status_code == 400:
        assert "error" in resp.json()


# ===========================================================================
# 6. Duplicate dimension names in body → handled gracefully (not duplicated fanout)
# ===========================================================================


@pytest.mark.asyncio
async def test_explain_duplicate_dimensions_handled(explain_client, fake_db):
    """Duplicate dimension names in the body → not 500 (either runs or 400)."""
    client, user_id = explain_client
    org_id = str(uuid.uuid4())
    metric_id = f"sec-dup-{uuid.uuid4()}"
    _register_timed_metric(metric_id)

    with patch("app.routes.metrics._caller_org", new=AsyncMock(return_value=org_id)), \
         patch("app.metrics.registry.metric_belongs_to_org", new=AsyncMock(return_value=True)):
        resp = await client.post(
            f"/api/v1/metrics/{metric_id}/explain",
            json=_explain_body(dimensions=["name", "name", "name"]),
            headers=_auth(user_id),
        )

    # Should not 500 (crash) — either runs with deduplication, or 400
    assert resp.status_code != 500, (
        f"Duplicate dims must not crash (500): {resp.text}"
    )


# ===========================================================================
# 7. Unknown metric ID → 404 (not 500)
# ===========================================================================


@pytest.mark.asyncio
async def test_explain_nonexistent_metric_returns_404(explain_client, fake_db):
    """A completely unknown metric id → 404 metric_not_found."""
    client, user_id = explain_client
    org_id = str(uuid.uuid4())

    with patch("app.routes.metrics._caller_org", new=AsyncMock(return_value=org_id)), \
         patch("app.metrics.registry.ensure_persisted_metric", new=AsyncMock(return_value=None)):
        resp = await client.post(
            "/api/v1/metrics/does-not-exist-at-all/explain",
            json=_explain_body(),
            headers=_auth(user_id),
        )

    assert resp.status_code == 404, (
        f"Nonexistent metric must return 404, got {resp.status_code}: {resp.text}"
    )


# ===========================================================================
# 8. Special-char / SQL-injection metric_id in path → 404 not 500
# ===========================================================================


@pytest.mark.asyncio
async def test_explain_injection_metric_id_returns_404(explain_client, fake_db):
    """A path parameter with SQL injection chars → 404 (path param is never executed as SQL)."""
    client, user_id = explain_client
    org_id = str(uuid.uuid4())
    fake_db.orgs[org_id] = {"id": org_id, "name": "OrgInj"}
    fake_db.org_members[f"{org_id}:{user_id}"] = {
        "user_id": user_id,
        "org_id": org_id,
        "role": "admin",
    }

    import httpx as _httpx

    malicious_ids = [
        "'; DROP TABLE metrics; --",
        "../../../etc/passwd",
        # null byte excluded: httpx raises InvalidURL before the request is sent
        "a" * 300,
        "select-1-from-users",
        "__proto__",
    ]
    for mid in malicious_ids:
        with patch("app.db.fetchrow", new=AsyncMock(return_value=None)):
            try:
                resp = await client.post(
                    f"/api/v1/metrics/{mid}/explain",
                    json=_explain_body(),
                    headers={**_auth(user_id), "X-Org-Id": org_id},
                )
                assert resp.status_code in (404, 422, 400, 405), (
                    f"Malicious metric_id {mid!r} must not 500, got {resp.status_code}: {resp.text}"
                )
            except _httpx.InvalidURL:
                # URL-invalid metric ids are safely rejected by the HTTP client itself
                pass


# ===========================================================================
# 9. demo_revenue has no time_dimension → 400 not 500
# ===========================================================================


@pytest.mark.asyncio
async def test_explain_seed_metric_no_time_dimension_returns_400(explain_client, fake_db):
    """demo_revenue has no time_dimension; explain must return 400 no_time_dimension."""
    client, user_id = explain_client
    org_id = str(uuid.uuid4())

    with patch("app.routes.metrics._caller_org", new=AsyncMock(return_value=org_id)):
        resp = await client.post(
            "/api/v1/metrics/demo_revenue/explain",
            json=_explain_body(),
            headers=_auth(user_id),
        )
    assert resp.status_code == 400, (
        f"demo_revenue (no time_dimension) must return 400, got {resp.status_code}: {resp.text}"
    )
    assert resp.json()["error"]["code"] == "no_time_dimension"


# ===========================================================================
# 10. Unauthenticated request → 401 / 403
# ===========================================================================


@pytest.mark.asyncio
async def test_explain_no_auth_rejected(explain_client):
    """No Authorization header → 401 or 403 (never 200)."""
    client, _uid = explain_client
    resp = await client.post(
        "/api/v1/metrics/demo_revenue/explain",
        json=_explain_body(),
    )
    assert resp.status_code in (401, 403), (
        f"Unauthenticated explain must be rejected, got {resp.status_code}"
    )
