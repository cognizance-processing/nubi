"""Billing-model simulation harness.

Runs the EE billing model end-to-end with NO network and NO database, so you
can *see the billing model run*:

    - multiple orgs on different tiers (Starter / Team / Pro)
    - realistic monthly usage, some breaching quota → metered overages
    - a prepaid usage wallet that covers overages first
    - VAT applied only when the business is VAT-registered
    - USD-anchored prices collected in ZAR at a *drifting* monthly FX rate
    - Paystack collection simulated (mostly success, one decline)
    - a real invoice PDF written to disk for each cycle

Run it::

    cd backend && python -m scripts.simulate_billing
    cd backend && python -m scripts.simulate_billing --vat        # VAT-registered
    cd backend && python -m scripts.simulate_billing --out /tmp/x # PDF output dir

It prints a per-cycle ledger and a summary, and writes one PDF per invoice.
This is a developer tool — it never touches Paystack or sends real email.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from datetime import datetime, timezone
from decimal import Decimal

from app.ee.billing.invoice import BusinessInfo
from app.ee.billing.invoice_store import InMemoryInvoiceStore, set_invoice_store_for_tests
from app.ee.billing.reconcile import UsageSnapshot, run_billing_cycle
from app.ee.billing.store import InMemoryBillingStore, set_billing_store_for_tests
from app.ee.billing.tiers import BillingTier
from app.ee.billing.wallet_store import InMemoryWalletStore, set_wallet_store_for_tests
from app.jobs.report import NullSender

# Three consecutive months with a drifting USD→ZAR rate (ZAR weakening), to
# show the customer's ZAR amount varying slightly cycle to cycle.
MONTHS = [
    (datetime(2026, 3, 1, tzinfo=timezone.utc), datetime(2026, 3, 31, tzinfo=timezone.utc), Decimal("18.20")),
    (datetime(2026, 4, 1, tzinfo=timezone.utc), datetime(2026, 4, 30, tzinfo=timezone.utc), Decimal("18.66")),
    (datetime(2026, 5, 1, tzinfo=timezone.utc), datetime(2026, 5, 31, tzinfo=timezone.utc), Decimal("19.10")),
]


class SimCharge:
    """Mock Paystack charge: succeeds, except a configured (org, month) decline."""

    def __init__(self, decline: set[tuple[str, int]] | None = None) -> None:
        self.decline = decline or set()
        self.month = 0
        self.total_collected_cents = 0

    async def __call__(self, *, org_id, amount_zar_cents, reference, metadata):
        if (org_id, self.month) in self.decline:
            return {"status": "failed", "reference": reference}
        self.total_collected_cents += amount_zar_cents
        return {"status": "success", "reference": reference}


def _business(vat: bool) -> BusinessInfo:
    return BusinessInfo(
        name="Nubi",
        legal_name="Nubi (Pty) Ltd",
        reg_number="2026/123456/07",
        vat_number="4567891234" if vat else "",
        vat_rate=Decimal("0.15"),
        address="12 Bree Street\nCape Town, 8001\nSouth Africa",
        email="billing@nubi.io",
        website="https://nubi.io",
        currency="ZAR",
        invoice_number_prefix="NUBI",
    )


def _zar(d: Decimal) -> str:
    return f"R{d:,.2f}".replace(",", " ")


# Org scenarios: (label, org_id, tier, monthly usage, wallet funding in USD cents)
SCENARIOS = [
    ("Starter / within quota", "org-starter-0001", BillingTier.STARTER,
     UsageSnapshot(storage_gb=3, compute_units=1500, ai_calls=4, embedded_sessions=800), 0),
    ("Team / light overage, no wallet", "org-team-0002", BillingTier.TEAM,
     UsageSnapshot(storage_gb=18, compute_units=7200, ai_calls=25, embedded_sessions=6200, agent_runs=14), 0),
    ("Pro / heavy overage, wallet-funded", "org-pro-0003", BillingTier.PRO,
     UsageSnapshot(storage_gb=70, compute_units=21000, ai_calls=180, embedded_sessions=40000, agent_runs=120), 20_000),
]


async def run(vat: bool, out_dir: str) -> None:
    set_billing_store_for_tests(InMemoryBillingStore())
    wallet = InMemoryWalletStore()
    set_wallet_store_for_tests(wallet)
    set_invoice_store_for_tests(InMemoryInvoiceStore())
    os.makedirs(out_dir, exist_ok=True)

    business = _business(vat)
    sender = NullSender()
    # Decline the Pro org's collection in month index 1 to show past_due handling.
    charge = SimCharge(decline={("org-pro-0003", 1)})

    print("=" * 96)
    print(f"  NUBI BILLING-MODEL SIMULATION   (VAT-registered: {vat})")
    print(f"  USD-anchored prices · collected in ZAR · FX drifts {MONTHS[0][2]} → {MONTHS[-1][2]}")
    print("=" * 96)

    # Seed wallets.
    for _, org_id, _, _, fund in SCENARIOS:
        if fund:
            await wallet.set_balance(org_id, fund)

    for m, (start, end, rate) in enumerate(MONTHS):
        charge.month = m
        print(f"\n┌── {start:%B %Y}  ·  FX R{rate}/USD (+2% buffer applied to subscription) "
              f"{'─' * 18}")
        for label, org_id, tier, usage, _ in SCENARIOS:
            res = await run_billing_cycle(
                org_id=org_id, tier=tier, usage=usage,
                period_start=start, period_end=end, issued_at=end,
                customer_email=f"billing@{org_id}.example",
                customer_name=label.split(" / ")[0] + " Co",
                fx_rate=rate, business=business,
                use_wallet=True, collect=True, send_email=True,
                charge_fn=charge, email_sender=sender,
            )
            inv = res.invoice
            base = next((li.amount_zar for li in inv.line_items if li.kind == "subscription"), Decimal("0"))
            status_icon = {"paid": "✓ paid", "past_due": "✗ past_due", "pending": "… pending"}.get(inv.status, inv.status)
            print(f"│  {label:<34} {inv.invoice_number}")
            print(f"│      base {_zar(base):>12} | overage {_zar(res.overage_total_zar):>10} "
                  f"| wallet -{_zar(res.wallet_applied_zar):>9} | VAT {_zar(inv.vat_amount_zar):>9} "
                  f"| TOTAL {_zar(inv.total_zar):>12}  [{status_icon}]")
            # Write the PDF.
            path = os.path.join(out_dir, inv.pdf_filename)
            with open(path, "wb") as f:
                f.write(res.pdf_bytes)
        print("└" + "─" * 94)

    # Summary.
    print("\n" + "=" * 96)
    print("  SUMMARY")
    print("=" * 96)
    print(f"  Invoices generated : {len(SCENARIOS) * len(MONTHS)}")
    print(f"  Collected via Paystack (mock): {_zar(Decimal(charge.total_collected_cents) / 100)}")
    print(f"  Invoice emails dispatched (NullSender): {len(sender.sent)}")
    print(f"  PDFs written to: {out_dir}")
    print(f"  VAT line on invoices: {'YES @ 15%' if vat else 'NONE (not VAT-registered)'}")
    print("\n  Wallet balances after 3 cycles:")
    for label, org_id, _, _, fund in SCENARIOS:
        if fund:
            bal = await wallet.get_balance(org_id)
            print(f"    {label:<34} ${bal['balance_usd_cents'] / 100:,.2f} "
                  f"(started ${fund / 100:,.2f})")
    print("=" * 96)

    # Reset global singletons.
    set_billing_store_for_tests(None)
    set_wallet_store_for_tests(None)
    set_invoice_store_for_tests(None)


def main() -> None:
    ap = argparse.ArgumentParser(description="Simulate the Nubi billing model end-to-end.")
    ap.add_argument("--vat", action="store_true", help="Run as a VAT-registered business (adds 15% VAT).")
    ap.add_argument("--out", default="/tmp/nubi_invoices", help="Directory for generated invoice PDFs.")
    args = ap.parse_args()
    asyncio.run(run(args.vat, args.out))


if __name__ == "__main__":
    main()
