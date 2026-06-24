"""Signed async delivery for outbound webhooks.

A delivery is a POST of a canonical JSON body to a host-registered HTTPS URL,
authenticated with an HMAC-SHA256 signature the host can verify.

Signing
-------
The signature covers ``"{timestamp}.{body}"`` (the Stripe-style signed payload)
so a host can reject replays by checking the timestamp skew. Headers:

    X-Nubi-Signature  hex HMAC-SHA256 of ``"{ts}.{body}"`` keyed by the secret
    X-Nubi-Timestamp  the unix timestamp (seconds) used in the signed payload
    X-Nubi-Event      the event type (e.g. ``watch_breach``)
    Content-Type      application/json

The body is serialised ONCE with ``json.dumps(..., separators=(",", ":"),
sort_keys=True)`` and the SAME bytes are both signed and sent, so the host's
recomputation matches byte-for-byte.

Delivery discipline
--------------------
- Bounded retry with exponential backoff on transport errors and 5xx / 429.
- 2xx is success; other 4xx are permanent (no retry — the host rejected it).
- Fire-and-forget: :func:`dispatch_event` schedules deliveries on the running
  loop and returns immediately; failures are logged, NEVER raised, so a webhook
  can never break the request that triggered the event.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from typing import Any

logger = logging.getLogger("app.webhooks.delivery")

# Tunables (kept small so a slow/dead host never ties up resources).
_TIMEOUT_S = 10.0
_MAX_ATTEMPTS = 4
_BASE_BACKOFF_S = 0.5


def canonical_body(payload: dict[str, Any]) -> bytes:
    """Serialise *payload* to the canonical bytes that are signed AND sent.

    Stable key order + compact separators so the host can recompute the exact
    same bytes for signature verification.
    """
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def sign(secret: str, body: bytes, timestamp: int) -> str:
    """Return the hex HMAC-SHA256 over ``"{timestamp}.{body}"`` keyed by *secret*."""
    signed_payload = f"{timestamp}.".encode("utf-8") + body
    return hmac.new(
        secret.encode("utf-8"), signed_payload, hashlib.sha256
    ).hexdigest()


def verify(secret: str, body: bytes, timestamp: int, signature: str) -> bool:
    """Constant-time verify a signature produced by :func:`sign`.

    Provided for hosts/tests; the delivery path itself only signs.
    """
    expected = sign(secret, body, timestamp)
    return hmac.compare_digest(expected, signature)


async def deliver_one(
    url: str,
    secret: str,
    event_type: str,
    payload: dict[str, Any],
    *,
    max_attempts: int = _MAX_ATTEMPTS,
    base_backoff_s: float = _BASE_BACKOFF_S,
    timeout_s: float = _TIMEOUT_S,
) -> bool:
    """Deliver one signed event to *url*, retrying with backoff. Never raises.

    Returns ``True`` on a 2xx response, ``False`` if all attempts are exhausted
    or the host returned a permanent (non-429) 4xx.
    """
    import httpx  # noqa: PLC0415 — lazy so the module imports without httpx in odd envs

    body = canonical_body(payload)
    timestamp = int(time.time())
    signature = sign(secret, body, timestamp)
    headers = {
        "Content-Type": "application/json",
        "X-Nubi-Signature": signature,
        "X-Nubi-Timestamp": str(timestamp),
        "X-Nubi-Event": event_type,
    }

    for attempt in range(1, max_attempts + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                resp = await client.post(url, content=body, headers=headers)
            status = resp.status_code
            if 200 <= status < 300:
                return True
            # 4xx (except 429) is permanent — the host rejected the delivery.
            if 400 <= status < 500 and status != 429:
                logger.warning(
                    "webhook %s -> %s rejected (status=%s); not retrying",
                    event_type,
                    url,
                    status,
                )
                return False
            # 5xx / 429 → retryable.
            logger.info(
                "webhook %s -> %s attempt %d/%d got status=%s; will retry",
                event_type,
                url,
                attempt,
                max_attempts,
                status,
            )
        except Exception as exc:  # noqa: BLE001 — transport errors are retryable.
            logger.info(
                "webhook %s -> %s attempt %d/%d transport error: %s",
                event_type,
                url,
                attempt,
                max_attempts,
                exc,
            )

        if attempt < max_attempts:
            # Exponential backoff: base * 2^(attempt-1).
            await asyncio.sleep(base_backoff_s * (2 ** (attempt - 1)))

    logger.warning(
        "webhook %s -> %s exhausted %d attempts; giving up",
        event_type,
        url,
        max_attempts,
    )
    return False


async def deliver_to_org(
    org_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> int:
    """Fan out *event_type* to every active subscribed endpoint in *org_id*.

    STRICTLY org-scoped: endpoints are looked up by ``org_id`` so org A's
    endpoints never receive org B's events. Returns the number of endpoints the
    event was delivered to successfully. Never raises — a store or delivery
    failure is logged and treated as a non-delivery.
    """
    from app.webhooks.models import get_webhook_store  # noqa: PLC0415

    if not org_id:
        return 0

    try:
        endpoints = await get_webhook_store().list_active_for_event(
            str(org_id), event_type
        )
    except Exception as exc:  # noqa: BLE001 — never break the caller on a store error.
        logger.warning(
            "webhook lookup failed for org=%s event=%s: %s",
            org_id,
            event_type,
            exc,
        )
        return 0

    if not endpoints:
        return 0

    results = await asyncio.gather(
        *(
            deliver_one(ep["url"], ep["secret"], event_type, payload)
            for ep in endpoints
        ),
        return_exceptions=True,
    )
    return sum(1 for r in results if r is True)


def dispatch_event(
    org_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    """Fire-and-forget entrypoint: schedule org-scoped delivery, never block/raise.

    If a running event loop is available the fan-out runs as a background task;
    otherwise (no loop — e.g. a sync context) it runs synchronously to
    completion. Either way, exceptions are swallowed: a webhook must never break
    the request/flow that triggered it.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None:
        task = loop.create_task(_safe_deliver(org_id, event_type, payload))
        # Drop a reference so the task isn't GC'd before it runs; we never await it.
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)
        return

    # No running loop: deliver synchronously (best-effort, still never raises).
    try:
        asyncio.run(_safe_deliver(org_id, event_type, payload))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "webhook dispatch (sync) failed for org=%s event=%s: %s",
            org_id,
            event_type,
            exc,
        )


# Strong refs to in-flight fire-and-forget tasks (avoids premature GC).
_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


async def _safe_deliver(
    org_id: str, event_type: str, payload: dict[str, Any]
) -> None:
    """Wrapper that guarantees the background task never propagates an error."""
    try:
        await deliver_to_org(org_id, event_type, payload)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "webhook delivery task failed for org=%s event=%s: %s",
            org_id,
            event_type,
            exc,
        )
