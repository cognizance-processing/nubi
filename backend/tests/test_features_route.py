"""Tests for GET /features — feature-flag REST endpoint.

Coverage
--------
1. Without any license / EE: ``GET /features`` returns ``{"features": []}``.
2. After ``register_feature("billing", lambda: True)``: response includes
   ``"billing"`` in the features list.
3. ``reset_for_tests`` is called between tests so state does not leak.

The test uses a minimal AsyncClient + ASGI transport (no live DB) and patches
out the ``current_user`` dependency so the endpoint is exercised without a real
JWT or DB user lookup.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from unittest.mock import patch, AsyncMock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client():
    """Create an ASGI test client against the full FastAPI app.

    ``current_user`` is patched to return a fake user dict so that endpoints
    guarded by ``Depends(current_user)`` do not need a real DB or JWT.
    """
    fake_user = {
        "id": "00000000-0000-0000-0000-000000000001",
        "email": "test@nubi.dev",
        "name": "Test User",
        "avatar_url": None,
        "email_verified": True,
        "created_at": None,
    }

    # We cannot import main at module level because conftest.py sets env vars.
    # Import inside the fixture so the env is already in place.
    from main import app  # noqa: PLC0415
    from app.auth.deps import current_user  # noqa: PLC0415

    async def _fake_current_user():
        return fake_user

    app.dependency_overrides[current_user] = _fake_current_user
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            yield ac
    finally:
        app.dependency_overrides.pop(current_user, None)


# ---------------------------------------------------------------------------
# Reset feature state between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_features():
    """Ensure the feature registry is clean before and after every test."""
    from app.features import reset_for_tests  # noqa: PLC0415

    reset_for_tests()
    yield
    reset_for_tests()


# ===========================================================================
# Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_features_empty_in_oss_mode(client: AsyncClient):
    """With no EE / no registered checkers the features list must be empty."""
    response = await client.get("/api/v1/features")
    assert response.status_code == 200
    body = response.json()
    assert "features" in body
    assert body["features"] == [], f"Expected [] in OSS mode, got {body['features']!r}"


@pytest.mark.asyncio
async def test_features_includes_billing_after_register(client: AsyncClient):
    """After register_feature('billing', lambda: True) the response includes 'billing'."""
    from app.features import register_feature  # noqa: PLC0415

    register_feature("billing", lambda: True)

    response = await client.get("/api/v1/features")
    assert response.status_code == 200
    body = response.json()
    assert "features" in body
    assert "billing" in body["features"], (
        f"Expected 'billing' in features after register_feature, got {body['features']!r}"
    )


@pytest.mark.asyncio
async def test_features_excludes_disabled_commercial(client: AsyncClient):
    """Commercial features with no registered checker are excluded."""
    # Register 'billing' as True but leave 'paid_tiers' unregistered (False).
    from app.features import register_feature  # noqa: PLC0415

    register_feature("billing", lambda: True)

    response = await client.get("/api/v1/features")
    body = response.json()
    features = body["features"]
    assert "billing" in features
    assert "paid_tiers" not in features, (
        f"'paid_tiers' should NOT appear when its checker is not registered, got {features!r}"
    )


@pytest.mark.asyncio
async def test_features_reset_between_tests(client: AsyncClient):
    """The autouse fixture ensures state is clean — billing must not leak from previous tests."""
    # No register_feature call here; should be clean after the autouse reset.
    response = await client.get("/api/v1/features")
    body = response.json()
    assert body["features"] == [], (
        f"Feature state leaked from a previous test: {body['features']!r}"
    )
