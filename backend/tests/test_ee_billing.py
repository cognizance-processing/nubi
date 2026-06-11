"""Tests for EE billing sub-package.

Coverage
--------
1. Tiers
   a. All three tiers are in the catalogue.
   b. ZAR pricing: FREE=0, PRO=1499, ENTERPRISE=5999.
   c. Resource limits: FREE has low caps, ENTERPRISE has None (unlimited).
   d. Security-dial → tier mapping.
   e. billing_tier_from_license_tier round-trips correctly.

2. Feature gating
   a. feature_enabled('billing') returns False without EE loaded.
   b. feature_enabled('billing') returns True after billing.setup() with
      a paid license.
   c. feature_enabled('billing') returns False after billing.setup() with
      a FREE license.
   d. reset_for_tests() restores default deny.

3. Paystack client
   a. verify_webhook_signature: valid HMAC-SHA512 returns True.
   b. verify_webhook_signature: wrong signature returns False.
   c. verify_webhook_signature: missing key returns False.
   d. initialize_transaction calls client.post with correct params.
   e. verify_transaction calls client.get with correct path.

4. Billing store (InMemoryBillingStore)
   a. get_subscription returns None for unknown org.
   b. upsert_subscription creates a new record.
   c. Second upsert updates the existing record (preserves id / created_at).
   d. record_billing_event + list_billing_events round-trip.
   e. list_billing_events returns newest first.
   f. list_billing_events respects limit.

5. Webhook route (no network — store injected)
   a. Valid signature + charge.success → subscription upserted as active/pro.
   b. Valid signature + subscription.disable → subscription status=cancelled.
   c. Invalid signature → 401.
   d. Missing org_id in metadata → 200 OK (no crash).

6. Tier route
   a. Unknown org → FREE tier limits returned.
   b. Known PRO org → PRO limits returned.

All tests: no network, no DB — InMemoryBillingStore + mocked Paystack.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Ensure test env is set before importing app modules.
# ---------------------------------------------------------------------------

os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef")
os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("ENV", "test")

# Set a PRO license key for tests that need billing enabled.
_PRO_LICENSE_KEY = "nubi_pro_test_key"
_FREE_LICENSE_KEY = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_webhook_payload(
    event: str,
    org_id: str,
    tier: str = "pro",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "event": event,
        "data": {
            "metadata": {"org_id": org_id, "tier": tier},
            "customer": {"customer_code": f"CUS_{org_id[:8]}"},
            **(extra or {}),
        },
    }
    return payload


def _sign_payload(raw_body: bytes, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha512,
    ).hexdigest()


# ============================================================================
# 1. Tiers
# ============================================================================


class TestTiers:
    def test_all_tiers_in_catalogue(self) -> None:
        from app.ee.billing.tiers import BillingTier, all_tiers

        tiers = {t.tier for t in all_tiers()}
        assert tiers == set(BillingTier)

    def test_free_tier_price_zero(self) -> None:
        from decimal import Decimal

        from app.ee.billing.tiers import BillingTier, get_tier_limits

        limits = get_tier_limits(BillingTier.FREE)
        assert limits.monthly_price_zar == Decimal("0.00")

    def test_pro_tier_price_2480(self) -> None:
        """PRO ZAR reference price is R2,480 in v3 pricing blueprint.

        $149 × R16.26 × 1.02 = R2,472.87 → ceil10 = R2,480.
        """
        from decimal import Decimal

        from app.ee.billing.tiers import BillingTier, get_tier_limits

        limits = get_tier_limits(BillingTier.PRO)
        assert limits.monthly_price_zar == Decimal("2480.00")

    def test_enterprise_tier_price_16590(self) -> None:
        """ENTERPRISE ZAR floor is R16,590 in v3 pricing blueprint.

        $1,000 × R16.26 × 1.02 = R16,585.20 → ceil10 = R16,590.
        """
        from decimal import Decimal

        from app.ee.billing.tiers import BillingTier, get_tier_limits

        limits = get_tier_limits(BillingTier.ENTERPRISE)
        assert limits.monthly_price_zar == Decimal("16590.00")

    def test_enterprise_limits_are_unlimited(self) -> None:
        from app.ee.billing.tiers import BillingTier, get_tier_limits

        limits = get_tier_limits(BillingTier.ENTERPRISE)
        assert limits.max_seats is None
        assert limits.max_connectors is None
        assert limits.max_query_rows is None
        assert limits.max_dashboards is None
        assert limits.max_flows is None
        # Hosted ENTERPRISE: max_storage_gb=500; BYOC is unlimited (handled externally).
        # max_storage_gb is 500 for hosted. Test that unlimited quotas are None.
        assert limits.max_viewer_seats is None
        assert limits.max_embedded_sessions_per_month is None

    def test_free_limits_are_bounded_by_compute_not_seats(self) -> None:
        """Blueprint v1.0: Free tier is rate-limited by compute quota, not seat count.

        Seats are unlimited (None) at all tiers. Compute, connectors, and
        query rows remain bounded to prevent abuse.
        """
        from app.ee.billing.tiers import BillingTier, get_tier_limits

        limits = get_tier_limits(BillingTier.FREE)
        # Seats now unlimited — rate-limiting via compute quota instead.
        assert limits.max_seats is None
        assert limits.max_viewer_seats is None
        # Non-seat quotas remain bounded.
        assert limits.max_query_rows is not None and limits.max_query_rows > 0
        assert limits.max_compute_units_per_month is not None and limits.max_compute_units_per_month > 0
        assert limits.max_connectors is not None and limits.max_connectors > 0

    def test_security_dial_low_maps_to_free(self) -> None:
        from app.ee.billing.tiers import BillingTier, tier_for_security_dial

        assert tier_for_security_dial(0) == BillingTier.FREE
        assert tier_for_security_dial(20) == BillingTier.FREE
        assert tier_for_security_dial(40) == BillingTier.FREE

    def test_security_dial_mid_maps_to_starter_team_pro(self) -> None:
        """Dial mapping (v3 + Team): 41-60 → STARTER, 61-70 → TEAM, 71-80 → PRO."""
        from app.ee.billing.tiers import BillingTier, tier_for_security_dial

        # 41–60 → STARTER
        assert tier_for_security_dial(41) == BillingTier.STARTER
        assert tier_for_security_dial(50) == BillingTier.STARTER
        assert tier_for_security_dial(60) == BillingTier.STARTER
        # 61–70 → TEAM
        assert tier_for_security_dial(61) == BillingTier.TEAM
        assert tier_for_security_dial(70) == BillingTier.TEAM
        # 71–80 → PRO
        assert tier_for_security_dial(71) == BillingTier.PRO
        assert tier_for_security_dial(80) == BillingTier.PRO

    def test_security_dial_high_maps_to_enterprise(self) -> None:
        """81-100 → ENTERPRISE (v3 blueprint)."""
        from app.ee.billing.tiers import BillingTier, tier_for_security_dial

        assert tier_for_security_dial(81) == BillingTier.ENTERPRISE
        assert tier_for_security_dial(100) == BillingTier.ENTERPRISE

    def test_security_dial_out_of_range_raises(self) -> None:
        from app.ee.billing.tiers import tier_for_security_dial

        with pytest.raises(ValueError):
            tier_for_security_dial(-1)
        with pytest.raises(ValueError):
            tier_for_security_dial(101)

    def test_billing_tier_from_license_tier_round_trip(self) -> None:
        from app.ee.billing.tiers import BillingTier, billing_tier_from_license_tier

        for tier in BillingTier:
            assert billing_tier_from_license_tier(tier.value) == tier

    def test_billing_tier_from_license_tier_unknown_defaults_free(self) -> None:
        from app.ee.billing.tiers import BillingTier, billing_tier_from_license_tier

        assert billing_tier_from_license_tier("unknown_tier") == BillingTier.FREE


# ============================================================================
# 2. Feature gating
# ============================================================================


class TestFeatureGating:
    def setup_method(self) -> None:
        from app.features import reset_for_tests

        reset_for_tests()

    def teardown_method(self) -> None:
        from app.features import reset_for_tests

        reset_for_tests()
        # Also clear license cache.
        from app.ee.licensing.license import reset_license_cache

        reset_license_cache()
        # Remove license key from env.
        os.environ.pop("NUBI_LICENSE_KEY", None)

    def test_billing_disabled_without_ee(self) -> None:
        from app.features import feature_enabled

        assert not feature_enabled("billing")

    def test_billing_disabled_with_free_license(self) -> None:
        os.environ["NUBI_LICENSE_KEY"] = _FREE_LICENSE_KEY
        from app.ee.licensing.license import reset_license_cache

        reset_license_cache()

        from app.ee.billing import setup

        setup(app=None)

        from app.features import feature_enabled

        assert not feature_enabled("billing")

    def test_billing_enabled_with_pro_license(self) -> None:
        os.environ["NUBI_LICENSE_KEY"] = _PRO_LICENSE_KEY
        from app.ee.licensing.license import reset_license_cache

        reset_license_cache()

        from app.features import reset_for_tests

        reset_for_tests()

        from app.ee.billing import setup

        setup(app=None)

        from app.features import feature_enabled

        assert feature_enabled("billing")

    def test_paid_tiers_enabled_with_pro_license(self) -> None:
        os.environ["NUBI_LICENSE_KEY"] = _PRO_LICENSE_KEY
        from app.ee.licensing.license import reset_license_cache

        reset_license_cache()

        from app.features import reset_for_tests

        reset_for_tests()

        from app.ee.billing import setup

        setup(app=None)

        from app.features import feature_enabled

        assert feature_enabled("paid_tiers")

    def test_reset_for_tests_restores_deny(self) -> None:
        from app.features import feature_enabled, register_feature

        register_feature("billing", lambda: True)
        assert feature_enabled("billing")

        from app.features import reset_for_tests

        reset_for_tests()
        assert not feature_enabled("billing")


# ============================================================================
# 3. Paystack client
# ============================================================================


class TestPaystackWebhookSignature:
    def test_valid_signature_returns_true(self) -> None:
        from app.ee.billing.paystack import verify_webhook_signature

        secret = "sk_test_abc123"
        body = b'{"event":"charge.success"}'
        sig = _sign_payload(body, secret)
        assert verify_webhook_signature(body, sig, secret_key=secret)

    def test_wrong_signature_returns_false(self) -> None:
        from app.ee.billing.paystack import verify_webhook_signature

        secret = "sk_test_abc123"
        body = b'{"event":"charge.success"}'
        assert not verify_webhook_signature(body, "deadbeef" * 16, secret_key=secret)

    def test_missing_key_returns_false(self) -> None:
        from app.ee.billing.paystack import verify_webhook_signature

        # Ensure env var is absent.
        os.environ.pop("PAYSTACK_SECRET_KEY", None)
        body = b'{"event":"test"}'
        assert not verify_webhook_signature(body, "any", secret_key=None)

    def test_signature_is_case_insensitive(self) -> None:
        """verify_webhook_signature compares lowercased values."""
        from app.ee.billing.paystack import verify_webhook_signature

        secret = "sk_test_abc123"
        body = b'{"event":"charge.success"}'
        sig = _sign_payload(body, secret).upper()  # Force uppercase
        assert verify_webhook_signature(body, sig, secret_key=secret)


class TestPaystackClient:
    @pytest.mark.asyncio
    async def test_initialize_transaction_calls_post(self) -> None:
        from app.ee.billing.paystack import (
            PaystackClient,
            initialize_transaction,
            set_client_for_tests,
        )

        mock_client = AsyncMock(spec=PaystackClient)
        mock_client.post.return_value = {
            "status": True,
            "data": {
                "authorization_url": "https://checkout.paystack.com/xyz",
                "reference": "nubi-sub-test",
                "access_code": "abc",
            },
        }
        set_client_for_tests(mock_client)

        try:
            result = await initialize_transaction(
                email="test@example.com",
                amount_kobo=149900,
                reference="nubi-sub-test",
                callback_url="https://app.nubi.io/billing/confirm",
                secret_key="sk_test_key",
            )
        finally:
            set_client_for_tests(None)

        mock_client.post.assert_awaited_once()
        call_kwargs = mock_client.post.call_args
        assert call_kwargs.kwargs["payload"]["currency"] == "ZAR"
        assert call_kwargs.kwargs["payload"]["amount"] == 149900
        assert result["status"] is True

    @pytest.mark.asyncio
    async def test_verify_transaction_calls_get(self) -> None:
        from app.ee.billing.paystack import (
            PaystackClient,
            set_client_for_tests,
            verify_transaction,
        )

        mock_client = AsyncMock(spec=PaystackClient)
        mock_client.get.return_value = {
            "status": True,
            "data": {"status": "success", "reference": "ref123"},
        }
        set_client_for_tests(mock_client)

        try:
            result = await verify_transaction("ref123", secret_key="sk_test")
        finally:
            set_client_for_tests(None)

        mock_client.get.assert_awaited_once_with(
            "/transaction/verify/ref123",
            secret_key="sk_test",
        )
        assert result["data"]["status"] == "success"


# ============================================================================
# 4. Billing store — InMemoryBillingStore
# ============================================================================


class TestInMemoryBillingStore:
    def setup_method(self) -> None:
        from app.ee.billing.store import InMemoryBillingStore, set_billing_store_for_tests

        self.store = InMemoryBillingStore()
        set_billing_store_for_tests(self.store)

    def teardown_method(self) -> None:
        from app.ee.billing.store import set_billing_store_for_tests

        set_billing_store_for_tests(None)

    @pytest.mark.asyncio
    async def test_get_subscription_unknown_org_returns_none(self) -> None:
        result = await self.store.get_subscription(str(uuid.uuid4()))
        assert result is None

    @pytest.mark.asyncio
    async def test_upsert_creates_new_subscription(self) -> None:
        org_id = str(uuid.uuid4())
        sub = await self.store.upsert_subscription(
            org_id, tier="pro", status="active"
        )
        assert sub["org_id"] == org_id
        assert sub["tier"] == "pro"
        assert sub["status"] == "active"
        assert sub["id"] is not None
        assert sub["created_at"] is not None

    @pytest.mark.asyncio
    async def test_upsert_update_preserves_id_and_created_at(self) -> None:
        org_id = str(uuid.uuid4())
        first = await self.store.upsert_subscription(org_id, tier="free", status="active")
        second = await self.store.upsert_subscription(org_id, tier="pro", status="active")
        assert first["id"] == second["id"]
        assert first["created_at"] == second["created_at"]
        assert second["tier"] == "pro"
        assert second["updated_at"] >= first["updated_at"]

    @pytest.mark.asyncio
    async def test_get_subscription_returns_stored_record(self) -> None:
        org_id = str(uuid.uuid4())
        await self.store.upsert_subscription(org_id, tier="enterprise", status="trialing")
        result = await self.store.get_subscription(org_id)
        assert result is not None
        assert result["tier"] == "enterprise"
        assert result["status"] == "trialing"

    @pytest.mark.asyncio
    async def test_record_and_list_billing_events_round_trip(self) -> None:
        org_id = str(uuid.uuid4())
        payload = {"event": "charge.success", "data": {"amount": 149900}}
        await self.store.record_billing_event(org_id, "charge.success", payload)
        events = await self.store.list_billing_events(org_id)
        assert len(events) == 1
        assert events[0]["event_type"] == "charge.success"
        assert events[0]["payload"]["data"]["amount"] == 149900

    @pytest.mark.asyncio
    async def test_list_billing_events_returns_newest_first(self) -> None:
        org_id = str(uuid.uuid4())
        for i in range(3):
            await self.store.record_billing_event(org_id, f"event_{i}", {"seq": i})
        events = await self.store.list_billing_events(org_id)
        # Newest (highest seq) should be first.
        seqs = [e["payload"]["seq"] for e in events]
        assert seqs == sorted(seqs, reverse=True)

    @pytest.mark.asyncio
    async def test_list_billing_events_respects_limit(self) -> None:
        org_id = str(uuid.uuid4())
        for i in range(10):
            await self.store.record_billing_event(org_id, "test_event", {"i": i})
        events = await self.store.list_billing_events(org_id, limit=3)
        assert len(events) <= 3

    @pytest.mark.asyncio
    async def test_status_only_upsert_preserves_period_codes_and_cancel_flag(self) -> None:
        """Patch semantics: a tier/status-only upsert (e.g. marking past_due on
        a failed charge) must not wipe the billing period, Paystack codes, or a
        scheduled cancellation — mirrors PgBillingStore's COALESCE behaviour."""
        org_id = str(uuid.uuid4())
        start = datetime(2026, 5, 1, tzinfo=timezone.utc)
        end = datetime(2026, 6, 1, tzinfo=timezone.utc)
        await self.store.upsert_subscription(
            org_id, tier="pro", status="active",
            paystack_customer_code="CUS_abc",
            paystack_subscription_code="SUB_xyz",
            current_period_start=start, current_period_end=end,
            cancel_at_period_end=True,
        )
        sub = await self.store.upsert_subscription(org_id, tier="pro", status="past_due")
        assert sub["status"] == "past_due"
        assert sub["cancel_at_period_end"] is True
        assert sub["current_period_start"] == start
        assert sub["current_period_end"] == end
        assert sub["paystack_customer_code"] == "CUS_abc"
        assert sub["paystack_subscription_code"] == "SUB_xyz"

    @pytest.mark.asyncio
    async def test_explicit_cancel_false_overwrites_stored_flag(self) -> None:
        org_id = str(uuid.uuid4())
        await self.store.upsert_subscription(
            org_id, tier="pro", status="cancelled", cancel_at_period_end=True
        )
        sub = await self.store.upsert_subscription(
            org_id, tier="pro", status="active", cancel_at_period_end=False
        )
        assert sub["cancel_at_period_end"] is False

    @pytest.mark.asyncio
    async def test_new_subscription_defaults_cancel_flag_false(self) -> None:
        org_id = str(uuid.uuid4())
        sub = await self.store.upsert_subscription(org_id, tier="free", status="active")
        assert sub["cancel_at_period_end"] is False

    @pytest.mark.asyncio
    async def test_returned_subscription_is_deep_copy(self) -> None:
        """Mutations on returned dict must not affect internal state."""
        org_id = str(uuid.uuid4())
        await self.store.upsert_subscription(org_id, tier="free", status="active")
        result = await self.store.get_subscription(org_id)
        assert result is not None
        result["tier"] = "MUTATED"
        fresh = await self.store.get_subscription(org_id)
        assert fresh is not None
        assert fresh["tier"] == "free"


# ============================================================================
# 5. Webhook route
# ============================================================================


class TestWebhookRoute:
    """Integration-style tests using httpx + ASGI without a live DB."""

    def setup_method(self) -> None:
        from app.ee.billing.store import InMemoryBillingStore, set_billing_store_for_tests

        self.store = InMemoryBillingStore()
        set_billing_store_for_tests(self.store)
        self._secret = "sk_test_webhook_secret"
        os.environ["PAYSTACK_SECRET_KEY"] = self._secret

    def teardown_method(self) -> None:
        from app.ee.billing.store import set_billing_store_for_tests

        set_billing_store_for_tests(None)
        os.environ.pop("PAYSTACK_SECRET_KEY", None)

    def _make_client(self):
        """Build a test ASGI client with the billing router mounted."""
        from fastapi import FastAPI
        from app.ee.billing.routes import router

        app = FastAPI()
        app.include_router(router)
        from httpx import ASGITransport, AsyncClient
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    @pytest.mark.asyncio
    async def test_charge_success_upserts_subscription(self) -> None:
        org_id = str(uuid.uuid4())
        payload = _make_webhook_payload("charge.success", org_id, tier="pro")
        raw_body = json.dumps(payload).encode()
        sig = _sign_payload(raw_body, self._secret)

        async with self._make_client() as client:
            resp = await client.post(
                "/ee/billing/webhook",
                content=raw_body,
                headers={
                    "Content-Type": "application/json",
                    "X-Paystack-Signature": sig,
                },
            )

        assert resp.status_code == 200
        sub = await self.store.get_subscription(org_id)
        assert sub is not None
        assert sub["tier"] == "pro"
        assert sub["status"] == "active"

    @pytest.mark.asyncio
    async def test_subscription_disable_sets_cancelled(self) -> None:
        org_id = str(uuid.uuid4())
        # Pre-seed a PRO subscription.
        await self.store.upsert_subscription(org_id, tier="pro", status="active")

        payload = _make_webhook_payload("subscription.disable", org_id)
        raw_body = json.dumps(payload).encode()
        sig = _sign_payload(raw_body, self._secret)

        async with self._make_client() as client:
            resp = await client.post(
                "/ee/billing/webhook",
                content=raw_body,
                headers={
                    "Content-Type": "application/json",
                    "X-Paystack-Signature": sig,
                },
            )

        assert resp.status_code == 200
        sub = await self.store.get_subscription(org_id)
        assert sub is not None
        assert sub["status"] == "cancelled"
        assert sub["cancel_at_period_end"] is True

    @pytest.mark.asyncio
    async def test_invalid_signature_returns_401(self) -> None:
        org_id = str(uuid.uuid4())
        payload = _make_webhook_payload("charge.success", org_id)
        raw_body = json.dumps(payload).encode()

        async with self._make_client() as client:
            resp = await client.post(
                "/ee/billing/webhook",
                content=raw_body,
                headers={
                    "Content-Type": "application/json",
                    "X-Paystack-Signature": "wrong_signature",
                },
            )

        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_org_id_returns_200(self) -> None:
        """Webhooks without org_id metadata should not crash."""
        payload = {"event": "charge.success", "data": {"metadata": {}}}
        raw_body = json.dumps(payload).encode()
        sig = _sign_payload(raw_body, self._secret)

        async with self._make_client() as client:
            resp = await client.post(
                "/ee/billing/webhook",
                content=raw_body,
                headers={
                    "Content-Type": "application/json",
                    "X-Paystack-Signature": sig,
                },
            )

        assert resp.status_code == 200


# ============================================================================
# 6. Tier route
# ============================================================================


class TestTierRoute:
    def setup_method(self) -> None:
        from app.ee.billing.store import InMemoryBillingStore, set_billing_store_for_tests

        self.store = InMemoryBillingStore()
        set_billing_store_for_tests(self.store)

    def teardown_method(self) -> None:
        from app.ee.billing.store import set_billing_store_for_tests

        set_billing_store_for_tests(None)

    def _make_client(self):
        from fastapi import FastAPI
        from app.auth.deps import current_user
        from app.ee.billing.routes import router

        app = FastAPI()
        app.include_router(router)
        # The /tier and /events routes now require an authenticated user via
        # Depends(current_user) (BUG 2 fix — the dependency is declared in the
        # endpoint signature so it survives include_router).  Override it with a
        # stub user so these store-focused tests don't need a real bearer token.
        app.dependency_overrides[current_user] = lambda: {
            "id": str(uuid.uuid4()),
            "email": "tier-test@nubi.io",
        }
        from httpx import ASGITransport, AsyncClient
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    @pytest.mark.asyncio
    async def test_unknown_org_returns_free_tier(self) -> None:
        org_id = str(uuid.uuid4())
        async with self._make_client() as client:
            resp = await client.get(f"/ee/billing/tier?org_id={org_id}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["tier"] == "free"
        assert body["org_id"] == org_id

    @pytest.mark.asyncio
    async def test_known_pro_org_returns_pro_limits(self) -> None:
        org_id = str(uuid.uuid4())
        await self.store.upsert_subscription(org_id, tier="pro", status="active")

        async with self._make_client() as client:
            resp = await client.get(f"/ee/billing/tier?org_id={org_id}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["tier"] == "pro"
        # Blueprint v1.0: unlimited seats at all tiers; PRO dial max = 80.
        assert body["limits"]["max_seats"] is None
        assert body["limits"]["security_dial_max"] == 80


# ============================================================================
# 7. Authenticated billing routes enforce the current_user dependency
# ============================================================================


class TestBillingRoutesRequireAuth:
    """BUG 2 regression: /tier, /checkout, /events must apply Depends(current_user).

    The dependency is declared in each endpoint signature so it survives
    ``app.include_router`` (which copies fresh Dependant objects — the old code
    mutated ``dep.call`` after the fact, which was a no-op).
    """

    def _app(self):
        from fastapi import FastAPI
        from app.errors import register_handlers
        from app.ee.billing.routes import router

        app = FastAPI()
        register_handlers(app)  # so AppError(401) surfaces as a 401 response
        app.include_router(router)
        return app

    def _client(self, app):
        from httpx import ASGITransport, AsyncClient
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    def test_tier_route_declares_current_user_dependency(self) -> None:
        """The route's dependant must reference current_user (survives include_router)."""
        from app.auth.deps import current_user
        from app.ee.billing.routes import router

        tier_route = next(
            r for r in router.routes if getattr(r, "path", "") == "/ee/billing/tier"
        )
        dep_calls = {d.call for d in tier_route.dependant.dependencies}
        assert current_user in dep_calls

    @pytest.mark.asyncio
    async def test_tier_without_token_is_unauthorized(self) -> None:
        app = self._app()
        async with self._client(app) as client:
            resp = await client.get(f"/ee/billing/tier?org_id={uuid.uuid4()}")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_events_without_token_is_unauthorized(self) -> None:
        app = self._app()
        async with self._client(app) as client:
            resp = await client.get(f"/ee/billing/events?org_id={uuid.uuid4()}")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_tier_with_overridden_user_succeeds(self) -> None:
        """With current_user overridden, the route resolves and returns a tier."""
        from app.auth.deps import current_user
        from app.ee.billing.store import InMemoryBillingStore, set_billing_store_for_tests

        set_billing_store_for_tests(InMemoryBillingStore())
        try:
            app = self._app()
            app.dependency_overrides[current_user] = lambda: {
                "id": str(uuid.uuid4()),
                "email": "auth-test@nubi.io",
            }
            async with self._client(app) as client:
                resp = await client.get(f"/ee/billing/tier?org_id={uuid.uuid4()}")
            assert resp.status_code == 200
            assert resp.json()["tier"] == "free"
        finally:
            set_billing_store_for_tests(None)


# ============================================================================
# 7. Runtime usage-quota enforcement (app.ee.billing.quota)
# ============================================================================


class TestQuotaChecker:
    """The EE quota checker implements the canonical billing model:

    - Unlimited (None) quotas always allow.
    - Dimensions with an overage rate allow-and-meter (wallet, then invoice).
    - Dimensions without one (FREE everywhere; agent runs on STARTER) hard-stop
      once current-period usage reaches the tier quota.
    - Without a paid deployment license, quotas never bind (no billing → no
      usage limits — OSS/self-hosted stays unrestricted).
    """

    def setup_method(self) -> None:
        os.environ["NUBI_LICENSE_KEY"] = _PRO_LICENSE_KEY
        from app.compute.metering import InMemorySink, set_sink
        from app.ee.billing.store import InMemoryBillingStore, set_billing_store_for_tests
        from app.ee.licensing.license import reset_license_cache
        from app.features import reset_for_tests

        reset_for_tests()
        reset_license_cache()
        set_billing_store_for_tests(InMemoryBillingStore())
        set_sink(InMemorySink())

    def teardown_method(self) -> None:
        from app.compute.metering import set_sink
        from app.ee.billing.store import set_billing_store_for_tests
        from app.ee.licensing.license import reset_license_cache
        from app.features import reset_for_tests

        reset_for_tests()
        set_billing_store_for_tests(None)
        set_sink(None)
        os.environ.pop("NUBI_LICENSE_KEY", None)
        reset_license_cache()

    async def _seed_subscription(self, org_id: str, tier: str) -> None:
        from app.ee.billing.store import get_billing_store

        await get_billing_store().upsert_subscription(org_id, tier=tier, status="active")

    async def _seed_usage(self, org_id: str, kind: str, units: float) -> None:
        from app.compute.metering import record_usage

        await record_usage(kind=kind, user_id="u1", org_id=org_id, units=units)

    # ── FREE tier: hard stop (no overage billing) ─────────────────────────────

    @pytest.mark.asyncio
    async def test_free_org_ai_calls_denied(self) -> None:
        """FREE includes 0 AI calls and has no overage rate → hard stop."""
        from app.ee.billing.quota import billing_quota_checker

        allowed, reason = await billing_quota_checker(
            org_id=str(uuid.uuid4()), dimension="ai_calls", amount=1.0
        )
        assert allowed is False
        assert "free" in reason
        assert "Upgrade" in reason

    @pytest.mark.asyncio
    async def test_free_org_embedded_sessions_denied(self) -> None:
        from app.ee.billing.quota import billing_quota_checker

        allowed, _ = await billing_quota_checker(
            org_id=str(uuid.uuid4()), dimension="embedded_sessions", amount=1.0
        )
        assert allowed is False

    @pytest.mark.asyncio
    async def test_free_org_compute_allowed_under_quota(self) -> None:
        """FREE includes 500 CU/month — usage below that is allowed."""
        from app.ee.billing.quota import billing_quota_checker

        org_id = str(uuid.uuid4())
        await self._seed_usage(org_id, "kernel", 499)
        allowed, _ = await billing_quota_checker(
            org_id=org_id, dimension="compute_units", amount=1.0
        )
        assert allowed is True

    @pytest.mark.asyncio
    async def test_free_org_compute_hard_stops_at_500_cu(self) -> None:
        """The documented FREE '500 CU hard stop' — usage at quota is denied."""
        from app.ee.billing.quota import billing_quota_checker

        org_id = str(uuid.uuid4())
        await self._seed_usage(org_id, "kernel", 500)
        allowed, reason = await billing_quota_checker(
            org_id=org_id, dimension="compute_units", amount=1.0
        )
        assert allowed is False
        assert "500" in reason

    @pytest.mark.asyncio
    async def test_other_orgs_usage_does_not_count(self) -> None:
        """Usage events are org-scoped — another org's burn never blocks us."""
        from app.ee.billing.quota import billing_quota_checker

        other_org, my_org = str(uuid.uuid4()), str(uuid.uuid4())
        await self._seed_usage(other_org, "kernel", 10_000)
        allowed, _ = await billing_quota_checker(
            org_id=my_org, dimension="compute_units", amount=1.0
        )
        assert allowed is True

    # ── Paid tiers: overages allow-and-meter ─────────────────────────────────

    @pytest.mark.asyncio
    async def test_starter_ai_calls_beyond_quota_allowed(self) -> None:
        """STARTER has an ai_call overage rate → beyond-quota usage is billable,
        drawn from the wallet then invoiced — never blocked."""
        from app.ee.billing.quota import billing_quota_checker

        org_id = str(uuid.uuid4())
        await self._seed_subscription(org_id, "starter")
        await self._seed_usage(org_id, "ai_call", 50)  # quota is 5
        allowed, _ = await billing_quota_checker(
            org_id=org_id, dimension="ai_calls", amount=1.0
        )
        assert allowed is True

    @pytest.mark.asyncio
    async def test_starter_agent_runs_denied(self) -> None:
        """STARTER: agent runs quota 0 AND no overage rate → hard stop
        ('no remote kernel on entry tier')."""
        from app.ee.billing.quota import billing_quota_checker

        org_id = str(uuid.uuid4())
        await self._seed_subscription(org_id, "starter")
        allowed, _ = await billing_quota_checker(
            org_id=org_id, dimension="agent_runs", amount=1.0
        )
        assert allowed is False

    @pytest.mark.asyncio
    async def test_enterprise_embedded_sessions_unlimited(self) -> None:
        """ENTERPRISE embed sessions are unlimited (quota None) → always allow."""
        from app.ee.billing.quota import billing_quota_checker

        org_id = str(uuid.uuid4())
        await self._seed_subscription(org_id, "enterprise")
        await self._seed_usage(org_id, "embedded_session", 1_000_000)
        allowed, _ = await billing_quota_checker(
            org_id=org_id, dimension="embedded_sessions", amount=1.0
        )
        assert allowed is True

    # ── Deployment-license gate ───────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_unpaid_license_never_binds_quotas(self) -> None:
        """No paid license → no billing → no usage limits (OSS/self-hosted)."""
        os.environ["NUBI_LICENSE_KEY"] = _FREE_LICENSE_KEY
        from app.ee.licensing.license import reset_license_cache

        reset_license_cache()

        from app.ee.billing.quota import billing_quota_checker

        allowed, _ = await billing_quota_checker(
            org_id=str(uuid.uuid4()), dimension="ai_calls", amount=1.0
        )
        assert allowed is True

    @pytest.mark.asyncio
    async def test_unknown_dimension_allowed(self) -> None:
        from app.ee.billing.quota import billing_quota_checker

        allowed, _ = await billing_quota_checker(
            org_id=str(uuid.uuid4()), dimension="nonsense", amount=1.0
        )
        assert allowed is True

    # ── setup() registration into the core hook ──────────────────────────────

    @pytest.mark.asyncio
    async def test_setup_registers_checker_in_core_hook(self) -> None:
        """billing.setup() wires the checker into app.features.check_quota."""
        from app.ee.billing import setup

        setup(app=None)

        from app.features import check_quota

        allowed, reason = await check_quota(str(uuid.uuid4()), "ai_calls", 1.0)
        assert allowed is False
        assert "Upgrade" in reason

    @pytest.mark.asyncio
    async def test_checker_failure_fails_open(self) -> None:
        """A broken billing store must never block a request (fail-open)."""
        from unittest.mock import patch as _patch

        from app.ee.billing.quota import billing_quota_checker

        with _patch(
            "app.ee.billing.store.get_billing_store",
            side_effect=RuntimeError("store down"),
        ):
            allowed, _ = await billing_quota_checker(
                org_id=str(uuid.uuid4()), dimension="ai_calls", amount=1.0
            )
        assert allowed is True

    # ── Usage-limits provider (read-only; feeds the OSS core usage view) ──────

    @pytest.mark.asyncio
    async def test_usage_limits_provider_maps_tier_to_metric_limits(self) -> None:
        """The provider maps a paid org's tier limits onto core usage-metric ids."""
        from app.ee.billing.quota import usage_limits_provider

        org_id = str(uuid.uuid4())
        await self._seed_subscription(org_id, "starter")
        limits = await usage_limits_provider(org_id)
        # STARTER: 2,000 CU/month, 5 AI calls, 1,000 embed sessions, 5 GB storage,
        # 0 agent runs (flow_runs).
        assert limits["compute_units"] == 2_000.0
        assert limits["ai_tokens"] == 5.0
        assert limits["embedded_sessions"] == 1_000.0
        assert limits["storage_gb"] == 5.0
        assert limits["flow_runs"] == 0.0

    @pytest.mark.asyncio
    async def test_usage_limits_provider_unlimited_maps_to_none(self) -> None:
        """ENTERPRISE unlimited embed sessions → None (core treats as unlimited)."""
        from app.ee.billing.quota import usage_limits_provider

        org_id = str(uuid.uuid4())
        await self._seed_subscription(org_id, "enterprise")
        limits = await usage_limits_provider(org_id)
        assert limits["embedded_sessions"] is None  # unlimited on Enterprise

    @pytest.mark.asyncio
    async def test_usage_limits_provider_empty_without_paid_license(self) -> None:
        """No paid license → no limits surfaced (OSS/self-hosted = unlimited view)."""
        os.environ["NUBI_LICENSE_KEY"] = _FREE_LICENSE_KEY
        from app.ee.licensing.license import reset_license_cache

        reset_license_cache()

        from app.ee.billing.quota import usage_limits_provider

        assert await usage_limits_provider(str(uuid.uuid4())) == {}

    @pytest.mark.asyncio
    async def test_setup_registers_usage_limits_provider(self) -> None:
        """billing.setup() wires the provider into app.features.get_usage_limits."""
        from app.ee.billing import setup

        setup(app=None)

        org_id = str(uuid.uuid4())
        await self._seed_subscription(org_id, "team")
        from app.features import get_usage_limits

        limits = await get_usage_limits(org_id)
        # TEAM: 6,000 CU/month.
        assert limits.get("compute_units") == 6_000.0
