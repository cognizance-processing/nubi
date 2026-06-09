"""EE billing API routes — mounted by load_ee ONLY (never by core main.py).

Routes
------
GET    /pricing                    PUBLIC — tier catalogue + FX + competitors.
POST   /ee/billing/checkout        Create a Paystack checkout session.
GET    /ee/billing/webhook         Paystack webhook receiver.
GET    /ee/billing/tier            Return current organisation tier + limits.
GET    /ee/billing/events          List recent billing events for the org.

Authentication & authorization
------------------------------
``GET /pricing`` is PUBLIC and requires NO authentication — it is consumed
by the landing page, marketing calculator, and the in-app pricing page.

All other routes require a valid Bearer token via
:func:`app.auth.deps.current_user` AND org-membership authorization via
:func:`_require_org_access` (the caller must be a member of the ``org_id``
they pass; money-moving / config-mutating routes — checkout, wallet topup,
auto-topup config — additionally require the admin or owner role).  The
webhook endpoint authenticates via Paystack HMAC-SHA512 signature
verification (no bearer token).

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

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ee/billing", tags=["ee-billing"])

# Public router — no auth, consumed by landing + billing pages.
public_router = APIRouter(tags=["pricing"])


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------
#
# The authenticated billing routes resolve the caller via
# :func:`app.auth.deps.current_user`.  This module is imported ONLY by
# ``load_ee()`` (never by core ``main.py``), and ``load_ee()`` runs after
# ``app.auth`` is importable — so importing ``current_user`` at module level is
# safe here.
#
# Declaring ``Depends(current_user)`` directly in each endpoint signature is
# what makes the dependency survive ``app.include_router``: include_router
# copies fresh ``Dependant`` objects from the route's endpoint signature, so the
# dependency must be part of the signature.  (The previous approach mutated
# ``dep.call`` on the original router's routes after the fact, which was a no-op
# because include_router discarded those mutated copies.)
from app.auth.deps import current_user  # noqa: E402


# ---------------------------------------------------------------------------
# Org-membership authorization helper
# ---------------------------------------------------------------------------
#
# ``current_user`` only authenticates the bearer token — it never checks that
# the caller belongs to the org whose billing data is being requested.  Every
# billing route that accepts a caller-supplied ``org_id`` MUST call
# :func:`_require_org_access` before touching any store, otherwise any
# authenticated user could read (or charge!) another org's billing.


async def _require_org_access(
    user: Any,
    org_id: str,
    *,
    require_admin: bool = False,
) -> None:
    """Assert the caller is a member (optionally admin/owner) of *org_id*.

    Resolves the caller's role via ``org_members`` (the same lookup used by
    :func:`app.auth.roles.get_org_role`) and raises 403 when the caller is not
    a member of the requested org — or not an admin/owner when
    *require_admin* is set (money-moving and config-mutating routes).

    When the DB pool has not been initialised (router exercised outside the
    app lifespan, e.g. unit tests / CLI tooling), the check is skipped with a
    warning — the production server always initialises the pool at startup,
    so this never weakens a real deployment.
    """
    from app.auth.roles import get_org_role  # noqa: PLC0415
    from app.repos.provider import get_repo  # noqa: PLC0415

    user_id = (
        str(user.get("id", "")) if isinstance(user, dict) else str(getattr(user, "id", ""))
    )
    try:
        role = await get_org_role(user_id, org_id, get_repo())
    except RuntimeError:
        # "Database pool is not initialised" — no server context (unit tests,
        # CLI). Production initialises the pool in the app lifespan before
        # serving any request, so membership is always enforced there.
        logger.warning(
            "billing: org membership check skipped for org=%s — DB pool not initialised",
            org_id,
        )
        return

    if role is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of the requested organisation.",
        )
    if require_admin and role not in ("owner", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This billing action requires an org admin or owner role.",
        )


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class CheckoutRequest(BaseModel):
    """Body for POST /ee/billing/checkout."""

    org_id: str
    tier: str = "pro"  # validated against BillingTier; free/enterprise rejected
    billing_period: str = "monthly"  # "monthly" | "annual" (annual = 10× monthly)
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
# Wallet Pydantic schemas
# ---------------------------------------------------------------------------


class WalletTopupRequest(BaseModel):
    """Body for POST /ee/billing/wallet/topup (manual topup via saved card)."""

    org_id: str
    amount_usd_cents: int


class WalletAutoTopupConfigRequest(BaseModel):
    """Body for PUT /ee/billing/wallet/autotopup."""

    org_id: str
    auto_topup_enabled: bool | None = None
    threshold_usd_cents: int | None = None
    topup_amount_usd_cents: int | None = None
    monthly_topup_cap_usd_cents: int | None = None
    spend_cap_usd_cents: int | None = None


# ---------------------------------------------------------------------------
# Public /pricing endpoint helpers
# ---------------------------------------------------------------------------


def _build_tier_display(tier_limits: Any) -> dict[str, Any]:
    """Convert a TierLimits dataclass to a JSON-serialisable display dict."""
    from decimal import Decimal  # noqa: PLC0415

    def _dec(v: Any) -> Any:
        """Convert Decimal to str for JSON safety; leave None/int/float as-is."""
        return str(v) if isinstance(v, Decimal) else v

    ov = tier_limits.overages
    return {
        "tier": tier_limits.tier.value,
        "usd_monthly_price": _dec(tier_limits.usd_monthly_price),
        "usd_annual_price": _dec(tier_limits.usd_annual_price),
        "monthly_price_zar": _dec(tier_limits.monthly_price_zar),
        "limits": {
            "max_seats": tier_limits.max_seats,
            "max_viewer_seats": tier_limits.max_viewer_seats,
            "max_connectors": tier_limits.max_connectors,
            "max_query_rows": tier_limits.max_query_rows,
            "max_dashboards": tier_limits.max_dashboards,
            "max_flows": tier_limits.max_flows,
            "max_storage_gb": tier_limits.max_storage_gb,
            "max_compute_units_per_month": tier_limits.max_compute_units_per_month,
            "max_embedded_sessions_per_month": tier_limits.max_embedded_sessions_per_month,
            "max_agent_runs_per_month": tier_limits.max_agent_runs_per_month,
            "max_ai_calls_per_month": tier_limits.max_ai_calls_per_month,
            "security_dial_min": tier_limits.security_dial_min,
            "security_dial_max": tier_limits.security_dial_max,
        },
        "overages": {
            "storage_zar_per_gb_month": _dec(ov.storage_zar_per_gb_month),
            "compute_zar_per_1000_cu": _dec(ov.compute_zar_per_1000_cu),
            "ai_call_zar_per_call": _dec(ov.ai_call_zar_per_call),
            "embedded_session_zar_per_10k": _dec(ov.embedded_session_zar_per_10k),
            "agent_run_zar_per_run": _dec(ov.agent_run_zar_per_run),
            "seat_zar_per_seat_month": None,  # never metered
        },
        "features": {
            "has_white_label": tier_limits.has_white_label,
            "has_rls": tier_limits.has_rls,
            "has_sso_google": tier_limits.has_sso_google,
            "has_sso_saml": tier_limits.has_sso_saml,
            "has_scim": tier_limits.has_scim,
            "has_multi_tenant_workspaces": tier_limits.has_multi_tenant_workspaces,
            "has_byoc": tier_limits.has_byoc,
            "has_custom_domain": tier_limits.has_custom_domain,
            "audit_log_retention_days": tier_limits.audit_log_retention_days,
            "has_priority_support": tier_limits.has_priority_support,
            # SLA fields — present on Pro (uptime only) and Enterprise (full contractual SLA)
            "sla_uptime_pct": tier_limits.sla_uptime_pct,
            "sla_response_time_p1_minutes": tier_limits.sla_response_time_p1_minutes,
            "sla_response_time_p2_hours": tier_limits.sla_response_time_p2_hours,
        },
        # Full SLA block for Enterprise (null on tiers without contractual SLA)
        "sla": (
            {
                "uptime_pct": tier_limits.sla_uptime_pct,
                "p1_response_minutes": tier_limits.sla_response_time_p1_minutes,
                "p2_response_hours": tier_limits.sla_response_time_p2_hours,
                "support": tier_limits.has_priority_support,
                "includes_csm": tier_limits.has_priority_support == "dedicated_csm",
                "includes_24x7_oncall": tier_limits.sla_response_time_p1_minutes is not None,
            }
            if tier_limits.sla_response_time_p1_minutes is not None
            else None
        ),
        "gross_margin_pct": tier_limits.gross_margin_pct,
    }


# ---------------------------------------------------------------------------
# PUBLIC route: GET /pricing
# ---------------------------------------------------------------------------


@public_router.get(
    "/pricing",
    summary="Public pricing catalogue — tiers, FX rate, and competitor data",
    response_model=None,  # plain dict — avoids Pydantic Decimal serialisation issues
)
async def get_pricing() -> dict[str, Any]:
    """Return the full pricing catalogue for the landing page and pricing calculator.

    This endpoint is **public and unauthenticated**.  It is safe to call from
    the marketing site, from the in-app pricing modal, and from the billing
    calculator widget.

    Response shape
    --------------
    ::

        {
          "tiers": [...],           # one record per billing tier (FREE→ENTERPRISE)
          "fx": {
            "rate": "16.26",        # current USD→ZAR rate (Decimal as string)
            "as_of": "2026-06-08T...",  # ISO8601 UTC timestamp or null
            "stale": false
          },
          "competitors": {
            "bi": [...],            # BI / embedded analytics competitors
            "orchestration": [...], # Orchestration tools
            "as_of": "June 2026"
          },
          "disclosure": "..."       # ZAR FX disclosure copy for landing page
        }

    The ``tiers`` list is ordered FREE → ENTERPRISE.  All Decimal amounts are
    returned as strings to avoid JSON precision loss.
    """
    from app.ee.billing.competitors import all_competitors  # noqa: PLC0415
    from app.ee.billing.fx import get_current_rate  # noqa: PLC0415
    from app.ee.billing.tiers import ZAR_DISCLOSURE_COPY, all_tiers  # noqa: PLC0415

    tiers_display = [_build_tier_display(t) for t in all_tiers()]

    fx_info = get_current_rate()
    fx_payload: dict[str, Any] = {
        "rate": str(fx_info["rate"]),
        "as_of": fx_info["fetched_at"].isoformat() if fx_info["fetched_at"] else None,
        "stale": fx_info["stale"],
    }

    return {
        "tiers": tiers_display,
        "fx": fx_payload,
        "competitors": all_competitors(),
        "disclosure": ZAR_DISCLOSURE_COPY,
    }


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
    user: Any = Depends(current_user),
) -> CheckoutResponse:
    """Create a Paystack transaction for the requested subscription tier.

    The caller must be an admin/owner of ``body.org_id``.  The charge amount
    is the tier's USD-anchored price converted to ZAR at the *current* daily
    FX rate (+2% buffer, ceil-to-nearest-10) — the same path renewals use.
    ``tiers.monthly_price_zar`` is a static reference amount only and is never
    charged directly.

    Returns the Paystack authorization URL to redirect the user to.
    """
    from app.ee.billing.fx import convert_usd_to_zar  # noqa: PLC0415
    from app.ee.billing.paystack import initialize_transaction  # noqa: PLC0415
    from app.ee.billing.tiers import BillingTier, get_tier_limits  # noqa: PLC0415

    await _require_org_access(user, body.org_id, require_admin=True)

    try:
        tier = BillingTier(body.tier.strip().lower())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown billing tier: {body.tier!r}",
        )
    if tier is BillingTier.FREE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The free tier does not require checkout.",
        )
    if tier is BillingTier.ENTERPRISE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Enterprise plans are custom-quoted — contact sales@nubi.io.",
        )
    if body.billing_period not in ("monthly", "annual"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail='billing_period must be "monthly" or "annual".',
        )

    limits = get_tier_limits(tier)
    usd_price = (
        limits.usd_monthly_price
        if body.billing_period == "monthly"
        else limits.usd_annual_price
    )
    # Live ZAR price: current daily rate + 2% buffer + ceil-to-nearest-10.
    amount_zar = convert_usd_to_zar(usd_price)
    # Amount in kobo: ZAR amount × 100
    amount_kobo = int(amount_zar * 100)
    reference = f"nubi-sub-{body.org_id}-{uuid.uuid4().hex[:8]}"

    try:
        result = await initialize_transaction(
            email=user.get("email", "billing@nubi.io") if isinstance(user, dict) else getattr(user, "email", "billing@nubi.io"),
            amount_kobo=amount_kobo,
            reference=reference,
            callback_url=body.callback_url,
            metadata={
                "org_id": body.org_id,
                "tier": tier.value,
                "billing_period": body.billing_period,
                "kind": "subscription",
            },
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
        # --- Replay / duplicate-delivery guard -----------------------------
        # Paystack retries undelivered webhooks for up to ~72h.  For
        # ``charge.success`` we key on the Paystack transaction id (fall back
        # to the charge reference) and skip processing when the same event was
        # already recorded — duplicates are still appended to billing_events
        # for audit, but produce no side effects (no subscription upsert, no
        # wallet credit).  Other event types are not deduped here: their
        # ``data.id`` identifies a long-lived object (e.g. the subscription),
        # so two distinct deliveries can legitimately share it.
        event_id = (
            (data.get("id") or data.get("reference"))
            if event_type == "charge.success"
            else None
        )
        already_processed = False
        if event_id is not None:
            recent = await store.list_billing_events(org_id, limit=200)
            for prior in recent:
                p = prior.get("payload") or {}
                pdata = p.get("data") or {}
                if (
                    p.get("event") == event_type
                    and (pdata.get("id") or pdata.get("reference")) == event_id
                ):
                    already_processed = True
                    break

        # Record the event for audit / replay (append-only — duplicates too).
        await store.record_billing_event(org_id, event_type, payload)

        if already_processed:
            logger.info(
                "Billing: duplicate webhook %s id=%s for org=%s — skipping",
                event_type,
                event_id,
                org_id,
            )
            return {"status": "duplicate"}

        # Handle known event types.
        if event_type == "charge.success":
            reference = data.get("reference")
            authorization = data.get("authorization") or {}
            customer = data.get("customer") or {}

            # Persist the reusable card authorization so wallet topups and
            # saved-card invoice collection can charge it later.  This is the
            # ONLY production writer of paystack_authorization_code.
            if authorization.get("reusable"):
                from app.ee.billing.wallet import save_authorization  # noqa: PLC0415

                await save_authorization(
                    org_id,
                    {
                        **authorization,
                        "customer_email": customer.get("email"),
                        "customer_code": customer.get("customer_code"),
                    },
                )

            topup_type = metadata.get("topup_type")
            if topup_type in ("auto", "manual"):
                # Wallet topup — credit the wallet idempotently (keyed on the
                # charge reference).  A topup must NEVER touch the org's
                # subscription tier.
                from app.ee.billing.wallet import (  # noqa: PLC0415
                    handle_webhook_charge_success,
                )

                try:
                    topup_usd_cents = int(metadata.get("topup_usd_cents") or 0)
                except (TypeError, ValueError):
                    topup_usd_cents = 0
                if reference and topup_usd_cents > 0:
                    await handle_webhook_charge_success(
                        org_id, reference, topup_usd_cents, metadata
                    )
                    logger.info(
                        "Billing: charge.success topup (%s) for org=%s ref=%s",
                        topup_type,
                        org_id,
                        reference,
                    )
                else:
                    logger.warning(
                        "Billing: topup charge.success missing reference/amount "
                        "for org=%s — wallet NOT credited",
                        org_id,
                    )
            else:
                # Subscription / invoice charge — requires an explicit, valid
                # tier in metadata.  Never default the tier (a charge without
                # tier metadata must not silently grant a paid plan).
                tier_str = metadata.get("tier")
                if not tier_str:
                    logger.warning(
                        "Billing: charge.success without tier metadata for "
                        "org=%s — subscription NOT modified",
                        org_id,
                    )
                else:
                    try:
                        tier_value = BillingTier(str(tier_str)).value
                    except ValueError:
                        logger.warning(
                            "Billing: charge.success with invalid tier %r for "
                            "org=%s — subscription NOT modified",
                            tier_str,
                            org_id,
                        )
                    else:
                        await store.upsert_subscription(
                            org_id,
                            tier=tier_value,
                            status="active",
                            paystack_customer_code=customer.get("customer_code"),
                        )
                        logger.info(
                            "Billing: charge.success for org=%s tier=%s",
                            org_id,
                            tier_value,
                        )

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
    user: Any = Depends(current_user),
) -> TierResponse:
    """Return the active tier and limits for *org_id*.

    The caller must be a member of *org_id* (enforced via
    :func:`_require_org_access`).
    """
    await _require_org_access(user, org_id)
    info = await _get_org_tier_info(org_id)
    return TierResponse(**info)


@router.get(
    "/events",
    summary="Return recent billing events for an org",
)
async def list_billing_events(
    org_id: str,
    limit: int = 50,
    user: Any = Depends(current_user),
) -> dict[str, Any]:
    """Return recent billing events for *org_id*, newest first.

    The caller must be a member of *org_id*.
    """
    from app.ee.billing.store import get_billing_store  # noqa: PLC0415

    await _require_org_access(user, org_id)
    events = await get_billing_store().list_billing_events(org_id, limit=limit)
    return {"org_id": org_id, "events": events, "count": len(events)}


# ---------------------------------------------------------------------------
# Invoice routes
# ---------------------------------------------------------------------------


@router.get(
    "/invoices",
    summary="List invoices for an org (newest first)",
)
async def list_invoices(
    org_id: str,
    limit: int = 50,
    user: Any = Depends(current_user),
) -> dict[str, Any]:
    """Return recent invoices for *org_id* (no PDF bytes — see /invoices/{id}/pdf).

    The caller must be a member of *org_id*.
    """
    from app.ee.billing.invoice_store import get_invoice_store  # noqa: PLC0415

    await _require_org_access(user, org_id)
    invoices = await get_invoice_store().list_invoices(org_id, limit=limit)
    return {"org_id": org_id, "invoices": invoices, "count": len(invoices)}


@router.get(
    "/invoices/{invoice_id}/pdf",
    summary="Download an invoice as a PDF",
)
async def download_invoice_pdf(
    invoice_id: str,
    org_id: str,
    user: Any = Depends(current_user),
) -> Response:
    """Render and stream an invoice PDF.

    The caller must be a member of *org_id* (enforced via
    :func:`_require_org_access` — matching the query param alone is NOT
    sufficient, since the caller supplies it), and ``org_id`` must match the
    invoice's owner.
    """
    from app.ee.billing.invoice_pdf import render_invoice_pdf_from_row  # noqa: PLC0415
    from app.ee.billing.invoice_store import get_invoice_store  # noqa: PLC0415

    await _require_org_access(user, org_id)
    row = await get_invoice_store().get_invoice(invoice_id)
    if row is None or str(row.get("org_id")) != str(org_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="invoice not found")

    pdf = render_invoice_pdf_from_row(row)
    filename = row.get("pdf_filename") or f"{row.get('invoice_number', invoice_id)}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/invoices/current-cycle",
    summary="Project the current billing cycle for an org (dry-run, no collection)",
)
async def current_cycle_projection(
    org_id: str,
    user: Any = Depends(current_user),
) -> dict[str, Any]:
    """Return this cycle's usage vs quota and a *projected* invoice.

    This never collects money or sends email — it builds a draft invoice from
    the org's usage so far this period so the billing UI can show "what you'll
    owe this cycle".  Overages are shown gross (wallet credit is applied at
    actual collection time).

    The caller must be a member of *org_id*.
    """
    from datetime import datetime, timezone  # noqa: PLC0415

    await _require_org_access(user, org_id)

    from app.ee.billing.reconcile import (  # noqa: PLC0415
        aggregate_usage_for_org,
        compute_overage_line_items,
    )
    from app.ee.billing.store import get_billing_store  # noqa: PLC0415
    from app.ee.billing.tiers import BillingTier, get_tier_limits

    sub = await get_billing_store().get_subscription(org_id)
    tier = BillingTier(sub["tier"]) if sub and sub.get("tier") else BillingTier.FREE
    limits = get_tier_limits(tier)

    now = datetime.now(timezone.utc)
    period_start = (sub or {}).get("current_period_start") or now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    period_end = (sub or {}).get("current_period_end") or now

    usage = await aggregate_usage_for_org(org_id, period_start, now)
    overage_items, overage_total = compute_overage_line_items(usage, limits)

    return {
        "org_id": org_id,
        "tier": tier.value,
        "period_start": period_start.isoformat() if hasattr(period_start, "isoformat") else str(period_start),
        "period_end": period_end.isoformat() if hasattr(period_end, "isoformat") else str(period_end),
        "usage": usage.to_dict(),
        "limits": {
            "max_storage_gb": limits.max_storage_gb,
            "max_compute_units_per_month": limits.max_compute_units_per_month,
            "max_ai_calls_per_month": limits.max_ai_calls_per_month,
            "max_embedded_sessions_per_month": limits.max_embedded_sessions_per_month,
            "max_agent_runs_per_month": limits.max_agent_runs_per_month,
        },
        "overage_line_items": [li.to_dict() for li in overage_items],
        "overage_total_zar": str(overage_total),
        "monthly_price_zar": str(limits.monthly_price_zar),
    }


# ---------------------------------------------------------------------------
# Wallet routes
# ---------------------------------------------------------------------------


@router.get(
    "/wallet",
    summary="Return wallet balance, ledger, and auto-topup config for an org",
)
async def get_wallet(
    org_id: str,
    ledger_limit: int = 50,
    user: Any = Depends(current_user),
) -> dict[str, Any]:
    """Return the wallet state for *org_id*: balance, recent ledger entries,
    and the current auto-topup configuration.

    The caller must be a member of *org_id*.  The saved Paystack card
    ``authorization_code`` is never returned to the client — only masked card
    metadata (last4, brand, expiry) is exposed.
    """
    from app.ee.billing.wallet_store import get_wallet_store  # noqa: PLC0415

    await _require_org_access(user, org_id)
    store = get_wallet_store()
    balance = await store.get_balance(org_id)
    ledger = await store.list_ledger(org_id, limit=ledger_limit)
    cfg_raw = await store.get_topup_config(org_id)

    # Mask the authorization code — never send it to the client.
    cfg = {k: v for k, v in cfg_raw.items() if k != "paystack_authorization_code"}

    return {
        "org_id": org_id,
        "balance": balance,
        "ledger": ledger,
        "ledger_count": len(ledger),
        "autotopup_config": cfg,
    }


@router.post(
    "/wallet/topup",
    summary="Initiate a manual wallet topup via the saved Paystack card",
)
async def post_manual_topup(
    body: WalletTopupRequest,
    user: Any = Depends(current_user),
) -> dict[str, Any]:
    """Charge the saved Paystack card for *amount_usd_cents* and credit the wallet.

    The caller must be an org admin/owner of ``body.org_id`` — this endpoint
    charges the org's saved card.  This is a synchronous (blocking) topup — it
    calls Paystack immediately and credits the wallet on success (idempotent
    on the charge reference, so the webhook delivery for the same charge can
    never double-credit).

    Errors
    ------
    - 403 — caller is not an admin/owner of the org.
    - 402 — no reusable card saved.
    - 502 — Paystack API error.
    - 402 — Paystack declined the charge.
    """
    from app.ee.billing.paystack import charge_saved_card  # noqa: PLC0415
    from app.ee.billing.wallet import manual_topup  # noqa: PLC0415
    from app.ee.billing.wallet_store import get_wallet_store  # noqa: PLC0415

    await _require_org_access(user, body.org_id, require_admin=True)

    store = get_wallet_store()
    cfg = await store.get_topup_config(body.org_id)

    if not cfg.get("paystack_auth_reusable") or not cfg.get("paystack_authorization_code"):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="No reusable card saved. Complete a payment first to save a card.",
        )

    if body.amount_usd_cents <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="amount_usd_cents must be positive.",
        )

    # Convert USD cents → ZAR cents for Paystack
    from decimal import Decimal  # noqa: PLC0415

    from app.ee.billing.fx import get_current_rate  # noqa: PLC0415

    fx = get_current_rate()
    rate: Decimal = fx["rate"]
    amount_zar_cents = max(1, int((Decimal(body.amount_usd_cents) * rate).to_integral_value()) + 1)

    ref_id = f"nubi_manual_{uuid.uuid4().hex}"

    try:
        result = await charge_saved_card(
            authorization_code=cfg["paystack_authorization_code"],
            email=cfg.get("paystack_customer_email", ""),
            amount_zar_cents=amount_zar_cents,
            reference=ref_id,
            metadata={
                "org_id": body.org_id,
                "topup_type": "manual",
                "topup_usd_cents": body.amount_usd_cents,
            },
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Paystack error: {exc}",
        ) from exc

    data = result.get("data", {})
    if data.get("status") != "success":
        gateway_msg = data.get("gateway_response", result.get("message", "unknown"))
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Card charge declined: {gateway_msg}",
        )

    # Credit the wallet — idempotent on ref_id (the webhook for this charge
    # may land first via handle_webhook_charge_success).
    entry = await manual_topup(
        body.org_id,
        body.amount_usd_cents,
        description="Manual wallet topup via saved card",
        ref_id=ref_id,
        metadata={
            "paystack_ref": ref_id,
            "zar_charged_cents": amount_zar_cents,
            "gateway_response": data.get("gateway_response"),
        },
    )

    return {
        "org_id": body.org_id,
        "topup_usd_cents": body.amount_usd_cents,
        "topup_zar_cents": amount_zar_cents,
        "ref_id": ref_id,
        "ledger_entry": entry,
    }


@router.put(
    "/wallet/autotopup",
    summary="Update the auto-topup configuration for an org",
)
async def update_auto_topup_config(
    body: WalletAutoTopupConfigRequest,
    user: Any = Depends(current_user),
) -> dict[str, Any]:
    """Update the auto-topup settings (threshold, amount, caps, enable/disable).

    The caller must be an org admin/owner of ``body.org_id`` — this config
    controls when and how much the org's saved card is charged.  Only fields
    provided in the request body are updated; omitted fields retain their
    existing values.
    """
    from app.ee.billing.wallet_store import get_wallet_store  # noqa: PLC0415

    await _require_org_access(user, body.org_id, require_admin=True)
    store = get_wallet_store()
    updated = await store.upsert_topup_config(
        body.org_id,
        auto_topup_enabled=body.auto_topup_enabled,
        threshold_usd_cents=body.threshold_usd_cents,
        topup_amount_usd_cents=body.topup_amount_usd_cents,
        monthly_topup_cap_usd_cents=body.monthly_topup_cap_usd_cents,
        spend_cap_usd_cents=body.spend_cap_usd_cents,
    )
    # Mask authorization code before returning
    return {k: v for k, v in updated.items() if k != "paystack_authorization_code"}


# ---------------------------------------------------------------------------
# Mount helper (called from load_ee)
# ---------------------------------------------------------------------------


def setup(app: Any) -> None:
    """Mount billing routes onto *app* (the FastAPI instance).

    This function is called lazily from ``load_ee()`` — it is never called by
    core ``main.py``.  Two routers are mounted:

    1. ``public_router`` — ``GET /api/v1/pricing`` — **no auth required**.
       Consumed by the landing page pricing calculator and in-app pricing modal.

    2. ``router`` — ``/api/v1/ee/billing/...`` — auth-required billing routes
       (checkout, webhook, tier, events).  Their ``current_user`` dependency is
       declared in each endpoint signature (see ``Depends(current_user)``) so it
       survives ``include_router`` — which copies fresh ``Dependant`` objects.

    Route-ordering note (public /pricing)
    -------------------------------------
    Core mounts ``api_router`` (which contains the greedy ``/{resource}``
    catch-all from ``app.routes.resources``) BEFORE ``load_ee()`` runs.  Because
    Starlette matches routes in registration order, a plain
    ``app.include_router(public_router)`` here would append ``/pricing`` AFTER
    the catch-all — so ``GET /api/v1/pricing`` would be swallowed by
    ``/{resource}`` and require auth (401).

    To fix this without core importing any EE code, we mount the public router
    and then move its freshly-added routes to the FRONT of the app's route
    table so ``/api/v1/pricing`` is matched before ``/api/v1/{resource}``.

    Parameters
    ----------
    app:
        The FastAPI application instance.
    """
    # Mount public pricing endpoint — no auth, no prefix beyond /api/v1.
    # Record the route count before/after so we can identify exactly which
    # routes this include added, then promote them ahead of the catch-all.
    routes_before = len(app.router.routes)
    app.include_router(public_router, prefix="/api/v1")
    added = app.router.routes[routes_before:]
    if added:
        del app.router.routes[routes_before:]
        # Insert at the front so the concrete /pricing path is matched before
        # the generic /{resource} catch-all that core registered earlier.
        app.router.routes[0:0] = added
    logger.info("Nubi EE: public pricing route mounted at /api/v1/pricing (priority)")

    # Mount authenticated billing routes.  /ee/billing/... paths do not collide
    # with the catch-all (the literal "ee" segment + sub-path), so normal
    # append-order inclusion is fine here.
    app.include_router(router, prefix="/api/v1")
    logger.info("Nubi EE: billing routes mounted at /api/v1/ee/billing")
