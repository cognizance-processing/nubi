"""Billing subscription store — dual InMemory + Pg pattern.

Tracks per-organisation subscription records and the current tier state.

Two implementations
-------------------
PgBillingStore       — asyncpg-backed; reads/writes the ``subscriptions`` and
                       ``billing_events`` tables created by migration 0017.
InMemoryBillingStore — dict-backed; used in tests (no DB required).

Provider
--------
The module-level singleton is obtained via :func:`get_billing_store`.
Tests swap in an :class:`InMemoryBillingStore` via
:func:`set_billing_store_for_tests`.

Subscription shape
------------------
``{
    id: str (uuid),
    org_id: str (uuid),
    tier: str (BillingTier value — "free"/"starter"/"team"/"pro"/"enterprise"),
    status: str ("active"/"cancelled"/"past_due"/"trialing"),
    paystack_customer_code: str | None,
    paystack_subscription_code: str | None,
    current_period_start: datetime | None,
    current_period_end: datetime | None,
    cancel_at_period_end: bool,
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
# Type aliases
# ---------------------------------------------------------------------------

Subscription = dict[str, Any]


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------


class BillingStore:
    """Interface for billing subscription storage."""

    async def get_subscription(self, org_id: str) -> Subscription | None:
        """Return the active subscription for *org_id*, or ``None``."""
        raise NotImplementedError

    async def upsert_subscription(
        self,
        org_id: str,
        *,
        tier: str,
        status: str,
        paystack_customer_code: str | None = None,
        paystack_subscription_code: str | None = None,
        current_period_start: datetime | None = None,
        current_period_end: datetime | None = None,
        cancel_at_period_end: bool | None = None,
    ) -> Subscription:
        """Create or update the subscription for *org_id*.

        Patch semantics: ``tier`` and ``status`` always overwrite; every other
        kwarg left as ``None`` preserves the stored value (so a status-only
        upsert — e.g. marking past_due on a failed charge — can never wipe the
        billing period, Paystack codes, or a scheduled cancellation).

        Parameters
        ----------
        org_id:
            UUID string identifying the organisation.
        tier:
            A :class:`~app.ee.billing.tiers.BillingTier` value string.
        status:
            Subscription lifecycle state
            (``"active"``, ``"cancelled"``, ``"past_due"``, ``"trialing"``).
        paystack_customer_code:
            Paystack customer code (``CUS_...``).
        paystack_subscription_code:
            Paystack subscription code (``SUB_...``).
        current_period_start:
            Start of the current billing period.
        current_period_end:
            End of the current billing period.
        cancel_at_period_end:
            Whether the subscription is set to cancel at period end.
            ``None`` keeps the stored flag (``False`` on first insert).

        Returns
        -------
        Subscription
            The stored (or updated) subscription dict.
        """
        raise NotImplementedError

    async def record_billing_event(
        self,
        org_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        """Append a billing event for audit / replay purposes.

        Parameters
        ----------
        org_id:
            UUID string identifying the organisation.
        event_type:
            Paystack event type string (e.g. ``"charge.success"``).
        payload:
            Full webhook payload dict.
        """
        raise NotImplementedError

    async def list_billing_events(
        self,
        org_id: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return recent billing events for *org_id*, newest first.

        Parameters
        ----------
        org_id:
            UUID string identifying the organisation.
        limit:
            Maximum number of events to return.
        """
        raise NotImplementedError


# ---------------------------------------------------------------------------
# In-memory implementation (tests)
# ---------------------------------------------------------------------------


class InMemoryBillingStore(BillingStore):
    """Dict-backed billing store for tests.

    Usage::

        from app.ee.billing.store import InMemoryBillingStore, set_billing_store_for_tests
        store = InMemoryBillingStore()
        set_billing_store_for_tests(store)
    """

    def __init__(self) -> None:
        # org_id -> Subscription dict
        self._subscriptions: dict[str, Subscription] = {}
        # org_id -> list[billing_event dict]
        self._events: dict[str, list[dict[str, Any]]] = {}

    def reset(self) -> None:
        """Clear all stored state."""
        self._subscriptions.clear()
        self._events.clear()

    async def get_subscription(self, org_id: str) -> Subscription | None:
        row = self._subscriptions.get(str(org_id))
        return deepcopy(row) if row is not None else None

    async def upsert_subscription(
        self,
        org_id: str,
        *,
        tier: str,
        status: str,
        paystack_customer_code: str | None = None,
        paystack_subscription_code: str | None = None,
        current_period_start: datetime | None = None,
        current_period_end: datetime | None = None,
        cancel_at_period_end: bool | None = None,
    ) -> Subscription:
        key = str(org_id)
        now = datetime.now(timezone.utc)
        existing = self._subscriptions.get(key)
        prev: Subscription = existing or {}
        # Patch semantics (mirrors PgBillingStore's COALESCEs): None kwargs
        # preserve the stored value — a status-only upsert must never wipe the
        # period, Paystack codes, or a scheduled cancellation.
        sub: Subscription = {
            "id": existing["id"] if existing else str(uuid.uuid4()),
            "org_id": key,
            "tier": tier,
            "status": status,
            "paystack_customer_code": (
                paystack_customer_code
                if paystack_customer_code is not None
                else prev.get("paystack_customer_code")
            ),
            "paystack_subscription_code": (
                paystack_subscription_code
                if paystack_subscription_code is not None
                else prev.get("paystack_subscription_code")
            ),
            "current_period_start": (
                current_period_start
                if current_period_start is not None
                else prev.get("current_period_start")
            ),
            "current_period_end": (
                current_period_end
                if current_period_end is not None
                else prev.get("current_period_end")
            ),
            "cancel_at_period_end": (
                cancel_at_period_end
                if cancel_at_period_end is not None
                else prev.get("cancel_at_period_end", False)
            ),
            "created_at": existing["created_at"] if existing else now,
            "updated_at": now,
        }
        self._subscriptions[key] = sub
        return deepcopy(sub)

    async def record_billing_event(
        self,
        org_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        key = str(org_id)
        if key not in self._events:
            self._events[key] = []
        self._events[key].append(
            {
                "id": str(uuid.uuid4()),
                "org_id": key,
                "event_type": event_type,
                "payload": deepcopy(payload),
                "created_at": datetime.now(timezone.utc),
            }
        )

    async def list_billing_events(
        self,
        org_id: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        events = self._events.get(str(org_id), [])
        # Newest first
        return deepcopy(events[-limit:][::-1])


# ---------------------------------------------------------------------------
# Postgres implementation
# ---------------------------------------------------------------------------


class PgBillingStore(BillingStore):
    """asyncpg-backed billing store using the ``subscriptions`` table.

    Reads/writes the tables created by migration 0017_billing.sql.
    All DB access is via ``app.db.execute`` / ``app.db.fetchrow`` /
    ``app.db.fetch`` — imported lazily to avoid circular imports.
    """

    async def get_subscription(self, org_id: str) -> Subscription | None:
        from app.db import fetchrow  # noqa: PLC0415

        row = await fetchrow(
            """
            SELECT id::text, org_id::text, tier, status,
                   paystack_customer_code, paystack_subscription_code,
                   current_period_start, current_period_end,
                   cancel_at_period_end, created_at, updated_at
            FROM subscriptions
            WHERE org_id = $1::uuid
            ORDER BY created_at DESC
            LIMIT 1
            """,
            org_id,
        )
        return dict(row) if row is not None else None

    async def upsert_subscription(
        self,
        org_id: str,
        *,
        tier: str,
        status: str,
        paystack_customer_code: str | None = None,
        paystack_subscription_code: str | None = None,
        current_period_start: datetime | None = None,
        current_period_end: datetime | None = None,
        cancel_at_period_end: bool | None = None,
    ) -> Subscription:
        from app.db import fetchrow  # noqa: PLC0415

        row = await fetchrow(
            """
            INSERT INTO subscriptions
                (org_id, tier, status, paystack_customer_code,
                 paystack_subscription_code, current_period_start,
                 current_period_end, cancel_at_period_end)
            VALUES
                ($1::uuid, $2, $3, $4, $5, $6, $7, COALESCE($8, false))
            ON CONFLICT (org_id) DO UPDATE SET
                tier                      = EXCLUDED.tier,
                status                    = EXCLUDED.status,
                paystack_customer_code    = COALESCE(EXCLUDED.paystack_customer_code,
                                                     subscriptions.paystack_customer_code),
                paystack_subscription_code = COALESCE(EXCLUDED.paystack_subscription_code,
                                                      subscriptions.paystack_subscription_code),
                current_period_start      = COALESCE(EXCLUDED.current_period_start,
                                                     subscriptions.current_period_start),
                current_period_end        = COALESCE(EXCLUDED.current_period_end,
                                                     subscriptions.current_period_end),
                -- $8 (not EXCLUDED — already defaulted to false for the
                -- insert): None keeps the stored flag, so a status-only
                -- upsert never wipes a scheduled cancellation.
                cancel_at_period_end      = COALESCE($8, subscriptions.cancel_at_period_end),
                updated_at                = now()
            RETURNING id::text, org_id::text, tier, status,
                      paystack_customer_code, paystack_subscription_code,
                      current_period_start, current_period_end,
                      cancel_at_period_end, created_at, updated_at
            """,
            org_id,
            tier,
            status,
            paystack_customer_code,
            paystack_subscription_code,
            current_period_start,
            current_period_end,
            cancel_at_period_end,
        )
        return dict(row)  # type: ignore[arg-type]

    async def record_billing_event(
        self,
        org_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        import json  # noqa: PLC0415

        from app.db import execute  # noqa: PLC0415

        await execute(
            """
            INSERT INTO billing_events (org_id, event_type, payload)
            VALUES ($1::uuid, $2, $3::jsonb)
            """,
            org_id,
            event_type,
            json.dumps(payload),
        )

    async def list_billing_events(
        self,
        org_id: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        from app.db import fetch  # noqa: PLC0415

        rows = await fetch(
            """
            SELECT id::text, org_id::text, event_type, payload, created_at
            FROM billing_events
            WHERE org_id = $1::uuid
            ORDER BY created_at DESC
            LIMIT $2
            """,
            org_id,
            limit,
        )
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Provider singleton
# ---------------------------------------------------------------------------

_billing_store: BillingStore | None = None


def set_billing_store_for_tests(store: BillingStore | None) -> None:
    """Inject a test double or reset to default PgBillingStore.

    Parameters
    ----------
    store:
        An :class:`InMemoryBillingStore` instance for tests, or ``None``
        to restore the default production :class:`PgBillingStore`.
    """
    global _billing_store  # noqa: PLW0603
    _billing_store = store


def get_billing_store() -> BillingStore:
    """Return the active :class:`BillingStore` singleton.

    Lazily instantiates a :class:`PgBillingStore` on first call if no
    override has been set via :func:`set_billing_store_for_tests`.
    """
    global _billing_store  # noqa: PLW0603
    if _billing_store is None:
        _billing_store = PgBillingStore()
    return _billing_store
