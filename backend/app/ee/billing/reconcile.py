"""Billing-cycle reconciliation — the "run the billing model" engine.

Closes one billing cycle for one organisation:

    0. Idempotency guard — if an invoice already exists for the exact
       (org, period) the cycle is NOT re-run: a paid invoice is returned
       as-is and an unpaid one is resumed (collection re-attempted with the
       same deterministic Paystack reference).  Re-running a cycle can never
       double-debit the wallet or double-charge the card.
    1. Aggregate the org's metered usage for the period.
    2. Compare usage to the tier's included quota → compute overages.
    3. Draw overages from the prepaid usage wallet first; bill the remainder.
    4. Build an invoice (base subscription + uncovered overages + VAT).
    5. Persist the invoice BEFORE collecting, so money never moves without a
       record to reconcile against.
    6. Collect the total from the saved Paystack card (in ZAR, at the live FX
       rate — prices are anchored in USD) using a deterministic per-cycle
       reference so Paystack's reference dedup defuses accidental retries.
    7. Render the invoice PDF, persist the final state, record billing events,
       and advance the subscription period.
    8. Email the invoice to the customer (best-effort).

Everything that touches the network (Paystack) or sends mail is injectable so
the cycle can be run end-to-end in tests and the simulation harness with no
external services.  Time is injected too (``period_start`` / ``period_end`` /
``issued_at``) so a simulation can "advance billing time" deterministically.

This module is EE-only and must NEVER be imported by open-source core code.
"""

from __future__ import annotations

import calendar
import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Any, Awaitable, Callable

from app.ee.billing.invoice import (
    BusinessInfo,
    Invoice,
    InvoiceLineItem,
    build_invoice,
    business_info_from_settings,
)
from app.ee.billing.tiers import BillingTier, OverageRates, TierLimits, get_tier_limits

logger = logging.getLogger("nubi.billing.reconcile")

# A charge function: (org_id, amount_zar_cents, reference, metadata) -> result dict.
# Result must include {"status": "success" | "failed" | "no_card", ...}.
ChargeFn = Callable[..., Awaitable[dict[str, Any]]]


# ---------------------------------------------------------------------------
# Usage snapshot
# ---------------------------------------------------------------------------


@dataclass
class UsageSnapshot:
    """An org's metered usage over one billing period.

    Storage is a peak/representative GB figure for the period; the other
    dimensions are period totals.
    """

    storage_gb: float = 0.0
    compute_units: int = 0
    ai_calls: int = 0
    embedded_sessions: int = 0
    agent_runs: int = 0
    # Informational breakout: the portion of compute_units consumed on the
    # warehouse (heavy-query pool), already 4×-multiplied at metering time.
    # NOT a separate billable dimension — it is a subset of compute_units —
    # but invoices and the current-cycle panel can show "of which warehouse".
    warehouse_cu: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "storage_gb": self.storage_gb,
            "compute_units": self.compute_units,
            "ai_calls": self.ai_calls,
            "embedded_sessions": self.embedded_sessions,
            "agent_runs": self.agent_runs,
            "warehouse_cu": self.warehouse_cu,
        }


# Maps a usage_events ``kind`` to a UsageSnapshot dimension.  ``"kernel"`` is
# the legacy compute kind already recorded by app.compute.metering.
_KIND_TO_DIMENSION = {
    "kernel": "compute_units",
    "compute": "compute_units",
    "ai_call": "ai_calls",
    "ai": "ai_calls",
    "embedded_session": "embedded_sessions",
    "embed": "embedded_sessions",
    "agent_run": "agent_runs",
    "agent": "agent_runs",
}


async def aggregate_usage_for_org(
    org_id: str, period_start: datetime, period_end: datetime
) -> UsageSnapshot:
    """Aggregate an org's metered usage for a period from ``usage_events``.

    Reads the ``usage_events`` table when a DB pool is available; otherwise
    falls back to the in-process metering sink (local dev / tests).  Always
    returns a snapshot — never raises — so it is safe to call from a route.
    """
    try:
        from app.db import fetch  # noqa: PLC0415

        rows = await fetch(
            """
            SELECT kind,
                   (tier LIKE '%:warehouse') AS is_warehouse,
                   COALESCE(SUM(units), 0) AS total_units,
                   COUNT(*) AS n, COALESCE(MAX(units), 0) AS max_units
            FROM usage_events
            WHERE org_id = $1::uuid
              AND created_at >= $2 AND created_at < $3
            GROUP BY kind, (tier LIKE '%:warehouse')
            """,
            str(org_id), period_start, period_end,
        )
        events: list[dict[str, Any]] = []
        for r in rows:
            kind = (r["kind"] or "").lower()
            if kind == "storage":
                events.append({"kind": "storage", "units": float(r["max_units"] or 0)})
            elif kind in ("kernel", "compute"):
                events.append(
                    {
                        "kind": "compute",
                        "units": float(r["total_units"] or 0),
                        "warehouse": bool(r["is_warehouse"]),
                    }
                )
            else:
                # Count of events for discrete dimensions (ai_call, embed, agent).
                for _ in range(int(r["n"])):
                    events.append({"kind": kind, "units": 1})
        return aggregate_usage_from_events(events)
    except Exception:  # noqa: BLE001 — DB not available → fall back to in-memory sink
        try:
            from app.compute.metering import get_usage  # noqa: PLC0415

            events = [e for e in get_usage() if str(e.get("org_id")) == str(org_id)]
            return aggregate_usage_from_events(events)
        except Exception:  # noqa: BLE001
            return UsageSnapshot()


def aggregate_usage_from_events(events: list[dict[str, Any]]) -> UsageSnapshot:
    """Build a :class:`UsageSnapshot` from raw ``usage_events`` rows.

    Convention
    ----------
    - ``kind="kernel"/"compute"`` → compute units (summed ``units``).
    - ``kind="ai_call"`` → AI calls (count, or summed ``units`` when > 1).
    - ``kind="embedded_session"`` → embedded sessions (count / ``units``).
    - ``kind="agent_run"`` → agent runs (count / ``units``).
    - ``kind="storage"`` → storage GB (max ``units`` over the period).
    """
    snap = UsageSnapshot()
    storage_peak = 0.0
    for ev in events:
        kind = (ev.get("kind") or "").lower()
        units = ev.get("units")
        if kind == "storage":
            storage_peak = max(storage_peak, float(units or ev.get("output_bytes", 0) / 1e9))
            continue
        dim = _KIND_TO_DIMENSION.get(kind)
        if dim is None:
            continue
        amount = units if units is not None else 1
        if dim == "compute_units":
            snap.compute_units += int(round(float(amount)))
            # Warehouse breakout: pre-aggregated DB rows carry a "warehouse"
            # flag; raw in-memory sink events carry the ":warehouse" tier
            # suffix stamped by the heavy-query pool.
            if ev.get("warehouse") or str(ev.get("tier") or "").endswith(":warehouse"):
                snap.warehouse_cu += int(round(float(amount)))
        else:
            setattr(snap, dim, getattr(snap, dim) + int(round(float(amount or 1))))
    snap.storage_gb = storage_peak
    return snap


# ---------------------------------------------------------------------------
# Overage computation
# ---------------------------------------------------------------------------


def _overage(used: float, included: float | int | None) -> float:
    """Return the amount of *used* beyond *included* (0 if within / unlimited)."""
    if included is None:  # unlimited
        return 0.0
    return max(0.0, float(used) - float(included))


def compute_overage_line_items(
    usage: UsageSnapshot, tier: BillingTier | TierLimits
) -> tuple[list[InvoiceLineItem], Decimal]:
    """Return (overage line items, total overage ZAR) for *usage* on *tier*.

    Each dimension that exceeds its included quota becomes one priced line item
    using the tier's :class:`OverageRates`.  Dimensions with no rate (or no
    overage) are skipped.
    """
    limits = tier if isinstance(tier, TierLimits) else get_tier_limits(tier)
    r: OverageRates = limits.overages
    items: list[InvoiceLineItem] = []

    def add(desc: str, qty: float, unit: str, rate: Decimal | None, divisor: float = 1.0) -> None:
        if rate is None or rate <= 0 or qty <= 0:
            return
        billable = Decimal(str(qty)) / Decimal(str(divisor))
        amount = (billable * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if amount <= 0:
            return
        items.append(
            InvoiceLineItem(
                description=desc,
                amount_zar=amount,
                kind="overage",
                quantity=Decimal(str(qty)),
                unit=unit,
                unit_price_zar=rate,
            )
        )

    # Storage — billed per GB-month over the included allowance.
    over_storage = _overage(usage.storage_gb, limits.max_storage_gb)
    if over_storage > 0:
        amount = (Decimal(str(over_storage)) * r.storage_zar_per_gb_month).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        ) if r.storage_zar_per_gb_month else Decimal("0")
        if amount > 0:
            items.append(
                InvoiceLineItem(
                    f"Storage overage ({over_storage:.2f} GB over {limits.max_storage_gb:g} GB)",
                    amount, kind="overage",
                    quantity=Decimal(str(round(over_storage, 2))), unit="GB",
                    unit_price_zar=r.storage_zar_per_gb_month,
                )
            )

    # Compute units — billed per 1,000 CU.
    over_cu = _overage(usage.compute_units, limits.max_compute_units_per_month)
    if over_cu > 0 and r.compute_zar_per_1000_cu:
        amount = (Decimal(str(over_cu)) / Decimal("1000") * r.compute_zar_per_1000_cu).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        if amount > 0:
            items.append(
                InvoiceLineItem(
                    f"Compute overage ({int(over_cu):,} CU over {limits.max_compute_units_per_month:,})".replace(",", " "),
                    amount, kind="overage",
                    # Quantity is in the priced unit (1k CU) so the printed
                    # quantity × unit price reproduces the line amount.
                    quantity=Decimal(str(int(over_cu))) / Decimal("1000"), unit="1k CU",
                    unit_price_zar=r.compute_zar_per_1000_cu,
                )
            )

    # AI calls — billed per call.
    over_ai = _overage(usage.ai_calls, limits.max_ai_calls_per_month)
    add(f"AI calls overage ({int(over_ai)} over {limits.max_ai_calls_per_month})",
        over_ai, "call", r.ai_call_zar_per_call)

    # Embedded sessions — billed per 10,000.
    over_embed = _overage(usage.embedded_sessions, limits.max_embedded_sessions_per_month)
    if over_embed > 0 and r.embedded_session_zar_per_10k:
        amount = (Decimal(str(over_embed)) / Decimal("10000") * r.embedded_session_zar_per_10k).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        if amount > 0:
            items.append(
                InvoiceLineItem(
                    f"Embedded sessions overage ({int(over_embed):,} over {limits.max_embedded_sessions_per_month:,})".replace(",", " "),
                    amount, kind="overage",
                    # Quantity is in the priced unit (10k sessions) so the
                    # printed quantity × unit price reproduces the line amount.
                    quantity=Decimal(str(int(over_embed))) / Decimal("10000"), unit="10k sess",
                    unit_price_zar=r.embedded_session_zar_per_10k,
                )
            )

    # Agent / kernel runs — billed per run.
    over_agent = _overage(usage.agent_runs, limits.max_agent_runs_per_month)
    add(f"Agent runs overage ({int(over_agent)} over {limits.max_agent_runs_per_month})",
        over_agent, "run", r.agent_run_zar_per_run)

    total = sum((li.amount_zar for li in items), Decimal("0.00"))
    return items, total


# ---------------------------------------------------------------------------
# Cycle result
# ---------------------------------------------------------------------------


@dataclass
class CycleResult:
    """Outcome of one billing-cycle run."""

    invoice: Invoice
    pdf_bytes: bytes
    usage: UsageSnapshot
    overage_total_zar: Decimal
    wallet_applied_zar: Decimal
    charge: dict[str, Any] = field(default_factory=dict)
    email: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {
            "invoice_number": self.invoice.invoice_number,
            "status": self.invoice.status,
            "subtotal_zar": str(self.invoice.subtotal_zar),
            "vat_amount_zar": str(self.invoice.vat_amount_zar),
            "total_zar": str(self.invoice.total_zar),
            "overage_total_zar": str(self.overage_total_zar),
            "wallet_applied_zar": str(self.wallet_applied_zar),
            "charge": {k: v for k, v in self.charge.items() if k != "raw"},
            "email": self.email,
        }


# ---------------------------------------------------------------------------
# Default Paystack charge function
# ---------------------------------------------------------------------------


async def _default_charge_fn(
    *, org_id: str, amount_zar_cents: int, reference: str, metadata: dict[str, Any]
) -> dict[str, Any]:
    """Charge the org's saved Paystack card for *amount_zar_cents*.

    Reads the saved authorization from the wallet config.  Returns a normalised
    result dict; never raises on a declined charge (only on transport errors,
    which are caught and reported as ``status="error"``).
    """
    from app.ee.billing import paystack  # noqa: PLC0415
    from app.ee.billing.wallet_store import get_wallet_store  # noqa: PLC0415

    cfg = await get_wallet_store().get_topup_config(org_id)
    auth = (cfg or {}).get("paystack_authorization_code")
    email = (cfg or {}).get("paystack_customer_email")
    if not auth or not email:
        return {"status": "no_card", "reference": reference}

    try:
        resp = await paystack.charge_saved_card(
            authorization_code=auth,
            email=email,
            amount_zar_cents=amount_zar_cents,
            reference=reference,
            metadata=metadata,
        )
    except RuntimeError as exc:  # transport / server error
        logger.warning("Billing: charge transport error org=%s: %s", org_id, exc)
        return {"status": "error", "reference": reference, "error": str(exc)}

    data = resp.get("data", {}) if isinstance(resp, dict) else {}
    ok = data.get("status") == "success"
    return {
        "status": "success" if ok else "failed",
        "reference": reference,
        "paused": bool(data.get("paused")),
        "raw": resp,
    }


# ---------------------------------------------------------------------------
# The cycle
# ---------------------------------------------------------------------------


async def run_billing_cycle(
    *,
    org_id: str,
    tier: BillingTier | str,
    usage: UsageSnapshot,
    period_start: datetime,
    period_end: datetime,
    customer_email: str,
    customer_name: str = "",
    fx_rate: Decimal | None = None,
    business: BusinessInfo | None = None,
    use_wallet: bool = True,
    collect: bool = True,
    send_email: bool = True,
    charge_fn: ChargeFn | None = None,
    email_sender: Any | None = None,
    issued_at: datetime | None = None,
    advance_subscription: bool = True,
) -> CycleResult:
    """Run one billing cycle for *org_id* and return a :class:`CycleResult`.

    See the module docstring for the full sequence.  All side-effecting
    collaborators (``charge_fn``, ``email_sender``) are injectable for tests /
    simulation; ``period_*`` / ``issued_at`` make time deterministic.
    """
    # Lazy imports keep this module importable without a DB / settings.
    from app.ee.billing import fx as fxmod  # noqa: PLC0415
    from app.ee.billing import wallet as walletmod  # noqa: PLC0415
    from app.ee.billing.invoice_email import send_invoice  # noqa: PLC0415
    from app.ee.billing.invoice_pdf import invoice_pdf_filename, render_invoice_pdf  # noqa: PLC0415
    from app.ee.billing.invoice_store import get_invoice_store, invoice_from_row  # noqa: PLC0415
    from app.ee.billing.store import get_billing_store  # noqa: PLC0415

    btier = tier if isinstance(tier, BillingTier) else BillingTier(str(tier))
    limits = get_tier_limits(btier)
    business = business or business_info_from_settings()
    issued_at = issued_at or datetime.now(timezone.utc)

    invoice_store = get_invoice_store()
    billing_store = get_billing_store()

    # 0. Idempotency guard — never close the same cycle twice.  A retried job,
    #    crash recovery, or operator re-run for an already-billed (org, period)
    #    must NOT debit the wallet or charge the card again.  A paid invoice is
    #    returned as-is; an unpaid one (crash mid-cycle / earlier failed
    #    charge) is resumed: we re-attempt collection on the SAME invoice with
    #    the same deterministic reference instead of building a new one.
    resumed = False
    existing_row = await _find_existing_cycle_invoice(
        invoice_store, str(org_id), period_start, period_end
    )
    if existing_row is not None:
        invoice = invoice_from_row(existing_row)
        overage_total = sum(
            (li.amount_zar for li in invoice.line_items if li.kind == "overage"),
            Decimal("0.00"),
        )
        wallet_applied_zar = invoice.wallet_applied_zar
        if invoice.status == "paid":
            try:
                pdf_bytes = render_invoice_pdf(invoice)
            except Exception as exc:  # noqa: BLE001 — PDF is re-renderable on demand
                logger.warning("Billing: invoice PDF re-render failed org=%s: %s", org_id, exc)
                pdf_bytes = b""
            return CycleResult(
                invoice=invoice,
                pdf_bytes=pdf_bytes,
                usage=usage,
                overage_total_zar=overage_total,
                wallet_applied_zar=wallet_applied_zar,
                charge={"status": "already_billed", "reference": invoice.paystack_reference},
                email={"sent": False, "skipped": "already_billed"},
            )
        resumed = True
    else:
        # FX rate — live unless overridden (simulation passes an explicit rate).
        if fx_rate is None:
            fx_rate = fxmod.get_current_rate()["rate"]
        fx_rate = Decimal(str(fx_rate))

        # 1. Base subscription line (live USD→ZAR; 0 for Free).
        line_items: list[InvoiceLineItem] = []
        base_zar = Decimal("0.00")
        if limits.usd_monthly_price > 0:
            base_zar = fxmod.convert_usd_to_zar(limits.usd_monthly_price, fx_rate=fx_rate)
            line_items.append(
                InvoiceLineItem(
                    f"{btier.value.capitalize()} plan — monthly subscription",
                    base_zar, kind="subscription",
                    quantity=Decimal("1"), unit="mo",
                    unit_price_zar=base_zar,
                )
            )

        # 2. Overages.
        overage_items, overage_total = compute_overage_line_items(usage, limits)
        line_items.extend(overage_items)

        # 3. Wallet draw — overages drawn from prepaid credit first.
        wallet_applied_zar = Decimal("0.00")
        if use_wallet and overage_total > 0:
            wallet_applied_zar = await _apply_wallet_credit(
                walletmod, org_id, overage_total, fx_rate, period_end
            )
            if wallet_applied_zar > 0:
                line_items.append(
                    InvoiceLineItem(
                        "Prepaid wallet credit applied",
                        -wallet_applied_zar, kind="wallet",
                    )
                )

        # 4. Build invoice (computes subtotal / VAT / total).
        year = issued_at.year
        invoice_number = await invoice_store.next_invoice_number(business.invoice_number_prefix, year)
        invoice = build_invoice(
            org_id=str(org_id),
            tier=btier.value,
            period_start=period_start,
            period_end=period_end,
            customer_email=customer_email,
            customer_name=customer_name,
            line_items=line_items,
            business=business,
            fx_rate=fx_rate,
            wallet_applied_zar=wallet_applied_zar,
            invoice_number=invoice_number,
            issued_at=issued_at,
        )

    # 5. Persist the invoice BEFORE collecting — money must never move without
    #    a record to reconcile against (a crash between charge and save would
    #    otherwise leave a charge with no invoice row anywhere).
    if invoice.total_zar <= 0:
        # Nothing to collect (e.g. Free tier, or wallet covered everything).
        invoice.status = "paid"
        invoice.paid_at = invoice.paid_at or issued_at
    elif invoice.status == "draft":
        # Issued and awaiting collection.
        invoice.status = "pending"
    invoice.pdf_filename = invoice_pdf_filename(invoice)
    await invoice_store.save_invoice(invoice)
    if not resumed:
        await billing_store.record_billing_event(
            str(org_id), "invoice.created", invoice.to_dict()
        )

    # 6. Collect from the saved Paystack card.  The reference is deterministic
    #    per (org, cycle) — NOT per invoice number — so any retry presents the
    #    same reference and Paystack's reference-level dedup turns an
    #    accidental second charge into a no-op.
    charge_result: dict[str, Any] = {"status": "skipped"}
    if collect and invoice.total_zar > 0 and invoice.status != "paid":
        charge_fn = charge_fn or _default_charge_fn
        ref = _cycle_charge_reference(str(org_id), period_end)
        charge_result = await charge_fn(
            org_id=str(org_id),
            amount_zar_cents=invoice.total_zar_cents,
            reference=ref,
            metadata={"org_id": str(org_id), "tier": btier.value,
                      "invoice_number": invoice.invoice_number, "kind": "invoice"},
        )
        if charge_result.get("status") == "success":
            invoice.mark_paid(paystack_reference=charge_result.get("reference", ref), paid_at=issued_at)
        else:
            invoice.status = "past_due" if charge_result.get("status") in ("failed", "error") else "pending"

    # 7. Render PDF (guarded — a render failure must not lose the ledger; the
    #    PDF is re-rendered on demand from the stored row) + persist the
    #    post-collection state.
    pdf_bytes = b""
    try:
        pdf_bytes = render_invoice_pdf(invoice)
    except Exception as exc:  # noqa: BLE001 — PDF must not fail the cycle
        logger.warning("Billing: invoice PDF render failed org=%s: %s", org_id, exc)
    await invoice_store.save_invoice(invoice)

    # 8. Record events + advance subscription.
    if invoice.status == "paid":
        await billing_store.record_billing_event(
            str(org_id), "invoice.paid",
            {"invoice_number": invoice.invoice_number, "total_zar": str(invoice.total_zar),
             "reference": invoice.paystack_reference},
        )
        if advance_subscription:
            await billing_store.upsert_subscription(
                str(org_id), tier=btier.value, status="active",
                current_period_start=period_end,
                current_period_end=_add_one_month(period_end),
            )
    elif invoice.status == "past_due":
        await billing_store.record_billing_event(
            str(org_id), "invoice.payment_failed",
            {"invoice_number": invoice.invoice_number, "total_zar": str(invoice.total_zar),
             "charge": {k: v for k, v in charge_result.items() if k != "raw"}},
        )
        await billing_store.upsert_subscription(
            str(org_id), tier=btier.value, status="past_due",
        )

    # 9. Email the invoice PDF.
    email_result: dict[str, Any] = {"sent": False}
    if send_email and customer_email and pdf_bytes:
        try:
            email_result = send_invoice(invoice, pdf_bytes, sender=email_sender)
            email_result["sent"] = True
        except Exception as exc:  # noqa: BLE001 — email must not fail the cycle
            logger.warning("Billing: invoice email failed org=%s: %s", org_id, exc)
            email_result = {"sent": False, "error": str(exc)}

    return CycleResult(
        invoice=invoice,
        pdf_bytes=pdf_bytes,
        usage=usage,
        overage_total_zar=overage_total,
        wallet_applied_zar=wallet_applied_zar,
        charge=charge_result,
        email=email_result,
    )


def _cycle_charge_reference(org_id: str, period_end: datetime) -> str:
    """Deterministic Paystack reference for one (org, billing cycle).

    Derived from the cycle key — NOT the (freshly allocated) invoice number —
    so every retry of the same cycle presents the same reference and
    Paystack's reference-level dedup turns a duplicate charge into a no-op.
    """
    return f"nubi-inv-{org_id}-{period_end.strftime('%Y%m%d')}".lower().replace(" ", "")


async def _find_existing_cycle_invoice(
    invoice_store: Any, org_id: str, period_start: datetime, period_end: datetime
) -> dict[str, Any] | None:
    """Return the stored non-void invoice row for exactly this (org, period).

    The application-level idempotency guard for :func:`run_billing_cycle` —
    a cycle that already produced an invoice must never be billed again.
    """
    rows = await invoice_store.list_invoices(org_id, limit=200)
    for row in rows:
        if row.get("status") == "void":
            continue
        row_start = _parse_period_dt(row.get("period_start"))
        row_end = _parse_period_dt(row.get("period_end"))
        if row_start == period_start and row_end == period_end:
            return row
    return None


def _parse_period_dt(value: Any) -> datetime | None:
    """Parse a stored period boundary (datetime or ISO string) to a datetime."""
    if isinstance(value, datetime):
        return value
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


async def _apply_wallet_credit(
    walletmod: Any, org_id: str, overage_total_zar: Decimal, fx_rate: Decimal, period_end: datetime
) -> Decimal:
    """Debit the wallet to cover as much of *overage_total_zar* as it can.

    Returns the ZAR value actually drawn from the wallet.  Converts the ZAR
    overage to USD cents at *fx_rate* (no FX buffer — we are crediting the
    customer's prepaid balance), debits ``min(balance, overage)``, and returns
    the ZAR equivalent of the cents drawn.

    Idempotent per period via ``ref_id``: if the deterministic ref already
    exists in the wallet ledger (an earlier run debited but crashed before the
    invoice was persisted), the recorded draw is reused — the prepaid balance
    is never debited twice for the same period.
    """
    from app.ee.billing.wallet_store import get_wallet_store  # noqa: PLC0415

    wallet_store = get_wallet_store()
    ref_id = f"overage-{org_id}-{period_end.strftime('%Y%m')}"
    try:
        if await wallet_store.ledger_ref_exists(ref_id):
            return await _previously_drawn_zar(
                wallet_store, org_id, ref_id, fx_rate, overage_total_zar
            )
    except Exception as exc:  # noqa: BLE001 — fall through to the normal draw path
        logger.warning("Billing: wallet ledger ref check failed org=%s: %s", org_id, exc)

    try:
        balance = await walletmod.get_balance(org_id)
    except Exception as exc:  # noqa: BLE001 — no wallet → bill it all on the invoice
        logger.info("Billing: no wallet balance for org=%s (%s); billing overage on invoice", org_id, exc)
        return Decimal("0.00")

    balance_usd_cents = int(balance.get("balance_usd_cents", 0) or 0)
    if balance_usd_cents <= 0:
        return Decimal("0.00")

    # ZAR overage → USD cents (floor, so we never over-draw the wallet).
    overage_usd_cents = int(
        (overage_total_zar / fx_rate * Decimal("100")).to_integral_value(rounding=ROUND_FLOOR)
    )
    draw_cents = min(balance_usd_cents, overage_usd_cents)
    if draw_cents <= 0:
        return Decimal("0.00")

    try:
        await walletmod.debit(
            org_id, draw_cents, "USAGE_OVERAGE",
            description=f"Metered overage for {period_end.strftime('%B %Y')}",
            ref_id=ref_id,
            metadata={"fx_rate": str(fx_rate), "overage_zar": str(overage_total_zar)},
        )
    except Exception as exc:  # noqa: BLE001 — on any wallet error, bill it on the invoice
        logger.warning("Billing: wallet debit failed org=%s: %s", org_id, exc)
        return Decimal("0.00")

    applied_zar = (Decimal(draw_cents) / Decimal("100") * fx_rate).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    # Never credit more than the overage total (guards rounding overshoot).
    return min(applied_zar, overage_total_zar)


async def _previously_drawn_zar(
    wallet_store: Any, org_id: str, ref_id: str, fx_rate: Decimal, overage_total_zar: Decimal
) -> Decimal:
    """Return the ZAR value of the wallet draw already recorded under *ref_id*.

    Used when a re-run finds the period's overage debit already in the ledger
    (crash between wallet debit and invoice persist on a previous run): the
    rebuilt invoice credits the customer the amount actually drawn instead of
    debiting the wallet a second time.  Converts at the FX rate recorded with
    the original debit when available.
    """
    try:
        entries = await wallet_store.list_ledger(org_id, limit=500, entry_type="USAGE_OVERAGE")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Billing: could not read wallet ledger org=%s: %s", org_id, exc)
        return Decimal("0.00")
    for entry in entries:
        if entry.get("ref_id") != ref_id:
            continue
        drawn_cents = abs(int(entry.get("amount_usd_cents", 0) or 0))
        meta = entry.get("metadata") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except ValueError:
                meta = {}
        rate = Decimal(str(meta.get("fx_rate") or fx_rate))
        applied_zar = (Decimal(drawn_cents) / Decimal("100") * rate).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        return min(applied_zar, overage_total_zar)
    return Decimal("0.00")


def _add_one_month(dt: datetime) -> datetime:
    """Return *dt* advanced by one calendar month, clamped to end-of-month.

    Anchors on the last day of a month stay on month-end (Jan 31 → Feb 28 →
    Mar 31) rather than drifting to a shorter day forever; other anchor days
    are clamped only when the next month is shorter (e.g. Mar 30 → Apr 30) —
    never to an arbitrary "safe" day, so no full-price cycle is ever cut
    short while later days exist in the month.
    """
    year = dt.year + (1 if dt.month == 12 else 0)
    month = 1 if dt.month == 12 else dt.month + 1
    last_day_of_next = calendar.monthrange(year, month)[1]
    if dt.day >= calendar.monthrange(dt.year, dt.month)[1]:
        # Month-end anchor: stay on month-end.
        day = last_day_of_next
    else:
        day = min(dt.day, last_day_of_next)
    return dt.replace(year=year, month=month, day=day)
