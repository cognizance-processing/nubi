"""EE billing tier definitions for Nubi.

Defines ZAR pricing tiers (FREE / STARTER / PRO / ENTERPRISE),
the security-dial → tier mapping, and per-tier resource limits.  This module
is EE-only and must NEVER be imported by open-source core code.

Tier overview (v3 — June 2026)
-------------------------------
| Tier       | USD/mo  | Seats | SLA       |
|------------|---------|-------|-----------|
| Free       |   $0    | ∞     | none      |
| Starter    |   $9    | ∞     | none      |
| Team       |  $49    | ∞     | none      |
| Pro        | $149    | ∞     | 99.5%     |
| Enterprise | $1,000  | ∞     | 99.95%    |

No per-seat pricing at any tier — unlimited seats everywhere.
Overages beyond the tier's included quota are drawn from the org's
usage wallet (prepaid credit balance).

Pricing blueprint (June 2026, USD-anchored, ZAR @ R16.26 + 2% FX buffer)
-------------------------------------------------------------------------
All ZAR reference amounts are computed as:
    ceil_to_nearest_10(usd * 16.26 * 1.02)

These are reference amounts only — the live ZAR amount at billing time is
computed by ``app.ee.billing.fx.convert_usd_to_zar`` using the daily
refreshed FX rate.

Gross margins (≥70% floor, ≥75% target at all tiers; all tiers meet ≥75%)
--------------------------------------------------------------------------
| Tier       | USD/mo   | ZAR/mo   | Total COGS | Gross Margin     |
|------------|----------|----------|------------|------------------|
| Starter    |   $9     |   R150   |   R20.12   | 86.6%  ✓ ≥75%   |
| Team       |  $49     |   R820   |  R117.96   | 85.6%  ✓ ≥75%   |
| Pro        | $149     | R2,480   |  R504.57   | 79.7%  ✓ ≥75%   |
| Enterprise | $1,000   | R16,590  | R4,065.72  | 75.5%  ✓ ≥75%   |

Enterprise COGS includes SLA monitoring, on-call overhead, and dedicated
CSM/support allocation (~R700/org/month premium over equivalent hosted infra).

Free tier estimated COGS: R143.82/org/month (acquisition only, no revenue).
Paystack effective rate used: 3.41% (local ZA card, incl. 15% VAT on fee).

COGS line mapping (what each billable metric maps to in our cost structure)
---------------------------------------------------------------------------
Nubi charges ONLY for things that map to a real COGS line we pay for.
Unlimited dimensions (seats, connectors count, dashboards, saved queries,
flow definitions) cost ~R0/month incremental and are never metered.

| Metric                         | COGS line                                      |
|--------------------------------|------------------------------------------------|
| Storage GB/month               | Object-storage (S3/R2) + Postgres WAL cost     |
| Compute units (flow runs +     | Container-compute (ECS/k8s task-seconds) +     |
|   query compute)               | DuckDB CPU-time on query nodes                 |
| Embedded sessions              | Egress bandwidth + per-request compute (CDN)   |
|   (/10K sessions)              | Each session = ~2 API calls + JS bundle egress |
| AI / agent runs                | Anthropic Claude API token cost (Haiku ~$0.25/ |
|   (per call)                   | 1M tok; each call ~200 tok → ~$0.00005)        |
| Bandwidth (egress)             | Cloud provider egress; bundled into embedded   |
|   (implicit)                   | session metering above                         |

NOT metered (zero marginal COGS):
  - Seats / viewers: 1 DB row + 1 auth check ≈ R0.001/user/month
  - Connector count: 1 DB row + connection pool entry ≈ R0.002/connector/month
  - Dashboard count: 1 DB row + JSON blob ≈ R0.001/dashboard/month
  - Saved query count: 1 DB row ≈ R0.001/query/month
  - Flow definition count: 1 DB row + JSON spec ≈ R0.001/flow/month

Overage rates (wallet draw-down; COGS + margin)
-----------------------------------------------
Overages beyond the tier's included quota are drawn from the org's usage
wallet prepaid credit balance.  Rates are denominated in ZAR and priced at
our COGS + margin:

| Dimension                       | Rate          | COGS basis               | Margin  |
|---------------------------------|---------------|--------------------------|---------|
| storage_zar_per_gb_month        | R1.50/GB      | S3/R2 + WAL              | ~84%    |
| compute_zar_per_1000_cu         | R100/1,000 CU | Container/DuckDB compute  | ~77%    |
| ai_call_zar_per_call            | R5.00/call    | Anthropic API tokens      | ~93%    |
| embedded_session_zar_per_10k    | R50/10K sess  | Egress + CDN compute      | ~99%    |
| agent_run_zar_per_run           | R2.00/run     | Remote kernel compute     | ~99%    |

No per-seat overage at any tier.  Wallet mechanics (topup, spend caps,
ledger) are handled by the WalletAgent — tiers.py only defines the rates.

Seat policy
-----------
**All tiers provide unlimited seats and viewers** — no per-seat pricing at
any tier.  One additional user = one DB row + one auth check; incremental
COGS < R0.001/month.  Rate-limiting is enforced by compute quota (CU/month),
not user count.

Enterprise SLA
--------------
The Enterprise tier ($1,000/mo) includes a contractual SLA:
  - Uptime: 99.95% monthly (allows ~22 min downtime/month)
  - Incident response: P1 (site-down) < 30 minutes, P2 (degraded) < 2 hours
  - Dedicated Customer Success Manager (CSM) with named contact
  - Monthly business review calls
  - Private Slack/Teams channel for support escalation
  - 24/7 emergency on-call (P1 only)

Security dial
-------------
Nubi core exposes a "security dial" (0–100) that gates query complexity,
data-volume controls, and audit stringency.  Billing maps paid tiers to the
allowed dial range — higher tiers unlock a wider range.

| Dial value | Minimum tier required |
|------------|-----------------------|
| 0–40       | Free                  |
| 41–60      | Starter               |
| 61–80      | Pro                   |
| 81–100     | Enterprise            |

Usage
-----
>>> from app.ee.billing.tiers import get_tier_limits, TierLimits, BillingTier
>>> limits = get_tier_limits(BillingTier.PRO)
>>> limits.max_seats is None  # unlimited
True
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal


# ---------------------------------------------------------------------------
# Billing tiers
# ---------------------------------------------------------------------------


class BillingTier(str, enum.Enum):
    """Billing / subscription tier names.

    These mirror :class:`app.ee.licensing.license.Tier` but are owned by the
    billing sub-module so that the billing domain can evolve independently of
    the license-key scheme.

    Enum order matters for ``all_tiers()`` — it yields tiers FREE → ENTERPRISE.
    The ``billing_tiers_enum_values`` canonical list is:
    ["free", "starter", "team", "pro", "enterprise"].
    """

    FREE = "free"
    STARTER = "starter"
    TEAM = "team"
    PRO = "pro"
    ENTERPRISE = "enterprise"


# ---------------------------------------------------------------------------
# Overage rate schedule
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OverageRates:
    """Per-unit overage pricing for a billing tier.

    All monetary amounts are in South African Rand (ZAR).
    ``None`` indicates overages are not available for this tier.

    Overages are drawn from the org's usage wallet (prepaid credit balance).
    Gross margins on overages (for reference):
        storage_zar_per_gb_month       R1.50  → ~84% margin
        compute_zar_per_1000_cu        R100   → ~77% margin
        ai_call_zar_per_call           R5     → ~93% margin
        embedded_session_zar_per_10k   R50    → ~99% margin
        agent_run_zar_per_run          R2     → ~99% margin
        seat_zar_per_seat_month        None   (no per-seat pricing at any tier)
    """

    storage_zar_per_gb_month: Decimal | None = None
    compute_zar_per_1000_cu: Decimal | None = None
    ai_call_zar_per_call: Decimal | None = None
    embedded_session_zar_per_10k: Decimal | None = None
    agent_run_zar_per_run: Decimal | None = None
    # seat_zar_per_seat_month is permanently None at all tiers — no per-seat pricing.
    seat_zar_per_seat_month: Decimal | None = None


# ---------------------------------------------------------------------------
# Per-tier resource limits
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TierLimits:
    """Immutable resource limits and feature flags for a billing tier.

    All monetary amounts are in South African Rand (ZAR).  The ZAR reference
    price is the June 2026 computed amount; live prices are derived at
    billing time via ``app.ee.billing.fx.convert_usd_to_zar``.

    Attributes
    ----------
    tier:
        The :class:`BillingTier` these limits apply to.
    usd_monthly_price:
        USD monthly anchor price (immutable per billing cycle).
    usd_annual_price:
        USD annual price (10 months: 2 months free).
    monthly_price_zar:
        ZAR reference price for June 2026 @ R16.26 + 2% FX buffer.
        This is the emergency fallback; live price from fx.convert_usd_to_zar.
    max_seats:
        Maximum number of editor seats.  ``None`` means unlimited.
    max_viewer_seats:
        Maximum number of internal viewer seats.  ``None`` means unlimited.
    max_connectors:
        Maximum number of live data connectors.  ``None`` means unlimited.
    max_query_rows:
        Maximum number of rows returned per query execution.
        ``None`` means unlimited.
    max_dashboards:
        Maximum number of dashboards per organisation.  ``None`` means unlimited.
    max_flows:
        Maximum number of scheduled flows.  ``None`` means unlimited.
    max_storage_gb:
        Maximum aggregate storage in gigabytes.  ``None`` means unlimited.
    max_compute_units_per_month:
        Maximum compute units per month.  ``None`` means unlimited.
    max_embedded_sessions_per_month:
        Maximum embedded view sessions per month.  ``None`` means unlimited.
    max_agent_runs_per_month:
        Maximum agent/flow remote kernel runs per month.  ``None`` means unlimited.
    max_ai_calls_per_month:
        Maximum AI generate/grounding calls per month.  ``None`` means unlimited.
    security_dial_min:
        Minimum security-dial value that this tier maps to (inclusive).
    security_dial_max:
        Maximum security-dial value that this tier supports (inclusive).
    overages:
        Per-unit overage rates for this tier.  See :class:`OverageRates`.
        Overages beyond the included quota are drawn from the org's usage
        wallet prepaid credit balance.
    has_white_label:
        White-label capability level.
    has_rls:
        Row-level security capability level.
    has_sso_google:
        Whether Google OAuth SSO is available.
    has_sso_saml:
        SAML SSO level: False / "1_idp" / "unlimited_idps".
    has_scim:
        Whether SCIM provisioning is available.
    has_multi_tenant_workspaces:
        Whether multi-tenant workspace management is available.
    has_byoc:
        Whether Bring-Your-Own-Cloud deployment is available.
    has_custom_domain:
        Whether custom domain mapping is available.
    audit_log_retention_days:
        Audit log retention in days.  ``None`` means unlimited.
    has_priority_support:
        Priority support level: False / "email_slack" / "dedicated_csm".
    sla_uptime_pct:
        SLA uptime percentage.  ``None`` for tiers without SLA.
    sla_response_time_p1_minutes:
        P1 (site-down) incident response SLA in minutes.  ``None`` if no SLA.
    sla_response_time_p2_hours:
        P2 (degraded) incident response SLA in hours.  ``None`` if no SLA.
    infra_cogs_zar:
        Reference infrastructure COGS in ZAR (for margin tracking).
    total_cogs_zar:
        Reference total COGS in ZAR including Paystack fees.
    gross_margin_pct:
        Reference gross margin percentage.
    """

    tier: BillingTier
    usd_monthly_price: Decimal
    usd_annual_price: Decimal
    monthly_price_zar: Decimal  # reference amount — June 2026 @ R16.26 + 2% buffer
    max_seats: int | None
    max_viewer_seats: int | None
    max_connectors: int | None
    max_query_rows: int | None
    max_dashboards: int | None
    max_flows: int | None
    max_storage_gb: float | None
    max_compute_units_per_month: int | None
    max_embedded_sessions_per_month: int | None
    max_agent_runs_per_month: int | None
    max_ai_calls_per_month: int | None
    security_dial_min: int
    security_dial_max: int
    overages: OverageRates = field(default_factory=OverageRates)
    # Feature flags
    has_white_label: Literal[False, "badge_removable", "full", "full_multi_tenant", "full_custom_sdk"] = False
    has_rls: Literal[False, "basic", "full_jwt", "full_jwt_passthrough", "full_hipaa_ready"] = False
    has_sso_google: bool = False
    has_sso_saml: Literal[False, "1_idp", "unlimited_idps"] = False
    has_scim: bool = False
    has_multi_tenant_workspaces: bool = False
    has_byoc: bool = False
    has_custom_domain: bool = False
    audit_log_retention_days: int | None = 0  # 0 = none; None = unlimited
    has_priority_support: Literal[False, "email_slack", "dedicated_csm"] = False
    sla_uptime_pct: float | None = None
    sla_response_time_p1_minutes: int | None = None
    sla_response_time_p2_hours: int | None = None
    # COGS / margin reference data (June 2026)
    infra_cogs_zar: Decimal = Decimal("0.00")
    total_cogs_zar: Decimal = Decimal("0.00")
    gross_margin_pct: float | None = None


# ---------------------------------------------------------------------------
# Tier catalogue (USD-anchored, ZAR reference amounts @ June 2026)
# ---------------------------------------------------------------------------

_TIER_CATALOGUE: dict[BillingTier, TierLimits] = {
    # ── FREE ──────────────────────────────────────────────────────────────────
    # No revenue; COGS R143.82/org/month (acquisition cost recovered in 1st paid month).
    # Abuse-capped: 60 req/min/org, 500 CU hard stop, storage eviction after 30-day inactivity.
    # Unlimited seats and viewers — rate-limited by compute quota, not user count.
    BillingTier.FREE: TierLimits(
        tier=BillingTier.FREE,
        usd_monthly_price=Decimal("0.00"),
        usd_annual_price=Decimal("0.00"),
        monthly_price_zar=Decimal("0.00"),
        max_seats=None,         # unlimited — no per-seat restriction
        max_viewer_seats=None,  # unlimited internal viewers
        max_connectors=3,
        max_query_rows=10_000,
        max_dashboards=5,
        max_flows=2,
        max_storage_gb=1.0,
        max_compute_units_per_month=500,
        max_embedded_sessions_per_month=0,
        max_agent_runs_per_month=0,
        max_ai_calls_per_month=0,
        security_dial_min=0,
        security_dial_max=40,
        overages=OverageRates(),  # no overages — upgrade required
        has_white_label=False,
        has_rls=False,
        has_sso_google=False,
        has_sso_saml=False,
        has_scim=False,
        has_multi_tenant_workspaces=False,
        has_byoc=False,
        has_custom_domain=False,
        audit_log_retention_days=0,
        has_priority_support=False,
        sla_uptime_pct=None,
        sla_response_time_p1_minutes=None,
        sla_response_time_p2_hours=None,
        infra_cogs_zar=Decimal("143.82"),
        total_cogs_zar=Decimal("143.82"),
        gross_margin_pct=None,  # no revenue
    ),

    # ── STARTER ($9/mo | R150 ZAR | 86.6% gross margin) ─────────────────────
    # Target: individuals, early-stage startups, hobby projects.
    # Annual: $90/yr (2 months free) → R125/mo equiv.
    # COGS breakdown: Infra R15.00 + Paystack R5.12 = R20.12 total.
    # Paystack (local ZA card, 3.41% effective incl. 15% VAT): R150 × 3.41% ≈ R5.12.
    # ZAR ref: R16.26 × 1.02 × $9 = R149.33 → ceil10 = R150.
    # Unlimited seats and viewers — no per-seat pricing at this tier.
    # Overages drawn from the org's usage wallet prepaid credit balance.
    BillingTier.STARTER: TierLimits(
        tier=BillingTier.STARTER,
        usd_monthly_price=Decimal("9.00"),
        usd_annual_price=Decimal("90.00"),
        monthly_price_zar=Decimal("150.00"),  # R16.26 × 1.02 × $9 = R149.33 → ceil10 = R150
        max_seats=None,         # unlimited — no per-seat restriction
        max_viewer_seats=None,  # unlimited internal viewers
        max_connectors=5,
        max_query_rows=100_000,
        max_dashboards=10,
        max_flows=3,
        max_storage_gb=5.0,
        max_compute_units_per_month=2_000,
        max_embedded_sessions_per_month=1_000,
        max_agent_runs_per_month=0,   # no remote kernel on entry tier
        max_ai_calls_per_month=5,     # Haiku grounding only
        security_dial_min=0,
        security_dial_max=60,
        overages=OverageRates(
            storage_zar_per_gb_month=Decimal("1.50"),      # ~84% margin; COGS = S3/R2
            compute_zar_per_1000_cu=Decimal("100.00"),     # ~77% margin; COGS = container/DuckDB
            ai_call_zar_per_call=Decimal("5.00"),          # ~93% margin; COGS = Anthropic API tokens
            embedded_session_zar_per_10k=Decimal("50.00"), # ~99% margin; COGS = egress + CDN
            agent_run_zar_per_run=None,                    # not available on Starter
            seat_zar_per_seat_month=None,                  # no per-seat pricing at any tier
        ),
        has_white_label=False,
        has_rls="basic",
        has_sso_google=True,
        has_sso_saml=False,
        has_scim=False,
        has_multi_tenant_workspaces=False,
        has_byoc=False,
        has_custom_domain=False,
        audit_log_retention_days=7,
        has_priority_support=False,
        sla_uptime_pct=None,  # no SLA on entry tier
        sla_response_time_p1_minutes=None,
        sla_response_time_p2_hours=None,
        infra_cogs_zar=Decimal("15.00"),
        total_cogs_zar=Decimal("20.12"),
        gross_margin_pct=86.6,
    ),

    # ── TEAM ($49/mo | R820 ZAR | 85.6% gross margin) ───────────────────────
    # Target: small teams that outgrew Starter but don't yet need Pro's scale,
    # SLA, or full white-label.  Smooths the old $9 → $149 gap (was a 16.5×
    # jump) into 5.4× (Starter → Team) and 3.0× (Team → Pro) steps.
    # Annual: $490/yr (2 months free) → R683/mo equiv.
    # COGS breakdown: Infra R90.00 + Paystack R27.96 = R117.96 total.
    # Paystack (local ZA card, 3.41% effective): R820 × 3.41% ≈ R27.96.
    # ZAR ref: R16.26 × 1.02 × $49 = R812.67 → ceil10 = R820.
    # Unlimited seats and viewers — no per-seat pricing at this tier.
    # Overages drawn from the org's usage wallet prepaid credit balance.
    BillingTier.TEAM: TierLimits(
        tier=BillingTier.TEAM,
        usd_monthly_price=Decimal("49.00"),
        usd_annual_price=Decimal("490.00"),
        monthly_price_zar=Decimal("820.00"),  # R16.26 × 1.02 × $49 = R812.67 → ceil10 = R820
        max_seats=None,         # unlimited — no per-seat restriction
        max_viewer_seats=None,  # unlimited internal viewers
        max_connectors=15,
        max_query_rows=1_000_000,
        max_dashboards=30,
        max_flows=8,
        max_storage_gb=15.0,
        max_compute_units_per_month=6_000,
        max_embedded_sessions_per_month=5_000,
        max_agent_runs_per_month=10,    # entry-level remote kernel allowance
        max_ai_calls_per_month=15,      # Haiku grounding + light Sonnet chat
        security_dial_min=0,
        security_dial_max=70,
        overages=OverageRates(
            storage_zar_per_gb_month=Decimal("1.50"),      # ~84% margin; COGS = S3/R2
            compute_zar_per_1000_cu=Decimal("100.00"),     # ~77% margin; COGS = container/DuckDB
            ai_call_zar_per_call=Decimal("5.00"),          # ~93% margin; COGS = Anthropic API tokens
            embedded_session_zar_per_10k=Decimal("50.00"), # ~99% margin; COGS = egress + CDN
            agent_run_zar_per_run=Decimal("2.00"),         # ~99% margin; COGS = remote kernel compute
            seat_zar_per_seat_month=None,                  # no per-seat pricing at any tier
        ),
        has_white_label="badge_removable",  # remove Nubi badge; full custom SDK is Pro+
        has_rls="full_jwt",
        has_sso_google=True,
        has_sso_saml=False,             # SAML starts at Pro
        has_scim=False,
        has_multi_tenant_workspaces=False,
        has_byoc=False,
        has_custom_domain=False,        # custom domain starts at Pro
        audit_log_retention_days=30,
        has_priority_support=False,     # email/Slack priority starts at Pro
        sla_uptime_pct=None,            # no contractual SLA below Pro
        sla_response_time_p1_minutes=None,
        sla_response_time_p2_hours=None,
        infra_cogs_zar=Decimal("90.00"),
        total_cogs_zar=Decimal("117.96"),
        gross_margin_pct=85.6,
    ),

    # ── PRO ($149/mo | R2,480 ZAR | 79.7% gross margin) ─────────────────────
    # Target: growing teams, ISVs building embedded analytics products.
    # Annual: $1,490/yr (2 months free) → R2,067/mo equiv.
    # COGS breakdown: Infra R420.00 + Paystack R84.57 = R504.57 total.
    # Paystack (local ZA card, 3.41% effective): R2,480 × 3.41% ≈ R84.57.
    # ZAR ref: R16.26 × 1.02 × $149 = R2,472.87 → ceil10 = R2,480.
    # Unlimited seats and viewers — no per-seat pricing at this tier.
    # Overages drawn from the org's usage wallet prepaid credit balance.
    # SLA: 99.5% uptime; best-effort support (no dedicated CSM).
    BillingTier.PRO: TierLimits(
        tier=BillingTier.PRO,
        usd_monthly_price=Decimal("149.00"),
        usd_annual_price=Decimal("1490.00"),
        monthly_price_zar=Decimal("2480.00"),  # R16.26 × 1.02 × $149 = R2,472.87 → ceil10 = R2,480
        max_seats=None,         # unlimited — no per-seat restriction
        max_viewer_seats=None,  # unlimited internal viewers
        max_connectors=None,    # all connectors
        max_query_rows=5_000_000,
        max_dashboards=100,
        max_flows=20,
        max_storage_gb=50.0,
        max_compute_units_per_month=15_000,
        max_embedded_sessions_per_month=25_000,
        max_agent_runs_per_month=50,     # remote kernel included
        max_ai_calls_per_month=50,       # Haiku grounding + Sonnet chat
        security_dial_min=0,
        security_dial_max=80,
        overages=OverageRates(
            storage_zar_per_gb_month=Decimal("1.50"),      # ~84% margin; COGS = S3/R2
            compute_zar_per_1000_cu=Decimal("100.00"),     # ~77% margin; COGS = container/DuckDB
            ai_call_zar_per_call=Decimal("5.00"),          # ~93% margin; COGS = Anthropic API tokens
            embedded_session_zar_per_10k=Decimal("50.00"), # ~99% margin; COGS = egress + CDN
            agent_run_zar_per_run=Decimal("2.00"),         # ~99% margin; COGS = remote kernel compute
            seat_zar_per_seat_month=None,                  # no per-seat pricing at any tier
        ),
        has_white_label="full",
        has_rls="full_jwt",
        has_sso_google=True,
        has_sso_saml="1_idp",
        has_scim=False,
        has_multi_tenant_workspaces=False,
        has_byoc=False,
        has_custom_domain=True,
        audit_log_retention_days=90,
        has_priority_support="email_slack",
        sla_uptime_pct=99.5,
        sla_response_time_p1_minutes=None,  # no contractual P1 SLA on Pro
        sla_response_time_p2_hours=None,
        infra_cogs_zar=Decimal("420.00"),
        total_cogs_zar=Decimal("504.57"),
        gross_margin_pct=79.7,
    ),

    # ── ENTERPRISE ($1,000/mo floor | R16,590 ZAR | 75.5% gross margin) ─────
    # Target: large SaaS, white-label platforms, BYOC/on-prem, compliance-heavy.
    # Annual: $10,000/yr floor (2 months free) → R13,825/mo equiv.
    # Typical contract: $1,000–$5,000/mo. BYOC pushes gross margin to ~86%.
    # COGS breakdown:
    #   Infra R3,000.00 (hosted; 500GB storage, 200K CU, unlimited embed sessions)
    #   SLA / on-call / monitoring overhead R700.00 (99.95% SLA commitment)
    #   Dedicated CSM allocation R700.00 (named contact, monthly reviews, Slack channel)
    #   Subtotal infra+support R3,500.00 (approx)
    #   Paystack (local ZA card, 3.41% effective): R16,590 × 3.41% ≈ R565.72
    #   Total COGS: R4,065.72
    # Paystack effective rate: 3.41% (same basis as other tiers).
    # ZAR ref: R16.26 × 1.02 × $1,000 = R16,585.20 → ceil10 = R16,590.
    # Unlimited seats, viewers, connectors, dashboards, flows.
    # No per-seat pricing. Overages drawn from the org's usage wallet.
    # SLA: 99.95% monthly uptime (~22 min/month), P1 < 30 min, P2 < 2 hr.
    BillingTier.ENTERPRISE: TierLimits(
        tier=BillingTier.ENTERPRISE,
        usd_monthly_price=Decimal("1000.00"),   # floor; custom-quoted above
        usd_annual_price=Decimal("10000.00"),   # floor annual (10 × $1,000)
        monthly_price_zar=Decimal("16590.00"),  # R16.26 × 1.02 × $1,000 = R16,585.20 → ceil10 = R16,590
        max_seats=None,         # unlimited
        max_viewer_seats=None,
        max_connectors=None,
        max_query_rows=None,    # unlimited
        max_dashboards=None,
        max_flows=None,         # unlimited — flow definitions are DB rows (~R0.001/flow/mo COGS)
        max_storage_gb=500.0,   # hosted; None for BYOC
        max_compute_units_per_month=200_000,   # hosted; None for BYOC
        max_embedded_sessions_per_month=None,  # unlimited embed sessions
        max_agent_runs_per_month=1_000,
        max_ai_calls_per_month=500,
        security_dial_min=0,
        security_dial_max=100,
        overages=OverageRates(
            storage_zar_per_gb_month=Decimal("1.50"),
            compute_zar_per_1000_cu=Decimal("100.00"),
            ai_call_zar_per_call=Decimal("5.00"),
            embedded_session_zar_per_10k=Decimal("0.00"),  # unlimited embed included → R0 overage
            agent_run_zar_per_run=Decimal("2.00"),
            seat_zar_per_seat_month=None,                  # unlimited seats
        ),
        has_white_label="full_custom_sdk",
        has_rls="full_hipaa_ready",
        has_sso_google=True,
        has_sso_saml="unlimited_idps",
        has_scim=True,
        has_multi_tenant_workspaces=True,
        has_byoc=True,
        has_custom_domain=True,
        audit_log_retention_days=None,  # unlimited retention
        has_priority_support="dedicated_csm",
        # SLA: 99.95% monthly uptime (≤ ~22 minutes downtime/month).
        # P1 (site-down, data-loss risk): first response ≤ 30 minutes, 24/7.
        # P2 (major feature degraded): first response ≤ 2 hours, business hours.
        sla_uptime_pct=99.95,
        sla_response_time_p1_minutes=30,
        sla_response_time_p2_hours=2,
        infra_cogs_zar=Decimal("3500.00"),
        total_cogs_zar=Decimal("4065.72"),
        gross_margin_pct=75.5,  # hosted; BYOC ~86%
    ),
}


# ---------------------------------------------------------------------------
# Security-dial → tier mapping
# ---------------------------------------------------------------------------

# Ordered list of (threshold, BillingTier).
# Interpretation: dial_value > threshold → tier (or higher) required.
# The list is checked from highest threshold to lowest.
#
# | Dial range | Threshold crossed | Minimum tier |
# |------------|-------------------|--------------|
# | 0–40       | none              | FREE         |
# | 41–60      | 40                | STARTER      |
# | 61–70      | 60                | TEAM         |
# | 71–80      | 70                | PRO          |
# | 81–100     | 80                | ENTERPRISE   |
_DIAL_TIER_ORDER: list[tuple[int, BillingTier]] = [
    (80, BillingTier.ENTERPRISE),
    (70, BillingTier.PRO),
    (60, BillingTier.TEAM),
    (40, BillingTier.STARTER),
    (0, BillingTier.FREE),
]


def tier_for_security_dial(dial_value: int) -> BillingTier:
    """Return the minimum billing tier required for *dial_value*.

    The security dial is an integer 0–100 representing how restrictive the
    platform's security posture is.  Higher dial values require paid tiers
    because they unlock features (e.g. row-level security, audit logs, SCIM)
    that are only available in STARTER / PRO / ENTERPRISE.

    | Dial range | Minimum tier |
    |------------|--------------|
    | 0–40       | FREE         |
    | 41–60      | STARTER      |
    | 61–70      | TEAM         |
    | 71–80      | PRO          |
    | 81–100     | ENTERPRISE   |

    Parameters
    ----------
    dial_value:
        An integer in [0, 100].

    Returns
    -------
    BillingTier
        The minimum tier that supports the given dial value.

    Raises
    ------
    ValueError
        When *dial_value* is outside [0, 100].
    """
    if not 0 <= dial_value <= 100:
        raise ValueError(f"dial_value must be in [0, 100], got {dial_value!r}")

    for threshold, tier in _DIAL_TIER_ORDER:
        if dial_value > threshold:
            return tier
    return BillingTier.FREE


# ---------------------------------------------------------------------------
# Feature / quota helpers
# ---------------------------------------------------------------------------


def is_feature_available(tier: BillingTier, feature: str) -> bool:
    """Return whether *feature* is available for *tier*.

    Convenience helper for feature-gate checks.  The *feature* string maps to
    a boolean or string attribute on :class:`TierLimits`.

    Parameters
    ----------
    tier:
        A :class:`BillingTier` value.
    feature:
        Feature name — one of:
        ``"white_label"``, ``"rls"``, ``"sso_google"``, ``"sso_saml"``,
        ``"scim"``, ``"multi_tenant_workspaces"``, ``"byoc"``,
        ``"custom_domain"``, ``"priority_support"``, ``"audit_logs"``.

    Returns
    -------
    bool
        ``True`` when the feature is enabled (non-False) for the tier.
    """
    limits = get_tier_limits(tier)
    attr_map: dict[str, str] = {
        "white_label": "has_white_label",
        "rls": "has_rls",
        "sso_google": "has_sso_google",
        "sso_saml": "has_sso_saml",
        "scim": "has_scim",
        "multi_tenant_workspaces": "has_multi_tenant_workspaces",
        "byoc": "has_byoc",
        "custom_domain": "has_custom_domain",
        "priority_support": "has_priority_support",
        "audit_logs": "audit_log_retention_days",
    }
    attr = attr_map.get(feature)
    if attr is None:
        return False
    value = getattr(limits, attr, False)
    if attr == "audit_log_retention_days":
        # 0 = no audit logs; any other value (including None = unlimited) = enabled.
        return value != 0
    return bool(value)


def is_within_quota(tier: BillingTier, quota: str, value: int | float) -> bool:
    """Return whether *value* is within the quota limit for *tier*.

    Parameters
    ----------
    tier:
        A :class:`BillingTier` value.
    quota:
        Quota name — one of ``"seats"``, ``"connectors"``, ``"query_rows"``,
        ``"dashboards"``, ``"flows"``, ``"storage_gb"``,
        ``"compute_units_per_month"``, ``"embedded_sessions_per_month"``,
        ``"agent_runs_per_month"``, ``"ai_calls_per_month"``.
    value:
        The current usage value to check.

    Returns
    -------
    bool
        ``True`` when *value* is within the limit (or the limit is unlimited).
    """
    limits = get_tier_limits(tier)
    attr_map: dict[str, str] = {
        "seats": "max_seats",
        "viewer_seats": "max_viewer_seats",
        "connectors": "max_connectors",
        "query_rows": "max_query_rows",
        "dashboards": "max_dashboards",
        "flows": "max_flows",
        "storage_gb": "max_storage_gb",
        "compute_units_per_month": "max_compute_units_per_month",
        "embedded_sessions_per_month": "max_embedded_sessions_per_month",
        "agent_runs_per_month": "max_agent_runs_per_month",
        "ai_calls_per_month": "max_ai_calls_per_month",
    }
    attr = attr_map.get(quota)
    if attr is None:
        return True  # unknown quota → allow
    limit = getattr(limits, attr, None)
    if limit is None:
        return True  # unlimited
    return value <= limit


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def get_tier_limits(tier: BillingTier) -> TierLimits:
    """Return the :class:`TierLimits` for *tier*.

    Parameters
    ----------
    tier:
        A :class:`BillingTier` value.

    Returns
    -------
    TierLimits
        Always succeeds (all tiers are in the catalogue).
    """
    return _TIER_CATALOGUE[tier]


def all_tiers() -> list[TierLimits]:
    """Return all tiers ordered from FREE → ENTERPRISE."""
    return [_TIER_CATALOGUE[t] for t in BillingTier]


def billing_tier_from_license_tier(license_tier_value: str) -> BillingTier:
    """Convert a license tier string value to a :class:`BillingTier`.

    Accepts the string values from :class:`app.ee.licensing.license.Tier`
    (``"free"``, ``"pro"``, ``"enterprise"``) as well as the new billing
    tier values (``"starter"``).  Defaults to FREE for unknown values.

    Legacy values from the 5-tier model (``"business"``) are mapped to
    ENTERPRISE as the closest equivalent.

    Parameters
    ----------
    license_tier_value:
        The ``.value`` of a :class:`~app.ee.licensing.license.Tier` enum,
        or a BillingTier value string.

    Returns
    -------
    BillingTier
    """
    normalized = license_tier_value.lower()
    # Legacy 5-tier "business" → Enterprise (closest equivalent)
    if normalized == "business":
        return BillingTier.ENTERPRISE
    try:
        return BillingTier(normalized)
    except ValueError:
        return BillingTier.FREE


# ---------------------------------------------------------------------------
# Customer-facing disclosure copy
# ---------------------------------------------------------------------------

ZAR_DISCLOSURE_COPY: str = (
    "Nubi's subscription prices are set in US dollars (USD) and converted to "
    "South African rand (ZAR) using a daily exchange rate refreshed from "
    "tier-1 FX providers. The ZAR amount charged each billing cycle may vary "
    "slightly as the exchange rate changes between cycles. Your USD price anchor "
    "remains fixed for the duration of your plan. The current exchange rate and "
    "when it was last updated is visible in your billing settings. Questions? "
    "Contact billing@nubi.io."
)
