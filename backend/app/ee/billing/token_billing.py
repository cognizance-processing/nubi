"""Token-passthrough billing for LLM completions — EE billing.

Replaces the flat per-call ``ai_calls`` overage rate (retired — see
``app.ee.billing.tiers`` / ``app.ee.billing.quota``'s ``ai_tokens``
dimension) with REAL-TIME, per-token billing that mirrors the actual vendor
cost of each metered (non-BYO) AI call:

    usd_marked_up = usd_cost * (1 + NUBI_TOKEN_MARKUP_PCT / 100)
    zar_charge    = usd_marked_up * fx_rate

Only the portion of a call's tokens that falls BEYOND the org's free monthly
token allowance (``TierLimits.max_ai_tokens_per_month``) is charged — the
free allowance is covered by the tier subscription itself.  BYO-key calls
(``provider.is_byo``) are never charged: the org already pays the vendor
directly for those.

Call sites
----------
``app.routes.ai`` (POST /ai/ask, /ai/dashboard, /ai/chat, /ai/chat/stream,
/ai/sql) and ``app.routes.chat`` (POST /chat/stream) call
:func:`meter_and_charge` once per completed request/turn with the provider's
accumulated :class:`~app.ai.provider.LLMUsage` for that request.

This module is EE-only and must NEVER be imported by open-source core code.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

logger = logging.getLogger("nubi.billing.tokens")

#: usage_events kind that carries per-call token totals in ``units`` (shared
#: with the legacy call-count dimension — see app.ee.billing.reconcile).
_METER_KIND = "ai_call"


# ---------------------------------------------------------------------------
# Pure billing math (unit-testable, no I/O)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TokenCharge:
    """Computed charge for the chargeable portion of one metered LLM call."""

    #: Raw provider cost (pre-markup) for the CHARGEABLE portion only.
    usd_cost: Decimal
    #: usd_cost * (1 + markup_pct / 100).
    usd_marked_up: Decimal
    #: usd_marked_up * fx_rate, rounded to 2dp — the customer-facing ZAR amount.
    zar_charge: Decimal
    #: usd_marked_up in integer cents — what the wallet actually debits (the
    #: wallet is USD-denominated; ZAR conversion happens only at Paystack
    #: collection time, per app.ee.billing.wallet's design).
    usd_cents: int


def compute_token_charge(
    usd_cost: float | Decimal,
    *,
    fx_rate: Decimal,
    markup_pct: float | Decimal,
) -> TokenCharge:
    """Compute the ZAR-denominated charge + USD-cent wallet debit for *usd_cost*.

    Formula::

        usd_marked_up = usd_cost * (1 + markup_pct / 100)
        zar_charge    = usd_marked_up * fx_rate

    Parameters
    ----------
    usd_cost:
        The raw (pre-markup) USD cost of the chargeable tokens, e.g. from
        ``litellm.completion_cost`` (already prorated to the chargeable
        fraction of the call — see :func:`chargeable_fraction`).
    fx_rate:
        USD→ZAR mid-market rate (e.g. ``app.ee.billing.fx.get_current_rate()``).
        No FX buffer is applied here (unlike subscription pricing) — this is a
        direct cost pass-through, not a priced product.
    markup_pct:
        Percentage markup on top of the raw cost (``NUBI_TOKEN_MARKUP_PCT``).

    Returns
    -------
    TokenCharge
    """
    usd_cost_d = Decimal(str(usd_cost))
    markup_d = Decimal(str(markup_pct))
    if usd_cost_d < 0:
        usd_cost_d = Decimal("0")
    usd_marked_up = usd_cost_d * (Decimal("1") + markup_d / Decimal("100"))
    zar_charge = (usd_marked_up * fx_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    usd_cents = int((usd_marked_up * Decimal("100")).to_integral_value(rounding=ROUND_HALF_UP))
    return TokenCharge(
        usd_cost=usd_cost_d,
        usd_marked_up=usd_marked_up,
        zar_charge=zar_charge,
        usd_cents=max(usd_cents, 0),
    )


def chargeable_fraction(
    *,
    total_tokens: int,
    tokens_used_before: int,
    free_allowance_tokens: int | None,
) -> float:
    """Return the fraction (0..1) of THIS call's tokens beyond the free allowance.

    Given the org has already used ``tokens_used_before`` tokens this billing
    period (from prior calls), and this call adds ``total_tokens`` more, only
    the portion that pushes the running total past ``free_allowance_tokens``
    is chargeable — the rest is covered by the tier's included allowance.

    Examples
    --------
    >>> chargeable_fraction(total_tokens=1000, tokens_used_before=0, free_allowance_tokens=5000)
    0.0
    >>> chargeable_fraction(total_tokens=1000, tokens_used_before=4500, free_allowance_tokens=5000)
    0.5
    >>> chargeable_fraction(total_tokens=1000, tokens_used_before=5000, free_allowance_tokens=5000)
    1.0
    >>> chargeable_fraction(total_tokens=1000, tokens_used_before=0, free_allowance_tokens=None)
    0.0

    Parameters
    ----------
    total_tokens:
        Prompt + completion tokens for THIS call.
    tokens_used_before:
        Tokens already used this billing period, BEFORE this call.
    free_allowance_tokens:
        The tier's ``max_ai_tokens_per_month`` — ``None`` means unlimited (no
        wallet draw ever; every token is "free").
    """
    if free_allowance_tokens is None or total_tokens <= 0:
        return 0.0
    tokens_after = tokens_used_before + total_tokens
    over_after = max(0, tokens_after - free_allowance_tokens)
    over_before = max(0, tokens_used_before - free_allowance_tokens)
    chargeable_tokens = min(total_tokens, over_after - over_before)
    return max(0.0, chargeable_tokens / total_tokens)


# ---------------------------------------------------------------------------
# Period usage lookup
# ---------------------------------------------------------------------------


async def _period_start(org_id: str, now: datetime) -> datetime:
    """Return the org's current billing-period start (subscription period, or
    calendar month-to-date fallback — mirrors app.ee.billing.quota's fallback)."""
    try:
        from app.ee.billing.store import get_billing_store  # noqa: PLC0415

        sub = await get_billing_store().get_subscription(org_id)
        start = (sub or {}).get("current_period_start")
        if start is not None:
            return start
    except Exception:  # noqa: BLE001 — fall back to calendar month
        pass
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def tokens_used_this_period(org_id: str, *, now: datetime | None = None) -> int:
    """Return the org's total metered AI tokens used so far this billing period.

    Sums ``usage_events.units`` for ``kind in ('ai_call', 'ai')`` — the SAME
    events ``app.routes.ai`` / ``app.routes.chat`` record with the real token
    count (see :func:`record_token_usage`).  Prefers the DB; falls back to the
    in-process metering sink (local dev / tests).  Never raises.
    """
    now = now or datetime.now(timezone.utc)
    try:
        start = await _period_start(org_id, now)
        try:
            from app.db import fetch  # noqa: PLC0415

            rows = await fetch(
                """
                SELECT COALESCE(SUM(units), 0) AS total
                FROM usage_events
                WHERE org_id = $1::uuid AND kind IN ('ai_call', 'ai')
                  AND created_at >= $2 AND created_at < $3
                """,
                org_id,
                start,
                now,
            )
            return int(round(float(rows[0]["total"]))) if rows else 0
        except Exception:  # noqa: BLE001 — DB not available → in-memory fallback
            from app.compute.metering import get_usage  # noqa: PLC0415

            total = 0.0
            for ev in get_usage():
                if str(ev.get("org_id")) != str(org_id):
                    continue
                if (ev.get("kind") or "").lower() not in ("ai_call", "ai"):
                    continue
                total += float(ev.get("units") or 0.0)
            return int(round(total))
    except Exception as exc:  # noqa: BLE001 — fail-open: 0 used (never falsely block)
        logger.warning("token_billing: could not resolve period usage for org=%s: %s", org_id, exc)
        return 0


# ---------------------------------------------------------------------------
# Metering + charging orchestration
# ---------------------------------------------------------------------------


async def record_token_usage(
    *,
    org_id: str | None,
    user_id: str,
    total_tokens: int,
    endpoint: str,
) -> None:
    """Record one AI call's token usage into ``usage_events`` (visibility only).

    Uses ``kind="ai_call"`` — the SAME kind the legacy call-count dimension
    used — but with ``units=total_tokens`` instead of a flat ``1.0``, so
    ``app.usage.aggregate``'s ``ai_tokens`` metric (which already sums this
    kind's units) reflects real token usage, and
    ``app.ee.billing.reconcile.aggregate_usage_for_org`` can split it into
    both the legacy COUNT (``ai_calls``) and the new SUM (``ai_tokens``).
    Never raises.
    """
    try:
        from app.compute.metering import record_usage  # noqa: PLC0415

        await record_usage(
            kind=_METER_KIND,
            user_id=user_id,
            org_id=org_id,
            units=float(max(total_tokens, 0)),
            tier=endpoint,
        )
    except Exception:  # noqa: BLE001 — metering must never fail the request
        logger.exception("token_billing: failed to record usage org=%s endpoint=%s", org_id, endpoint)


async def meter_and_charge(
    *,
    org_id: str | None,
    user_id: str,
    endpoint: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    usd_cost: float,
    is_byo: bool,
) -> dict[str, Any]:
    """Charge the org's usage wallet for the metered (non-BYO) portion of a call.

    NOTE: usage_events RECORDING (``kind="ai_call"``, visibility-only) is done
    by the CORE call site (``app.routes.ai`` / ``app.routes.chat``) via
    :func:`record_token_usage` BEFORE this function is invoked (through the
    ``app.features.meter_ai_usage`` hook) — this keeps AI usage visibility
    working in OSS builds with no EE billing present.  This function assumes
    the current call's tokens are ALREADY reflected in
    :func:`tokens_used_this_period` and subtracts them back out to compute
    the period total "before" this call.

    Steps
    -----
    1. BYO calls, zero-token calls, or zero-cost calls: no charge (return
       early).
    2. Resolve the org's tier limits + free monthly token allowance.  A
       ``None`` allowance (unlimited) or FREE tier (no wallet) never charges.
    3. Compute how many of THIS call's tokens fall beyond the allowance
       (:func:`chargeable_fraction`), prorate ``usd_cost`` by that fraction,
       and compute the marked-up ZAR/USD-cent charge
       (:func:`compute_token_charge`).
    4. Debit the wallet.  FAILS OPEN: a wallet error (including
       ``WalletInsufficientError``) is logged but never raised — the LLM call
       already happened and its cost was already incurred; blocking the
       response after the fact would only lose the user's answer without
       recovering the spend.

    Returns
    -------
    dict
        ``{charged: bool, usd_cost: float, zar_charge: str | None,
        usd_cents: int, chargeable_fraction: float}`` — for logging/tests.
        Never raises.
    """
    result: dict[str, Any] = {
        "charged": False,
        "usd_cost": float(usd_cost or 0.0),
        "zar_charge": None,
        "usd_cents": 0,
        "chargeable_fraction": 0.0,
    }

    if is_byo:
        return result
    if not org_id or total_tokens <= 0 or not usd_cost or usd_cost <= 0:
        return result

    try:
        from app.ee.billing.quota import resolve_org_limits  # noqa: PLC0415
        from app.ee.billing.tiers import BillingTier  # noqa: PLC0415

        tier, limits, billing_disabled = await resolve_org_limits(org_id)
        if billing_disabled:
            # No wallet/invoice relationship for manually-managed orgs — the
            # superadmin override hard-caps usage elsewhere (quota.py); never
            # charge a wallet that doesn't apply here.
            return result
        if tier == BillingTier.FREE:
            # FREE has no wallet / no billing relationship — its free monthly
            # allowance is enforced as a HARD STOP pre-flight (app.features
            # enforce_quota → app.ee.billing.quota's "ai_tokens" dimension),
            # never a wallet draw. A single oversized call can still slip past
            # the pre-flight check (it only tests "any capacity left", not the
            # real token count of THIS call) — never attempt to charge a wallet
            # that doesn't exist for this org.
            return result

        free_allowance = limits.max_ai_tokens_per_month
        tokens_before = await tokens_used_this_period(org_id)
        # This call's own tokens were already recorded above (step 1), so
        # "before" = period total minus this call's own contribution.
        tokens_before = max(0, tokens_before - total_tokens)

        frac = chargeable_fraction(
            total_tokens=total_tokens,
            tokens_used_before=tokens_before,
            free_allowance_tokens=free_allowance,
        )
        result["chargeable_fraction"] = frac
        if frac <= 0:
            return result

        from app.ee.billing.fx import get_current_rate  # noqa: PLC0415
        from app.config import get_settings  # noqa: PLC0415

        fx_info = get_current_rate()
        markup_pct = getattr(get_settings(), "NUBI_TOKEN_MARKUP_PCT", 7.5)
        charge = compute_token_charge(
            float(usd_cost) * frac,
            fx_rate=fx_info["rate"],
            markup_pct=markup_pct,
        )
        result["usd_cost"] = float(charge.usd_cost)
        result["zar_charge"] = str(charge.zar_charge)
        result["usd_cents"] = charge.usd_cents
        if charge.usd_cents <= 0:
            return result

        from app.ee.billing import wallet as walletmod  # noqa: PLC0415
        from app.ee.billing.wallet import WalletInsufficientError  # noqa: PLC0415

        try:
            await walletmod.debit(
                org_id,
                charge.usd_cents,
                "USAGE_LLM",
                description=f"AI tokens — {endpoint} ({total_tokens} tokens, {tier.value} tier)",
                metadata={
                    "endpoint": endpoint,
                    "total_tokens": total_tokens,
                    "chargeable_fraction": frac,
                    "fx_rate": str(fx_info["rate"]),
                    "markup_pct": str(markup_pct),
                    "zar_charge": str(charge.zar_charge),
                },
            )
            result["charged"] = True
        except WalletInsufficientError as exc:
            # Fail-open: the response was already generated; log for
            # visibility/collections follow-up rather than pretending the
            # call never happened.
            logger.warning(
                "token_billing: wallet debit skipped (insufficient balance) org=%s cents=%d: %s",
                org_id, charge.usd_cents, exc,
            )
        except Exception:  # noqa: BLE001 — never fail the request over billing
            logger.exception(
                "token_billing: wallet debit failed org=%s cents=%d", org_id, charge.usd_cents
            )
    except Exception:  # noqa: BLE001 — metering/charging must never break the response
        logger.exception("token_billing: meter_and_charge failed org=%s endpoint=%s", org_id, endpoint)

    return result
