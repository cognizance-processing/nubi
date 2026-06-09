"""EE billing invoices — domain model + VAT-aware builder.

This module is EE-only and must NEVER be imported by open-source core code.

An :class:`Invoice` is a frozen-ish record (we mutate a couple of lifecycle
fields — ``status`` / ``paid_at`` / ``paystack_reference`` / ``pdf_filename``)
describing one billing cycle for one organisation:

    base subscription
      + metered overages NOT covered by the prepaid wallet
      + VAT  (only when the business is VAT-registered)
      = total collected via Paystack (in ZAR)

All monetary amounts are South African Rand (ZAR) ``Decimal`` values quantised
to 2 decimal places.  We anchor subscription prices in USD and collect in ZAR
(see :mod:`app.ee.billing.fx`); an invoice records the FX rate used so the
amount is reproducible.

VAT policy
----------
VAT is charged **only when the business entity is VAT-registered** — i.e. when
``settings.COMPANY_VAT_NUMBER`` is a non-empty string.  If you are not
VAT-registered, leave it blank and invoices carry no VAT line.  See
:func:`business_info_from_settings`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

# Two-decimal money quantum (cents).
_CENTS = Decimal("0.01")

LineItemKind = Literal["subscription", "overage", "credit", "adjustment", "wallet"]
InvoiceStatus = Literal["draft", "pending", "paid", "past_due", "void"]


def _money(value: Decimal | int | float | str) -> Decimal:
    """Quantise *value* to 2 decimal places (ZAR cents), rounding half-up."""
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(_CENTS, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Business entity (issuer) — sourced from settings / .env
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BusinessInfo:
    """The legal entity that issues invoices (the "from" / seller).

    Populated from ``app.config.settings`` (env-driven) via
    :func:`business_info_from_settings`.  ``vat_number`` empty ⇒ not
    VAT-registered ⇒ no VAT is charged.
    """

    name: str
    legal_name: str
    reg_number: str
    vat_number: str
    vat_rate: Decimal
    address: str
    email: str
    website: str
    currency: str = "ZAR"
    invoice_number_prefix: str = "NUBI"

    @property
    def is_vat_registered(self) -> bool:
        """True when a (non-empty) VAT number is configured."""
        return bool(self.vat_number.strip())


def business_info_from_settings(settings: Any | None = None) -> BusinessInfo:
    """Build a :class:`BusinessInfo` from app settings (env-driven).

    Parameters
    ----------
    settings:
        An ``app.config.Settings`` instance; when ``None`` the live
        ``app.config.settings`` singleton is used.  Passing an explicit object
        keeps this testable without mutating global env.
    """
    if settings is None:
        from app.config import settings as _settings  # noqa: PLC0415

        settings = _settings

    legal_name = (getattr(settings, "COMPANY_LEGAL_NAME", "") or "").strip()
    name = (getattr(settings, "COMPANY_NAME", "") or "Nubi").strip()
    return BusinessInfo(
        name=name,
        legal_name=legal_name or name,
        reg_number=(getattr(settings, "COMPANY_REG_NUMBER", "") or "").strip(),
        vat_number=(getattr(settings, "COMPANY_VAT_NUMBER", "") or "").strip(),
        vat_rate=Decimal(str(getattr(settings, "COMPANY_VAT_RATE", 0.15) or 0)),
        address=(getattr(settings, "COMPANY_ADDRESS", "") or "").strip(),
        email=(getattr(settings, "COMPANY_EMAIL", "") or "billing@nubi.io").strip(),
        website=(getattr(settings, "COMPANY_WEBSITE", "") or "").strip(),
        currency=(getattr(settings, "INVOICE_CURRENCY", "") or "ZAR").strip(),
        invoice_number_prefix=(getattr(settings, "INVOICE_NUMBER_PREFIX", "") or "NUBI").strip(),
    )


# ---------------------------------------------------------------------------
# Line items
# ---------------------------------------------------------------------------


@dataclass
class InvoiceLineItem:
    """One line on an invoice.

    ``amount_zar`` is the line total (``quantity × unit_price_zar`` for metered
    overages, or a flat amount for the subscription).  ``quantity`` /
    ``unit_price_zar`` are optional descriptive fields for metered lines.
    """

    description: str
    amount_zar: Decimal
    kind: LineItemKind = "overage"
    quantity: Decimal | None = None
    unit: str | None = None
    unit_price_zar: Decimal | None = None

    def __post_init__(self) -> None:
        self.amount_zar = _money(self.amount_zar)
        if self.unit_price_zar is not None:
            self.unit_price_zar = _money(self.unit_price_zar)

    def to_dict(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "kind": self.kind,
            "quantity": str(self.quantity) if self.quantity is not None else None,
            "unit": self.unit,
            "unit_price_zar": str(self.unit_price_zar) if self.unit_price_zar is not None else None,
            "amount_zar": str(self.amount_zar),
        }


# ---------------------------------------------------------------------------
# Invoice
# ---------------------------------------------------------------------------


@dataclass
class Invoice:
    """A single billing-cycle invoice for one organisation (amounts in ZAR)."""

    org_id: str
    tier: str
    period_start: datetime
    period_end: datetime
    customer_email: str
    line_items: list[InvoiceLineItem]
    business: BusinessInfo
    customer_name: str = ""
    currency: str = "ZAR"
    fx_rate: Decimal | None = None
    # Wallet credit applied to overages this cycle (informational; not billed
    # again on the invoice — it was prepaid).
    wallet_applied_zar: Decimal = Decimal("0.00")
    status: InvoiceStatus = "draft"
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    invoice_number: str = ""
    issued_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    paid_at: datetime | None = None
    paystack_reference: str | None = None
    pdf_filename: str | None = None
    notes: str = ""

    # Computed totals (set in __post_init__ / recompute()).
    subtotal_zar: Decimal = Decimal("0.00")
    vat_rate: Decimal = Decimal("0.00")
    vat_amount_zar: Decimal = Decimal("0.00")
    total_zar: Decimal = Decimal("0.00")

    def __post_init__(self) -> None:
        self.wallet_applied_zar = _money(self.wallet_applied_zar)
        self.recompute()

    def recompute(self) -> None:
        """Recompute subtotal, VAT, and total from the current line items.

        VAT is applied only when the issuing business is VAT-registered
        (``business.is_vat_registered``).  Otherwise the VAT line is zero and
        the total equals the subtotal.
        """
        self.subtotal_zar = _money(sum((li.amount_zar for li in self.line_items), Decimal("0")))
        if self.business.is_vat_registered:
            self.vat_rate = self.business.vat_rate
            self.vat_amount_zar = _money(self.subtotal_zar * self.vat_rate)
        else:
            self.vat_rate = Decimal("0.00")
            self.vat_amount_zar = Decimal("0.00")
        self.total_zar = _money(self.subtotal_zar + self.vat_amount_zar)

    @property
    def total_zar_cents(self) -> int:
        """Total in ZAR cents (kobo) — the unit Paystack expects."""
        return int((self.total_zar * 100).to_integral_value(rounding=ROUND_HALF_UP))

    def mark_paid(self, *, paystack_reference: str, paid_at: datetime | None = None) -> None:
        self.status = "paid"
        self.paystack_reference = paystack_reference
        self.paid_at = paid_at or datetime.now(timezone.utc)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "invoice_number": self.invoice_number,
            "org_id": self.org_id,
            "tier": self.tier,
            "status": self.status,
            "currency": self.currency,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "issued_at": self.issued_at.isoformat(),
            "paid_at": self.paid_at.isoformat() if self.paid_at else None,
            "customer_email": self.customer_email,
            "customer_name": self.customer_name,
            "line_items": [li.to_dict() for li in self.line_items],
            "subtotal_zar": str(self.subtotal_zar),
            "vat_rate": str(self.vat_rate),
            "vat_amount_zar": str(self.vat_amount_zar),
            "total_zar": str(self.total_zar),
            "wallet_applied_zar": str(self.wallet_applied_zar),
            "fx_rate": str(self.fx_rate) if self.fx_rate is not None else None,
            "paystack_reference": self.paystack_reference,
            "pdf_filename": self.pdf_filename,
            "vat_registered": self.business.is_vat_registered,
            "vat_number": self.business.vat_number,
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# Invoice numbering
# ---------------------------------------------------------------------------


def format_invoice_number(prefix: str, year: int, sequence: int) -> str:
    """Return a human-readable invoice number, e.g. ``NUBI-2026-000123``."""
    return f"{prefix}-{year}-{sequence:06d}"


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_invoice(
    *,
    org_id: str,
    tier: str,
    period_start: datetime,
    period_end: datetime,
    customer_email: str,
    line_items: list[InvoiceLineItem],
    business: BusinessInfo | None = None,
    customer_name: str = "",
    fx_rate: Decimal | None = None,
    wallet_applied_zar: Decimal = Decimal("0.00"),
    invoice_number: str = "",
    issued_at: datetime | None = None,
    notes: str = "",
) -> Invoice:
    """Construct an :class:`Invoice`, computing subtotal / VAT / total.

    VAT is applied only when *business* is VAT-registered.  When *business* is
    ``None`` it is loaded from settings.
    """
    if business is None:
        business = business_info_from_settings()
    inv = Invoice(
        org_id=str(org_id),
        tier=tier,
        period_start=period_start,
        period_end=period_end,
        customer_email=customer_email,
        customer_name=customer_name,
        line_items=line_items,
        business=business,
        currency=business.currency,
        fx_rate=fx_rate,
        wallet_applied_zar=wallet_applied_zar,
        invoice_number=invoice_number,
        notes=notes,
    )
    if issued_at is not None:
        inv.issued_at = issued_at
    return inv
