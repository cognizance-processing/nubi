"""Wallet storage — dual InMemory + Pg pattern.

Tracks per-organisation wallet balance, the append-only ledger of credits and
debits, and the auto-topup configuration (thresholds, saved card details).

Two implementations
-------------------
PgWalletStore         — asyncpg-backed; reads/writes the tables created by
                        migration 0022_wallet.sql.
InMemoryWalletStore   — dict-backed; used in tests (no DB required).

Provider
--------
The module-level singleton is obtained via :func:`get_wallet_store`.
Tests swap in an :class:`InMemoryWalletStore` via
:func:`set_wallet_store_for_tests`.

Ledger entry shape
------------------
``{
    id: str (uuid),
    org_id: str,
    entry_type: str,          # one of ENTRY_TYPES
    amount_usd_cents: int,    # positive = credit, negative = debit
    balance_after_usd_cents: int,
    description: str | None,
    ref_id: str | None,
    metadata: dict | None,
    created_at: datetime,
}``

WalletTopupConfig shape
-----------------------
``{
    org_id: str,
    auto_topup_enabled: bool,
    threshold_usd_cents: int,
    topup_amount_usd_cents: int,
    monthly_topup_cap_usd_cents: int | None,
    spend_cap_usd_cents: int | None,
    topup_in_flight: bool,
    paystack_authorization_code: str | None,
    paystack_customer_email: str | None,
    paystack_customer_code: str | None,
    paystack_card_last4: str | None,
    paystack_card_brand: str | None,
    paystack_card_exp_month: str | None,
    paystack_card_exp_year: str | None,
    paystack_auth_reusable: bool,
    created_at: datetime,
    updated_at: datetime,
}``
"""

from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Entry type constants
# ---------------------------------------------------------------------------

ENTRY_TYPES = {
    "TOPUP_MANUAL",
    "TOPUP_AUTO",
    "TOPUP_PROMO",
    "TOPUP_FAILED",
    "USAGE_LLM",
    "USAGE_STORAGE",
    "USAGE_COMPUTE",
    "USAGE_EMBED",
    "USAGE_OVERAGE",
    "ADJUSTMENT_CREDIT",
    "ADJUSTMENT_DEBIT",
    "EXPIRY",
}

# Type aliases
LedgerEntry = dict[str, Any]
WalletBalance = dict[str, Any]
WalletTopupConfig = dict[str, Any]


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------


class WalletStore:
    """Interface for wallet storage."""

    # ------------------------------------------------------------------
    # Balance
    # ------------------------------------------------------------------

    async def get_balance(self, org_id: str) -> WalletBalance:
        """Return the wallet balance record for *org_id*.

        Returns a dict with at least ``balance_usd_cents``.  Creates the record
        with zero balance if it doesn't exist.
        """
        raise NotImplementedError

    async def set_balance(self, org_id: str, balance_usd_cents: int) -> WalletBalance:
        """Atomically set (overwrite) the balance.

        Callers should NOT call this directly — use :meth:`credit_balance` and
        :meth:`debit_balance` for safe atomic mutations.
        """
        raise NotImplementedError

    async def credit_balance(
        self, org_id: str, amount_usd_cents: int
    ) -> int:
        """Add *amount_usd_cents* to the balance and return the new balance."""
        raise NotImplementedError

    async def debit_balance(
        self, org_id: str, amount_usd_cents: int
    ) -> int:
        """Subtract *amount_usd_cents* from the balance and return the new balance.

        Raises :class:`ValueError` if the resulting balance would go negative.
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Ledger
    # ------------------------------------------------------------------

    async def append_ledger(
        self,
        org_id: str,
        *,
        entry_type: str,
        amount_usd_cents: int,
        balance_after_usd_cents: int,
        description: str | None = None,
        ref_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LedgerEntry:
        """Append one immutable ledger row and return it."""
        raise NotImplementedError

    async def list_ledger(
        self,
        org_id: str,
        *,
        limit: int = 50,
        entry_type: str | None = None,
    ) -> list[LedgerEntry]:
        """Return ledger entries for *org_id*, newest first."""
        raise NotImplementedError

    async def sum_credits_this_month(self, org_id: str) -> int:
        """Return the total credits (TOPUP_*) added this calendar month in USD cents."""
        raise NotImplementedError

    async def sum_auto_topups_this_month(self, org_id: str) -> int:
        """Return the total of TOPUP_AUTO credits this calendar month in USD cents."""
        raise NotImplementedError

    async def ledger_ref_exists(self, ref_id: str) -> bool:
        """Return True if *ref_id* already exists in the ledger (idempotency guard)."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Topup config
    # ------------------------------------------------------------------

    async def get_topup_config(self, org_id: str) -> WalletTopupConfig:
        """Return the topup config for *org_id*.  Creates a default record if absent."""
        raise NotImplementedError

    async def upsert_topup_config(
        self,
        org_id: str,
        *,
        auto_topup_enabled: bool | None = None,
        threshold_usd_cents: int | None = None,
        topup_amount_usd_cents: int | None = None,
        monthly_topup_cap_usd_cents: int | None = None,
        spend_cap_usd_cents: int | None = None,
        paystack_authorization_code: str | None = None,
        paystack_customer_email: str | None = None,
        paystack_customer_code: str | None = None,
        paystack_card_last4: str | None = None,
        paystack_card_brand: str | None = None,
        paystack_card_exp_month: str | None = None,
        paystack_card_exp_year: str | None = None,
        paystack_auth_reusable: bool | None = None,
    ) -> WalletTopupConfig:
        """Create or patch the topup config; only non-None kwargs overwrite."""
        raise NotImplementedError

    async def set_topup_in_flight(self, org_id: str, in_flight: bool) -> None:
        """Set/clear the idempotency lock flag on the topup config."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# In-memory implementation (tests)
# ---------------------------------------------------------------------------


class InMemoryWalletStore(WalletStore):
    """Dict-backed wallet store for tests.

    Usage::

        from app.ee.billing.wallet_store import InMemoryWalletStore, set_wallet_store_for_tests
        store = InMemoryWalletStore()
        set_wallet_store_for_tests(store)
    """

    def __init__(self) -> None:
        self._balances: dict[str, int] = {}          # org_id -> balance_usd_cents
        self._ledger: dict[str, list[LedgerEntry]] = {}
        self._configs: dict[str, WalletTopupConfig] = {}

    def reset(self) -> None:
        """Clear all stored state."""
        self._balances.clear()
        self._ledger.clear()
        self._configs.clear()

    # ------------------------------------------------------------------
    # Balance
    # ------------------------------------------------------------------

    async def get_balance(self, org_id: str) -> WalletBalance:
        key = str(org_id)
        return {
            "org_id": key,
            "balance_usd_cents": self._balances.get(key, 0),
            "balance_zar_cents": 0,
            "last_fx_rate": None,
            "last_fx_at": None,
        }

    async def set_balance(self, org_id: str, balance_usd_cents: int) -> WalletBalance:
        self._balances[str(org_id)] = balance_usd_cents
        return await self.get_balance(org_id)

    async def credit_balance(self, org_id: str, amount_usd_cents: int) -> int:
        key = str(org_id)
        self._balances[key] = self._balances.get(key, 0) + amount_usd_cents
        return self._balances[key]

    async def debit_balance(self, org_id: str, amount_usd_cents: int) -> int:
        key = str(org_id)
        current = self._balances.get(key, 0)
        if current < amount_usd_cents:
            raise ValueError(
                f"Insufficient wallet balance: have {current} cents, need {amount_usd_cents} cents"
            )
        self._balances[key] = current - amount_usd_cents
        return self._balances[key]

    # ------------------------------------------------------------------
    # Ledger
    # ------------------------------------------------------------------

    async def append_ledger(
        self,
        org_id: str,
        *,
        entry_type: str,
        amount_usd_cents: int,
        balance_after_usd_cents: int,
        description: str | None = None,
        ref_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LedgerEntry:
        key = str(org_id)
        entry: LedgerEntry = {
            "id": str(uuid.uuid4()),
            "org_id": key,
            "entry_type": entry_type,
            "amount_usd_cents": amount_usd_cents,
            "balance_after_usd_cents": balance_after_usd_cents,
            "description": description,
            "ref_id": ref_id,
            "metadata": deepcopy(metadata) if metadata else None,
            "created_at": datetime.now(timezone.utc),
        }
        if key not in self._ledger:
            self._ledger[key] = []
        self._ledger[key].append(entry)
        return deepcopy(entry)

    async def list_ledger(
        self,
        org_id: str,
        *,
        limit: int = 50,
        entry_type: str | None = None,
    ) -> list[LedgerEntry]:
        rows = self._ledger.get(str(org_id), [])
        if entry_type:
            rows = [r for r in rows if r["entry_type"] == entry_type]
        return deepcopy(rows[-limit:][::-1])

    async def sum_credits_this_month(self, org_id: str) -> int:
        now = datetime.now(timezone.utc)
        rows = self._ledger.get(str(org_id), [])
        total = 0
        for r in rows:
            if (
                r["entry_type"].startswith("TOPUP_")
                and r["entry_type"] != "TOPUP_FAILED"
                and r["amount_usd_cents"] > 0
                and r["created_at"].year == now.year
                and r["created_at"].month == now.month
            ):
                total += r["amount_usd_cents"]
        return total

    async def sum_auto_topups_this_month(self, org_id: str) -> int:
        now = datetime.now(timezone.utc)
        rows = self._ledger.get(str(org_id), [])
        total = 0
        for r in rows:
            if (
                r["entry_type"] == "TOPUP_AUTO"
                and r["amount_usd_cents"] > 0
                and r["created_at"].year == now.year
                and r["created_at"].month == now.month
            ):
                total += r["amount_usd_cents"]
        return total

    async def ledger_ref_exists(self, ref_id: str) -> bool:
        for entries in self._ledger.values():
            for e in entries:
                if e["ref_id"] == ref_id:
                    return True
        return False

    # ------------------------------------------------------------------
    # Topup config
    # ------------------------------------------------------------------

    def _default_config(self, org_id: str) -> WalletTopupConfig:
        now = datetime.now(timezone.utc)
        return {
            "org_id": str(org_id),
            "auto_topup_enabled": False,
            "threshold_usd_cents": 1000,
            "topup_amount_usd_cents": 5000,
            "monthly_topup_cap_usd_cents": None,
            "spend_cap_usd_cents": None,
            "topup_in_flight": False,
            "paystack_authorization_code": None,
            "paystack_customer_email": None,
            "paystack_customer_code": None,
            "paystack_card_last4": None,
            "paystack_card_brand": None,
            "paystack_card_exp_month": None,
            "paystack_card_exp_year": None,
            "paystack_auth_reusable": False,
            "created_at": now,
            "updated_at": now,
        }

    async def get_topup_config(self, org_id: str) -> WalletTopupConfig:
        key = str(org_id)
        if key not in self._configs:
            self._configs[key] = self._default_config(key)
        return deepcopy(self._configs[key])

    async def upsert_topup_config(
        self,
        org_id: str,
        *,
        auto_topup_enabled: bool | None = None,
        threshold_usd_cents: int | None = None,
        topup_amount_usd_cents: int | None = None,
        monthly_topup_cap_usd_cents: int | None = None,
        spend_cap_usd_cents: int | None = None,
        paystack_authorization_code: str | None = None,
        paystack_customer_email: str | None = None,
        paystack_customer_code: str | None = None,
        paystack_card_last4: str | None = None,
        paystack_card_brand: str | None = None,
        paystack_card_exp_month: str | None = None,
        paystack_card_exp_year: str | None = None,
        paystack_auth_reusable: bool | None = None,
    ) -> WalletTopupConfig:
        key = str(org_id)
        if key not in self._configs:
            self._configs[key] = self._default_config(key)
        cfg = self._configs[key]
        # Only overwrite explicitly provided kwargs (not None means "update this field")
        update_fields = {
            "auto_topup_enabled": auto_topup_enabled,
            "threshold_usd_cents": threshold_usd_cents,
            "topup_amount_usd_cents": topup_amount_usd_cents,
            "monthly_topup_cap_usd_cents": monthly_topup_cap_usd_cents,
            "spend_cap_usd_cents": spend_cap_usd_cents,
            "paystack_authorization_code": paystack_authorization_code,
            "paystack_customer_email": paystack_customer_email,
            "paystack_customer_code": paystack_customer_code,
            "paystack_card_last4": paystack_card_last4,
            "paystack_card_brand": paystack_card_brand,
            "paystack_card_exp_month": paystack_card_exp_month,
            "paystack_card_exp_year": paystack_card_exp_year,
            "paystack_auth_reusable": paystack_auth_reusable,
        }
        for field, val in update_fields.items():
            if val is not None:
                cfg[field] = val
        cfg["updated_at"] = datetime.now(timezone.utc)
        return deepcopy(cfg)

    async def set_topup_in_flight(self, org_id: str, in_flight: bool) -> None:
        key = str(org_id)
        if key not in self._configs:
            self._configs[key] = self._default_config(key)
        self._configs[key]["topup_in_flight"] = in_flight
        self._configs[key]["updated_at"] = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Postgres implementation
# ---------------------------------------------------------------------------


class PgWalletStore(WalletStore):
    """asyncpg-backed wallet store using the tables from migration 0022."""

    # ------------------------------------------------------------------
    # Balance
    # ------------------------------------------------------------------

    async def get_balance(self, org_id: str) -> WalletBalance:
        from app.db import fetchrow  # noqa: PLC0415

        row = await fetchrow(
            """
            INSERT INTO wallet_balance (org_id)
            VALUES ($1::uuid)
            ON CONFLICT (org_id) DO NOTHING;

            SELECT org_id::text, balance_usd_cents, balance_zar_cents,
                   last_fx_rate, last_fx_at, created_at, updated_at
            FROM wallet_balance
            WHERE org_id = $1::uuid
            """,
            org_id,
        )
        return dict(row) if row else {"org_id": org_id, "balance_usd_cents": 0}

    async def set_balance(self, org_id: str, balance_usd_cents: int) -> WalletBalance:
        from app.db import fetchrow  # noqa: PLC0415

        row = await fetchrow(
            """
            INSERT INTO wallet_balance (org_id, balance_usd_cents)
            VALUES ($1::uuid, $2)
            ON CONFLICT (org_id) DO UPDATE
                SET balance_usd_cents = EXCLUDED.balance_usd_cents,
                    updated_at = NOW()
            RETURNING org_id::text, balance_usd_cents, balance_zar_cents,
                      last_fx_rate, last_fx_at, created_at, updated_at
            """,
            org_id,
            balance_usd_cents,
        )
        return dict(row)  # type: ignore[arg-type]

    async def credit_balance(self, org_id: str, amount_usd_cents: int) -> int:
        from app.db import fetchrow  # noqa: PLC0415

        row = await fetchrow(
            """
            INSERT INTO wallet_balance (org_id, balance_usd_cents)
            VALUES ($1::uuid, $2)
            ON CONFLICT (org_id) DO UPDATE
                SET balance_usd_cents = wallet_balance.balance_usd_cents + EXCLUDED.balance_usd_cents,
                    updated_at = NOW()
            RETURNING balance_usd_cents
            """,
            org_id,
            amount_usd_cents,
        )
        return row["balance_usd_cents"]  # type: ignore[index]

    async def debit_balance(self, org_id: str, amount_usd_cents: int) -> int:
        from app.db import fetchrow  # noqa: PLC0415

        row = await fetchrow(
            """
            UPDATE wallet_balance
            SET balance_usd_cents = balance_usd_cents - $2,
                updated_at = NOW()
            WHERE org_id = $1::uuid
              AND balance_usd_cents >= $2
            RETURNING balance_usd_cents
            """,
            org_id,
            amount_usd_cents,
        )
        if row is None:
            raise ValueError(
                f"Insufficient wallet balance for org {org_id}: need {amount_usd_cents} cents"
            )
        return row["balance_usd_cents"]  # type: ignore[index]

    # ------------------------------------------------------------------
    # Ledger
    # ------------------------------------------------------------------

    async def append_ledger(
        self,
        org_id: str,
        *,
        entry_type: str,
        amount_usd_cents: int,
        balance_after_usd_cents: int,
        description: str | None = None,
        ref_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LedgerEntry:
        import json  # noqa: PLC0415

        from app.db import fetchrow  # noqa: PLC0415

        row = await fetchrow(
            """
            INSERT INTO wallet_ledger
                (org_id, entry_type, amount_usd_cents, balance_after_usd_cents,
                 description, ref_id, metadata)
            VALUES
                ($1::uuid, $2::wallet_entry_type, $3, $4, $5, $6, $7::jsonb)
            RETURNING id::text, org_id::text, entry_type::text,
                      amount_usd_cents, balance_after_usd_cents,
                      description, ref_id, metadata, created_at
            """,
            org_id,
            entry_type,
            amount_usd_cents,
            balance_after_usd_cents,
            description,
            ref_id,
            json.dumps(metadata) if metadata else None,
        )
        return dict(row)  # type: ignore[arg-type]

    async def list_ledger(
        self,
        org_id: str,
        *,
        limit: int = 50,
        entry_type: str | None = None,
    ) -> list[LedgerEntry]:
        from app.db import fetch  # noqa: PLC0415

        if entry_type:
            rows = await fetch(
                """
                SELECT id::text, org_id::text, entry_type::text,
                       amount_usd_cents, balance_after_usd_cents,
                       description, ref_id, metadata, created_at
                FROM wallet_ledger
                WHERE org_id = $1::uuid AND entry_type = $2::wallet_entry_type
                ORDER BY created_at DESC
                LIMIT $3
                """,
                org_id,
                entry_type,
                limit,
            )
        else:
            rows = await fetch(
                """
                SELECT id::text, org_id::text, entry_type::text,
                       amount_usd_cents, balance_after_usd_cents,
                       description, ref_id, metadata, created_at
                FROM wallet_ledger
                WHERE org_id = $1::uuid
                ORDER BY created_at DESC
                LIMIT $2
                """,
                org_id,
                limit,
            )
        return [dict(r) for r in rows]

    async def sum_credits_this_month(self, org_id: str) -> int:
        from app.db import fetchrow  # noqa: PLC0415

        row = await fetchrow(
            """
            SELECT COALESCE(SUM(amount_usd_cents), 0) AS total
            FROM wallet_ledger
            WHERE org_id = $1::uuid
              AND entry_type IN ('TOPUP_MANUAL', 'TOPUP_AUTO', 'TOPUP_PROMO')
              AND amount_usd_cents > 0
              AND date_trunc('month', created_at) = date_trunc('month', NOW())
            """,
            org_id,
        )
        return int(row["total"])  # type: ignore[index]

    async def sum_auto_topups_this_month(self, org_id: str) -> int:
        from app.db import fetchrow  # noqa: PLC0415

        row = await fetchrow(
            """
            SELECT COALESCE(SUM(amount_usd_cents), 0) AS total
            FROM wallet_ledger
            WHERE org_id = $1::uuid
              AND entry_type = 'TOPUP_AUTO'
              AND amount_usd_cents > 0
              AND date_trunc('month', created_at) = date_trunc('month', NOW())
            """,
            org_id,
        )
        return int(row["total"])  # type: ignore[index]

    async def ledger_ref_exists(self, ref_id: str) -> bool:
        from app.db import fetchrow  # noqa: PLC0415

        row = await fetchrow(
            "SELECT 1 FROM wallet_ledger WHERE ref_id = $1 LIMIT 1",
            ref_id,
        )
        return row is not None

    # ------------------------------------------------------------------
    # Topup config
    # ------------------------------------------------------------------

    async def get_topup_config(self, org_id: str) -> WalletTopupConfig:
        from app.db import fetchrow  # noqa: PLC0415

        row = await fetchrow(
            """
            INSERT INTO wallet_topup_config (org_id)
            VALUES ($1::uuid)
            ON CONFLICT (org_id) DO NOTHING;

            SELECT org_id::text, auto_topup_enabled,
                   threshold_usd_cents, topup_amount_usd_cents,
                   monthly_topup_cap_usd_cents, spend_cap_usd_cents,
                   topup_in_flight,
                   paystack_authorization_code, paystack_customer_email,
                   paystack_customer_code, paystack_card_last4,
                   paystack_card_brand, paystack_card_exp_month,
                   paystack_card_exp_year, paystack_auth_reusable,
                   created_at, updated_at
            FROM wallet_topup_config
            WHERE org_id = $1::uuid
            """,
            org_id,
        )
        return dict(row) if row else {"org_id": org_id, "auto_topup_enabled": False}

    async def upsert_topup_config(
        self,
        org_id: str,
        *,
        auto_topup_enabled: bool | None = None,
        threshold_usd_cents: int | None = None,
        topup_amount_usd_cents: int | None = None,
        monthly_topup_cap_usd_cents: int | None = None,
        spend_cap_usd_cents: int | None = None,
        paystack_authorization_code: str | None = None,
        paystack_customer_email: str | None = None,
        paystack_customer_code: str | None = None,
        paystack_card_last4: str | None = None,
        paystack_card_brand: str | None = None,
        paystack_card_exp_month: str | None = None,
        paystack_card_exp_year: str | None = None,
        paystack_auth_reusable: bool | None = None,
    ) -> WalletTopupConfig:
        from app.db import fetchrow  # noqa: PLC0415

        # Fetch current config then merge (only overwrite non-None fields).
        current = await self.get_topup_config(org_id)
        merged = {
            "auto_topup_enabled": auto_topup_enabled
            if auto_topup_enabled is not None
            else current.get("auto_topup_enabled", False),
            "threshold_usd_cents": threshold_usd_cents
            if threshold_usd_cents is not None
            else current.get("threshold_usd_cents", 1000),
            "topup_amount_usd_cents": topup_amount_usd_cents
            if topup_amount_usd_cents is not None
            else current.get("topup_amount_usd_cents", 5000),
            "monthly_topup_cap_usd_cents": monthly_topup_cap_usd_cents
            if monthly_topup_cap_usd_cents is not None
            else current.get("monthly_topup_cap_usd_cents"),
            "spend_cap_usd_cents": spend_cap_usd_cents
            if spend_cap_usd_cents is not None
            else current.get("spend_cap_usd_cents"),
            "paystack_authorization_code": paystack_authorization_code
            if paystack_authorization_code is not None
            else current.get("paystack_authorization_code"),
            "paystack_customer_email": paystack_customer_email
            if paystack_customer_email is not None
            else current.get("paystack_customer_email"),
            "paystack_customer_code": paystack_customer_code
            if paystack_customer_code is not None
            else current.get("paystack_customer_code"),
            "paystack_card_last4": paystack_card_last4
            if paystack_card_last4 is not None
            else current.get("paystack_card_last4"),
            "paystack_card_brand": paystack_card_brand
            if paystack_card_brand is not None
            else current.get("paystack_card_brand"),
            "paystack_card_exp_month": paystack_card_exp_month
            if paystack_card_exp_month is not None
            else current.get("paystack_card_exp_month"),
            "paystack_card_exp_year": paystack_card_exp_year
            if paystack_card_exp_year is not None
            else current.get("paystack_card_exp_year"),
            "paystack_auth_reusable": paystack_auth_reusable
            if paystack_auth_reusable is not None
            else current.get("paystack_auth_reusable", False),
        }
        row = await fetchrow(
            """
            INSERT INTO wallet_topup_config (
                org_id, auto_topup_enabled,
                threshold_usd_cents, topup_amount_usd_cents,
                monthly_topup_cap_usd_cents, spend_cap_usd_cents,
                paystack_authorization_code, paystack_customer_email,
                paystack_customer_code, paystack_card_last4,
                paystack_card_brand, paystack_card_exp_month,
                paystack_card_exp_year, paystack_auth_reusable
            ) VALUES (
                $1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14
            )
            ON CONFLICT (org_id) DO UPDATE SET
                auto_topup_enabled           = EXCLUDED.auto_topup_enabled,
                threshold_usd_cents          = EXCLUDED.threshold_usd_cents,
                topup_amount_usd_cents       = EXCLUDED.topup_amount_usd_cents,
                monthly_topup_cap_usd_cents  = EXCLUDED.monthly_topup_cap_usd_cents,
                spend_cap_usd_cents          = EXCLUDED.spend_cap_usd_cents,
                paystack_authorization_code  = EXCLUDED.paystack_authorization_code,
                paystack_customer_email      = EXCLUDED.paystack_customer_email,
                paystack_customer_code       = EXCLUDED.paystack_customer_code,
                paystack_card_last4          = EXCLUDED.paystack_card_last4,
                paystack_card_brand          = EXCLUDED.paystack_card_brand,
                paystack_card_exp_month      = EXCLUDED.paystack_card_exp_month,
                paystack_card_exp_year       = EXCLUDED.paystack_card_exp_year,
                paystack_auth_reusable       = EXCLUDED.paystack_auth_reusable,
                updated_at                   = NOW()
            RETURNING org_id::text, auto_topup_enabled,
                      threshold_usd_cents, topup_amount_usd_cents,
                      monthly_topup_cap_usd_cents, spend_cap_usd_cents,
                      topup_in_flight,
                      paystack_authorization_code, paystack_customer_email,
                      paystack_customer_code, paystack_card_last4,
                      paystack_card_brand, paystack_card_exp_month,
                      paystack_card_exp_year, paystack_auth_reusable,
                      created_at, updated_at
            """,
            org_id,
            merged["auto_topup_enabled"],
            merged["threshold_usd_cents"],
            merged["topup_amount_usd_cents"],
            merged["monthly_topup_cap_usd_cents"],
            merged["spend_cap_usd_cents"],
            merged["paystack_authorization_code"],
            merged["paystack_customer_email"],
            merged["paystack_customer_code"],
            merged["paystack_card_last4"],
            merged["paystack_card_brand"],
            merged["paystack_card_exp_month"],
            merged["paystack_card_exp_year"],
            merged["paystack_auth_reusable"],
        )
        return dict(row)  # type: ignore[arg-type]

    async def set_topup_in_flight(self, org_id: str, in_flight: bool) -> None:
        from app.db import execute  # noqa: PLC0415

        await execute(
            """
            UPDATE wallet_topup_config
            SET topup_in_flight = $2, updated_at = NOW()
            WHERE org_id = $1::uuid
            """,
            org_id,
            in_flight,
        )


# ---------------------------------------------------------------------------
# Provider singleton
# ---------------------------------------------------------------------------

_wallet_store: WalletStore | None = None


def set_wallet_store_for_tests(store: WalletStore | None) -> None:
    """Inject a test double or reset to the default :class:`PgWalletStore`.

    Parameters
    ----------
    store:
        An :class:`InMemoryWalletStore` instance for tests, or ``None``
        to restore the default production store.
    """
    global _wallet_store  # noqa: PLW0603
    _wallet_store = store


def get_wallet_store() -> WalletStore:
    """Return the active :class:`WalletStore` singleton.

    Lazily instantiates a :class:`PgWalletStore` on first call if no
    override has been set via :func:`set_wallet_store_for_tests`.
    """
    global _wallet_store  # noqa: PLW0603
    if _wallet_store is None:
        _wallet_store = PgWalletStore()
    return _wallet_store
