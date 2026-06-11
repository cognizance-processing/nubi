"""Web Push (VAPID) delivery + subscription store.

Two parts
---------
1. :func:`send_push` — encrypt + POST a payload to a single browser push
   endpoint via ``pywebpush``. Degrades to a **logged no-op** (never raises) when
   ``pywebpush`` is not installed or the VAPID keys are unset, so the dispatch
   path is never blocked by push being unconfigured. When the push *service*
   replies 404/410 the subscription is dead — :func:`send_push` returns
   :data:`PUSH_GONE` so the caller can prune it.
2. :class:`PushStore` (Pg + InMemory) over ``push_subscriptions`` — upsert by
   endpoint, list a set of users' subscriptions, delete by endpoint.

VAPID config comes from the environment (``VAPID_PUBLIC_KEY``,
``VAPID_PRIVATE_KEY``, ``VAPID_SUBJECT``). Several subsystems read os.getenv
directly rather than Settings; we follow that here so push stays optional.

Provider pattern mirrors :mod:`app.auth.api_keys`: a module-level singleton via
:func:`get_push_store`, swappable in tests via
:func:`set_push_store_for_tests`. Every read/write is org-scoped.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("nubi.push")

#: send_push outcomes.
PUSH_OK = "ok"
PUSH_GONE = "gone"  # 404/410 — the caller should prune this subscription.
PUSH_SKIPPED = "skipped"  # no-op: pywebpush missing or VAPID unset.
PUSH_ERROR = "error"  # transient/other failure — swallowed, not pruned.


def vapid_public_key() -> str | None:
    """Return the configured VAPID public key (browser applicationServerKey)."""
    return os.getenv("VAPID_PUBLIC_KEY") or None


def _vapid_private_key() -> str | None:
    return os.getenv("VAPID_PRIVATE_KEY") or None


def _vapid_subject() -> str:
    # mailto: or https: subject; pywebpush requires one when claims are sent.
    return os.getenv("VAPID_SUBJECT") or "mailto:admin@nubi.local"


def push_configured() -> bool:
    """Return True when both VAPID keys are present (push can actually send)."""
    return bool(vapid_public_key() and _vapid_private_key())


def _subscription_info(subscription: dict[str, Any]) -> dict[str, Any]:
    """Coerce a stored row or a raw browser PushSubscription into webpush shape.

    A browser ``PushSubscription.toJSON()`` is already
    ``{endpoint, keys: {p256dh, auth}}``. A DB row stores p256dh/auth flat.
    """
    if "keys" in subscription and "endpoint" in subscription:
        return {"endpoint": subscription["endpoint"], "keys": subscription["keys"]}
    return {
        "endpoint": subscription["endpoint"],
        "keys": {
            "p256dh": subscription.get("p256dh"),
            "auth": subscription.get("auth"),
        },
    }


def send_push(subscription: dict[str, Any], payload: dict[str, Any]) -> str:
    """Send *payload* (JSON) to one push *subscription*. Best-effort; never raises.

    Returns one of :data:`PUSH_OK`, :data:`PUSH_GONE` (prune the subscription),
    :data:`PUSH_SKIPPED` (push not configured) or :data:`PUSH_ERROR`.
    """
    if not push_configured():
        logger.debug("send_push: VAPID keys unset — skipping (no-op).")
        return PUSH_SKIPPED

    try:
        from pywebpush import WebPushException, webpush  # type: ignore
    except ImportError:
        logger.info("send_push: pywebpush not installed — skipping (no-op).")
        return PUSH_SKIPPED

    import json

    try:
        webpush(
            subscription_info=_subscription_info(subscription),
            data=json.dumps(payload),
            vapid_private_key=_vapid_private_key(),
            vapid_claims={"sub": _vapid_subject()},
        )
        return PUSH_OK
    except WebPushException as exc:  # noqa: BLE001 — best-effort delivery.
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (404, 410):
            logger.info("send_push: endpoint gone (%s) — prune subscription.", status)
            return PUSH_GONE
        logger.warning("send_push: delivery failed (status=%s): %s", status, exc)
        return PUSH_ERROR
    except Exception as exc:  # noqa: BLE001 — never propagate a push failure.
        logger.warning("send_push: unexpected failure: %s", exc)
        return PUSH_ERROR


# ---------------------------------------------------------------------------
# Subscription store — interface
# ---------------------------------------------------------------------------


def _public_sub(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "user_id": str(row["user_id"]),
        "org_id": str(row["org_id"]),
        "endpoint": row["endpoint"],
        "p256dh": row["p256dh"],
        "auth": row["auth"],
        "user_agent": row.get("user_agent"),
    }


class PushStore:
    """Interface for push-subscription persistence (structural duck-typing)."""

    async def upsert(
        self,
        user_id: str,
        org_id: str,
        endpoint: str,
        p256dh: str,
        auth: str,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        """Upsert a subscription keyed by *endpoint*. Return the row."""
        raise NotImplementedError

    async def list_for_users(
        self, org_id: str, user_ids: list[str]
    ) -> list[dict[str, Any]]:
        """Return all subscriptions in *org_id* for the given *user_ids*."""
        raise NotImplementedError

    async def delete(self, endpoint: str, user_id: str) -> bool:
        """Delete *endpoint* IFF it belongs to *user_id*. Return True if removed.

        Scoping the delete to the owning user closes an IDOR: a push endpoint is
        a low-entropy, guessable/leakable URL, so an unscoped delete would let any
        authenticated user prune another user's subscription.
        """
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Postgres implementation
# ---------------------------------------------------------------------------


class PgPushStore(PushStore):
    """asyncpg-backed store over ``push_subscriptions``."""

    async def upsert(
        self,
        user_id: str,
        org_id: str,
        endpoint: str,
        p256dh: str,
        auth: str,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        from app.db import fetchrow  # local import to avoid circular load

        # ON CONFLICT updates only when the existing row already belongs to the
        # SAME user — so a caller cannot hijack an endpoint another user has
        # registered (which would silently redirect that user's pushes). The
        # WHERE guard makes the upsert a no-op for a foreign-owned endpoint;
        # we then surface that as a 409-equivalent (empty row) to the caller.
        row = await fetchrow(
            """
            INSERT INTO push_subscriptions
                (id, user_id, org_id, endpoint, p256dh, auth, user_agent, last_used_at)
            VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7, now())
            ON CONFLICT (endpoint) DO UPDATE
                SET org_id       = EXCLUDED.org_id,
                    p256dh       = EXCLUDED.p256dh,
                    auth         = EXCLUDED.auth,
                    user_agent   = EXCLUDED.user_agent,
                    last_used_at = now()
                WHERE push_subscriptions.user_id = EXCLUDED.user_id
            RETURNING id, user_id, org_id, endpoint, p256dh, auth, user_agent
            """,
            str(uuid.uuid4()),
            user_id,
            org_id,
            endpoint,
            p256dh,
            auth,
            user_agent,
        )
        return _public_sub(dict(row)) if row is not None else {}

    async def list_for_users(
        self, org_id: str, user_ids: list[str]
    ) -> list[dict[str, Any]]:
        from app.db import fetch  # local import

        if not user_ids:
            return []
        rows = await fetch(
            """
            SELECT id, user_id, org_id, endpoint, p256dh, auth, user_agent
            FROM push_subscriptions
            WHERE org_id = $1::uuid AND user_id = ANY($2::uuid[])
            """,
            org_id,
            [str(u) for u in user_ids],
        )
        return [_public_sub(dict(r)) for r in rows]

    async def delete(self, endpoint: str, user_id: str) -> bool:
        from app.db import execute  # local import

        status = await execute(
            "DELETE FROM push_subscriptions WHERE endpoint = $1 AND user_id = $2::uuid",
            endpoint,
            user_id,
        )
        try:
            return int(status.split()[-1]) > 0
        except (IndexError, ValueError, AttributeError):
            return False


# ---------------------------------------------------------------------------
# In-memory implementation (tests)
# ---------------------------------------------------------------------------


class InMemoryPushStore(PushStore):
    """Dict-backed push store for tests (keyed by endpoint)."""

    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}

    def reset(self) -> None:
        self._store.clear()

    async def upsert(
        self,
        user_id: str,
        org_id: str,
        endpoint: str,
        p256dh: str,
        auth: str,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        existing = self._store.get(endpoint)
        # Refuse to hijack an endpoint already registered to a DIFFERENT user.
        if existing is not None and str(existing["user_id"]) != str(user_id):
            return {}
        row = {
            "id": existing["id"] if existing else str(uuid.uuid4()),
            "user_id": str(user_id),
            "org_id": str(org_id),
            "endpoint": endpoint,
            "p256dh": p256dh,
            "auth": auth,
            "user_agent": user_agent,
            "last_used_at": datetime.now(tz=timezone.utc),
        }
        self._store[endpoint] = row
        return _public_sub(row)

    async def list_for_users(
        self, org_id: str, user_ids: list[str]
    ) -> list[dict[str, Any]]:
        wanted = {str(u) for u in user_ids}
        return [
            _public_sub(r)
            for r in self._store.values()
            if r["org_id"] == str(org_id) and r["user_id"] in wanted
        ]

    async def delete(self, endpoint: str, user_id: str) -> bool:
        row = self._store.get(endpoint)
        if row is None or str(row["user_id"]) != str(user_id):
            return False
        del self._store[endpoint]
        return True


# ---------------------------------------------------------------------------
# Provider singleton
# ---------------------------------------------------------------------------

_store: Optional[PushStore] = None


def set_push_store_for_tests(store: PushStore | None) -> None:
    """Inject a test double (or pass None to restore the default Pg store)."""
    global _store
    _store = store


def get_push_store() -> PushStore:
    """Return the active :class:`PushStore` singleton (lazy Pg default)."""
    global _store
    if _store is None:
        _store = PgPushStore()
    return _store
