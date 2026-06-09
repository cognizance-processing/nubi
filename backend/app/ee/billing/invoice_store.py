"""Invoice persistence — dual InMemory + Pg pattern.

Stores :class:`~app.ee.billing.invoice.Invoice` records and allocates
human-readable per-year invoice numbers (e.g. ``NUBI-2026-000123``).

Two implementations
-------------------
PgInvoiceStore       — asyncpg-backed; reads/writes the ``invoices`` and
                       ``invoice_counters`` tables (migration 0027).
InMemoryInvoiceStore — dict-backed; used in tests / simulation (no DB).

The module-level singleton is obtained via :func:`get_invoice_store`; tests
swap in an :class:`InMemoryInvoiceStore` via
:func:`set_invoice_store_for_tests`.
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.ee.billing.invoice import (
    BusinessInfo,
    Invoice,
    InvoiceLineItem,
    format_invoice_number,
)


def _invoice_db_row(inv: Invoice) -> dict[str, Any]:
    """Flatten an :class:`Invoice` to a JSON-serialisable row dict."""
    d = inv.to_dict()
    d["business"] = asdict(inv.business)
    # Decimal in business snapshot → str for JSON.
    d["business"]["vat_rate"] = str(inv.business.vat_rate)
    return d


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------


class InvoiceStore:
    """Interface for invoice storage + numbering."""

    async def next_invoice_number(self, prefix: str, year: int) -> str:
        """Allocate and return the next invoice number for *year*."""
        raise NotImplementedError

    async def save_invoice(self, invoice: Invoice) -> Invoice:
        """Persist (insert or update) *invoice*; returns it."""
        raise NotImplementedError

    async def get_invoice(self, invoice_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    async def list_invoices(self, org_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """Return invoices for *org_id*, newest first."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# In-memory implementation (tests / simulation)
# ---------------------------------------------------------------------------


class InMemoryInvoiceStore(InvoiceStore):
    """Dict-backed invoice store for tests and the billing simulation."""

    def __init__(self) -> None:
        self._invoices: dict[str, dict[str, Any]] = {}  # id -> row dict
        self._counters: dict[int, int] = {}             # year -> last seq

    def reset(self) -> None:
        self._invoices.clear()
        self._counters.clear()

    async def next_invoice_number(self, prefix: str, year: int) -> str:
        nxt = self._counters.get(year, 0) + 1
        self._counters[year] = nxt
        return format_invoice_number(prefix, year, nxt)

    async def save_invoice(self, invoice: Invoice) -> Invoice:
        self._invoices[invoice.id] = _invoice_db_row(invoice)
        return invoice

    async def get_invoice(self, invoice_id: str) -> dict[str, Any] | None:
        row = self._invoices.get(invoice_id)
        return deepcopy(row) if row is not None else None

    async def list_invoices(self, org_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = [r for r in self._invoices.values() if r["org_id"] == str(org_id)]
        rows.sort(key=lambda r: r["issued_at"], reverse=True)
        return deepcopy(rows[:limit])


# ---------------------------------------------------------------------------
# Postgres implementation
# ---------------------------------------------------------------------------


class PgInvoiceStore(InvoiceStore):
    """asyncpg-backed invoice store (migration 0027)."""

    async def next_invoice_number(self, prefix: str, year: int) -> str:
        from app.db import fetchrow  # noqa: PLC0415

        # Atomic upsert-and-increment; returns the new value.
        row = await fetchrow(
            """
            INSERT INTO invoice_counters (year, last_value)
            VALUES ($1, 1)
            ON CONFLICT (year) DO UPDATE SET
                last_value = invoice_counters.last_value + 1
            RETURNING last_value
            """,
            year,
        )
        return format_invoice_number(prefix, year, int(row["last_value"]))

    async def save_invoice(self, invoice: Invoice) -> Invoice:
        from app.db import execute  # noqa: PLC0415

        d = _invoice_db_row(invoice)
        await execute(
            """
            INSERT INTO invoices
                (id, org_id, invoice_number, tier, status, currency,
                 period_start, period_end, issued_at, paid_at,
                 customer_email, customer_name, line_items, business,
                 subtotal_zar, vat_rate, vat_amount_zar, total_zar,
                 wallet_applied_zar, fx_rate, vat_number,
                 paystack_reference, pdf_filename, notes)
            VALUES
                ($1::uuid, $2::uuid, $3, $4, $5, $6,
                 $7, $8, $9, $10,
                 $11, $12, $13::jsonb, $14::jsonb,
                 $15, $16, $17, $18,
                 $19, $20, $21,
                 $22, $23, $24)
            ON CONFLICT (id) DO UPDATE SET
                status             = EXCLUDED.status,
                paid_at            = EXCLUDED.paid_at,
                paystack_reference = EXCLUDED.paystack_reference,
                pdf_filename       = EXCLUDED.pdf_filename,
                line_items         = EXCLUDED.line_items,
                subtotal_zar       = EXCLUDED.subtotal_zar,
                vat_rate           = EXCLUDED.vat_rate,
                vat_amount_zar     = EXCLUDED.vat_amount_zar,
                total_zar          = EXCLUDED.total_zar,
                wallet_applied_zar = EXCLUDED.wallet_applied_zar,
                notes              = EXCLUDED.notes
            """,
            invoice.id,
            invoice.org_id,
            invoice.invoice_number,
            invoice.tier,
            invoice.status,
            invoice.currency,
            invoice.period_start,
            invoice.period_end,
            invoice.issued_at,
            invoice.paid_at,
            invoice.customer_email,
            invoice.customer_name,
            json.dumps(d["line_items"]),
            json.dumps(d["business"]),
            invoice.subtotal_zar,
            invoice.vat_rate,
            invoice.vat_amount_zar,
            invoice.total_zar,
            invoice.wallet_applied_zar,
            invoice.fx_rate,
            invoice.business.vat_number,
            invoice.paystack_reference,
            invoice.pdf_filename,
            invoice.notes,
        )
        return invoice

    async def get_invoice(self, invoice_id: str) -> dict[str, Any] | None:
        from app.db import fetchrow  # noqa: PLC0415

        row = await fetchrow(
            "SELECT * FROM invoices WHERE id = $1::uuid",
            invoice_id,
        )
        return _row_to_dict(row) if row is not None else None

    async def list_invoices(self, org_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        from app.db import fetch  # noqa: PLC0415

        rows = await fetch(
            """
            SELECT * FROM invoices
            WHERE org_id = $1::uuid
            ORDER BY issued_at DESC
            LIMIT $2
            """,
            org_id,
            limit,
        )
        return [_row_to_dict(r) for r in rows]


def _row_to_dict(row: Any) -> dict[str, Any]:
    """Normalise an asyncpg invoices row into the public dict shape."""
    d = dict(row)
    # asyncpg returns numeric as Decimal, jsonb as str/loaded depending on codec.
    for k in ("subtotal_zar", "vat_rate", "vat_amount_zar", "total_zar", "wallet_applied_zar", "fx_rate"):
        if isinstance(d.get(k), Decimal):
            d[k] = str(d[k])
    for k in ("line_items", "business"):
        v = d.get(k)
        if isinstance(v, str):
            d[k] = json.loads(v)
    for k in ("id", "org_id"):
        if d.get(k) is not None:
            d[k] = str(d[k])
    for k in ("period_start", "period_end", "issued_at", "paid_at", "created_at"):
        v = d.get(k)
        if isinstance(v, datetime):
            d[k] = v.isoformat()
    return d


# ---------------------------------------------------------------------------
# Helper: reconstruct an Invoice from a stored row (e.g. for PDF re-render)
# ---------------------------------------------------------------------------


def invoice_from_row(row: dict[str, Any]) -> Invoice:
    """Reconstruct an :class:`Invoice` from a stored row dict.

    Used to re-render a PDF on demand without keeping the object in memory.
    """
    biz = row.get("business") or {}
    business = BusinessInfo(
        name=biz.get("name", "Nubi"),
        legal_name=biz.get("legal_name", biz.get("name", "Nubi")),
        reg_number=biz.get("reg_number", ""),
        vat_number=biz.get("vat_number", ""),
        vat_rate=Decimal(str(biz.get("vat_rate", "0"))),
        address=biz.get("address", ""),
        email=biz.get("email", ""),
        website=biz.get("website", ""),
        currency=biz.get("currency", row.get("currency", "ZAR")),
        invoice_number_prefix=biz.get("invoice_number_prefix", "NUBI"),
    )
    items = [
        InvoiceLineItem(
            description=li["description"],
            amount_zar=Decimal(str(li["amount_zar"])),
            kind=li.get("kind", "overage"),
            quantity=Decimal(str(li["quantity"])) if li.get("quantity") is not None else None,
            unit=li.get("unit"),
            unit_price_zar=Decimal(str(li["unit_price_zar"])) if li.get("unit_price_zar") is not None else None,
        )
        for li in row.get("line_items", [])
    ]
    inv = Invoice(
        org_id=str(row["org_id"]),
        tier=row["tier"],
        period_start=_parse_dt(row["period_start"]),
        period_end=_parse_dt(row["period_end"]),
        customer_email=row.get("customer_email", ""),
        customer_name=row.get("customer_name", ""),
        line_items=items,
        business=business,
        currency=row.get("currency", "ZAR"),
        fx_rate=Decimal(str(row["fx_rate"])) if row.get("fx_rate") is not None else None,
        wallet_applied_zar=Decimal(str(row.get("wallet_applied_zar", "0"))),
        status=row.get("status", "pending"),
        id=str(row["id"]),
        invoice_number=row.get("invoice_number", ""),
        paystack_reference=row.get("paystack_reference"),
        pdf_filename=row.get("pdf_filename"),
        notes=row.get("notes", ""),
    )
    if row.get("issued_at"):
        inv.issued_at = _parse_dt(row["issued_at"])
    if row.get("paid_at"):
        inv.paid_at = _parse_dt(row["paid_at"])
    inv.recompute()
    return inv


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


# ---------------------------------------------------------------------------
# Provider singleton
# ---------------------------------------------------------------------------

_invoice_store: InvoiceStore | None = None


def set_invoice_store_for_tests(store: InvoiceStore | None) -> None:
    """Inject a test double, or pass ``None`` to restore the default Pg store."""
    global _invoice_store  # noqa: PLW0603
    _invoice_store = store


def get_invoice_store() -> InvoiceStore:
    """Return the active :class:`InvoiceStore` singleton (lazy Pg default)."""
    global _invoice_store  # noqa: PLW0603
    if _invoice_store is None:
        _invoice_store = PgInvoiceStore()
    return _invoice_store
