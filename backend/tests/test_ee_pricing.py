"""Tests for the Nubi EE billing model (v3 — 4-tier redesign).

Coverage
--------
1.  BillingTier enum + all_tiers() — four tiers, correct order.
2.  USD anchor prices — match approved design verbatim.
3.  ZAR reference prices — match approved design verbatim.
4.  Annual prices = 10 months (2 months free).
5.  Gross margin data present; ALL paid tiers ≥75% (new target floor).
6.  COGS-based unlimited quota policy:
       - max_seats is None at ALL tiers (no per-seat pricing)
       - max_viewer_seats is None at ALL tiers
       - max_connectors is None at Pro/Enterprise
       - max_dashboards is None at Enterprise
       - max_flows is None at Enterprise (flow defs = DB rows, ~0 COGS)
7.  Metered dimensions map to real COGS lines:
       - max_storage_gb is bounded (object-storage cost)
       - max_compute_units_per_month is bounded (container/query compute cost)
       - max_embedded_sessions_per_month is bounded on Free/Starter/Pro
         (egress + per-request compute cost); None = unlimited on Enterprise
       - max_ai_calls_per_month is bounded (Anthropic API token cost)
       - max_agent_runs_per_month is bounded (container compute cost)
8.  Security-dial → tier mapping.
9.  Feature flags per tier.
10. Overage rates — present on Starter+, correct values.
11. is_feature_available() helper.
12. is_within_quota() helper.
13. billing_tier_from_license_tier() round-trips + defaults + legacy "business" mapping.
14. ZAR_DISCLOSURE_COPY contains required words.
15. Enterprise SLA attributes — uptime_pct, P1/P2 response times documented.
16. competitors.bi_competitors() — structure and required fields.
17. competitors.orchestration_competitors() — structure and required fields.
18. competitors.all_competitors() — both categories present + as_of.
19. Orchestration competitors do NOT charge per-seat (verify nubi_advantage).
20. Public GET /pricing endpoint — unauthenticated, correct shape.
21. GET /pricing fx block — rate as string, stale flag present.
22. GET /pricing competitors block — bi + orchestration present.
23. GET /pricing tiers — all four tiers, Decimal values serialised as strings.
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
# 1. BillingTier enum + all_tiers
# ============================================================================


class TestBillingTierEnum:
    def test_five_tier_values_exist(self) -> None:
        from app.ee.billing.tiers import BillingTier

        values = {t.value for t in BillingTier}
        assert values == {"free", "starter", "team", "pro", "enterprise"}

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
            BillingTier.TEAM,
            BillingTier.PRO,
            BillingTier.ENTERPRISE,
        ]
        assert [t.tier for t in tiers] == expected_order

    def test_no_business_tier(self) -> None:
        """v3 model drops the old Business tier."""
        from app.ee.billing.tiers import BillingTier

        assert "business" not in {t.value for t in BillingTier}


# ============================================================================
# 2. USD anchor prices
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

    def test_starter_usd_monthly_9(self) -> None:
        assert self._limits("starter").usd_monthly_price == Decimal("9.00")

    def test_starter_usd_annual_90(self) -> None:
        assert self._limits("starter").usd_annual_price == Decimal("90.00")

    def test_team_usd_monthly_49(self) -> None:
        assert self._limits("team").usd_monthly_price == Decimal("49.00")

    def test_team_usd_annual_490(self) -> None:
        assert self._limits("team").usd_annual_price == Decimal("490.00")

    def test_pro_usd_monthly_149(self) -> None:
        assert self._limits("pro").usd_monthly_price == Decimal("149.00")

    def test_pro_usd_annual_1490(self) -> None:
        assert self._limits("pro").usd_annual_price == Decimal("1490.00")

    def test_enterprise_usd_monthly_floor_1000(self) -> None:
        assert self._limits("enterprise").usd_monthly_price == Decimal("1000.00")

    def test_enterprise_usd_annual_floor_10000(self) -> None:
        assert self._limits("enterprise").usd_annual_price == Decimal("10000.00")

    def test_annual_price_equals_10_months_for_paid_tiers(self) -> None:
        """Annual = 10 × monthly (2 months free)."""
        from app.ee.billing.tiers import BillingTier, get_tier_limits

        for tier in (BillingTier.STARTER, BillingTier.TEAM, BillingTier.PRO, BillingTier.ENTERPRISE):
            limits = get_tier_limits(tier)
            assert limits.usd_annual_price == limits.usd_monthly_price * 10, (
                f"{tier.value}: annual {limits.usd_annual_price} != monthly "
                f"{limits.usd_monthly_price} × 10"
            )


# ============================================================================
# 3. ZAR reference prices
# ============================================================================


class TestZarReferencePrices:
    """ZAR reference amounts @ June 2026 (R16.26 + 2% FX buffer, ceil to R10)."""

    def _zar(self, tier_name: str) -> Decimal:
        from app.ee.billing.tiers import BillingTier, get_tier_limits
        return get_tier_limits(BillingTier(tier_name)).monthly_price_zar

    def test_free_zar_is_zero(self) -> None:
        assert self._zar("free") == Decimal("0.00")

    def test_starter_zar_150(self) -> None:
        assert self._zar("starter") == Decimal("150.00")

    def test_team_zar_820(self) -> None:
        assert self._zar("team") == Decimal("820.00")

    def test_pro_zar_2480(self) -> None:
        assert self._zar("pro") == Decimal("2480.00")

    def test_enterprise_zar_floor_16590(self) -> None:
        assert self._zar("enterprise") == Decimal("16590.00")


# ============================================================================
# 5. Gross margins (≥75% floor at ALL paid tiers in v3)
# ============================================================================


class TestGrossMargins:
    """All paid tiers must meet the ≥75% gross margin target in v3."""

    def test_paid_tiers_have_gross_margin_data(self) -> None:
        from app.ee.billing.tiers import BillingTier, get_tier_limits

        for tier in (BillingTier.STARTER, BillingTier.TEAM, BillingTier.PRO, BillingTier.ENTERPRISE):
            limits = get_tier_limits(tier)
            assert limits.gross_margin_pct is not None, f"{tier.value}: no margin data"
            assert limits.infra_cogs_zar > Decimal("0"), f"{tier.value}: no infra_cogs"
            assert limits.total_cogs_zar > Decimal("0"), f"{tier.value}: no total_cogs"

    def test_paid_tiers_meet_75_pct_target(self) -> None:
        """v3 raises the floor to ≥75% at all paid tiers."""
        from app.ee.billing.tiers import BillingTier, get_tier_limits

        for tier in (BillingTier.STARTER, BillingTier.TEAM, BillingTier.PRO, BillingTier.ENTERPRISE):
            limits = get_tier_limits(tier)
            assert limits.gross_margin_pct >= 75.0, (
                f"{tier.value}: margin {limits.gross_margin_pct}% < 75%"
            )

    def test_specific_margins_match_design(self) -> None:
        from app.ee.billing.tiers import BillingTier, get_tier_limits

        expected = {
            BillingTier.STARTER: 86.6,
            BillingTier.TEAM: 85.6,
            BillingTier.PRO: 79.7,
            BillingTier.ENTERPRISE: 75.5,
        }
        for tier, margin in expected.items():
            limits = get_tier_limits(tier)
            assert limits.gross_margin_pct == pytest.approx(margin, abs=0.2), (
                f"{tier.value}: margin {limits.gross_margin_pct} != {margin}"
            )

    def test_total_cogs_match_design(self) -> None:
        from app.ee.billing.tiers import BillingTier, get_tier_limits

        expected = {
            BillingTier.STARTER: Decimal("20.12"),
            BillingTier.TEAM: Decimal("117.96"),
            BillingTier.PRO: Decimal("504.57"),
            BillingTier.ENTERPRISE: Decimal("4065.72"),
        }
        for tier, cogs in expected.items():
            limits = get_tier_limits(tier)
            assert limits.total_cogs_zar == cogs, (
                f"{tier.value}: cogs {limits.total_cogs_zar} != {cogs}"
            )

    def test_free_tier_has_no_margin(self) -> None:
        from app.ee.billing.tiers import BillingTier, get_tier_limits

        limits = get_tier_limits(BillingTier.FREE)
        assert limits.gross_margin_pct is None


# ============================================================================
# 6. COGS-based unlimited quota policy
# ============================================================================


class TestUnlimitedNonCOGSDimensions:
    """Dimensions with ~zero marginal COGS must be unlimited (None) at appropriate tiers."""

    def _limits(self, tier_name: str):
        from app.ee.billing.tiers import BillingTier, get_tier_limits
        return get_tier_limits(BillingTier(tier_name))

    # ── Seats: unlimited at ALL tiers ──

    def test_free_max_seats_unlimited(self) -> None:
        assert self._limits("free").max_seats is None

    def test_starter_max_seats_unlimited(self) -> None:
        assert self._limits("starter").max_seats is None

    def test_pro_max_seats_unlimited(self) -> None:
        assert self._limits("pro").max_seats is None

    def test_enterprise_max_seats_unlimited(self) -> None:
        assert self._limits("enterprise").max_seats is None

    def test_all_tiers_max_viewer_seats_unlimited(self) -> None:
        """Viewer seats = DB row; always unlimited."""
        from app.ee.billing.tiers import BillingTier, get_tier_limits

        for tier in BillingTier:
            assert get_tier_limits(tier).max_viewer_seats is None, (
                f"{tier.value}: max_viewer_seats should be None (unlimited)"
            )

    # ── Connectors: unlimited at Pro+ ──

    def test_pro_max_connectors_unlimited(self) -> None:
        assert self._limits("pro").max_connectors is None

    def test_enterprise_max_connectors_unlimited(self) -> None:
        assert self._limits("enterprise").max_connectors is None

    # ── Dashboards: unlimited at Enterprise ──

    def test_enterprise_max_dashboards_unlimited(self) -> None:
        assert self._limits("enterprise").max_dashboards is None

    # ── Flows: flow definitions = DB rows (R0.001/flow/mo); unlimited at Enterprise ──

    def test_enterprise_max_flows_unlimited(self) -> None:
        assert self._limits("enterprise").max_flows is None

    def test_free_and_starter_and_pro_flows_bounded(self) -> None:
        """Lower tiers bound flows to limit abuse of the scheduler, not COGS."""
        assert self._limits("free").max_flows is not None
        assert self._limits("starter").max_flows is not None
        assert self._limits("pro").max_flows is not None

    # ── Query rows: unlimited at Enterprise ──

    def test_enterprise_max_query_rows_unlimited(self) -> None:
        assert self._limits("enterprise").max_query_rows is None


# ============================================================================
# 7. Metered dimensions map to real COGS lines
# ============================================================================


class TestMeteredDimensions:
    """Dimensions that have real COGS must be bounded (non-None) on relevant tiers."""

    def _limits(self, tier_name: str):
        from app.ee.billing.tiers import BillingTier, get_tier_limits
        return get_tier_limits(BillingTier(tier_name))

    def test_storage_gb_bounded_at_hosted_tiers(self) -> None:
        """Storage = object-storage COGS; must be bounded on hosted tiers."""
        for tier_name in ("free", "starter", "team", "pro", "enterprise"):
            limits = self._limits(tier_name)
            assert limits.max_storage_gb is not None, (
                f"{tier_name}: max_storage_gb should be bounded (object-storage COGS)"
            )
            assert limits.max_storage_gb > 0

    def test_compute_units_bounded_at_all_tiers(self) -> None:
        """Compute = container/query CPU COGS; must be bounded."""
        for tier_name in ("free", "starter", "team", "pro", "enterprise"):
            limits = self._limits(tier_name)
            assert limits.max_compute_units_per_month is not None, (
                f"{tier_name}: max_compute_units should be bounded (container compute COGS)"
            )
            assert limits.max_compute_units_per_month > 0

    def test_embedded_sessions_bounded_at_starter_and_pro(self) -> None:
        """Embedded sessions = egress + per-request compute COGS."""
        assert self._limits("starter").max_embedded_sessions_per_month is not None
        assert self._limits("pro").max_embedded_sessions_per_month is not None

    def test_enterprise_embedded_sessions_unlimited(self) -> None:
        """Enterprise includes unlimited embedded sessions."""
        assert self._limits("enterprise").max_embedded_sessions_per_month is None

    def test_ai_calls_bounded_at_starter_and_pro(self) -> None:
        """AI calls = Anthropic API token COGS ($0.25/1M Haiku tokens)."""
        assert self._limits("starter").max_ai_calls_per_month is not None
        assert self._limits("pro").max_ai_calls_per_month is not None

    def test_agent_runs_bounded_at_pro(self) -> None:
        """Agent runs = remote kernel container-compute COGS."""
        assert self._limits("pro").max_agent_runs_per_month is not None

    def test_compute_units_increase_tier_over_tier(self) -> None:
        """Higher tiers must include more compute — monotonically increasing."""
        from app.ee.billing.tiers import BillingTier, get_tier_limits

        tiers_in_order = [
            BillingTier.FREE,
            BillingTier.STARTER,
            BillingTier.PRO,
            BillingTier.ENTERPRISE,
        ]
        prev_cu = 0
        for tier in tiers_in_order:
            cu = get_tier_limits(tier).max_compute_units_per_month
            assert cu is not None
            assert cu > prev_cu, (
                f"{tier.value}: compute units {cu} not > previous {prev_cu}"
            )
            prev_cu = cu

    def test_storage_gb_increases_tier_over_tier(self) -> None:
        """Storage quotas must increase monotonically across hosted tiers."""
        from app.ee.billing.tiers import BillingTier, get_tier_limits

        tiers_in_order = [
            BillingTier.FREE,
            BillingTier.STARTER,
            BillingTier.PRO,
            BillingTier.ENTERPRISE,
        ]
        prev_gb = 0.0
        for tier in tiers_in_order:
            gb = get_tier_limits(tier).max_storage_gb
            assert gb is not None  # all hosted tiers have a storage cap
            assert gb > prev_gb, (
                f"{tier.value}: storage {gb} GB not > previous {prev_gb} GB"
            )
            prev_gb = gb

    def test_free_no_embedded_sessions(self) -> None:
        """Free tier has no embedded session quota."""
        limits = self._limits("free")
        assert limits.max_embedded_sessions_per_month == 0

    def test_free_no_ai_calls(self) -> None:
        """Free tier has no AI call quota."""
        limits = self._limits("free")
        assert limits.max_ai_calls_per_month == 0

    def test_starter_no_agent_runs(self) -> None:
        """Starter has no remote kernel agent runs (too cheap for this COGS)."""
        limits = self._limits("starter")
        assert limits.max_agent_runs_per_month == 0


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

    def test_dial_60_maps_to_starter(self) -> None:
        from app.ee.billing.tiers import BillingTier, tier_for_security_dial
        assert tier_for_security_dial(60) == BillingTier.STARTER

    def test_dial_61_maps_to_team(self) -> None:
        from app.ee.billing.tiers import BillingTier, tier_for_security_dial
        assert tier_for_security_dial(61) == BillingTier.TEAM

    def test_dial_70_maps_to_team(self) -> None:
        from app.ee.billing.tiers import BillingTier, tier_for_security_dial
        assert tier_for_security_dial(70) == BillingTier.TEAM

    def test_dial_71_maps_to_pro(self) -> None:
        from app.ee.billing.tiers import BillingTier, tier_for_security_dial
        assert tier_for_security_dial(71) == BillingTier.PRO

    def test_dial_80_maps_to_pro(self) -> None:
        from app.ee.billing.tiers import BillingTier, tier_for_security_dial
        assert tier_for_security_dial(80) == BillingTier.PRO

    def test_dial_81_maps_to_enterprise(self) -> None:
        from app.ee.billing.tiers import BillingTier, tier_for_security_dial
        assert tier_for_security_dial(81) == BillingTier.ENTERPRISE

    def test_dial_100_maps_to_enterprise(self) -> None:
        from app.ee.billing.tiers import BillingTier, tier_for_security_dial
        assert tier_for_security_dial(100) == BillingTier.ENTERPRISE

    def test_dial_out_of_range_raises_value_error(self) -> None:
        from app.ee.billing.tiers import tier_for_security_dial
        with pytest.raises(ValueError):
            tier_for_security_dial(-1)
        with pytest.raises(ValueError):
            tier_for_security_dial(101)


# ============================================================================
# 9. Feature flags per tier
# ============================================================================


class TestFeatureFlags:
    def _limits(self, tier_name: str):
        from app.ee.billing.tiers import BillingTier, get_tier_limits
        return get_tier_limits(BillingTier(tier_name))

    def test_free_no_white_label(self) -> None:
        assert self._limits("free").has_white_label is False

    def test_free_no_rls(self) -> None:
        assert self._limits("free").has_rls is False

    def test_free_no_sso(self) -> None:
        assert self._limits("free").has_sso_google is False
        assert self._limits("free").has_sso_saml is False

    def test_free_no_audit_logs(self) -> None:
        assert self._limits("free").audit_log_retention_days == 0

    def test_starter_basic_rls(self) -> None:
        assert self._limits("starter").has_rls == "basic"

    def test_starter_google_sso(self) -> None:
        assert self._limits("starter").has_sso_google is True

    def test_starter_no_saml(self) -> None:
        assert self._limits("starter").has_sso_saml is False

    def test_starter_no_white_label(self) -> None:
        """Entry $9 tier does not include white-label."""
        assert self._limits("starter").has_white_label is False

    def test_starter_audit_logs_7_days(self) -> None:
        assert self._limits("starter").audit_log_retention_days == 7

    def test_starter_no_sla(self) -> None:
        """Entry $9 tier has no contractual SLA."""
        assert self._limits("starter").sla_uptime_pct is None

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

    def test_pro_priority_support_email_slack(self) -> None:
        assert self._limits("pro").has_priority_support == "email_slack"

    def test_pro_sla_99_5(self) -> None:
        assert self._limits("pro").sla_uptime_pct == 99.5

    def test_pro_no_contractual_p1_response(self) -> None:
        """Pro has soft SLA uptime but no contractual P1 response time."""
        assert self._limits("pro").sla_response_time_p1_minutes is None

    def test_enterprise_full_custom_sdk_white_label(self) -> None:
        assert self._limits("enterprise").has_white_label == "full_custom_sdk"

    def test_enterprise_hipaa_rls(self) -> None:
        assert self._limits("enterprise").has_rls == "full_hipaa_ready"

    def test_enterprise_byoc(self) -> None:
        assert self._limits("enterprise").has_byoc is True

    def test_enterprise_audit_logs_unlimited(self) -> None:
        assert self._limits("enterprise").audit_log_retention_days is None

    def test_enterprise_dedicated_csm(self) -> None:
        assert self._limits("enterprise").has_priority_support == "dedicated_csm"

    def test_enterprise_scim_enabled(self) -> None:
        assert self._limits("enterprise").has_scim is True

    def test_enterprise_multi_tenant_workspaces(self) -> None:
        assert self._limits("enterprise").has_multi_tenant_workspaces is True

    def test_enterprise_unlimited_saml_idps(self) -> None:
        assert self._limits("enterprise").has_sso_saml == "unlimited_idps"


# ============================================================================
# 10. Overage rates — COGS-mapped, no per-seat pricing at any tier
# ============================================================================


class TestOverageRates:
    def _overages(self, tier_name: str):
        from app.ee.billing.tiers import BillingTier, get_tier_limits
        return get_tier_limits(BillingTier(tier_name)).overages

    def test_free_no_overages(self) -> None:
        ov = self._overages("free")
        assert ov.storage_zar_per_gb_month is None
        assert ov.compute_zar_per_1000_cu is None

    def test_starter_storage_overage_maps_to_object_storage_cogs(self) -> None:
        """R1.50/GB → ~84% margin: confirms COGS line is object-storage."""
        assert self._overages("starter").storage_zar_per_gb_month == Decimal("1.50")

    def test_starter_compute_overage_maps_to_container_compute_cogs(self) -> None:
        """R100/1000 CU → ~77% margin: confirms COGS line is container/query compute."""
        assert self._overages("starter").compute_zar_per_1000_cu == Decimal("100.00")

    def test_starter_ai_call_overage_maps_to_llm_api_cogs(self) -> None:
        """R5/call → ~93% margin: confirms COGS line is Anthropic API tokens."""
        assert self._overages("starter").ai_call_zar_per_call == Decimal("5.00")

    def test_starter_embedded_session_overage_maps_to_egress_cogs(self) -> None:
        """R50/10K sessions → ~99% margin: confirms COGS line is egress + CDN."""
        assert self._overages("starter").embedded_session_zar_per_10k == Decimal("50.00")

    def test_starter_no_agent_run_overage(self) -> None:
        """Starter has no remote kernel — agent_run overage is None."""
        assert self._overages("starter").agent_run_zar_per_run is None

    def test_no_tier_has_seat_overage(self) -> None:
        """Seats have ~0 COGS — no per-seat overage at any tier."""
        from app.ee.billing.tiers import BillingTier, get_tier_limits

        for tier in BillingTier:
            ov = get_tier_limits(tier).overages
            assert ov.seat_zar_per_seat_month is None, (
                f"{tier.value}: seat_zar_per_seat_month must be None (no per-seat pricing)"
            )

    def test_pro_agent_run_overage_maps_to_container_compute_cogs(self) -> None:
        """R2/run → ~99% margin: confirms COGS line is remote-kernel container compute."""
        assert self._overages("pro").agent_run_zar_per_run == Decimal("2.00")

    def test_enterprise_embed_session_overage_zero(self) -> None:
        """Enterprise includes unlimited embed sessions — overage rate = 0."""
        assert self._overages("enterprise").embedded_session_zar_per_10k == Decimal("0.00")

    def test_overage_rates_consistent_across_paid_tiers(self) -> None:
        """Storage, compute, and AI overage rates are the same at Starter, Pro, Enterprise."""
        from app.ee.billing.tiers import BillingTier, get_tier_limits

        for tier in (BillingTier.STARTER, BillingTier.TEAM, BillingTier.PRO, BillingTier.ENTERPRISE):
            ov = get_tier_limits(tier).overages
            assert ov.storage_zar_per_gb_month == Decimal("1.50")
            assert ov.compute_zar_per_1000_cu == Decimal("100.00")
            assert ov.ai_call_zar_per_call == Decimal("5.00")


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

    def test_starter_white_label_not_available(self) -> None:
        from app.ee.billing.tiers import BillingTier, is_feature_available
        assert not is_feature_available(BillingTier.STARTER, "white_label")

    def test_free_audit_logs_not_available(self) -> None:
        from app.ee.billing.tiers import BillingTier, is_feature_available
        assert not is_feature_available(BillingTier.FREE, "audit_logs")

    def test_starter_audit_logs_available(self) -> None:
        from app.ee.billing.tiers import BillingTier, is_feature_available
        assert is_feature_available(BillingTier.STARTER, "audit_logs")

    def test_enterprise_scim_available(self) -> None:
        from app.ee.billing.tiers import BillingTier, is_feature_available
        assert is_feature_available(BillingTier.ENTERPRISE, "scim")

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
        from app.ee.billing.tiers import BillingTier, is_within_quota
        # Unlimited seats at all tiers — always True regardless of count.
        assert is_within_quota(BillingTier.FREE, "seats", 3)
        assert is_within_quota(BillingTier.FREE, "seats", 100)
        assert is_within_quota(BillingTier.FREE, "seats", 10_000)

    def test_starter_5_connectors_within_limit(self) -> None:
        from app.ee.billing.tiers import BillingTier, is_within_quota
        assert is_within_quota(BillingTier.STARTER, "connectors", 5)

    def test_starter_6_connectors_exceeds_limit(self) -> None:
        from app.ee.billing.tiers import BillingTier, is_within_quota
        assert not is_within_quota(BillingTier.STARTER, "connectors", 6)

    def test_pro_connectors_unlimited(self) -> None:
        from app.ee.billing.tiers import BillingTier, is_within_quota
        assert is_within_quota(BillingTier.PRO, "connectors", 999_999)

    def test_enterprise_flows_unlimited(self) -> None:
        """Enterprise max_flows is None (unlimited) per COGS-mapped model."""
        from app.ee.billing.tiers import BillingTier, is_within_quota
        assert is_within_quota(BillingTier.ENTERPRISE, "flows", 10_000)

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
    def test_round_trips_all_four_tiers(self) -> None:
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

    def test_legacy_business_maps_to_enterprise(self) -> None:
        """Old 'business' tier value from 5-tier model maps to Enterprise."""
        from app.ee.billing.tiers import BillingTier, billing_tier_from_license_tier

        assert billing_tier_from_license_tier("business") == BillingTier.ENTERPRISE

    def test_legacy_business_case_insensitive(self) -> None:
        from app.ee.billing.tiers import BillingTier, billing_tier_from_license_tier

        assert billing_tier_from_license_tier("BUSINESS") == BillingTier.ENTERPRISE


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


# ============================================================================
# 15. Enterprise SLA — uptime, P1/P2 response times, support level
# ============================================================================


class TestEnterpriseSLA:
    def _limits(self):
        from app.ee.billing.tiers import BillingTier, get_tier_limits
        return get_tier_limits(BillingTier.ENTERPRISE)

    def test_enterprise_sla_uptime_99_95(self) -> None:
        """99.95% monthly uptime ≈ 22 minutes downtime/month."""
        assert self._limits().sla_uptime_pct == 99.95

    def test_enterprise_p1_response_30_minutes(self) -> None:
        """P1 (site-down) first response ≤ 30 minutes, 24/7."""
        assert self._limits().sla_response_time_p1_minutes == 30

    def test_enterprise_p2_response_2_hours(self) -> None:
        """P2 (degraded) first response ≤ 2 hours, business hours."""
        assert self._limits().sla_response_time_p2_hours == 2

    def test_enterprise_dedicated_csm_support(self) -> None:
        assert self._limits().has_priority_support == "dedicated_csm"

    def test_non_enterprise_tiers_have_no_p1_sla(self) -> None:
        from app.ee.billing.tiers import BillingTier, get_tier_limits

        for tier in (BillingTier.FREE, BillingTier.STARTER, BillingTier.PRO):
            limits = get_tier_limits(tier)
            assert limits.sla_response_time_p1_minutes is None, (
                f"{tier.value}: should not have contractual P1 response time"
            )
            assert limits.sla_response_time_p2_hours is None


# ============================================================================
# 16. competitors.bi_competitors()
# ============================================================================


_REQUIRED_BI_FIELDS = {
    "tool", "tagline", "model", "unit", "pricing",
    "free_tier", "free_tier_detail", "per_seat", "nubi_advantage",
}

_REQUIRED_ORCH_FIELDS = {
    "tool", "tagline", "model", "unit", "pricing",
    "free_tier", "free_tier_detail", "per_execution", "per_seat",
    "metered_charges", "nubi_advantage",
}


class TestBiCompetitors:
    def _competitors(self):
        from app.ee.billing.competitors import bi_competitors
        return bi_competitors()

    def test_returns_list_of_dicts(self) -> None:
        data = self._competitors()
        assert isinstance(data, list)
        assert all(isinstance(c, dict) for c in data)

    def test_minimum_seven_entries(self) -> None:
        """BI competitor list must cover all 7 documented tools."""
        assert len(self._competitors()) >= 7

    def test_required_fields_present_on_all_entries(self) -> None:
        for competitor in self._competitors():
            missing = _REQUIRED_BI_FIELDS - competitor.keys()
            assert not missing, (
                f"{competitor.get('tool', '?')}: missing fields {missing}"
            )

    def test_tool_names_include_key_players(self) -> None:
        tools = {c["tool"] for c in self._competitors()}
        for expected in ("Cube", "Metabase", "Hex", "Lightdash", "Holistics", "Luzmo", "Embeddable"):
            assert expected in tools, f"Missing BI competitor: {expected}"

    def test_each_entry_has_nubi_advantage_text(self) -> None:
        for c in self._competitors():
            assert isinstance(c["nubi_advantage"], str)
            assert len(c["nubi_advantage"]) > 20

    def test_free_tier_is_boolean(self) -> None:
        for c in self._competitors():
            assert isinstance(c["free_tier"], bool), (
                f"{c['tool']}: free_tier should be bool"
            )

    def test_nubi_advantage_references_current_price(self) -> None:
        """Holistics nubi_advantage must reference current Pro price $149."""
        from app.ee.billing.competitors import bi_competitors

        holistics = next(c for c in bi_competitors() if c["tool"] == "Holistics")
        adv = holistics["nubi_advantage"]
        assert "$149" in adv, f"Holistics nubi_advantage should reference $149/mo Pro price: {adv}"


# ============================================================================
# 17. competitors.orchestration_competitors()
# ============================================================================


class TestOrchestrationCompetitors:
    def _competitors(self):
        from app.ee.billing.competitors import orchestration_competitors
        return orchestration_competitors()

    def test_returns_list_of_dicts(self) -> None:
        data = self._competitors()
        assert isinstance(data, list)
        assert all(isinstance(c, dict) for c in data)

    def test_minimum_ten_entries(self) -> None:
        """Orchestration list must cover all 10 documented tools."""
        assert len(self._competitors()) >= 10

    def test_required_fields_present_on_all_entries(self) -> None:
        for competitor in self._competitors():
            missing = _REQUIRED_ORCH_FIELDS - competitor.keys()
            assert not missing, (
                f"{competitor.get('tool', '?')}: missing fields {missing}"
            )

    def test_tool_names_include_key_players(self) -> None:
        tools = {c["tool"] for c in self._competitors()}
        for expected in (
            "Prefect Cloud",
            "Apache Airflow (self-hosted)",
            "Astronomer (Astro)",
            "Dagster Cloud",
            "Temporal Cloud",
            "AWS MWAA",
            "Google Cloud Composer",
            "Mage.ai",
            "Kestra",
            "Windmill",
        ):
            assert expected in tools, f"Missing orchestration competitor: {expected}"

    def test_each_entry_has_nubi_advantage_text(self) -> None:
        for c in self._competitors():
            assert isinstance(c["nubi_advantage"], str)
            assert len(c["nubi_advantage"]) > 20

    def test_per_execution_is_boolean(self) -> None:
        for c in self._competitors():
            assert isinstance(c["per_execution"], bool), (
                f"{c['tool']}: per_execution should be bool"
            )

    def test_metered_charges_is_list(self) -> None:
        for c in self._competitors():
            assert isinstance(c["metered_charges"], list), (
                f"{c['tool']}: metered_charges should be list"
            )

    def test_gcp_composer_nubi_advantage_references_current_price(self) -> None:
        """GCP Composer nubi_advantage must reference current Pro price $149."""
        from app.ee.billing.competitors import orchestration_competitors

        composer = next(c for c in orchestration_competitors() if c["tool"] == "Google Cloud Composer")
        adv = composer["nubi_advantage"]
        assert "$149" in adv, f"Composer nubi_advantage should reference $149/mo Pro price: {adv}"


# ============================================================================
# 18. all_competitors()
# ============================================================================


class TestAllCompetitors:
    def test_has_bi_and_orchestration_keys(self) -> None:
        from app.ee.billing.competitors import all_competitors

        data = all_competitors()
        assert "bi" in data
        assert "orchestration" in data

    def test_has_as_of_key(self) -> None:
        from app.ee.billing.competitors import all_competitors

        data = all_competitors()
        assert "as_of" in data
        assert isinstance(data["as_of"], str)
        assert len(data["as_of"]) > 3

    def test_bi_and_orchestration_are_lists(self) -> None:
        from app.ee.billing.competitors import all_competitors

        data = all_competitors()
        assert isinstance(data["bi"], list)
        assert isinstance(data["orchestration"], list)


# ============================================================================
# 19. Orchestration competitors do NOT charge per-seat
# ============================================================================


class TestOrchestrationNubiBundledAdvantage:
    """Verify the nubi_advantage text references absence of per-run/per-seat charges."""

    def test_dagster_entry_mentions_per_run_credit(self) -> None:
        from app.ee.billing.competitors import orchestration_competitors

        dagster = next(c for c in orchestration_competitors() if c["tool"] == "Dagster Cloud")
        assert dagster["per_execution"] is True
        # nubi_advantage should call out the per-credit metering
        adv = dagster["nubi_advantage"].lower()
        assert "credit" in adv or "per-run" in adv or "zero" in adv

    def test_mwaa_entry_mentions_environment_fee(self) -> None:
        from app.ee.billing.competitors import orchestration_competitors

        mwaa = next(c for c in orchestration_competitors() if c["tool"] == "AWS MWAA")
        assert mwaa["free_tier"] is False
        adv = mwaa["nubi_advantage"].lower()
        assert "environment" in adv or "$360" in adv or "always-on" in adv

    def test_airflow_self_host_no_per_execution_fee(self) -> None:
        from app.ee.billing.competitors import orchestration_competitors

        airflow = next(
            c for c in orchestration_competitors()
            if c["tool"] == "Apache Airflow (self-hosted)"
        )
        assert airflow["per_execution"] is False
        assert airflow["per_seat"] is False

    def test_temporal_has_per_action_meter(self) -> None:
        from app.ee.billing.competitors import orchestration_competitors

        temporal = next(c for c in orchestration_competitors() if c["tool"] == "Temporal Cloud")
        assert temporal["per_execution"] is True
        assert len(temporal["metered_charges"]) >= 1


# ============================================================================
# 20–23. Public GET /pricing endpoint
# ============================================================================


class TestPublicPricingEndpoint:
    """Integration tests for the public /pricing endpoint (no auth required)."""

    def _make_client(self):
        from fastapi import FastAPI
        from app.ee.billing.routes import public_router

        app = FastAPI()
        app.include_router(public_router, prefix="/api/v1")
        from httpx import ASGITransport, AsyncClient
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    @pytest.mark.asyncio
    async def test_pricing_returns_200_without_auth(self) -> None:
        """GET /pricing must return 200 with no auth token."""
        async with self._make_client() as client:
            resp = await client.get("/api/v1/pricing")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_pricing_has_tiers_key(self) -> None:
        async with self._make_client() as client:
            resp = await client.get("/api/v1/pricing")
        body = resp.json()
        assert "tiers" in body
        assert isinstance(body["tiers"], list)

    @pytest.mark.asyncio
    async def test_pricing_tiers_count_is_five(self) -> None:
        """v3 model + Team tier has exactly 5 tiers."""
        async with self._make_client() as client:
            resp = await client.get("/api/v1/pricing")
        body = resp.json()
        assert len(body["tiers"]) == 5

    @pytest.mark.asyncio
    async def test_pricing_tiers_include_all_names(self) -> None:
        async with self._make_client() as client:
            resp = await client.get("/api/v1/pricing")
        body = resp.json()
        tier_names = {t["tier"] for t in body["tiers"]}
        assert tier_names == {"free", "starter", "team", "pro", "enterprise"}

    @pytest.mark.asyncio
    async def test_pricing_tiers_do_not_include_business(self) -> None:
        """'business' must not appear in v3 tier list."""
        async with self._make_client() as client:
            resp = await client.get("/api/v1/pricing")
        body = resp.json()
        tier_names = {t["tier"] for t in body["tiers"]}
        assert "business" not in tier_names

    @pytest.mark.asyncio
    async def test_pricing_tier_decimal_amounts_are_strings(self) -> None:
        """Decimal values (usd_monthly_price, monthly_price_zar) must be strings in JSON."""
        async with self._make_client() as client:
            resp = await client.get("/api/v1/pricing")
        body = resp.json()
        for tier_data in body["tiers"]:
            assert isinstance(tier_data["usd_monthly_price"], str), (
                f"{tier_data['tier']}: usd_monthly_price should be str"
            )
            assert isinstance(tier_data["monthly_price_zar"], str), (
                f"{tier_data['tier']}: monthly_price_zar should be str"
            )

    @pytest.mark.asyncio
    async def test_pricing_tiers_have_limits_block(self) -> None:
        async with self._make_client() as client:
            resp = await client.get("/api/v1/pricing")
        body = resp.json()
        for tier_data in body["tiers"]:
            assert "limits" in tier_data, f"{tier_data['tier']}: missing limits"
            assert "max_seats" in tier_data["limits"]
            assert "max_compute_units_per_month" in tier_data["limits"]

    @pytest.mark.asyncio
    async def test_pricing_tiers_have_overages_block(self) -> None:
        async with self._make_client() as client:
            resp = await client.get("/api/v1/pricing")
        body = resp.json()
        for tier_data in body["tiers"]:
            assert "overages" in tier_data
            assert "seat_zar_per_seat_month" in tier_data["overages"]
            # Seat overage must always be None — no per-seat pricing.
            assert tier_data["overages"]["seat_zar_per_seat_month"] is None, (
                f"{tier_data['tier']}: seat overage should be None"
            )

    @pytest.mark.asyncio
    async def test_pricing_tiers_have_features_block(self) -> None:
        async with self._make_client() as client:
            resp = await client.get("/api/v1/pricing")
        body = resp.json()
        for tier_data in body["tiers"]:
            assert "features" in tier_data
            assert "has_rls" in tier_data["features"]
            assert "has_sso_google" in tier_data["features"]

    @pytest.mark.asyncio
    async def test_pricing_has_fx_block(self) -> None:
        """FX block must have rate (str), as_of (str|null), stale (bool)."""
        async with self._make_client() as client:
            resp = await client.get("/api/v1/pricing")
        body = resp.json()
        assert "fx" in body
        fx = body["fx"]
        assert "rate" in fx
        assert isinstance(fx["rate"], str)
        assert "stale" in fx
        assert isinstance(fx["stale"], bool)
        assert "as_of" in fx  # may be None if no refresh has happened

    @pytest.mark.asyncio
    async def test_pricing_has_competitors_block(self) -> None:
        async with self._make_client() as client:
            resp = await client.get("/api/v1/pricing")
        body = resp.json()
        assert "competitors" in body
        comp = body["competitors"]
        assert "bi" in comp
        assert "orchestration" in comp
        assert isinstance(comp["bi"], list)
        assert isinstance(comp["orchestration"], list)

    @pytest.mark.asyncio
    async def test_pricing_competitors_bi_count(self) -> None:
        async with self._make_client() as client:
            resp = await client.get("/api/v1/pricing")
        body = resp.json()
        assert len(body["competitors"]["bi"]) >= 7

    @pytest.mark.asyncio
    async def test_pricing_competitors_orchestration_count(self) -> None:
        async with self._make_client() as client:
            resp = await client.get("/api/v1/pricing")
        body = resp.json()
        assert len(body["competitors"]["orchestration"]) >= 10

    @pytest.mark.asyncio
    async def test_pricing_has_disclosure_key(self) -> None:
        async with self._make_client() as client:
            resp = await client.get("/api/v1/pricing")
        body = resp.json()
        assert "disclosure" in body
        assert isinstance(body["disclosure"], str)
        assert len(body["disclosure"]) > 50

    @pytest.mark.asyncio
    async def test_pricing_enterprise_tier_max_flows_unlimited(self) -> None:
        """Verify the COGS-mapped unlimited flows policy is reflected in the API."""
        async with self._make_client() as client:
            resp = await client.get("/api/v1/pricing")
        body = resp.json()
        enterprise = next(t for t in body["tiers"] if t["tier"] == "enterprise")
        assert enterprise["limits"]["max_flows"] is None, (
            "Enterprise max_flows should be null (unlimited) — flow defs are DB rows with ~0 COGS"
        )

    @pytest.mark.asyncio
    async def test_pricing_starter_usd_price_is_9(self) -> None:
        async with self._make_client() as client:
            resp = await client.get("/api/v1/pricing")
        body = resp.json()
        starter = next(t for t in body["tiers"] if t["tier"] == "starter")
        assert starter["usd_monthly_price"] == "9.00"

    @pytest.mark.asyncio
    async def test_pricing_enterprise_usd_price_is_1000(self) -> None:
        async with self._make_client() as client:
            resp = await client.get("/api/v1/pricing")
        body = resp.json()
        enterprise = next(t for t in body["tiers"] if t["tier"] == "enterprise")
        assert enterprise["usd_monthly_price"] == "1000.00"
