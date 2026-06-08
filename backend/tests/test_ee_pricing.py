"""Tests for EE billing tier definitions — v1.0 pricing blueprint.

Coverage
--------
1. BillingTier enum has all five values: free, starter, pro, business, enterprise.
2. all_tiers() returns all five tiers in order.
3. USD anchor prices match the approved design verbatim.
4. ZAR reference prices match the approved design verbatim.
5. Annual prices = 10 months (2 months free).
6. Gross margin data is present and ≥70% for all paid tiers.
7. Per-tier quota limits (seats, storage, compute, AI calls, etc.).
8. Security-dial → tier mapping matches the approved design.
9. Feature flags per tier (RLS, white-label, SSO, SCIM, etc.).
10. Overage rates present and correct for Starter+.
11. is_feature_available() helper.
12. is_within_quota() helper.
13. billing_tier_from_license_tier() round-trips + defaults.
14. ZAR_DISCLOSURE_COPY contains required words.
"""

from __future__ import annotations

import os
from decimal import Decimal

import pytest

# ---------------------------------------------------------------------------
# Env setup before importing app modules.
# ---------------------------------------------------------------------------

os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef")
os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("ENV", "test")


# ============================================================================
# 1 & 2. BillingTier enum + all_tiers
# ============================================================================


class TestBillingTierEnum:
    def test_all_five_tier_values_exist(self) -> None:
        from app.ee.billing.tiers import BillingTier

        values = {t.value for t in BillingTier}
        assert values == {"free", "starter", "pro", "business", "enterprise"}

    def test_all_tiers_returns_five_tiers(self) -> None:
        from app.ee.billing.tiers import BillingTier, all_tiers

        tiers = all_tiers()
        assert len(tiers) == 5
        assert {t.tier for t in tiers} == set(BillingTier)

    def test_all_tiers_ordered_free_to_enterprise(self) -> None:
        from app.ee.billing.tiers import BillingTier, all_tiers

        tiers = all_tiers()
        expected_order = [
            BillingTier.FREE,
            BillingTier.STARTER,
            BillingTier.PRO,
            BillingTier.BUSINESS,
            BillingTier.ENTERPRISE,
        ]
        assert [t.tier for t in tiers] == expected_order


# ============================================================================
# 3. USD anchor prices
# ============================================================================


class TestUsdAnchorPrices:
    """All USD prices must match the approved design verbatim."""

    def _limits(self, tier_name: str):
        from app.ee.billing.tiers import BillingTier, get_tier_limits
        return get_tier_limits(BillingTier(tier_name))

    def test_free_usd_price_is_zero(self) -> None:
        limits = self._limits("free")
        assert limits.usd_monthly_price == Decimal("0.00")
        assert limits.usd_annual_price == Decimal("0.00")

    def test_starter_usd_monthly_79(self) -> None:
        limits = self._limits("starter")
        assert limits.usd_monthly_price == Decimal("79.00")

    def test_starter_usd_annual_790(self) -> None:
        limits = self._limits("starter")
        assert limits.usd_annual_price == Decimal("790.00")

    def test_pro_usd_monthly_199(self) -> None:
        limits = self._limits("pro")
        assert limits.usd_monthly_price == Decimal("199.00")

    def test_pro_usd_annual_1990(self) -> None:
        limits = self._limits("pro")
        assert limits.usd_annual_price == Decimal("1990.00")

    def test_business_usd_monthly_499(self) -> None:
        limits = self._limits("business")
        assert limits.usd_monthly_price == Decimal("499.00")

    def test_business_usd_annual_4990(self) -> None:
        limits = self._limits("business")
        assert limits.usd_annual_price == Decimal("4990.00")

    def test_enterprise_usd_monthly_floor_1799(self) -> None:
        limits = self._limits("enterprise")
        assert limits.usd_monthly_price == Decimal("1799.00")

    def test_enterprise_usd_annual_floor_17990(self) -> None:
        limits = self._limits("enterprise")
        assert limits.usd_annual_price == Decimal("17990.00")

    def test_annual_price_equals_10_months_for_paid_tiers(self) -> None:
        """Annual = 10 × monthly (2 months free)."""
        from app.ee.billing.tiers import BillingTier, get_tier_limits

        for tier in (BillingTier.STARTER, BillingTier.PRO, BillingTier.BUSINESS, BillingTier.ENTERPRISE):
            limits = get_tier_limits(tier)
            assert limits.usd_annual_price == limits.usd_monthly_price * 10, (
                f"{tier.value}: annual {limits.usd_annual_price} != monthly "
                f"{limits.usd_monthly_price} × 10"
            )


# ============================================================================
# 4. ZAR reference prices
# ============================================================================


class TestZarReferencePrices:
    """ZAR reference amounts @ June 2026 (R16.26 + 2% FX buffer, ceil to R10)."""

    def _zar(self, tier_name: str) -> Decimal:
        from app.ee.billing.tiers import BillingTier, get_tier_limits
        return get_tier_limits(BillingTier(tier_name)).monthly_price_zar

    def test_free_zar_is_zero(self) -> None:
        assert self._zar("free") == Decimal("0.00")

    def test_starter_zar_1320(self) -> None:
        # R16.26 × 1.02 × $79 = R1,310.23 → ceil10 = R1,320 (blueprint v1.0 fix)
        assert self._zar("starter") == Decimal("1320.00")

    def test_pro_zar_3310(self) -> None:
        # R16.26 × 1.02 × $199 = R3,300.45 → ceil10 = R3,310 (blueprint v1.0 fix)
        assert self._zar("pro") == Decimal("3310.00")

    def test_business_zar_8280(self) -> None:
        assert self._zar("business") == Decimal("8280.00")

    def test_enterprise_zar_floor_29840(self) -> None:
        assert self._zar("enterprise") == Decimal("29840.00")


# ============================================================================
# 6. Gross margin data
# ============================================================================


class TestGrossMargins:
    """All paid tiers must meet the ≥70% gross margin target."""

    def test_paid_tiers_have_gross_margin_data(self) -> None:
        from app.ee.billing.tiers import BillingTier, get_tier_limits

        for tier in (BillingTier.STARTER, BillingTier.PRO, BillingTier.BUSINESS, BillingTier.ENTERPRISE):
            limits = get_tier_limits(tier)
            assert limits.gross_margin_pct is not None, f"{tier.value}: no margin data"
            assert limits.infra_cogs_zar > Decimal("0"), f"{tier.value}: no infra_cogs"
            assert limits.total_cogs_zar > Decimal("0"), f"{tier.value}: no total_cogs"

    def test_paid_tiers_meet_70_pct_margin_target(self) -> None:
        from app.ee.billing.tiers import BillingTier, get_tier_limits

        for tier in (BillingTier.STARTER, BillingTier.PRO, BillingTier.BUSINESS, BillingTier.ENTERPRISE):
            limits = get_tier_limits(tier)
            assert limits.gross_margin_pct >= 70.0, (
                f"{tier.value}: margin {limits.gross_margin_pct}% < 70%"
            )

    def test_specific_margins_match_design(self) -> None:
        from app.ee.billing.tiers import BillingTier, get_tier_limits

        # Blueprint v1.0 margins: Starter 81.7%, Pro 72.6%, Business 70.5%, Enterprise 77.2%
        expected = {
            BillingTier.STARTER: 81.7,
            BillingTier.PRO: 72.6,
            BillingTier.BUSINESS: 70.5,
            BillingTier.ENTERPRISE: 77.2,
        }
        for tier, margin in expected.items():
            limits = get_tier_limits(tier)
            assert limits.gross_margin_pct == pytest.approx(margin, abs=0.1), (
                f"{tier.value}: margin {limits.gross_margin_pct} != {margin}"
            )

    def test_total_cogs_match_design(self) -> None:
        from app.ee.billing.tiers import BillingTier, get_tier_limits

        # Blueprint v1.0 COGS: Starter R242.15, Pro R906.71, Business R2,440.34, Enterprise R6,808.93
        expected = {
            BillingTier.STARTER: Decimal("242.15"),
            BillingTier.PRO: Decimal("906.71"),
            BillingTier.BUSINESS: Decimal("2440.34"),
            BillingTier.ENTERPRISE: Decimal("6808.93"),
        }
        for tier, cogs in expected.items():
            limits = get_tier_limits(tier)
            assert limits.total_cogs_zar == cogs, (
                f"{tier.value}: cogs {limits.total_cogs_zar} != {cogs}"
            )

    def test_free_tier_has_no_margin(self) -> None:
        from app.ee.billing.tiers import BillingTier, get_tier_limits

        limits = get_tier_limits(BillingTier.FREE)
        assert limits.gross_margin_pct is None  # no revenue → no margin


# ============================================================================
# 7. Per-tier quotas
# ============================================================================


class TestQuotas:
    def _limits(self, tier_name: str):
        from app.ee.billing.tiers import BillingTier, get_tier_limits
        return get_tier_limits(BillingTier(tier_name))

    # ── Free ──

    def test_free_max_seats_unlimited(self) -> None:
        # Blueprint v1.0: unlimited seats at every tier — no per-seat pricing.
        assert self._limits("free").max_seats is None

    def test_free_max_viewer_seats_unlimited(self) -> None:
        # Blueprint v1.0: unlimited viewers at every tier.
        assert self._limits("free").max_viewer_seats is None

    def test_free_max_connectors_3(self) -> None:
        assert self._limits("free").max_connectors == 3

    def test_free_max_storage_gb_2(self) -> None:
        assert self._limits("free").max_storage_gb == 2.0

    def test_free_max_compute_units_500(self) -> None:
        assert self._limits("free").max_compute_units_per_month == 500

    def test_free_max_embedded_sessions_0(self) -> None:
        assert self._limits("free").max_embedded_sessions_per_month == 0

    def test_free_max_ai_calls_0(self) -> None:
        assert self._limits("free").max_ai_calls_per_month == 0

    # ── Starter ──

    def test_starter_max_seats_unlimited(self) -> None:
        # Blueprint v1.0: unlimited seats at every tier — no per-seat pricing.
        assert self._limits("starter").max_seats is None

    def test_starter_max_viewer_seats_unlimited(self) -> None:
        assert self._limits("starter").max_viewer_seats is None

    def test_starter_max_connectors_10(self) -> None:
        assert self._limits("starter").max_connectors == 10

    def test_starter_max_storage_gb_10(self) -> None:
        assert self._limits("starter").max_storage_gb == 10.0

    def test_starter_max_compute_units_5000(self) -> None:
        assert self._limits("starter").max_compute_units_per_month == 5_000

    def test_starter_max_embedded_sessions_5000(self) -> None:
        assert self._limits("starter").max_embedded_sessions_per_month == 5_000

    def test_starter_max_ai_calls_10(self) -> None:
        assert self._limits("starter").max_ai_calls_per_month == 10

    def test_starter_no_agent_runs(self) -> None:
        assert self._limits("starter").max_agent_runs_per_month == 0

    # ── Pro ──

    def test_pro_max_seats_unlimited(self) -> None:
        # Blueprint v1.0: unlimited seats at every tier — no per-seat pricing.
        assert self._limits("pro").max_seats is None

    def test_pro_max_connectors_unlimited(self) -> None:
        assert self._limits("pro").max_connectors is None

    def test_pro_max_storage_gb_50(self) -> None:
        assert self._limits("pro").max_storage_gb == 50.0

    def test_pro_max_compute_units_10000(self) -> None:
        assert self._limits("pro").max_compute_units_per_month == 10_000

    def test_pro_max_embedded_sessions_25000(self) -> None:
        assert self._limits("pro").max_embedded_sessions_per_month == 25_000

    def test_pro_max_agent_runs_50(self) -> None:
        assert self._limits("pro").max_agent_runs_per_month == 50

    def test_pro_max_ai_calls_50(self) -> None:
        assert self._limits("pro").max_ai_calls_per_month == 50

    # ── Business ──

    def test_business_max_seats_unlimited(self) -> None:
        # Blueprint v1.0: unlimited seats at every tier — no per-seat pricing.
        assert self._limits("business").max_seats is None

    def test_business_max_storage_gb_200(self) -> None:
        assert self._limits("business").max_storage_gb == 200.0

    def test_business_max_compute_units_40000(self) -> None:
        assert self._limits("business").max_compute_units_per_month == 40_000

    def test_business_max_query_rows_unlimited(self) -> None:
        assert self._limits("business").max_query_rows is None

    def test_business_max_dashboards_unlimited(self) -> None:
        assert self._limits("business").max_dashboards is None

    # ── Enterprise ──

    def test_enterprise_max_seats_unlimited(self) -> None:
        assert self._limits("enterprise").max_seats is None

    def test_enterprise_max_storage_gb_500_hosted(self) -> None:
        # Hosted = 500 GB; BYOC = unlimited (None for BYOC handled externally).
        assert self._limits("enterprise").max_storage_gb == 500.0

    def test_enterprise_max_compute_units_200000(self) -> None:
        assert self._limits("enterprise").max_compute_units_per_month == 200_000

    def test_enterprise_max_agent_runs_1000(self) -> None:
        assert self._limits("enterprise").max_agent_runs_per_month == 1_000

    def test_enterprise_max_ai_calls_500(self) -> None:
        assert self._limits("enterprise").max_ai_calls_per_month == 500


# ============================================================================
# 8. Security-dial → tier mapping
# ============================================================================


class TestSecurityDialMapping:
    def test_dial_0_maps_to_free(self) -> None:
        from app.ee.billing.tiers import BillingTier, tier_for_security_dial
        assert tier_for_security_dial(0) == BillingTier.FREE

    def test_dial_40_maps_to_free(self) -> None:
        from app.ee.billing.tiers import BillingTier, tier_for_security_dial
        assert tier_for_security_dial(40) == BillingTier.FREE

    def test_dial_41_maps_to_starter(self) -> None:
        from app.ee.billing.tiers import BillingTier, tier_for_security_dial
        assert tier_for_security_dial(41) == BillingTier.STARTER

    def test_dial_50_maps_to_starter(self) -> None:
        from app.ee.billing.tiers import BillingTier, tier_for_security_dial
        assert tier_for_security_dial(50) == BillingTier.STARTER

    def test_dial_51_maps_to_pro(self) -> None:
        from app.ee.billing.tiers import BillingTier, tier_for_security_dial
        assert tier_for_security_dial(51) == BillingTier.PRO

    def test_dial_80_maps_to_pro(self) -> None:
        from app.ee.billing.tiers import BillingTier, tier_for_security_dial
        assert tier_for_security_dial(80) == BillingTier.PRO

    def test_dial_81_maps_to_business(self) -> None:
        from app.ee.billing.tiers import BillingTier, tier_for_security_dial
        assert tier_for_security_dial(81) == BillingTier.BUSINESS

    def test_dial_100_maps_to_business(self) -> None:
        from app.ee.billing.tiers import BillingTier, tier_for_security_dial
        assert tier_for_security_dial(100) == BillingTier.BUSINESS

    def test_dial_out_of_range_raises_value_error(self) -> None:
        from app.ee.billing.tiers import tier_for_security_dial
        with pytest.raises(ValueError):
            tier_for_security_dial(-1)
        with pytest.raises(ValueError):
            tier_for_security_dial(101)

    def test_dial_max_per_tier_matches_limits(self) -> None:
        """Each tier's security_dial_max matches the mapping table."""
        from app.ee.billing.tiers import BillingTier, get_tier_limits

        expected = {
            BillingTier.FREE: 40,
            BillingTier.STARTER: 50,
            BillingTier.PRO: 80,
            BillingTier.BUSINESS: 100,
            BillingTier.ENTERPRISE: 100,
        }
        for tier, max_dial in expected.items():
            limits = get_tier_limits(tier)
            assert limits.security_dial_max == max_dial, (
                f"{tier.value}: security_dial_max {limits.security_dial_max} != {max_dial}"
            )


# ============================================================================
# 9. Feature flags
# ============================================================================


class TestFeatureFlags:
    def _limits(self, tier_name: str):
        from app.ee.billing.tiers import BillingTier, get_tier_limits
        return get_tier_limits(BillingTier(tier_name))

    # ── Free: nothing enabled ──

    def test_free_no_white_label(self) -> None:
        assert self._limits("free").has_white_label is False

    def test_free_no_rls(self) -> None:
        assert self._limits("free").has_rls is False

    def test_free_no_sso(self) -> None:
        assert self._limits("free").has_sso_google is False
        assert self._limits("free").has_sso_saml is False

    def test_free_no_audit_logs(self) -> None:
        assert self._limits("free").audit_log_retention_days == 0

    # ── Starter ──

    def test_starter_badge_removable(self) -> None:
        assert self._limits("starter").has_white_label == "badge_removable"

    def test_starter_basic_rls(self) -> None:
        assert self._limits("starter").has_rls == "basic"

    def test_starter_google_sso(self) -> None:
        assert self._limits("starter").has_sso_google is True

    def test_starter_no_saml(self) -> None:
        assert self._limits("starter").has_sso_saml is False

    def test_starter_audit_logs_30_days(self) -> None:
        assert self._limits("starter").audit_log_retention_days == 30

    def test_starter_sla_99_5(self) -> None:
        assert self._limits("starter").sla_uptime_pct == 99.5

    # ── Pro ──

    def test_pro_full_white_label(self) -> None:
        assert self._limits("pro").has_white_label == "full"

    def test_pro_full_rls_jwt(self) -> None:
        assert self._limits("pro").has_rls == "full_jwt"

    def test_pro_saml_1_idp(self) -> None:
        assert self._limits("pro").has_sso_saml == "1_idp"

    def test_pro_no_scim(self) -> None:
        assert self._limits("pro").has_scim is False

    def test_pro_custom_domain(self) -> None:
        assert self._limits("pro").has_custom_domain is True

    def test_pro_audit_logs_90_days(self) -> None:
        assert self._limits("pro").audit_log_retention_days == 90

    # ── Business ──

    def test_business_full_multi_tenant_white_label(self) -> None:
        assert self._limits("business").has_white_label == "full_multi_tenant"

    def test_business_full_rls_jwt_passthrough(self) -> None:
        assert self._limits("business").has_rls == "full_jwt_passthrough"

    def test_business_saml_unlimited(self) -> None:
        assert self._limits("business").has_sso_saml == "unlimited_idps"

    def test_business_scim_enabled(self) -> None:
        assert self._limits("business").has_scim is True

    def test_business_multi_tenant_workspaces(self) -> None:
        assert self._limits("business").has_multi_tenant_workspaces is True

    def test_business_audit_logs_365_days(self) -> None:
        assert self._limits("business").audit_log_retention_days == 365

    def test_business_priority_support_email_slack(self) -> None:
        assert self._limits("business").has_priority_support == "email_slack"

    def test_business_sla_99_9(self) -> None:
        assert self._limits("business").sla_uptime_pct == 99.9

    # ── Enterprise ──

    def test_enterprise_full_custom_sdk_white_label(self) -> None:
        assert self._limits("enterprise").has_white_label == "full_custom_sdk"

    def test_enterprise_hipaa_rls(self) -> None:
        assert self._limits("enterprise").has_rls == "full_hipaa_ready"

    def test_enterprise_byoc(self) -> None:
        assert self._limits("enterprise").has_byoc is True

    def test_enterprise_audit_logs_unlimited(self) -> None:
        # None = unlimited retention.
        assert self._limits("enterprise").audit_log_retention_days is None

    def test_enterprise_dedicated_csm(self) -> None:
        assert self._limits("enterprise").has_priority_support == "dedicated_csm"

    def test_enterprise_sla_99_99(self) -> None:
        assert self._limits("enterprise").sla_uptime_pct == 99.99


# ============================================================================
# 10. Overage rates
# ============================================================================


class TestOverageRates:
    def _overages(self, tier_name: str):
        from app.ee.billing.tiers import BillingTier, get_tier_limits
        return get_tier_limits(BillingTier(tier_name)).overages

    def test_free_no_overages(self) -> None:
        ov = self._overages("free")
        assert ov.storage_zar_per_gb_month is None
        assert ov.compute_zar_per_1000_cu is None

    def test_starter_storage_overage_1_50(self) -> None:
        assert self._overages("starter").storage_zar_per_gb_month == Decimal("1.50")

    def test_starter_compute_overage_100(self) -> None:
        assert self._overages("starter").compute_zar_per_1000_cu == Decimal("100.00")

    def test_starter_ai_call_overage_5(self) -> None:
        assert self._overages("starter").ai_call_zar_per_call == Decimal("5.00")

    def test_starter_embedded_session_overage_50_per_10k(self) -> None:
        assert self._overages("starter").embedded_session_zar_per_10k == Decimal("50.00")

    def test_starter_no_seat_overage(self) -> None:
        # Starter has no seat overage — must upgrade to Pro.
        assert self._overages("starter").seat_zar_per_seat_month is None

    def test_pro_no_seat_overage(self) -> None:
        # Blueprint v1.0: no per-seat pricing at any tier.
        assert self._overages("pro").seat_zar_per_seat_month is None

    def test_pro_agent_run_overage_2(self) -> None:
        assert self._overages("pro").agent_run_zar_per_run == Decimal("2.00")

    def test_business_all_usage_overages_present(self) -> None:
        # Blueprint v1.0: Business has all usage-based overages but no seat overage.
        ov = self._overages("business")
        assert ov.storage_zar_per_gb_month is not None
        assert ov.compute_zar_per_1000_cu is not None
        assert ov.ai_call_zar_per_call is not None
        assert ov.embedded_session_zar_per_10k is not None
        assert ov.agent_run_zar_per_run is not None

    def test_business_no_seat_overage(self) -> None:
        # Blueprint v1.0: no per-seat pricing at any tier.
        assert self._overages("business").seat_zar_per_seat_month is None

    def test_enterprise_embed_session_overage_zero(self) -> None:
        # Enterprise includes unlimited embed sessions — overage rate = 0.
        ov = self._overages("enterprise")
        assert ov.embedded_session_zar_per_10k == Decimal("0.00")

    def test_enterprise_no_seat_overage(self) -> None:
        # Unlimited seats.
        assert self._overages("enterprise").seat_zar_per_seat_month is None


# ============================================================================
# 11. is_feature_available() helper
# ============================================================================


class TestIsFeatureAvailable:
    def test_free_rls_not_available(self) -> None:
        from app.ee.billing.tiers import BillingTier, is_feature_available
        assert not is_feature_available(BillingTier.FREE, "rls")

    def test_starter_rls_available(self) -> None:
        from app.ee.billing.tiers import BillingTier, is_feature_available
        assert is_feature_available(BillingTier.STARTER, "rls")

    def test_pro_white_label_available(self) -> None:
        from app.ee.billing.tiers import BillingTier, is_feature_available
        assert is_feature_available(BillingTier.PRO, "white_label")

    def test_free_audit_logs_not_available(self) -> None:
        from app.ee.billing.tiers import BillingTier, is_feature_available
        assert not is_feature_available(BillingTier.FREE, "audit_logs")

    def test_starter_audit_logs_available(self) -> None:
        from app.ee.billing.tiers import BillingTier, is_feature_available
        assert is_feature_available(BillingTier.STARTER, "audit_logs")

    def test_business_scim_available(self) -> None:
        from app.ee.billing.tiers import BillingTier, is_feature_available
        assert is_feature_available(BillingTier.BUSINESS, "scim")

    def test_pro_no_scim(self) -> None:
        from app.ee.billing.tiers import BillingTier, is_feature_available
        assert not is_feature_available(BillingTier.PRO, "scim")

    def test_enterprise_byoc_available(self) -> None:
        from app.ee.billing.tiers import BillingTier, is_feature_available
        assert is_feature_available(BillingTier.ENTERPRISE, "byoc")

    def test_unknown_feature_returns_false(self) -> None:
        from app.ee.billing.tiers import BillingTier, is_feature_available
        assert not is_feature_available(BillingTier.ENTERPRISE, "nonexistent_feature")


# ============================================================================
# 12. is_within_quota() helper
# ============================================================================


class TestIsWithinQuota:
    def test_free_seats_unlimited_always_allowed(self) -> None:
        # Blueprint v1.0: all tiers have unlimited seats — is_within_quota always True for seats.
        from app.ee.billing.tiers import BillingTier, is_within_quota
        assert is_within_quota(BillingTier.FREE, "seats", 3)
        assert is_within_quota(BillingTier.FREE, "seats", 100)
        assert is_within_quota(BillingTier.FREE, "seats", 10_000)

    def test_starter_10_connectors_within_limit(self) -> None:
        from app.ee.billing.tiers import BillingTier, is_within_quota
        assert is_within_quota(BillingTier.STARTER, "connectors", 10)

    def test_starter_11_connectors_exceeds_limit(self) -> None:
        from app.ee.billing.tiers import BillingTier, is_within_quota
        assert not is_within_quota(BillingTier.STARTER, "connectors", 11)

    def test_pro_connectors_unlimited(self) -> None:
        from app.ee.billing.tiers import BillingTier, is_within_quota
        assert is_within_quota(BillingTier.PRO, "connectors", 999_999)

    def test_enterprise_seats_unlimited(self) -> None:
        from app.ee.billing.tiers import BillingTier, is_within_quota
        assert is_within_quota(BillingTier.ENTERPRISE, "seats", 100_000)

    def test_unknown_quota_returns_true(self) -> None:
        from app.ee.billing.tiers import BillingTier, is_within_quota
        assert is_within_quota(BillingTier.FREE, "nonexistent_quota", 999)


# ============================================================================
# 13. billing_tier_from_license_tier
# ============================================================================


class TestBillingTierFromLicenseTier:
    def test_round_trips_all_five_tiers(self) -> None:
        from app.ee.billing.tiers import BillingTier, billing_tier_from_license_tier

        for tier in BillingTier:
            assert billing_tier_from_license_tier(tier.value) == tier

    def test_unknown_value_defaults_to_free(self) -> None:
        from app.ee.billing.tiers import BillingTier, billing_tier_from_license_tier

        assert billing_tier_from_license_tier("unknown_value") == BillingTier.FREE

    def test_case_insensitive(self) -> None:
        from app.ee.billing.tiers import BillingTier, billing_tier_from_license_tier

        assert billing_tier_from_license_tier("PRO") == BillingTier.PRO
        assert billing_tier_from_license_tier("Starter") == BillingTier.STARTER


# ============================================================================
# 14. ZAR_DISCLOSURE_COPY
# ============================================================================


class TestDisclosureCopy:
    def test_disclosure_copy_contains_required_words(self) -> None:
        from app.ee.billing.tiers import ZAR_DISCLOSURE_COPY

        copy = ZAR_DISCLOSURE_COPY.lower()
        assert "usd" in copy or "us dollar" in copy or "us dollars" in copy
        assert "zar" in copy or "rand" in copy
        assert "exchange rate" in copy
        assert "billing" in copy

    def test_disclosure_copy_is_non_empty_string(self) -> None:
        from app.ee.billing.tiers import ZAR_DISCLOSURE_COPY

        assert isinstance(ZAR_DISCLOSURE_COPY, str)
        assert len(ZAR_DISCLOSURE_COPY) > 50
