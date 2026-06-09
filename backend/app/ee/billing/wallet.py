"""Wallet service — credit / debit / auto-topup logic for Nubi EE billing.

This module is the ONLY place in EE code that mutates wallet balance + ledger.
It is never imported by core (open-source) code.

Public API
----------
get_balance(org_id)
    Return the current wallet balance record.

credit(org_id, amount_usd_cents, entry_type, *, description, ref_id, metadata)
    Add credits to the wallet (manual topup, promo, etc.).

debit(org_id, amount_usd_cents, entry_type, *, description, ref_id, metadata)
    Deduct usage from the wallet.  Enforces:
      - Hard stop if balance == 0 and usage exceeds tier's included quota.
      - Idempotency on ref_id (a retried billing cycle never double-draws).
      - Triggers auto-topup when balance < threshold (async, non-blocking).
    The monthly spend cap applies to incoming topups, not debits — it is
    enforced in the auto-topup path.

manual_topup(org_id, amount_usd_cents, *, ref_id, description)
    Record a user-initiated topup already confirmed by Paystack webhook.

save_authorization(org_id, authorization_data)
    Save a Paystack card authorization after first successful payment.

trigger_auto_topup(org_id)
    Attempt to charge the saved card.  Idempotency-guarded.

handle_webhook_charge_success(org_id, ref_id, amount_usd_cents, metadata)
    Idempotent handler called by the webhook route for ``charge.success`` events
    that carry topup_type == "auto" or "manual".

Error shapes
------------
When a hard stop is reached, callers receive a :class:`WalletInsufficientError`
(subclass of ValueError) with a ``detail`` dict suitable for JSON responses.

Design notes
------------
- All monetary amounts are USD cents (integer).
- ZAR conversion (for Paystack charge) uses :func:`app.ee.billing.fx.get_current_rate`
  at charge time, never at storage time.
- Auto-topup is *enqueued* (fire-and-forget coroutine) — the calling debit
  path is never blocked waiting for a Paystack API call.  A strong reference
  to the task is held until it completes.
- The ``topup_in_flight`` flag prevents concurrent auto-topup charges; it is
  claimed atomically (compare-and-set) and a stale claim self-heals after a TTL.
- Balance mutations and their ledger rows are written atomically (one
  transaction in the Pg store), so balance == sum(ledger) always holds.
- Balance is never negative (enforced by DB CHECK constraint and store).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from decimal import ROUND_CEILING, Decimal
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class WalletInsufficientError(ValueError):
    """Raised when a debit is blocked by zero balance or a spend cap.

    Attributes
    ----------
    detail:
        Dict suitable for use as a FastAPI JSON error body.
    """

    def __init__(
        self,
        message: str,
        *,
        balance_usd_cents: int,
        spend_cap_hit: bool = False,
    ) -> None:
        super().__init__(message)
        self.detail = {
            "error": "wallet_balance_insufficient",
            "message": message,
            "balance_usd_cents": balance_usd_cents,
            "spend_cap_hit": spend_cap_hit,
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _current_balance_cents(org_id: str) -> int:
    from app.ee.billing.wallet_store import get_wallet_store  # noqa: PLC0415

    record = await get_wallet_store().get_balance(org_id)
    return record.get("balance_usd_cents", 0)


async def _check_spend_cap(org_id: str, amount_usd_cents: int) -> None:
    """Raise :class:`WalletInsufficientError` if monthly spend cap would be exceeded."""
    from app.ee.billing.wallet_store import get_wallet_store  # noqa: PLC0415

    store = get_wallet_store()
    cfg = await store.get_topup_config(org_id)
    spend_cap = cfg.get("spend_cap_usd_cents")
    if spend_cap is None:
        return  # unlimited

    monthly_credits = await store.sum_credits_this_month(org_id)
    if monthly_credits + amount_usd_cents > spend_cap:
        balance = await _current_balance_cents(org_id)
        raise WalletInsufficientError(
            f"Monthly spend cap of ${spend_cap / 100:.2f} would be exceeded. "
            f"Spent ${monthly_credits / 100:.2f} this month.",
            balance_usd_cents=balance,
            spend_cap_hit=True,
        )


def _usd_cents_to_zar_cents(amount_usd_cents: int) -> int:
    """Convert USD cents to ZAR cents using the current FX rate + buffer.

    Applies the canonical pricing-policy formula ``zar = ceil(usd * rate * FX_BUFFER)``
    (see :mod:`app.ee.billing.fx`) so wallet topups are charged at the same
    buffered rate as every other ZAR charge path.
    """
    from app.ee.billing.fx import FX_BUFFER, get_current_rate  # noqa: PLC0415

    fx_info = get_current_rate()
    rate: Decimal = fx_info["rate"]
    if fx_info.get("stale"):
        logger.warning(
            "wallet: FX rate is stale (fetched_at=%s) — charging at last-known rate",
            fx_info.get("fetched_at"),
        )
    zar_cents = int(
        (Decimal(amount_usd_cents) * rate * FX_BUFFER).to_integral_value(rounding=ROUND_CEILING)
    )
    return max(zar_cents, 1)


# ---------------------------------------------------------------------------
# Auto-topup coroutine
# ---------------------------------------------------------------------------


async def _execute_auto_topup(org_id: str) -> None:
    """Charge the saved Paystack card for the configured topup amount.

    This coroutine is fire-and-forget — it does NOT raise; all errors are
    logged and written to the ledger as TOPUP_FAILED entries.

    Guard: atomically claims the ``topup_in_flight`` lock (compare-and-set in
    the store, so concurrent debits can never double-charge) and clears it on
    exit (success or failure).  A claim abandoned by a crashed process
    self-heals after ``TOPUP_IN_FLIGHT_TTL_SECONDS``.
    """
    from app.ee.billing.paystack import charge_saved_card  # noqa: PLC0415
    from app.ee.billing.wallet_store import get_wallet_store  # noqa: PLC0415

    store = get_wallet_store()

    cfg = await store.get_topup_config(org_id)

    if not cfg.get("auto_topup_enabled"):
        return
    if not cfg.get("paystack_auth_reusable"):
        logger.warning("wallet: auto-topup skipped for org=%s — no reusable card saved", org_id)
        return
    if not cfg.get("paystack_authorization_code"):
        return

    topup_amount = cfg["topup_amount_usd_cents"]
    auth_code = cfg["paystack_authorization_code"]
    customer_email = cfg["paystack_customer_email"]

    # --- Idempotency guard: atomically claim the in-flight lock ---
    # Must happen BEFORE the cap checks so two concurrent triggers cannot both
    # pass the caps and charge the card twice; the loser simply skips.
    if not await store.try_claim_topup_in_flight(org_id):
        logger.debug("wallet: auto-topup already in flight for org=%s, skipping", org_id)
        return

    ref_id = f"nubi_auto_{uuid.uuid4().hex}"

    try:
        # --- Monthly auto-topup cap guard ---
        monthly_auto = await store.sum_auto_topups_this_month(org_id)
        monthly_cap = cfg.get("monthly_topup_cap_usd_cents")
        if monthly_cap is not None and monthly_auto + topup_amount > monthly_cap:
            logger.info(
                "wallet: auto-topup blocked by monthly cap for org=%s "
                "(cap=%d, spent=%d, want=%d)",
                org_id,
                monthly_cap,
                monthly_auto,
                topup_amount,
            )
            return

        # --- Hard monthly spend cap guard ---
        try:
            await _check_spend_cap(org_id, topup_amount)
        except WalletInsufficientError as cap_exc:
            logger.info(
                "wallet: auto-topup blocked by spend cap for org=%s: %s",
                org_id,
                cap_exc,
            )
            return

        amount_zar_cents = _usd_cents_to_zar_cents(topup_amount)

        result = await charge_saved_card(
            authorization_code=auth_code,
            email=customer_email or "",
            amount_zar_cents=amount_zar_cents,
            reference=ref_id,
            metadata={
                "org_id": org_id,
                "topup_type": "auto",
                "topup_usd_cents": topup_amount,
            },
        )

        if result.get("data", {}).get("status") == "success":
            # Credit the wallet (balance + ledger in one atomic store operation)
            await store.credit_with_ledger(
                org_id,
                topup_amount,
                entry_type="TOPUP_AUTO",
                description="Auto-topup via saved card",
                ref_id=ref_id,
                metadata={
                    "paystack_ref": ref_id,
                    "zar_charged_cents": amount_zar_cents,
                    "gateway_response": result.get("data", {}).get("gateway_response"),
                },
            )
            logger.info(
                "wallet: auto-topup SUCCESS for org=%s amount=%d cents ref=%s",
                org_id,
                topup_amount,
                ref_id,
            )
        elif result.get("data", {}).get("paused"):
            # 3DS challenge required — cannot auto-complete; notify admin
            logger.warning(
                "wallet: auto-topup PAUSED (3DS required) for org=%s ref=%s",
                org_id,
                ref_id,
            )
            balance = await _current_balance_cents(org_id)
            await store.append_ledger(
                org_id,
                entry_type="TOPUP_FAILED",
                amount_usd_cents=0,
                balance_after_usd_cents=balance,
                description="Auto-topup paused — 3DS authentication required",
                ref_id=ref_id,
                metadata={"reason": "3ds_required"},
            )
        else:
            gateway_msg = result.get("data", {}).get("gateway_response", result.get("message", "unknown"))
            logger.warning(
                "wallet: auto-topup FAILED for org=%s reason=%s ref=%s",
                org_id,
                gateway_msg,
                ref_id,
            )
            balance = await _current_balance_cents(org_id)
            await store.append_ledger(
                org_id,
                entry_type="TOPUP_FAILED",
                amount_usd_cents=0,
                balance_after_usd_cents=balance,
                description=f"Auto-topup failed: {gateway_msg}",
                ref_id=ref_id,
                metadata={"reason": gateway_msg},
            )

    except Exception as exc:  # noqa: BLE001
        logger.error(
            "wallet: auto-topup exception for org=%s exc=%s ref=%s",
            org_id,
            exc,
            ref_id,
        )
        try:
            balance = await _current_balance_cents(org_id)
            await store.append_ledger(
                org_id,
                entry_type="TOPUP_FAILED",
                amount_usd_cents=0,
                balance_after_usd_cents=balance,
                description=f"Auto-topup exception: {exc}",
                ref_id=ref_id,
                metadata={"reason": str(exc)},
            )
        except Exception:  # noqa: BLE001
            pass
    finally:
        await store.set_topup_in_flight(org_id, False)


# Strong references to in-flight fire-and-forget topup tasks — the event loop
# only keeps weak references, so an unreferenced task can be garbage-collected
# mid-flight (charge made, wallet never credited).
_pending_topup_tasks: set[asyncio.Task] = set()


def _maybe_schedule_auto_topup(org_id: str, balance: int, threshold: int) -> None:
    """Schedule the auto-topup coroutine if balance is below threshold.

    Uses :func:`asyncio.ensure_future` — fire-and-forget; caller is not blocked.
    A strong reference to the task is held until it completes.
    """
    if balance < threshold:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                task = asyncio.ensure_future(_execute_auto_topup(org_id))
                _pending_topup_tasks.add(task)
                task.add_done_callback(_pending_topup_tasks.discard)
            else:
                loop.run_until_complete(_execute_auto_topup(org_id))
        except RuntimeError:
            # No event loop — silently skip (e.g., CLI context).
            logger.debug("wallet: no event loop to schedule auto-topup for org=%s", org_id)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def get_balance(org_id: str) -> dict[str, Any]:
    """Return the current wallet balance record for *org_id*.

    Returns a dict with keys: ``org_id``, ``balance_usd_cents``,
    ``balance_zar_cents``, ``last_fx_rate``, ``last_fx_at``.
    """
    from app.ee.billing.wallet_store import get_wallet_store  # noqa: PLC0415

    return await get_wallet_store().get_balance(org_id)


async def credit(
    org_id: str,
    amount_usd_cents: int,
    entry_type: str = "TOPUP_MANUAL",
    *,
    description: str | None = None,
    ref_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Add *amount_usd_cents* credits to the wallet and write a ledger entry.

    Parameters
    ----------
    org_id:
        Organisation UUID string.
    amount_usd_cents:
        Positive integer amount to credit.
    entry_type:
        One of the TOPUP_* or ADJUSTMENT_CREDIT values.
    description, ref_id, metadata:
        Optional ledger annotation fields.

    Returns
    -------
    dict
        The written ledger entry.
    """
    from app.ee.billing.wallet_store import get_wallet_store  # noqa: PLC0415

    if amount_usd_cents <= 0:
        raise ValueError(f"credit amount must be positive, got {amount_usd_cents}")

    store = get_wallet_store()
    return await store.credit_with_ledger(
        org_id,
        amount_usd_cents,
        entry_type=entry_type,
        description=description,
        ref_id=ref_id,
        metadata=metadata,
    )


async def debit(
    org_id: str,
    amount_usd_cents: int,
    entry_type: str = "USAGE_OVERAGE",
    *,
    description: str | None = None,
    ref_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Debit *amount_usd_cents* from the wallet for metered usage.

    Enforces:
    - Zero-balance hard stop: raises :class:`WalletInsufficientError` if balance == 0.
    - Idempotency on *ref_id*: if the ledger already has an effective entry with
      *ref_id*, the debit is skipped (e.g. a retried billing cycle never
      double-draws the same period's overage).
    - Triggers (schedules) auto-topup if balance after debit < configured threshold.

    The monthly spend cap does NOT block debits — it only blocks incoming
    topups (enforced in :func:`_execute_auto_topup` via :func:`_check_spend_cap`).

    Parameters
    ----------
    org_id:
        Organisation UUID string.
    amount_usd_cents:
        Positive integer amount to debit.
    entry_type:
        One of the USAGE_* values.
    description, ref_id, metadata:
        Optional ledger annotation fields.

    Returns
    -------
    dict
        The written ledger entry, or ``{"skipped": True, "ref_id": ref_id}``
        if an entry with *ref_id* already exists.

    Raises
    ------
    WalletInsufficientError
        When the balance is insufficient.
    """
    from app.ee.billing.wallet_store import get_wallet_store  # noqa: PLC0415

    if amount_usd_cents <= 0:
        raise ValueError(f"debit amount must be positive, got {amount_usd_cents}")

    store = get_wallet_store()

    # --- Idempotency check (mirrors manual_topup) ---
    if ref_id and await store.ledger_ref_exists(ref_id):
        logger.info("wallet: debit duplicate ref_id=%s org=%s — skipping", ref_id, org_id)
        return {"skipped": True, "ref_id": ref_id}

    # --- Zero-balance hard stop ---
    balance = await _current_balance_cents(org_id)
    if balance == 0:
        raise WalletInsufficientError(
            "Your usage wallet balance is depleted. Please top up to continue.",
            balance_usd_cents=0,
        )

    if balance < amount_usd_cents:
        raise WalletInsufficientError(
            f"Insufficient wallet balance: have ${balance / 100:.4f}, "
            f"need ${amount_usd_cents / 100:.4f}.",
            balance_usd_cents=balance,
        )

    # --- Execute debit (balance + ledger in one atomic store operation) ---
    try:
        entry = await store.debit_with_ledger(
            org_id,
            amount_usd_cents,
            entry_type=entry_type,
            description=description,
            ref_id=ref_id,
            metadata=metadata,
        )
    except ValueError as exc:
        # A concurrent debit won between our pre-check and the guarded UPDATE;
        # surface the structured error shape callers expect, not a bare ValueError.
        balance = await _current_balance_cents(org_id)
        raise WalletInsufficientError(
            f"Insufficient wallet balance: have ${balance / 100:.4f}, "
            f"need ${amount_usd_cents / 100:.4f}.",
            balance_usd_cents=balance,
        ) from exc

    # --- Schedule auto-topup if threshold breached ---
    cfg = await store.get_topup_config(org_id)
    if cfg.get("auto_topup_enabled") and cfg.get("paystack_auth_reusable"):
        _maybe_schedule_auto_topup(
            org_id, entry["balance_after_usd_cents"], cfg.get("threshold_usd_cents", 1000)
        )

    return entry


async def manual_topup(
    org_id: str,
    amount_usd_cents: int,
    *,
    ref_id: str | None = None,
    description: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record a user-initiated topup confirmed by Paystack.

    This is idempotent on *ref_id* — if the ledger already has an entry with
    *ref_id*, the credit is skipped (returns the existing entry's info shape).

    Returns
    -------
    dict
        Ledger entry or ``{"skipped": True, "ref_id": ref_id}`` if duplicate.
    """
    from app.ee.billing.wallet_store import get_wallet_store  # noqa: PLC0415

    store = get_wallet_store()

    # Idempotency check
    if ref_id and await store.ledger_ref_exists(ref_id):
        logger.info("wallet: manual_topup duplicate ref_id=%s org=%s — skipping", ref_id, org_id)
        return {"skipped": True, "ref_id": ref_id}

    return await credit(
        org_id,
        amount_usd_cents,
        "TOPUP_MANUAL",
        description=description or "Manual wallet topup",
        ref_id=ref_id,
        metadata=metadata,
    )


async def save_authorization(
    org_id: str,
    authorization_data: dict[str, Any],
) -> None:
    """Save a Paystack card authorization after a first successful payment.

    Parameters
    ----------
    org_id:
        Organisation UUID string.
    authorization_data:
        The ``authorization`` sub-object from the Paystack ``charge.success``
        webhook or verify response, combined with ``customer`` fields::

            {
                "authorization_code": "AUTH_xxxx",
                "reusable": True,
                "last4": "4081",
                "exp_month": "12",
                "exp_year": "2030",
                "brand": "visa",
                "customer_email": "user@example.com",
                "customer_code": "CUS_xxxx",
            }

        Only saved if ``reusable is True``.
    """
    from app.ee.billing.wallet_store import get_wallet_store  # noqa: PLC0415

    if not authorization_data.get("reusable"):
        logger.info("wallet: save_authorization skipped — reusable=False for org=%s", org_id)
        return

    await get_wallet_store().upsert_topup_config(
        org_id,
        paystack_authorization_code=authorization_data.get("authorization_code"),
        paystack_customer_email=authorization_data.get("customer_email"),
        paystack_customer_code=authorization_data.get("customer_code"),
        paystack_card_last4=authorization_data.get("last4"),
        paystack_card_brand=authorization_data.get("brand"),
        paystack_card_exp_month=authorization_data.get("exp_month"),
        paystack_card_exp_year=authorization_data.get("exp_year"),
        paystack_auth_reusable=True,
    )
    logger.info("wallet: card authorization saved for org=%s", org_id)


async def trigger_auto_topup(org_id: str) -> None:
    """Manually trigger an auto-topup attempt for *org_id*.

    Respects the same guards as the automatic path (in-flight flag,
    monthly cap, spend cap, reusable card required).
    """
    await _execute_auto_topup(org_id)


async def handle_webhook_charge_success(
    org_id: str,
    ref_id: str,
    amount_usd_cents: int,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Idempotent handler for Paystack ``charge.success`` webhook events.

    Determines the ``entry_type`` from ``metadata.topup_type``:
    - ``"auto"`` → ``TOPUP_AUTO``
    - ``"manual"`` → ``TOPUP_MANUAL``
    - anything else → ``TOPUP_MANUAL``

    Returns a ledger entry dict, or ``{"skipped": True}`` if already processed.
    """
    from app.ee.billing.wallet_store import get_wallet_store  # noqa: PLC0415

    store = get_wallet_store()

    if await store.ledger_ref_exists(ref_id):
        logger.info("wallet: webhook charge.success duplicate ref_id=%s — skipping", ref_id)
        return {"skipped": True, "ref_id": ref_id}

    topup_type = (metadata or {}).get("topup_type", "manual")
    entry_type = "TOPUP_AUTO" if topup_type == "auto" else "TOPUP_MANUAL"

    return await store.credit_with_ledger(
        org_id,
        amount_usd_cents,
        entry_type=entry_type,
        description=f"Paystack charge.success — {topup_type} topup",
        ref_id=ref_id,
        metadata=metadata,
    )
