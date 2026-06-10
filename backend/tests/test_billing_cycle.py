"""End-to-end billing-cycle reconciliation tests.

Exercises the full "run the billing model" path with NO network and NO DB:

    usage → overages → wallet draw → invoice (base + overages + VAT)
          → Paystack collection (mocked) → PDF → email (NullSender)

All collaborators are injected (charge_fn, email_sender) and time is passed in
(period_start/end, issued_at) so the cycle is fully deterministic.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.ee.billing.invoice import BusinessInfo
from app.ee.billing.reconcile import (
    UsageSnapshot,
    aggregate_usage_from_events,
    compute_overage_line_items,
    run_billing_cycle,
)
from app.ee.billing.tiers import BillingTier
from app.jobs.report import NullSender

PERIOD_START = datetime(2026, 5, 1, tzinfo=timezone.utc)
PERIOD_END = datetime(2026, 5, 31, tzinfo=timezone.utc)
ISSUED = datetime(2026, 6, 1, tzinfo=timezone.utc)
FX = Decimal("18.42")  # explicit so the cycle never touches the live FX cache


def _vat_business() -> BusinessInfo:
    return BusinessInfo(
        name="Nubi", legal_name="Nubi (Pty) Ltd", reg_number="2026/123456/07",
        vat_number="4567891234", vat_rate=Decimal("0.15"),
        address="12 Bree Street\nCape Town", email="billing@nubi.io",
        website="https://nubi.io", currency="ZAR", invoice_number_prefix="NUBI",
    )


def _no_vat_business() -> BusinessInfo:
    return BusinessInfo(
        name="Nubi", legal_name="Nubi", reg_number="", vat_number="",
        vat_rate=Decimal("0.15"), address="", email="billing@nubi.io",
        website="", currency="ZAR", invoice_number_prefix="NUBI",
    )


class _RecordingCharge:
    """Mock Paystack charge function that records calls and returns a verdict."""

    def __init__(self, status: str = "success") -> None:
        self.status = status
        self.calls: list[dict] = []

    async def __call__(self, *, org_id, amount_zar_cents, reference, metadata):
        self.calls.append(
            {"org_id": org_id, "amount_zar_cents": amount_zar_cents,
             "reference": reference, "metadata": metadata}
        )
        return {"status": self.status, "reference": reference}


@pytest.fixture
def stores():
    """Fresh in-memory billing / wallet / invoice stores for each test."""
    from app.ee.billing.invoice_store import InMemoryInvoiceStore, set_invoice_store_for_tests
    from app.ee.billing.store import InMemoryBillingStore, set_billing_store_for_tests
    from app.ee.billing.wallet_store import InMemoryWalletStore, set_wallet_store_for_tests

    billing = InMemoryBillingStore()
    wallet = InMemoryWalletStore()
    invoices = InMemoryInvoiceStore()
    set_billing_store_for_tests(billing)
    set_wallet_store_for_tests(wallet)
    set_invoice_store_for_tests(invoices)
    yield {"billing": billing, "wallet": wallet, "invoices": invoices}
    set_billing_store_for_tests(None)
    set_wallet_store_for_tests(None)
    set_invoice_store_for_tests(None)


async def _run(tier, usage, *, charge=None, sender=None, use_wallet=True,
               business=None, collect=True, send_email=True,
               org_id="11111111-1111-1111-1111-111111111111",
               period_start=PERIOD_START, period_end=PERIOD_END, issued_at=ISSUED):
    return await run_billing_cycle(
        org_id=org_id,
        tier=tier, usage=usage,
        period_start=period_start, period_end=period_end, issued_at=issued_at,
        customer_email="ops@acme.io", customer_name="Acme Inc",
        fx_rate=FX, business=business or _vat_business(),
        use_wallet=use_wallet, collect=collect, send_email=send_email,
        charge_fn=charge or _RecordingCharge("success"),
        email_sender=sender if sender is not None else NullSender(),
    )


# ── overage computation (pure) ──────────────────────────────────────────────


class TestOverageComputation:
    def test_no_overage_within_quota(self):
        usage = UsageSnapshot(storage_gb=10, compute_units=5000, ai_calls=10,
                              embedded_sessions=1000, agent_runs=5)
        items, total = compute_overage_line_items(usage, BillingTier.PRO)
        assert items == []
        assert total == Decimal("0.00")

    def test_ai_and_compute_overage_priced(self):
        # Pro: 50 AI calls, 15 000 CU included. Use 130 AI (80 over @ R5) and
        # 17 000 CU (2 000 over → 2 × R100 = R200).
        usage = UsageSnapshot(compute_units=17_000, ai_calls=130)
        items, total = compute_overage_line_items(usage, BillingTier.PRO)
        kinds = {li.description.split()[0] for li in items}
        assert "AI" in kinds and "Compute" in kinds
        ai = next(li for li in items if li.description.startswith("AI"))
        assert ai.amount_zar == Decimal("400.00")  # 80 × R5
        cu = next(li for li in items if li.description.startswith("Compute"))
        assert cu.amount_zar == Decimal("200.00")  # 2 × R100
        assert total == Decimal("600.00")

    def test_metered_lines_qty_times_unit_price_equals_amount(self):
        # A TAX INVOICE prints QTY | UNIT PRICE | AMOUNT side by side — the
        # printed columns must multiply out (quantities are stored in the
        # PRICED unit: 1k CU, 10k sessions), or the invoice contradicts itself.
        usage = UsageSnapshot(storage_gb=120, compute_units=17_000,
                              ai_calls=130, embedded_sessions=30_000)
        items, _ = compute_overage_line_items(usage, BillingTier.PRO)
        assert len(items) == 4
        for li in items:
            assert (li.quantity * li.unit_price_zar).quantize(Decimal("0.01")) == li.amount_zar
        cu = next(li for li in items if li.description.startswith("Compute"))
        assert cu.quantity == Decimal("2")        # 2 000 CU over = 2 × 1k CU
        assert cu.unit == "1k CU"
        embed = next(li for li in items if li.description.startswith("Embedded"))
        assert embed.quantity == Decimal("0.5")   # 5 000 over = 0.5 × 10k sess
        assert embed.unit == "10k sess"

    def test_starter_has_no_agent_overage_rate(self):
        # Starter has no agent-run allowance and no agent overage rate.
        usage = UsageSnapshot(agent_runs=100)
        items, total = compute_overage_line_items(usage, BillingTier.STARTER)
        assert all(not li.description.startswith("Agent") for li in items)

    def test_aggregate_from_events(self):
        events = [
            {"kind": "kernel", "units": 1500.0},
            {"kind": "compute", "units": 500.0},
            {"kind": "ai_call", "units": 1},
            {"kind": "ai_call", "units": 1},
            {"kind": "agent_run", "units": 1},
            {"kind": "storage", "units": 22.5},
        ]
        snap = aggregate_usage_from_events(events)
        assert snap.compute_units == 2000
        assert snap.ai_calls == 2
        assert snap.agent_runs == 1
        assert snap.storage_gb == 22.5


# ── full cycle ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestBillingCycle:
    async def test_base_subscription_uses_live_fx(self, stores):
        from app.ee.billing.fx import convert_usd_to_zar
        from app.ee.billing.tiers import get_tier_limits

        res = await _run(BillingTier.TEAM, UsageSnapshot(), use_wallet=False)
        expected_base = convert_usd_to_zar(get_tier_limits(BillingTier.TEAM).usd_monthly_price, fx_rate=FX)
        base = next(li for li in res.invoice.line_items if li.kind == "subscription")
        assert base.amount_zar == expected_base

    async def test_vat_applied_when_registered(self, stores):
        res = await _run(BillingTier.PRO, UsageSnapshot(ai_calls=130), use_wallet=False)
        inv = res.invoice
        assert inv.vat_amount_zar == (inv.subtotal_zar * Decimal("0.15")).quantize(Decimal("0.01"))
        assert inv.total_zar == inv.subtotal_zar + inv.vat_amount_zar

    async def test_no_vat_when_not_registered(self, stores):
        res = await _run(BillingTier.PRO, UsageSnapshot(), use_wallet=False,
                         business=_no_vat_business())
        assert res.invoice.vat_amount_zar == Decimal("0.00")
        assert res.invoice.total_zar == res.invoice.subtotal_zar

    async def test_collection_success_marks_paid_and_advances_period(self, stores):
        charge = _RecordingCharge("success")
        res = await _run(BillingTier.TEAM, UsageSnapshot(), charge=charge)
        assert res.invoice.status == "paid"
        assert res.invoice.paystack_reference
        # Paystack was charged the exact ZAR cents total.
        assert charge.calls[0]["amount_zar_cents"] == res.invoice.total_zar_cents
        # Subscription period advanced + paid event recorded.
        sub = await stores["billing"].get_subscription(res.invoice.org_id)
        assert sub["status"] == "active"
        assert sub["current_period_start"] == PERIOD_END
        events = await stores["billing"].list_billing_events(res.invoice.org_id)
        assert any(e["event_type"] == "invoice.paid" for e in events)

    async def test_collection_failure_marks_past_due(self, stores):
        charge = _RecordingCharge("failed")
        res = await _run(BillingTier.PRO, UsageSnapshot(), charge=charge)
        assert res.invoice.status == "past_due"
        events = await stores["billing"].list_billing_events(res.invoice.org_id)
        assert any(e["event_type"] == "invoice.payment_failed" for e in events)

    async def test_wallet_covers_overage_fully(self, stores):
        org = "11111111-1111-1111-1111-111111111111"
        # Overage = 80 AI × R5 = R400. Fund the wallet generously ($100 = R1842).
        await stores["wallet"].set_balance(org, 10_000)
        res = await _run(BillingTier.PRO, UsageSnapshot(ai_calls=130))
        # A negative wallet-credit line offsets (almost) the full R400 overage.
        # The wallet draw floors to whole USD cents so it never over-draws —
        # leaving at most a sub-rand rounding remainder billed on the invoice.
        wallet_line = next(li for li in res.invoice.line_items if li.kind == "wallet")
        assert wallet_line.amount_zar == -res.wallet_applied_zar
        assert Decimal("399.00") <= res.wallet_applied_zar <= Decimal("400.00")
        net_overage = sum(
            (li.amount_zar for li in res.invoice.line_items if li.kind in ("overage", "wallet")),
            Decimal("0"),
        )
        assert net_overage < Decimal("1.00")  # essentially fully covered
        # Wallet was debited (balance dropped).
        bal = await stores["wallet"].get_balance(org)
        assert bal["balance_usd_cents"] < 10_000

    async def test_wallet_partial_then_invoice_remainder(self, stores):
        org = "11111111-1111-1111-1111-111111111111"
        # Fund only $5 = R92.10 against a R400 overage → partial credit.
        await stores["wallet"].set_balance(org, 500)
        res = await _run(BillingTier.PRO, UsageSnapshot(ai_calls=130))
        assert Decimal("0.00") < res.wallet_applied_zar < Decimal("400.00")
        wallet_line = next(li for li in res.invoice.line_items if li.kind == "wallet")
        assert wallet_line.amount_zar == -res.wallet_applied_zar

    async def test_invoice_pdf_is_valid(self, stores):
        res = await _run(BillingTier.PRO, UsageSnapshot(ai_calls=130), use_wallet=False)
        assert res.pdf_bytes.startswith(b"%PDF-1.4")
        assert res.pdf_bytes.rstrip().endswith(b"%%EOF")
        assert res.invoice.pdf_filename.endswith(".pdf")

    async def test_invoice_emailed_with_pdf_attachment(self, stores):
        sender = NullSender()
        res = await _run(BillingTier.TEAM, UsageSnapshot(), sender=sender)
        assert len(sender.sent) == 1
        sent = sender.sent[0]
        assert sent["to"] == "ops@acme.io"
        assert sent["attachment_name"].endswith(".pdf")
        assert sent["attachment_data"].startswith(b"%PDF")
        assert res.email["sent"] is True

    async def test_invoice_persisted_and_listable(self, stores):
        res = await _run(BillingTier.PRO, UsageSnapshot())
        rows = await stores["invoices"].list_invoices(res.invoice.org_id)
        assert len(rows) == 1
        assert rows[0]["invoice_number"] == res.invoice.invoice_number

    async def test_invoice_numbering_increments(self, stores):
        # Two DIFFERENT cycles (the same period is idempotent and would
        # return the first invoice unchanged).
        r1 = await _run(BillingTier.TEAM, UsageSnapshot())
        r2 = await _run(
            BillingTier.TEAM, UsageSnapshot(),
            period_start=PERIOD_END, period_end=datetime(2026, 6, 30, tzinfo=timezone.utc),
            issued_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        )
        assert r1.invoice.invoice_number == "NUBI-2026-000001"
        assert r2.invoice.invoice_number == "NUBI-2026-000002"

    async def test_free_tier_zero_invoice_is_paid(self, stores):
        charge = _RecordingCharge("success")
        res = await _run(BillingTier.FREE, UsageSnapshot(), charge=charge)
        assert res.invoice.total_zar == Decimal("0.00")
        assert res.invoice.status == "paid"
        assert charge.calls == []  # nothing to collect

    async def test_collection_advances_period_to_month_end(self, stores):
        # PERIOD_END = May 31 (month-end anchor) → next period_end must be
        # Jun 30 (clamped to the ACTUAL month length, not a hard 28).
        res = await _run(BillingTier.TEAM, UsageSnapshot())
        sub = await stores["billing"].get_subscription(res.invoice.org_id)
        assert sub["current_period_end"] == datetime(2026, 6, 30, tzinfo=timezone.utc)

    # ── idempotency: re-running a cycle must never double-charge ────────────

    async def test_rerun_same_period_charges_card_once(self, stores):
        charge = _RecordingCharge("success")
        r1 = await _run(BillingTier.TEAM, UsageSnapshot(), charge=charge)
        r2 = await _run(BillingTier.TEAM, UsageSnapshot(), charge=charge)
        # One charge, one invoice — the rerun returns the settled invoice.
        assert len(charge.calls) == 1
        assert r2.invoice.invoice_number == r1.invoice.invoice_number
        assert r2.invoice.status == "paid"
        assert r2.charge["status"] == "already_billed"
        rows = await stores["invoices"].list_invoices(r1.invoice.org_id)
        assert len(rows) == 1

    async def test_rerun_same_period_does_not_double_debit_wallet(self, stores):
        org = "11111111-1111-1111-1111-111111111111"
        await stores["wallet"].set_balance(org, 10_000)
        r1 = await _run(BillingTier.PRO, UsageSnapshot(ai_calls=130))
        bal_after_first = (await stores["wallet"].get_balance(org))["balance_usd_cents"]
        r2 = await _run(BillingTier.PRO, UsageSnapshot(ai_calls=130))
        assert (await stores["wallet"].get_balance(org))["balance_usd_cents"] == bal_after_first
        assert r2.wallet_applied_zar == r1.wallet_applied_zar
        ledger = await stores["wallet"].list_ledger(org, entry_type="USAGE_OVERAGE")
        assert len(ledger) == 1  # exactly one draw for the period

    async def test_charge_reference_is_deterministic_per_cycle(self, stores):
        # The reference derives from (org, period) — not the invoice number —
        # so any retry presents the SAME reference and Paystack's
        # reference-level dedup can reject a duplicate charge.
        charge = _RecordingCharge("success")
        res = await _run(BillingTier.TEAM, UsageSnapshot(), charge=charge)
        org = res.invoice.org_id
        assert charge.calls[0]["reference"] == f"nubi-inv-{org}-20260531"

    async def test_failed_charge_rerun_retries_same_invoice(self, stores):
        org = "11111111-1111-1111-1111-111111111111"
        r1 = await _run(BillingTier.PRO, UsageSnapshot(), charge=_RecordingCharge("failed"))
        assert r1.invoice.status == "past_due"
        retry = _RecordingCharge("success")
        r2 = await _run(BillingTier.PRO, UsageSnapshot(), charge=retry)
        # The retry resumes the SAME invoice with the SAME reference.
        assert r2.invoice.invoice_number == r1.invoice.invoice_number
        assert r2.invoice.status == "paid"
        assert retry.calls[0]["reference"] == f"nubi-inv-{org}-20260531"
        rows = await stores["invoices"].list_invoices(org)
        assert len(rows) == 1
        assert rows[0]["status"] == "paid"

    async def test_invoice_persisted_before_charge(self, stores):
        # A crash DURING the charge must leave a pending invoice row — money
        # may never move without a record to reconcile against.
        org = "11111111-1111-1111-1111-111111111111"

        async def _exploding_charge(**_kw):
            raise RuntimeError("paystack transport exploded")

        with pytest.raises(RuntimeError):
            await _run(BillingTier.TEAM, UsageSnapshot(), charge=_exploding_charge)
        rows = await stores["invoices"].list_invoices(org)
        assert len(rows) == 1
        assert rows[0]["status"] == "pending"
        # The recovery run resumes that invoice (no new number) and collects.
        charge = _RecordingCharge("success")
        res = await _run(BillingTier.TEAM, UsageSnapshot(), charge=charge)
        assert res.invoice.invoice_number == rows[0]["invoice_number"]
        assert res.invoice.status == "paid"
        assert len(charge.calls) == 1
        assert len(await stores["invoices"].list_invoices(org)) == 1

    async def test_pdf_render_failure_does_not_lose_invoice(self, stores):
        # The PDF is re-renderable on demand — a render failure must not
        # blow up the cycle after money has been collected.
        from unittest.mock import patch

        charge = _RecordingCharge("success")
        with patch(
            "app.ee.billing.invoice_pdf.render_invoice_pdf",
            side_effect=RuntimeError("font cache corrupted"),
        ):
            res = await _run(BillingTier.TEAM, UsageSnapshot(), charge=charge)
        assert res.invoice.status == "paid"
        assert res.pdf_bytes == b""
        assert res.email["sent"] is False  # nothing to attach
        rows = await stores["invoices"].list_invoices(res.invoice.org_id)
        assert len(rows) == 1 and rows[0]["status"] == "paid"

    async def test_wallet_draw_is_ref_idempotent_after_partial_crash(self, stores):
        # Simulates a crash AFTER the wallet debit but BEFORE the invoice was
        # persisted: the rerun's draw must reuse the recorded ledger entry
        # instead of debiting the prepaid balance a second time.
        from app.ee.billing import wallet as walletmod
        from app.ee.billing.reconcile import _apply_wallet_credit

        org = "11111111-1111-1111-1111-111111111111"
        await stores["wallet"].set_balance(org, 10_000)
        first = await _apply_wallet_credit(walletmod, org, Decimal("400.00"), FX, PERIOD_END)
        bal_after_first = (await stores["wallet"].get_balance(org))["balance_usd_cents"]
        second = await _apply_wallet_credit(walletmod, org, Decimal("400.00"), FX, PERIOD_END)
        assert second == first
        assert (await stores["wallet"].get_balance(org))["balance_usd_cents"] == bal_after_first
        ledger = await stores["wallet"].list_ledger(org, entry_type="USAGE_OVERAGE")
        assert len(ledger) == 1


# ── subscription period advancement ──────────────────────────────────────────


class TestAddOneMonth:
    def _dt(self, y, m, d):
        return datetime(y, m, d, tzinfo=timezone.utc)

    def test_clamps_to_actual_month_length_not_28(self):
        from app.ee.billing.reconcile import _add_one_month

        # May 31 → Jun 30 (Jun 30 exists; a hard 28 would short the cycle).
        assert _add_one_month(self._dt(2026, 5, 31)) == self._dt(2026, 6, 30)

    def test_month_end_anchor_stays_month_end(self):
        from app.ee.billing.reconcile import _add_one_month

        # Jan 31 → Feb 28 → Mar 31: no permanent drift away from month-end.
        feb = _add_one_month(self._dt(2026, 1, 31))
        assert feb == self._dt(2026, 2, 28)
        assert _add_one_month(feb) == self._dt(2026, 3, 31)

    def test_mid_month_anchor_is_preserved(self):
        from app.ee.billing.reconcile import _add_one_month

        assert _add_one_month(self._dt(2026, 1, 15)) == self._dt(2026, 2, 15)

    def test_december_rolls_over_the_year(self):
        from app.ee.billing.reconcile import _add_one_month

        assert _add_one_month(self._dt(2026, 12, 31)) == self._dt(2027, 1, 31)

    def test_leap_february(self):
        from app.ee.billing.reconcile import _add_one_month

        assert _add_one_month(self._dt(2028, 1, 31)) == self._dt(2028, 2, 29)


# ── invoice HTTP routes ──────────────────────────────────────────────────────


def _client():
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from app.auth.deps import current_user
    from app.ee.billing.routes import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[current_user] = lambda: {"id": "u1", "email": "u@nubi.io"}
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
class TestInvoiceRoutes:
    async def test_list_and_download_invoice_pdf(self, stores):
        res = await _run(BillingTier.PRO, UsageSnapshot(ai_calls=130), use_wallet=False)
        org = res.invoice.org_id
        async with _client() as client:
            listing = await client.get(f"/ee/billing/invoices?org_id={org}")
            assert listing.status_code == 200
            body = listing.json()
            assert body["count"] == 1
            inv_id = body["invoices"][0]["id"]

            pdf = await client.get(f"/ee/billing/invoices/{inv_id}/pdf?org_id={org}")
            assert pdf.status_code == 200
            assert pdf.headers["content-type"] == "application/pdf"
            assert pdf.content.startswith(b"%PDF")

    async def test_download_rejects_wrong_org(self, stores):
        res = await _run(BillingTier.TEAM, UsageSnapshot(), use_wallet=False)
        async with _client() as client:
            resp = await client.get(
                f"/ee/billing/invoices/{res.invoice.id}/pdf?org_id=00000000-0000-0000-0000-000000000000"
            )
            assert resp.status_code == 404

    async def test_current_cycle_projection(self, stores):
        org = "22222222-2222-2222-2222-222222222222"
        await stores["billing"].upsert_subscription(org, tier="pro", status="active")
        async with _client() as client:
            resp = await client.get(f"/ee/billing/invoices/current-cycle?org_id={org}")
            assert resp.status_code == 200
            body = resp.json()
            assert body["tier"] == "pro"
            assert "usage" in body and "overage_total_zar" in body
            assert "limits" in body
