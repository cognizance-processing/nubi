"""Runtime usage-quota enforcement for the EE billing model.

Implements the quota checker that EE registers into the core hook
(:func:`app.features.register_quota_checker`).  Core call sites (compute,
flows, AI, embed) call :func:`app.features.enforce_quota` before executing a
metered operation; this module decides whether the operation may proceed.

Decision model (matches the canonical billing model in ``tiers.py``)
--------------------------------------------------------------------
1. Resolve the org's subscription tier from the billing store (FREE when no
   subscription exists).
2. ``None`` quota for the dimension → unlimited → ALLOW.
3. The tier has an overage rate for the dimension → ALLOW.  Overages beyond
   the included quota are billable: they draw from the prepaid usage wallet
   first and land on the next invoice (overdraw allowed), so there is never a
   reason to block.
4. No overage rate (FREE tier everywhere; e.g. agent runs on STARTER) →
   HARD STOP: aggregate the org's current-period usage from ``usage_events``
   (the same counters billing draws from) and DENY when
   ``used + amount > quota``.

This module is EE-only and must NEVER be imported by open-source core code —
core only sees the checker through the ``app.features`` registration hook.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal

from app.ee.billing.tiers import BillingTier, OverageRates, TierLimits, get_tier_limits

logger = logging.getLogger("nubi.billing.quota")

# UsageSnapshot dimension → (TierLimits quota attr, OverageRates rate attr).
_DIMENSIONS: dict[str, tuple[str, str]] = {
    "compute_units": ("max_compute_units_per_month", "compute_zar_per_1000_cu"),
    "ai_calls": ("max_ai_calls_per_month", "ai_call_zar_per_call"),
    "embedded_sessions": ("max_embedded_sessions_per_month", "embedded_session_zar_per_10k"),
    "agent_runs": ("max_agent_runs_per_month", "agent_run_zar_per_run"),
    "storage_gb": ("max_storage_gb", "storage_zar_per_gb_month"),
}


async def billing_quota_checker(
    *, org_id: str, dimension: str, amount: float = 1.0
) -> tuple[bool, str]:
    """Return ``(allowed, reason)`` for spending *amount* of *dimension*.

    See the module docstring for the decision model.  Never raises — any
    internal failure allows the operation (the core hook is fail-open anyway;
    metering still records the usage so it remains billable later).
    """
    try:
        return await _check(org_id=org_id, dimension=dimension, amount=amount)
    except Exception as exc:  # noqa: BLE001 — quota checks must never 500 a request
        logger.warning("Billing quota check failed org=%s dim=%s: %s", org_id, dimension, exc)
        return True, ""


async def _check(*, org_id: str, dimension: str, amount: float) -> tuple[bool, str]:
    # Quotas only bind where billing is live — i.e. a paid deployment license
    # (the Nubi cloud, or a licensed self-hosted EE install).  A self-hosted
    # build that merely ships the ee/ tree without a paid license has no
    # billing, so it is never usage-limited.  Checked at call time (like the
    # billing feature checkers) so license changes apply without a restart.
    from app.ee.licensing.license import get_license  # noqa: PLC0415

    if not get_license().is_paid:
        return True, ""

    # "warehouse" is a feature gate, not a numeric quota: heavy-query-pool
    # execution is available on tiers with has_warehouse and consumes normal
    # compute units (at WAREHOUSE_CU_MULTIPLIER), which the compute_units
    # dimension already meters and limits.
    if dimension == "warehouse":
        tier = await _resolve_tier(org_id)
        if get_tier_limits(tier).has_warehouse:
            return True, ""
        return False, (
            f"The hosted warehouse (heavy-query pool) is available on the Pro "
            f"and Enterprise plans. The {tier.value} plan executes queries on "
            f"standard machines. Upgrade your plan to run warehouse queries."
        )

    attrs = _DIMENSIONS.get(dimension)
    if attrs is None:
        return True, ""  # unknown dimension → allow (mirrors is_within_quota)
    quota_attr, rate_attr = attrs

    tier = await _resolve_tier(org_id)
    limits: TierLimits = get_tier_limits(tier)

    quota = getattr(limits, quota_attr, None)
    if quota is None:
        return True, ""  # unlimited

    rates: OverageRates = limits.overages
    rate: Decimal | None = getattr(rates, rate_attr, None)
    if rate is not None:
        # Overages are billable on this tier (wallet first, then invoice —
        # overdraw allowed), so usage beyond the quota is never blocked.
        return True, ""

    # Hard stop: no overage rate for this dimension on this tier.
    used = await _current_period_usage(org_id, dimension)
    if used + amount > float(quota):
        return False, (
            f"The {tier.value} plan includes {quota:g} {dimension.replace('_', ' ')} "
            f"per month and has no overage billing for this dimension "
            f"({used:g} used). Upgrade your plan to continue."
        )
    return True, ""


async def _resolve_tier(org_id: str) -> BillingTier:
    """Resolve the org's subscription tier (FREE when no subscription)."""
    from app.ee.billing.store import get_billing_store  # noqa: PLC0415

    sub = await get_billing_store().get_subscription(org_id)
    if sub and sub.get("tier"):
        try:
            return BillingTier(str(sub["tier"]))
        except ValueError:
            logger.warning("Billing quota: org=%s has unknown tier %r — treating as free", org_id, sub["tier"])
    return BillingTier.FREE


async def _current_period_usage(org_id: str, dimension: str) -> float:
    """Return the org's current-period usage for *dimension*.

    Reads the same counters billing draws from (``usage_events`` via
    :func:`app.ee.billing.reconcile.aggregate_usage_for_org`), over the
    subscription's current period (calendar month-to-date when no
    subscription period is recorded — the same fallback as the
    ``/invoices/current-cycle`` projection).
    """
    from app.ee.billing.reconcile import aggregate_usage_for_org  # noqa: PLC0415
    from app.ee.billing.store import get_billing_store  # noqa: PLC0415

    now = datetime.now(timezone.utc)
    sub = await get_billing_store().get_subscription(org_id)
    period_start = (sub or {}).get("current_period_start") or now.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    usage = await aggregate_usage_for_org(org_id, period_start, now)
    return float(getattr(usage, dimension, 0) or 0)


def register_quota_checker() -> None:
    """Register :func:`billing_quota_checker` into the core quota hook.

    Called from :func:`app.ee.billing.setup` at EE load time.  Idempotent —
    re-registration simply overwrites with the same checker.
    """
    from app.features import register_quota_checker as _register  # noqa: PLC0415

    _register(billing_quota_checker)
    logger.debug("EE billing: usage-quota checker registered")


# ---------------------------------------------------------------------------
# Usage-limits provider (read-only) — feeds the OSS-core usage view's soft
# quotas.  This is the visibility sibling of the quota CHECKER above: the
# checker decides allow/deny on the hot path; the provider just exposes the
# numeric limits so ``app.usage`` can render "used / limit / %".
# ---------------------------------------------------------------------------

# Core usage-metric id (app.usage.aggregate.METRICS) → TierLimits attr.
# Metrics with no tier limit (e.g. bytes_scanned uses a global TiB allowance,
# not a per-tier cap) are simply omitted → core treats them as unlimited.
_USAGE_METRIC_TO_TIER_ATTR: dict[str, str] = {
    "compute_units": "max_compute_units_per_month",
    "ai_tokens": "max_ai_calls_per_month",
    "embedded_sessions": "max_embedded_sessions_per_month",
    "flow_runs": "max_agent_runs_per_month",
    "storage_gb": "max_storage_gb",
}


async def usage_limits_provider(org_id: str) -> dict[str, float | None]:
    """Return per-metric usage limits for *org_id* from its subscription tier.

    Maps the core usage-metric ids to the resolved :class:`TierLimits` values.
    Limits only bind on a paid deployment (mirrors the quota checker); on an
    unlicensed OSS-with-ee build every limit is omitted (unlimited).  Never
    raises — the core hook is fail-open (a failure yields ``{}`` = unlimited).
    """
    try:
        from app.ee.licensing.license import get_license  # noqa: PLC0415

        if not get_license().is_paid:
            return {}
        tier = await _resolve_tier(org_id)
        limits: TierLimits = get_tier_limits(tier)
        out: dict[str, float | None] = {}
        for metric_id, attr in _USAGE_METRIC_TO_TIER_ATTR.items():
            value = getattr(limits, attr, None)
            out[metric_id] = (float(value) if value is not None else None)
        return out
    except Exception as exc:  # noqa: BLE001 — provider must never break the usage view
        logger.warning("Billing usage-limits provider failed org=%s: %s", org_id, exc)
        return {}


def register_usage_limits_provider() -> None:
    """Register :func:`usage_limits_provider` into the core usage-limits hook.

    Called from :func:`app.ee.billing.setup` at EE load time.  Idempotent.
    """
    from app.features import register_usage_limits_provider as _register  # noqa: PLC0415

    _register(usage_limits_provider)
    logger.debug("EE billing: usage-limits provider registered")
