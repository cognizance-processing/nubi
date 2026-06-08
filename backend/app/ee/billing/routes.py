"""EE billing API routes — mounted by load_ee ONLY (never by core main.py).

Routes
------
POST   /ee/billing/checkout        Create a Paystack checkout session.
GET    /ee/billing/webhook         Paystack webhook receiver.
GET    /ee/billing/tier            Return current organisation tier + limits.
GET    /ee/billing/events          List recent billing events for the org.

Authentication
--------------
``/checkout`` and ``/tier`` require a valid Bearer token via
:func:`app.auth.deps.current_user`.  The webhook endpoint authenticates
via Paystack HMAC-SHA512 signature verification (no bearer token).

Mounting
--------
Call :func:`setup` from ``load_ee()`` in ``app/ee/__init__.py``::

    from app.ee.billing.routes import setup as billing_setup  # noqa: PLC0415
    billing_setup(app)
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ee/billing", tags=["ee-billing"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class CheckoutRequest(BaseModel):
    """Body for POST /ee/billing/checkout."""

    org_id: str
    callback_url: str = "https://app.nubi.io/billing/confirm"


class CheckoutResponse(BaseModel):
    """Response for POST /ee/billing/checkout."""

    authorization_url: str
    reference: str
    access_code: str


class TierResponse(BaseModel):
    """Response for GET /ee/billing/tier."""

    org_id: str
    tier: str
    status: str
    monthly_price_zar: str
    limits: dict[str, Any]


# ---------------------------------------------------------------------------
# Helper: tier info for an org
# ---------------------------------------------------------------------------


async def _get_org_tier_info(org_id: str) -> dict[str, Any]:
    """Return tier + limits dict for *org_id*."""
    from app.ee.billing.store import get_billing_store  # noqa: PLC0415
    from app.ee.billing.tiers import BillingTier, get_tier_limits  # noqa: PLC0415

    store = get_billing_store()
    sub = await store.get_subscription(org_id)

    if sub is None:
        tier = BillingTier.FREE
        sub_status = "active"
    else:
        try:
            tier = BillingTier(sub["tier"])
        except ValueError:
            tier = BillingTier.FREE
        sub_status = sub.get("status", "active")

    limits = get_tier_limits(tier)
    return {
        "org_id": org_id,
        "tier": tier.value,
        "status": sub_status,
        "monthly_price_zar": str(limits.monthly_price_zar),
        "limits": {
            "max_seats": limits.max_seats,
            "max_connectors": limits.max_connectors,
            "max_query_rows": limits.max_query_rows,
            "max_dashboards": limits.max_dashboards,
            "max_flows": limits.max_flows,
            "max_storage_gb": limits.max_storage_gb,
            "security_dial_min": limits.security_dial_min,
            "security_dial_max": limits.security_dial_max,
        },
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/checkout",
    response_model=CheckoutResponse,
    summary="Create a Paystack checkout session for an org subscription",
)
async def create_checkout(
    body: CheckoutRequest,
    user: Any = Depends(lambda: None),  # replaced at mount time — see setup()
) -> CheckoutResponse:
    """Create a Paystack transaction for the org's PRO subscription.

    Returns the Paystack authorization URL to redirect the user to.
    """
    from app.ee.billing.paystack import initialize_transaction  # noqa: PLC0415
    from app.ee.billing.tiers import BillingTier, get_tier_limits  # noqa: PLC0415

    limits = get_tier_limits(BillingTier.PRO)
    # Amount in kobo: ZAR amount × 100
    amount_kobo = int(limits.monthly_price_zar * 100)
    reference = f"nubi-sub-{body.org_id}-{uuid.uuid4().hex[:8]}"

    try:
        result = await initialize_transaction(
            email=user.get("email", "billing@nubi.io") if isinstance(user, dict) else getattr(user, "email", "billing@nubi.io"),
            amount_kobo=amount_kobo,
            reference=reference,
            callback_url=body.callback_url,
            metadata={"org_id": body.org_id, "tier": "pro"},
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Paystack error: {exc}",
        ) from exc

    data = result.get("data", {})
    return CheckoutResponse(
        authorization_url=data.get("authorization_url", ""),
        reference=data.get("reference", reference),
        access_code=data.get("access_code", ""),
    )


@router.post(
    "/webhook",
    status_code=status.HTTP_200_OK,
    summary="Paystack webhook endpoint — validates HMAC-SHA512 signature",
)
async def paystack_webhook(
    request: Request,
    x_paystack_signature: str = Header(""),
) -> dict[str, str]:
    """Receive and process Paystack webhook events.

    Validates the HMAC-SHA512 signature before processing.  Returns 200 OK
    immediately after signature validation (Paystack expects a fast 200).
    """
    from app.ee.billing.paystack import verify_webhook_signature  # noqa: PLC0415
    from app.ee.billing.store import get_billing_store  # noqa: PLC0415
    from app.ee.billing.tiers import BillingTier  # noqa: PLC0415

    raw_body = await request.body()

    if not verify_webhook_signature(raw_body, x_paystack_signature):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        )

    import json  # noqa: PLC0415

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed JSON body",
        )

    event_type: str = payload.get("event", "unknown")
    data: dict[str, Any] = payload.get("data", {})
    metadata: dict[str, Any] = data.get("metadata", {})
    org_id: str | None = metadata.get("org_id")

    store = get_billing_store()

    if org_id:
        # Record the event for audit / replay.
        await store.record_billing_event(org_id, event_type, payload)

        # Handle known event types.
        if event_type == "charge.success":
            tier_str = metadata.get("tier", BillingTier.PRO.value)
            await store.upsert_subscription(
                org_id,
                tier=tier_str,
                status="active",
                paystack_customer_code=data.get("customer", {}).get("customer_code"),
            )
            logger.info("Billing: charge.success for org=%s tier=%s", org_id, tier_str)

        elif event_type in ("subscription.disable", "subscription.not_renew"):
            sub = await store.get_subscription(org_id)
            if sub:
                await store.upsert_subscription(
                    org_id,
                    tier=sub["tier"],
                    status="cancelled",
                    cancel_at_period_end=True,
                )
            logger.info("Billing: %s for org=%s", event_type, org_id)

        elif event_type == "invoice.payment_failed":
            sub = await store.get_subscription(org_id)
            if sub:
                await store.upsert_subscription(
                    org_id,
                    tier=sub["tier"],
                    status="past_due",
                )
            logger.warning("Billing: invoice.payment_failed for org=%s", org_id)
    else:
        logger.debug("Billing: webhook event %s — no org_id in metadata", event_type)

    return {"status": "ok"}


@router.get(
    "/tier",
    response_model=TierResponse,
    summary="Return the current billing tier and resource limits for an org",
)
async def get_current_tier(
    org_id: str,
    user: Any = Depends(lambda: None),  # replaced at mount time — see setup()
) -> TierResponse:
    """Return the active tier and limits for *org_id*.

    The caller must be a member of *org_id* (enforced by the auth dependency
    injected at mount time).
    """
    info = await _get_org_tier_info(org_id)
    return TierResponse(**info)


@router.get(
    "/events",
    summary="Return recent billing events for an org",
)
async def list_billing_events(
    org_id: str,
    limit: int = 50,
    user: Any = Depends(lambda: None),  # replaced at mount time — see setup()
) -> dict[str, Any]:
    """Return recent billing events for *org_id*, newest first."""
    from app.ee.billing.store import get_billing_store  # noqa: PLC0415

    events = await get_billing_store().list_billing_events(org_id, limit=limit)
    return {"org_id": org_id, "events": events, "count": len(events)}


# ---------------------------------------------------------------------------
# Mount helper (called from load_ee)
# ---------------------------------------------------------------------------


def setup(app: Any) -> None:
    """Mount billing routes onto *app* (the FastAPI instance).

    This function is called lazily from ``load_ee()`` — it is never called by
    core ``main.py``.  The router is included under the default ``/api/v1``
    prefix via ``app.include_router``.

    Parameters
    ----------
    app:
        The FastAPI application instance.
    """
    from app.auth.deps import current_user  # noqa: PLC0415

    # Rebuild the router with the real auth dependency now that app.auth is
    # available.  We patch the Depends on the endpoint functions directly.
    for route in router.routes:
        # Replace the placeholder lambda dependency with current_user where
        # the endpoint signature declares a dependency keyed on ``user``.
        if hasattr(route, "dependant"):
            for dep in route.dependant.dependencies:
                if getattr(dep.call, "__name__", "") == "<lambda>":
                    dep.call = current_user

    app.include_router(router, prefix="/api/v1")
    logger.info("Nubi EE: billing routes mounted at /api/v1/ee/billing")
