"""Tests for EE billing wallet — wallet.py, wallet_store.py, paystack extension.

Strategy
--------
- All tests use :class:`InMemoryWalletStore` — no DB, no network.
- Paystack API calls are replaced with a mock client via
  :func:`app.ee.billing.paystack.set_client_for_tests`.
- Tests cover: credit, debit (draw-down), auto-topup trigger, spend-cap block,
  ledger recording, and idempotency guards.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Setup: inject InMemoryWalletStore before any wallet module is imported
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_wallet_store():
    """Reset the InMemoryWalletStore before each test."""
    from app.ee.billing.wallet_store import (
        InMemoryWalletStore,
        set_wallet_store_for_tests,
    )

    store = InMemoryWalletStore()
    set_wallet_store_for_tests(store)
    yield store
    set_wallet_store_for_tests(None)


@pytest.fixture(autouse=True)
def _reset_paystack_client():
    """Reset Paystack client override and env var after each test."""
    import os
    from app.ee.billing.paystack import set_client_for_tests

    # Provide a dummy key so _get_secret_key() does not raise in auto-topup tests
    old_key = os.environ.get("PAYSTACK_SECRET_KEY")
    os.environ["PAYSTACK_SECRET_KEY"] = "sk_test_mock_placeholder"
    yield
    set_client_for_tests(None)
    if old_key is None:
        os.environ.pop("PAYSTACK_SECRET_KEY", None)
    else:
        os.environ["PAYSTACK_SECRET_KEY"] = old_key


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

ORG = str(uuid.uuid4())


def _make_mock_paystack(*, status: str = "success", paused: bool = False) -> AsyncMock:
    """Return a mock Paystack client whose POST returns a charge_authorization response."""
    mock = AsyncMock()
    mock.post = AsyncMock(
        return_value={
            "status": True,
            "message": "Charge attempted",
            "data": {
                "reference": f"nubi_auto_{uuid.uuid4().hex}",
                "status": status,
                "amount": 5000,
                "currency": "ZAR",
                "gateway_response": "Approved" if status == "success" else "Insufficient Funds",
                "paused": paused,
            },
        }
    )
    mock.get = AsyncMock(return_value={})
    return mock


async def _seed_topup_config(
    store,
    org_id: str,
    *,
    auto_topup_enabled: bool = True,
    threshold_usd_cents: int = 1000,
    topup_amount_usd_cents: int = 5000,
    monthly_topup_cap_usd_cents: int | None = None,
    spend_cap_usd_cents: int | None = None,
    reusable: bool = True,
) -> None:
    await store.upsert_topup_config(
        org_id,
        auto_topup_enabled=auto_topup_enabled,
        threshold_usd_cents=threshold_usd_cents,
        topup_amount_usd_cents=topup_amount_usd_cents,
        monthly_topup_cap_usd_cents=monthly_topup_cap_usd_cents,
        spend_cap_usd_cents=spend_cap_usd_cents,
        paystack_authorization_code="AUTH_test_xxx",
        paystack_customer_email="test@example.com",
        paystack_customer_code="CUS_test_xxx",
        paystack_card_last4="4081",
        paystack_card_brand="visa",
        paystack_card_exp_month="12",
        paystack_card_exp_year="2030",
        paystack_auth_reusable=reusable,
    )


# ===========================================================================
# InMemoryWalletStore unit tests
# ===========================================================================


class TestInMemoryWalletStore:
    """Direct tests of the InMemoryWalletStore methods."""

    @pytest.mark.asyncio
    async def test_initial_balance_is_zero(self, _clean_wallet_store):
        store = _clean_wallet_store
        bal = await store.get_balance(ORG)
        assert bal["balance_usd_cents"] == 0

    @pytest.mark.asyncio
    async def test_credit_balance(self, _clean_wallet_store):
        store = _clean_wallet_store
        new_bal = await store.credit_balance(ORG, 5000)
        assert new_bal == 5000
        record = await store.get_balance(ORG)
        assert record["balance_usd_cents"] == 5000

    @pytest.mark.asyncio
    async def test_debit_balance(self, _clean_wallet_store):
        store = _clean_wallet_store
        await store.credit_balance(ORG, 5000)
        new_bal = await store.debit_balance(ORG, 2000)
        assert new_bal == 3000

    @pytest.mark.asyncio
    async def test_debit_raises_on_insufficient_balance(self, _clean_wallet_store):
        store = _clean_wallet_store
        await store.credit_balance(ORG, 100)
        with pytest.raises(ValueError, match="Insufficient wallet balance"):
            await store.debit_balance(ORG, 500)

    @pytest.mark.asyncio
    async def test_append_and_list_ledger(self, _clean_wallet_store):
        store = _clean_wallet_store
        await store.append_ledger(
            ORG,
            entry_type="TOPUP_MANUAL",
            amount_usd_cents=5000,
            balance_after_usd_cents=5000,
            description="Test credit",
            ref_id="ref_001",
        )
        await store.append_ledger(
            ORG,
            entry_type="USAGE_LLM",
            amount_usd_cents=-100,
            balance_after_usd_cents=4900,
            description="LLM call",
        )
        entries = await store.list_ledger(ORG)
        assert len(entries) == 2
        # Newest first
        assert entries[0]["entry_type"] == "USAGE_LLM"
        assert entries[1]["entry_type"] == "TOPUP_MANUAL"

    @pytest.mark.asyncio
    async def test_ledger_ref_exists(self, _clean_wallet_store):
        store = _clean_wallet_store
        await store.append_ledger(
            ORG,
            entry_type="TOPUP_MANUAL",
            amount_usd_cents=5000,
            balance_after_usd_cents=5000,
            ref_id="unique_ref_abc",
        )
        assert await store.ledger_ref_exists("unique_ref_abc") is True
        assert await store.ledger_ref_exists("nonexistent_ref") is False

    @pytest.mark.asyncio
    async def test_sum_credits_this_month(self, _clean_wallet_store):
        store = _clean_wallet_store
        await store.append_ledger(
            ORG,
            entry_type="TOPUP_MANUAL",
            amount_usd_cents=3000,
            balance_after_usd_cents=3000,
        )
        await store.append_ledger(
            ORG,
            entry_type="TOPUP_AUTO",
            amount_usd_cents=5000,
            balance_after_usd_cents=8000,
        )
        await store.append_ledger(
            ORG,
            entry_type="USAGE_LLM",
            amount_usd_cents=-200,
            balance_after_usd_cents=7800,
        )
        total = await store.sum_credits_this_month(ORG)
        assert total == 8000  # only TOPUP_* positive entries

    @pytest.mark.asyncio
    async def test_sum_auto_topups_this_month(self, _clean_wallet_store):
        store = _clean_wallet_store
        await store.append_ledger(
            ORG,
            entry_type="TOPUP_MANUAL",
            amount_usd_cents=3000,
            balance_after_usd_cents=3000,
        )
        await store.append_ledger(
            ORG,
            entry_type="TOPUP_AUTO",
            amount_usd_cents=5000,
            balance_after_usd_cents=8000,
        )
        auto_total = await store.sum_auto_topups_this_month(ORG)
        assert auto_total == 5000

    @pytest.mark.asyncio
    async def test_topup_config_defaults(self, _clean_wallet_store):
        store = _clean_wallet_store
        cfg = await store.get_topup_config(ORG)
        assert cfg["auto_topup_enabled"] is False
        assert cfg["threshold_usd_cents"] == 1000
        assert cfg["topup_amount_usd_cents"] == 5000
        assert cfg["paystack_auth_reusable"] is False

    @pytest.mark.asyncio
    async def test_upsert_topup_config(self, _clean_wallet_store):
        store = _clean_wallet_store
        await store.upsert_topup_config(
            ORG,
            auto_topup_enabled=True,
            threshold_usd_cents=2000,
        )
        cfg = await store.get_topup_config(ORG)
        assert cfg["auto_topup_enabled"] is True
        assert cfg["threshold_usd_cents"] == 2000
        assert cfg["topup_amount_usd_cents"] == 5000  # unchanged

    @pytest.mark.asyncio
    async def test_set_topup_in_flight(self, _clean_wallet_store):
        store = _clean_wallet_store
        await store.get_topup_config(ORG)  # ensure record exists
        await store.set_topup_in_flight(ORG, True)
        cfg = await store.get_topup_config(ORG)
        assert cfg["topup_in_flight"] is True
        await store.set_topup_in_flight(ORG, False)
        cfg = await store.get_topup_config(ORG)
        assert cfg["topup_in_flight"] is False


# ===========================================================================
# wallet.py business logic tests
# ===========================================================================


class TestWalletService:
    """Tests for wallet.py — get_balance, credit, debit, auto-topup, caps."""

    @pytest.mark.asyncio
    async def test_get_balance_initial(self, _clean_wallet_store):
        from app.ee.billing.wallet import get_balance

        bal = await get_balance(ORG)
        assert bal["balance_usd_cents"] == 0

    @pytest.mark.asyncio
    async def test_credit_adds_balance_and_writes_ledger(self, _clean_wallet_store):
        from app.ee.billing.wallet import credit, get_balance

        entry = await credit(ORG, 10_000, "TOPUP_MANUAL", description="Initial topup")
        assert entry["entry_type"] == "TOPUP_MANUAL"
        assert entry["amount_usd_cents"] == 10_000
        assert entry["balance_after_usd_cents"] == 10_000

        bal = await get_balance(ORG)
        assert bal["balance_usd_cents"] == 10_000

    @pytest.mark.asyncio
    async def test_debit_draws_down_balance_and_writes_ledger(self, _clean_wallet_store):
        from app.ee.billing.wallet import credit, debit, get_balance

        await credit(ORG, 10_000, "TOPUP_MANUAL")
        entry = await debit(ORG, 300, "USAGE_LLM", description="Claude call", metadata={"tokens": 1500})
        assert entry["entry_type"] == "USAGE_LLM"
        assert entry["amount_usd_cents"] == -300
        assert entry["balance_after_usd_cents"] == 9_700

        bal = await get_balance(ORG)
        assert bal["balance_usd_cents"] == 9_700

    @pytest.mark.asyncio
    async def test_debit_blocks_at_zero_balance(self, _clean_wallet_store):
        from app.ee.billing.wallet import WalletInsufficientError, debit

        # Balance is zero by default — any debit should raise
        with pytest.raises(WalletInsufficientError) as exc_info:
            await debit(ORG, 100, "USAGE_LLM")
        assert exc_info.value.detail["error"] == "wallet_balance_insufficient"
        assert exc_info.value.detail["balance_usd_cents"] == 0
        assert exc_info.value.detail["spend_cap_hit"] is False

    @pytest.mark.asyncio
    async def test_debit_blocks_when_balance_insufficient(self, _clean_wallet_store):
        from app.ee.billing.wallet import WalletInsufficientError, credit, debit

        await credit(ORG, 50, "TOPUP_MANUAL")
        with pytest.raises(WalletInsufficientError) as exc_info:
            await debit(ORG, 200, "USAGE_COMPUTE")
        assert exc_info.value.detail["balance_usd_cents"] == 50

    @pytest.mark.asyncio
    async def test_debit_records_metadata(self, _clean_wallet_store):
        from app.ee.billing.wallet import credit, debit
        from app.ee.billing.wallet_store import get_wallet_store

        await credit(ORG, 5_000, "TOPUP_MANUAL")
        await debit(
            ORG, 100, "USAGE_LLM",
            metadata={"model": "claude-haiku", "tokens": 500, "session_id": "sess_abc"},
        )
        store = get_wallet_store()
        entries = await store.list_ledger(ORG, entry_type="USAGE_LLM")
        assert len(entries) == 1
        assert entries[0]["metadata"]["model"] == "claude-haiku"

    # ------------------------------------------------------------------
    # Auto-topup tests
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_auto_topup_fires_below_threshold(self, _clean_wallet_store):
        """When balance drops below threshold, auto-topup credits the wallet."""
        from app.ee.billing.paystack import set_client_for_tests
        from app.ee.billing.wallet import credit, debit

        mock_client = _make_mock_paystack(status="success")
        set_client_for_tests(mock_client)

        await _seed_topup_config(
            _clean_wallet_store, ORG,
            threshold_usd_cents=2000,
            topup_amount_usd_cents=5000,
        )
        # Start with balance at threshold level; one debit will drop below
        await credit(ORG, 2000, "TOPUP_MANUAL")

        # Debit 500 cents so balance = 1500 < threshold 2000
        await debit(ORG, 500, "USAGE_LLM")

        # Give the fire-and-forget coroutine time to run
        await asyncio.sleep(0.05)

        from app.ee.billing.wallet_store import get_wallet_store
        store = get_wallet_store()
        auto_entries = await store.list_ledger(ORG, entry_type="TOPUP_AUTO")
        assert len(auto_entries) == 1
        assert auto_entries[0]["amount_usd_cents"] == 5000

        bal = await store.get_balance(ORG)
        # balance = 2000 - 500 + 5000 = 6500
        assert bal["balance_usd_cents"] == 6500

    @pytest.mark.asyncio
    async def test_auto_topup_does_not_fire_above_threshold(self, _clean_wallet_store):
        """When balance remains above threshold, auto-topup should NOT fire."""
        from app.ee.billing.paystack import set_client_for_tests
        from app.ee.billing.wallet import credit, debit

        mock_client = _make_mock_paystack(status="success")
        set_client_for_tests(mock_client)

        await _seed_topup_config(_clean_wallet_store, ORG, threshold_usd_cents=500)
        await credit(ORG, 5000, "TOPUP_MANUAL")

        # Debit 100 — balance 4900 still above threshold 500
        await debit(ORG, 100, "USAGE_LLM")
        await asyncio.sleep(0.05)

        from app.ee.billing.wallet_store import get_wallet_store
        store = get_wallet_store()
        auto_entries = await store.list_ledger(ORG, entry_type="TOPUP_AUTO")
        assert len(auto_entries) == 0

    @pytest.mark.asyncio
    async def test_auto_topup_records_failed_ledger_on_decline(self, _clean_wallet_store):
        """Failed Paystack charge writes TOPUP_FAILED ledger entry."""
        from app.ee.billing.paystack import set_client_for_tests
        from app.ee.billing.wallet import credit, debit

        mock_client = _make_mock_paystack(status="failed")
        set_client_for_tests(mock_client)

        await _seed_topup_config(_clean_wallet_store, ORG, threshold_usd_cents=2000)
        await credit(ORG, 2000, "TOPUP_MANUAL")
        await debit(ORG, 500, "USAGE_LLM")  # drops below threshold
        await asyncio.sleep(0.05)

        from app.ee.billing.wallet_store import get_wallet_store
        store = get_wallet_store()
        failed_entries = await store.list_ledger(ORG, entry_type="TOPUP_FAILED")
        assert len(failed_entries) == 1
        assert failed_entries[0]["amount_usd_cents"] == 0  # no credit

    @pytest.mark.asyncio
    async def test_auto_topup_in_flight_prevents_double_charge(self, _clean_wallet_store):
        """topup_in_flight flag prevents concurrent duplicate charges."""
        from app.ee.billing.paystack import set_client_for_tests
        from app.ee.billing.wallet import trigger_auto_topup

        mock_client = _make_mock_paystack(status="success")
        set_client_for_tests(mock_client)

        await _seed_topup_config(_clean_wallet_store, ORG)
        # Manually set in-flight so the topup is skipped
        await _clean_wallet_store.set_topup_in_flight(ORG, True)

        await trigger_auto_topup(ORG)
        await asyncio.sleep(0.05)

        # No charges should have been made
        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_auto_topup_skips_when_no_reusable_card(self, _clean_wallet_store):
        """Auto-topup does nothing when no reusable card is saved."""
        from app.ee.billing.paystack import set_client_for_tests
        from app.ee.billing.wallet import credit, debit

        mock_client = _make_mock_paystack(status="success")
        set_client_for_tests(mock_client)

        # Config with auto-topup on but no reusable card
        await _seed_topup_config(_clean_wallet_store, ORG, reusable=False)
        await credit(ORG, 1000, "TOPUP_MANUAL")
        await debit(ORG, 100, "USAGE_LLM")
        await asyncio.sleep(0.05)

        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_auto_topup_respects_monthly_cap(self, _clean_wallet_store):
        """Auto-topup is blocked when it would exceed the monthly topup cap."""
        from app.ee.billing.paystack import set_client_for_tests
        from app.ee.billing.wallet import credit, debit
        from app.ee.billing.wallet_store import get_wallet_store

        mock_client = _make_mock_paystack(status="success")
        set_client_for_tests(mock_client)

        # Cap at 3000, topup_amount 5000 — topup would exceed cap, should be blocked
        await _seed_topup_config(
            _clean_wallet_store, ORG,
            threshold_usd_cents=2000,
            topup_amount_usd_cents=5000,
            monthly_topup_cap_usd_cents=3000,
        )
        await credit(ORG, 2000, "TOPUP_MANUAL")
        await debit(ORG, 500, "USAGE_LLM")  # drops below threshold
        await asyncio.sleep(0.05)

        store = get_wallet_store()
        auto_entries = await store.list_ledger(ORG, entry_type="TOPUP_AUTO")
        # Should be blocked — monthly_auto (0) + 5000 > cap (3000)
        assert len(auto_entries) == 0

    # ------------------------------------------------------------------
    # Spend cap tests
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_spend_cap_does_not_block_debit_directly(self, _clean_wallet_store):
        """Spend cap does not block debit operations — only blocks further auto-topups."""
        from app.ee.billing.wallet import credit, debit

        await _seed_topup_config(
            _clean_wallet_store, ORG,
            auto_topup_enabled=False,
            spend_cap_usd_cents=1000,  # cap at $10
        )
        await credit(ORG, 5000, "TOPUP_MANUAL")
        # Debit should succeed regardless of spend cap
        entry = await debit(ORG, 200, "USAGE_LLM")
        assert entry["amount_usd_cents"] == -200

    # ------------------------------------------------------------------
    # manual_topup idempotency
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_manual_topup_is_idempotent_on_ref_id(self, _clean_wallet_store):
        """Duplicate manual_topup calls with the same ref_id are no-ops after first."""
        from app.ee.billing.wallet import manual_topup, get_balance

        ref = "paystack_ref_xyz"
        first = await manual_topup(ORG, 5000, ref_id=ref)
        second = await manual_topup(ORG, 5000, ref_id=ref)

        assert first.get("skipped") is None  # first call credited wallet
        assert second.get("skipped") is True

        bal = await get_balance(ORG)
        assert bal["balance_usd_cents"] == 5000  # credited only once

    # ------------------------------------------------------------------
    # save_authorization
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_save_authorization_reusable(self, _clean_wallet_store):
        from app.ee.billing.wallet import save_authorization
        from app.ee.billing.wallet_store import get_wallet_store

        await save_authorization(ORG, {
            "authorization_code": "AUTH_abc123",
            "reusable": True,
            "last4": "4081",
            "exp_month": "06",
            "exp_year": "2029",
            "brand": "visa",
            "customer_email": "user@test.com",
            "customer_code": "CUS_xyz",
        })
        store = get_wallet_store()
        cfg = await store.get_topup_config(ORG)
        assert cfg["paystack_authorization_code"] == "AUTH_abc123"
        assert cfg["paystack_auth_reusable"] is True
        assert cfg["paystack_card_last4"] == "4081"

    @pytest.mark.asyncio
    async def test_save_authorization_non_reusable_skipped(self, _clean_wallet_store):
        from app.ee.billing.wallet import save_authorization
        from app.ee.billing.wallet_store import get_wallet_store

        await save_authorization(ORG, {
            "authorization_code": "AUTH_notreusable",
            "reusable": False,
        })
        store = get_wallet_store()
        cfg = await store.get_topup_config(ORG)
        # Should not have saved
        assert cfg.get("paystack_authorization_code") is None

    # ------------------------------------------------------------------
    # handle_webhook_charge_success
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_handle_webhook_charge_success_credits_wallet(self, _clean_wallet_store):
        from app.ee.billing.wallet import handle_webhook_charge_success, get_balance

        ref = f"nubi_auto_{uuid.uuid4().hex}"
        entry = await handle_webhook_charge_success(
            ORG, ref, 5000, {"topup_type": "auto"}
        )
        assert entry["entry_type"] == "TOPUP_AUTO"
        assert entry["amount_usd_cents"] == 5000

        bal = await get_balance(ORG)
        assert bal["balance_usd_cents"] == 5000

    @pytest.mark.asyncio
    async def test_handle_webhook_charge_success_idempotent(self, _clean_wallet_store):
        from app.ee.billing.wallet import handle_webhook_charge_success, get_balance

        ref = f"nubi_manual_{uuid.uuid4().hex}"
        first = await handle_webhook_charge_success(ORG, ref, 5000, {"topup_type": "manual"})
        second = await handle_webhook_charge_success(ORG, ref, 5000, {"topup_type": "manual"})

        assert first.get("skipped") is None
        assert second.get("skipped") is True

        bal = await get_balance(ORG)
        assert bal["balance_usd_cents"] == 5000  # only credited once

    # ------------------------------------------------------------------
    # Ledger filtering
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_list_ledger_by_entry_type(self, _clean_wallet_store):
        from app.ee.billing.wallet import credit, debit
        from app.ee.billing.wallet_store import get_wallet_store

        await credit(ORG, 10_000, "TOPUP_MANUAL")
        await debit(ORG, 100, "USAGE_LLM")
        await debit(ORG, 200, "USAGE_COMPUTE")
        await debit(ORG, 50, "USAGE_LLM")

        store = get_wallet_store()
        llm_entries = await store.list_ledger(ORG, entry_type="USAGE_LLM")
        assert len(llm_entries) == 2
        compute_entries = await store.list_ledger(ORG, entry_type="USAGE_COMPUTE")
        assert len(compute_entries) == 1

    @pytest.mark.asyncio
    async def test_credit_rejects_zero_amount(self):
        from app.ee.billing.wallet import credit

        with pytest.raises(ValueError, match="credit amount must be positive"):
            await credit(ORG, 0, "TOPUP_MANUAL")

    @pytest.mark.asyncio
    async def test_debit_rejects_zero_amount(self):
        from app.ee.billing.wallet import debit

        with pytest.raises(ValueError, match="debit amount must be positive"):
            await debit(ORG, 0, "USAGE_LLM")


# ===========================================================================
# Regression tests — confirmed billing bugs
# ===========================================================================


class TestPgWalletStoreSingleStatementSql:
    """PgWalletStore must never pass multi-command SQL to a prepared statement.

    asyncpg executes parameterised queries via the extended-query protocol and
    raises ``cannot insert multiple commands into a prepared statement`` for
    any multi-statement string.  These fakes enforce the same restriction.
    """

    @staticmethod
    def _assert_single_command(query: str) -> None:
        stripped = query.strip().rstrip(";")
        assert ";" not in stripped, (
            f"multi-command SQL passed to a prepared statement:\n{query}"
        )

    @pytest.fixture
    def _fake_db(self, monkeypatch):
        import app.db as db

        async def fake_fetchrow(query, *args):
            self._assert_single_command(query)
            return {"org_id": str(args[0]) if args else None, "balance_usd_cents": 0}

        async def fake_execute(query, *args):
            self._assert_single_command(query)
            return "INSERT 0 1"

        monkeypatch.setattr(db, "fetchrow", fake_fetchrow)
        monkeypatch.setattr(db, "execute", fake_execute)

    @pytest.mark.asyncio
    async def test_get_balance_is_single_statement(self, _fake_db):
        from app.ee.billing.wallet_store import PgWalletStore

        bal = await PgWalletStore().get_balance(ORG)
        assert bal["balance_usd_cents"] == 0

    @pytest.mark.asyncio
    async def test_get_topup_config_is_single_statement(self, _fake_db):
        from app.ee.billing.wallet_store import PgWalletStore

        cfg = await PgWalletStore().get_topup_config(ORG)
        assert cfg["org_id"] == ORG

    @pytest.mark.asyncio
    async def test_try_claim_topup_in_flight_is_single_statement(self, _fake_db):
        from app.ee.billing.wallet_store import PgWalletStore

        assert await PgWalletStore().try_claim_topup_in_flight(ORG) is True


class TestFxBufferOnTopups:
    """Wallet topup ZAR conversion must apply the canonical 2% FX buffer + ceil."""

    def test_usd_cents_to_zar_cents_applies_buffer(self, monkeypatch):
        from decimal import Decimal

        import app.ee.billing.fx as fxmod
        from app.ee.billing.wallet import _usd_cents_to_zar_cents

        monkeypatch.setattr(
            fxmod,
            "get_current_rate",
            lambda: {"rate": Decimal("16.26"), "fetched_at": None, "stale": False},
        )
        # 5000 * 16.26 * 1.02 = 82926 exactly
        assert _usd_cents_to_zar_cents(5000) == 82926
        # 333 * 16.26 * 1.02 = 5522.8716 → ceil → 5523
        assert _usd_cents_to_zar_cents(333) == 5523
        # 1 * 16.26 * 1.02 = 16.5852 → ceil → 17
        assert _usd_cents_to_zar_cents(1) == 17

    def test_conversion_matches_canonical_fx_formula(self, monkeypatch):
        from decimal import ROUND_CEILING, Decimal

        import app.ee.billing.fx as fxmod
        from app.ee.billing.fx import FX_BUFFER
        from app.ee.billing.wallet import _usd_cents_to_zar_cents

        rate = Decimal("18.4937")
        monkeypatch.setattr(
            fxmod,
            "get_current_rate",
            lambda: {"rate": rate, "fetched_at": None, "stale": False},
        )
        for usd_cents in (1, 999, 5000, 12345):
            expected = int(
                (Decimal(usd_cents) * rate * FX_BUFFER).to_integral_value(
                    rounding=ROUND_CEILING
                )
            )
            assert _usd_cents_to_zar_cents(usd_cents) == expected


class TestAutoTopupConcurrency:
    """The in-flight guard must be an atomic claim, not check-then-set."""

    @pytest.mark.asyncio
    async def test_concurrent_triggers_charge_card_only_once(self, _clean_wallet_store):
        """Two near-simultaneous topup triggers must produce exactly ONE charge."""
        from app.ee.billing.paystack import set_client_for_tests
        from app.ee.billing.wallet import trigger_auto_topup
        from app.ee.billing.wallet_store import (
            InMemoryWalletStore,
            set_wallet_store_for_tests,
        )

        class YieldingStore(InMemoryWalletStore):
            """InMemory store that yields to the event loop on every read —
            reproduces the suspension points a real DB roundtrip creates."""

            async def get_topup_config(self, org_id):
                await asyncio.sleep(0)
                return await super().get_topup_config(org_id)

            async def sum_auto_topups_this_month(self, org_id):
                await asyncio.sleep(0)
                return await super().sum_auto_topups_this_month(org_id)

            async def sum_credits_this_month(self, org_id):
                await asyncio.sleep(0)
                return await super().sum_credits_this_month(org_id)

        store = YieldingStore()
        set_wallet_store_for_tests(store)

        mock_client = _make_mock_paystack(status="success")
        set_client_for_tests(mock_client)
        await _seed_topup_config(store, ORG, topup_amount_usd_cents=5000)

        await asyncio.gather(trigger_auto_topup(ORG), trigger_auto_topup(ORG))

        assert mock_client.post.call_count == 1
        auto_entries = await store.list_ledger(ORG, entry_type="TOPUP_AUTO")
        assert len(auto_entries) == 1
        bal = await store.get_balance(ORG)
        assert bal["balance_usd_cents"] == 5000  # credited exactly once

    @pytest.mark.asyncio
    async def test_try_claim_is_exclusive_until_released(self, _clean_wallet_store):
        store = _clean_wallet_store
        assert await store.try_claim_topup_in_flight(ORG) is True
        assert await store.try_claim_topup_in_flight(ORG) is False  # held
        await store.set_topup_in_flight(ORG, False)
        assert await store.try_claim_topup_in_flight(ORG) is True  # released

    @pytest.mark.asyncio
    async def test_stale_claim_self_heals_after_ttl(self, _clean_wallet_store):
        """A claim abandoned by a crashed process is re-claimable after the TTL."""
        from datetime import timedelta

        from app.ee.billing.wallet_store import TOPUP_IN_FLIGHT_TTL_SECONDS

        store = _clean_wallet_store
        assert await store.try_claim_topup_in_flight(ORG) is True
        # Simulate a crash: flag stuck TRUE, claimed long ago.
        store._configs[ORG]["topup_in_flight_at"] = datetime.now(timezone.utc) - timedelta(
            seconds=TOPUP_IN_FLIGHT_TTL_SECONDS + 1
        )
        assert await store.try_claim_topup_in_flight(ORG) is True


class TestSpendCapEnforcement:
    """spend_cap_usd_cents must block auto-topups (it is the customer's hard stop)."""

    @pytest.mark.asyncio
    async def test_auto_topup_blocked_when_spend_cap_would_be_exceeded(
        self, _clean_wallet_store
    ):
        from app.ee.billing.paystack import set_client_for_tests
        from app.ee.billing.wallet import credit, debit

        mock_client = _make_mock_paystack(status="success")
        set_client_for_tests(mock_client)

        # Cap $60; $20 already credited this month; $50 topup would total $70 > cap.
        await _seed_topup_config(
            _clean_wallet_store, ORG,
            threshold_usd_cents=2000,
            topup_amount_usd_cents=5000,
            spend_cap_usd_cents=6000,
        )
        await credit(ORG, 2000, "TOPUP_MANUAL")
        await debit(ORG, 500, "USAGE_LLM")  # drops below threshold
        await asyncio.sleep(0.05)

        mock_client.post.assert_not_called()
        auto_entries = await _clean_wallet_store.list_ledger(ORG, entry_type="TOPUP_AUTO")
        assert len(auto_entries) == 0

    @pytest.mark.asyncio
    async def test_auto_topup_proceeds_under_spend_cap(self, _clean_wallet_store):
        from app.ee.billing.paystack import set_client_for_tests
        from app.ee.billing.wallet import credit, debit

        mock_client = _make_mock_paystack(status="success")
        set_client_for_tests(mock_client)

        # Cap $100; $20 credited + $50 topup = $70 ≤ cap — allowed.
        await _seed_topup_config(
            _clean_wallet_store, ORG,
            threshold_usd_cents=2000,
            topup_amount_usd_cents=5000,
            spend_cap_usd_cents=10000,
        )
        await credit(ORG, 2000, "TOPUP_MANUAL")
        await debit(ORG, 500, "USAGE_LLM")
        await asyncio.sleep(0.05)

        auto_entries = await _clean_wallet_store.list_ledger(ORG, entry_type="TOPUP_AUTO")
        assert len(auto_entries) == 1


class TestTopupFailedRefIdNotPoisoning:
    """TOPUP_FAILED rows must not block a later successful credit for the same ref."""

    @pytest.mark.asyncio
    async def test_ledger_ref_exists_ignores_topup_failed(self, _clean_wallet_store):
        store = _clean_wallet_store
        await store.append_ledger(
            ORG,
            entry_type="TOPUP_FAILED",
            amount_usd_cents=0,
            balance_after_usd_cents=0,
            description="Auto-topup exception: timeout",
            ref_id="nubi_auto_timeout_ref",
        )
        assert await store.ledger_ref_exists("nubi_auto_timeout_ref") is False

    @pytest.mark.asyncio
    async def test_webhook_credits_charge_that_failed_locally(self, _clean_wallet_store):
        """Timeout after Paystack success: TOPUP_FAILED written locally, then the
        charge.success webhook arrives for the same reference — must credit."""
        from app.ee.billing.wallet import get_balance, handle_webhook_charge_success

        ref = f"nubi_auto_{uuid.uuid4().hex}"
        await _clean_wallet_store.append_ledger(
            ORG,
            entry_type="TOPUP_FAILED",
            amount_usd_cents=0,
            balance_after_usd_cents=0,
            description="Auto-topup exception: network timeout",
            ref_id=ref,
        )

        entry = await handle_webhook_charge_success(ORG, ref, 5000, {"topup_type": "auto"})
        assert entry.get("skipped") is None
        assert entry["amount_usd_cents"] == 5000

        bal = await get_balance(ORG)
        assert bal["balance_usd_cents"] == 5000

        # A retried webhook delivery is still deduped on the successful row.
        second = await handle_webhook_charge_success(ORG, ref, 5000, {"topup_type": "auto"})
        assert second.get("skipped") is True
        bal = await get_balance(ORG)
        assert bal["balance_usd_cents"] == 5000


class TestDebitIdempotency:
    """debit() must be idempotent on ref_id — a retried billing cycle never
    double-draws the same period's overage."""

    @pytest.mark.asyncio
    async def test_debit_skips_duplicate_ref_id(self, _clean_wallet_store):
        from app.ee.billing.wallet import credit, debit, get_balance

        await credit(ORG, 10_000, "TOPUP_MANUAL")
        ref = f"overage-{ORG}-202606"

        first = await debit(ORG, 2_000, "USAGE_OVERAGE", ref_id=ref)
        second = await debit(ORG, 2_000, "USAGE_OVERAGE", ref_id=ref)

        assert first.get("skipped") is None
        assert second == {"skipped": True, "ref_id": ref}

        bal = await get_balance(ORG)
        assert bal["balance_usd_cents"] == 8_000  # drawn exactly once

    @pytest.mark.asyncio
    async def test_debit_without_ref_id_is_not_deduped(self, _clean_wallet_store):
        from app.ee.billing.wallet import credit, debit, get_balance

        await credit(ORG, 10_000, "TOPUP_MANUAL")
        await debit(ORG, 1_000, "USAGE_LLM")
        await debit(ORG, 1_000, "USAGE_LLM")
        bal = await get_balance(ORG)
        assert bal["balance_usd_cents"] == 8_000


class TestAtomicBalanceLedger:
    """Balance mutation + ledger append happen via single atomic store ops."""

    @pytest.mark.asyncio
    async def test_credit_with_ledger_keeps_invariant(self, _clean_wallet_store):
        store = _clean_wallet_store
        entry = await store.credit_with_ledger(
            ORG, 5000, entry_type="TOPUP_MANUAL", description="topup"
        )
        assert entry["balance_after_usd_cents"] == 5000
        bal = await store.get_balance(ORG)
        ledger = await store.list_ledger(ORG)
        assert bal["balance_usd_cents"] == sum(e["amount_usd_cents"] for e in ledger)

    @pytest.mark.asyncio
    async def test_debit_with_ledger_raises_on_insufficient(self, _clean_wallet_store):
        store = _clean_wallet_store
        await store.credit_with_ledger(ORG, 100, entry_type="TOPUP_MANUAL")
        with pytest.raises(ValueError, match="Insufficient wallet balance"):
            await store.debit_with_ledger(ORG, 500, entry_type="USAGE_LLM")
        # Failed debit must leave no ledger row and an unchanged balance.
        bal = await store.get_balance(ORG)
        assert bal["balance_usd_cents"] == 100
        ledger = await store.list_ledger(ORG)
        assert len(ledger) == 1

    @pytest.mark.asyncio
    async def test_debit_race_surfaces_structured_error(self, _clean_wallet_store, monkeypatch):
        """If a concurrent debit wins between the pre-check and the guarded
        UPDATE, callers get WalletInsufficientError — never a bare ValueError."""
        from app.ee.billing.wallet import WalletInsufficientError, credit, debit

        await credit(ORG, 1_000, "TOPUP_MANUAL")

        async def race_losing_debit(*args, **kwargs):
            raise ValueError("Insufficient wallet balance for org: need 1000 cents")

        monkeypatch.setattr(_clean_wallet_store, "debit_with_ledger", race_losing_debit)
        with pytest.raises(WalletInsufficientError) as exc_info:
            await debit(ORG, 1_000, "USAGE_COMPUTE")
        assert exc_info.value.detail["error"] == "wallet_balance_insufficient"
