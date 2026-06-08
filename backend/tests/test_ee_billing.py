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

    def test_pro_tier_price_3310(self) -> None:
        """PRO ZAR reference price corrected to R3,310 in v1.0 pricing blueprint.

        $199 × R16.26 × 1.02 = R3,300.45 → ceil10 = R3,310.
        The previous R3,300 used standard rounding; blueprint requires ceil.
        """
        from decimal import Decimal

        from app.ee.billing.tiers import BillingTier, get_tier_limits

        limits = get_tier_limits(BillingTier.PRO)
        assert limits.monthly_price_zar == Decimal("3310.00")

    def test_enterprise_tier_price_29840(self) -> None:
        """ENTERPRISE ZAR floor updated to R29,840 in v1.0 pricing blueprint."""
        from decimal import Decimal

        from app.ee.billing.tiers import BillingTier, get_tier_limits

        limits = get_tier_limits(BillingTier.ENTERPRISE)
        assert limits.monthly_price_zar == Decimal("29840.00")

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

    def test_security_dial_mid_maps_to_starter_then_pro(self) -> None:
        """Updated dial mapping: 41-50 → STARTER, 51-80 → PRO (v1.0 blueprint)."""
        from app.ee.billing.tiers import BillingTier, tier_for_security_dial

        # 41–50 → STARTER (new tier)
        assert tier_for_security_dial(41) == BillingTier.STARTER
        assert tier_for_security_dial(50) == BillingTier.STARTER
        # 51–80 → PRO
        assert tier_for_security_dial(51) == BillingTier.PRO
        assert tier_for_security_dial(60) == BillingTier.PRO
        assert tier_for_security_dial(80) == BillingTier.PRO

    def test_security_dial_high_maps_to_business(self) -> None:
        """Updated: 81-100 → BUSINESS (v1.0 blueprint; ENTERPRISE has same dial range)."""
        from app.ee.billing.tiers import BillingTier, tier_for_security_dial

        assert tier_for_security_dial(81) == BillingTier.BUSINESS
        assert tier_for_security_dial(100) == BillingTier.BUSINESS

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
        from app.ee.billing.routes import router

        app = FastAPI()
        app.include_router(router)
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
