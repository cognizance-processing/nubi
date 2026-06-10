"""Warehouse (heavy-query pool) in the billing model.

Coverage
--------
(1) Tier catalogue: has_warehouse is False on FREE/STARTER/TEAM, True on
    PRO/ENTERPRISE; is_feature_available exposes it as "warehouse".
(2) WAREHOUSE_CU_MULTIPLIER is the canonical 4× (matches fly.toml's
    NUBI_CU_MULTIPLIER on the query process group).
(3) Quota checker: dimension="warehouse" is a feature gate — denied on FREE
    with an upgrade message, allowed on PRO; not usage-counted (compute_units
    already meters warehouse CUs at the multiplier).
(4) /pricing serialisation: tier display dicts carry has_warehouse and
    warehouse_cu_multiplier (None when the tier has no warehouse).

No network, no DB: InMemoryBillingStore + patched license, same pattern as
test_ee_billing.py.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.ee.billing.quota import billing_quota_checker
from app.ee.billing.routes import _build_tier_display
from app.ee.billing.store import InMemoryBillingStore, set_billing_store_for_tests
from app.ee.billing.tiers import (
    WAREHOUSE_CU_MULTIPLIER,
    BillingTier,
    get_tier_limits,
    is_feature_available,
)


@pytest.fixture(autouse=True)
def _fresh_billing_store():
    store = InMemoryBillingStore()
    set_billing_store_for_tests(store)
    yield store
    set_billing_store_for_tests(None)


def _paid_license() -> MagicMock:
    lic = MagicMock()
    lic.is_paid = True
    return lic


# ---------------------------------------------------------------------------
# (1) + (2) Tier catalogue
# ---------------------------------------------------------------------------


class TestWarehouseTierFlags:
    def test_free_starter_team_have_no_warehouse(self):
        for tier in (BillingTier.FREE, BillingTier.STARTER, BillingTier.TEAM):
            assert get_tier_limits(tier).has_warehouse is False, tier

    def test_pro_and_enterprise_have_warehouse(self):
        for tier in (BillingTier.PRO, BillingTier.ENTERPRISE):
            assert get_tier_limits(tier).has_warehouse is True, tier

    def test_feature_name_mapping(self):
        assert is_feature_available(BillingTier.PRO, "warehouse") is True
        assert is_feature_available(BillingTier.FREE, "warehouse") is False

    def test_multiplier_is_canonical_4x(self):
        assert WAREHOUSE_CU_MULTIPLIER == 1  # bytes-scanned billing replaced the 4x warehouse penalty


# ---------------------------------------------------------------------------
# (3) Quota checker feature gate
# ---------------------------------------------------------------------------


class TestWarehouseQuotaGate:
    @pytest.mark.asyncio
    async def test_free_org_denied_with_upgrade_message(self, _fresh_billing_store):
        org_id = str(uuid.uuid4())  # no subscription row → FREE
        with patch(
            "app.ee.licensing.license.get_license", return_value=_paid_license()
        ):
            allowed, reason = await billing_quota_checker(
                org_id=org_id, dimension="warehouse", amount=1.0
            )
        assert allowed is False
        assert "warehouse" in reason.lower()
        assert "upgrade" in reason.lower()

    @pytest.mark.asyncio
    async def test_pro_org_allowed(self, _fresh_billing_store):
        org_id = str(uuid.uuid4())
        await _fresh_billing_store.upsert_subscription(
            org_id, tier="pro", status="active"
        )
        with patch(
            "app.ee.licensing.license.get_license", return_value=_paid_license()
        ):
            allowed, reason = await billing_quota_checker(
                org_id=org_id, dimension="warehouse", amount=1.0
            )
        assert allowed is True
        assert reason == ""

    @pytest.mark.asyncio
    async def test_unpaid_license_never_gated(self, _fresh_billing_store):
        """Self-hosted EE without a paid license is never usage-limited."""
        lic = MagicMock()
        lic.is_paid = False
        with patch("app.ee.licensing.license.get_license", return_value=lic):
            allowed, _ = await billing_quota_checker(
                org_id=str(uuid.uuid4()), dimension="warehouse", amount=1.0
            )
        assert allowed is True


# ---------------------------------------------------------------------------
# (4) Pricing serialisation
# ---------------------------------------------------------------------------


class TestWarehousePricingDisplay:
    def test_pro_display_carries_warehouse_fields(self):
        d = _build_tier_display(get_tier_limits(BillingTier.PRO))
        assert d["features"]["has_warehouse"] is True
        assert d["features"]["warehouse_cu_multiplier"] == WAREHOUSE_CU_MULTIPLIER

    def test_free_display_has_no_multiplier(self):
        d = _build_tier_display(get_tier_limits(BillingTier.FREE))
        assert d["features"]["has_warehouse"] is False
        assert d["features"]["warehouse_cu_multiplier"] is None


# ---------------------------------------------------------------------------
# (5) Usage aggregation breaks out warehouse CU (subset of compute_units)
# ---------------------------------------------------------------------------


class TestWarehouseUsageBreakout:
    def test_aggregate_splits_warehouse_cu(self):
        from app.ee.billing.reconcile import aggregate_usage_from_events

        events = [
            {"kind": "compute", "units": 10.0, "tier": "duckdb"},
            {"kind": "compute", "units": 8.0, "tier": "duckdb:warehouse"},
            {"kind": "compute", "units": 2.0, "warehouse": True},  # pre-aggregated DB row shape
            {"kind": "ai_call", "units": 1},
        ]
        snap = aggregate_usage_from_events(events)
        # Total compute includes warehouse CU; warehouse_cu is the breakout.
        assert snap.compute_units == 20
        assert snap.warehouse_cu == 10
        assert snap.to_dict()["warehouse_cu"] == 10

    def test_no_warehouse_events_means_zero_breakout(self):
        from app.ee.billing.reconcile import aggregate_usage_from_events

        snap = aggregate_usage_from_events([{"kind": "compute", "units": 5.0, "tier": "duckdb"}])
        assert snap.compute_units == 5
        assert snap.warehouse_cu == 0
