"""Invoice email delivery.

Sends a generated invoice PDF to the customer.  Reuses the synchronous
:class:`app.jobs.report.EmailSender` protocol so billing and report emails
share one transport contract:

    sender.send(to, subject, body, attachment_name, attachment_data)

Transports
----------
``SmtpEmailSender`` — real delivery via ``smtplib`` (stdlib).  Used when
``settings.SMTP_HOST`` is configured.
``NullSender``      — in-memory no-op (from :mod:`app.jobs.report`); the default
                      in tests / local dev / OSS builds with no mail server.

:func:`get_default_sender` picks the transport from settings; :func:`send_invoice`
renders the subject/body and dispatches the PDF as an attachment.
"""

from __future__ import annotations

from typing import Any

from app.ee.billing.invoice import Invoice
# The generic SMTP transport lives in OSS core (app.jobs.report); billing
# reuses it (EE may import core). Re-exported here for backward compatibility.
from app.jobs.report import (  # noqa: F401
    EmailSender,
    NullSender,
    SmtpEmailSender,
    get_default_sender,
)


def _invoice_email_body(invoice: Invoice) -> tuple[str, str]:
    """Return (subject, plain-text body) for an invoice email."""
    biz = invoice.business
    cur = "R" if invoice.currency == "ZAR" else invoice.currency + " "
    total = f"{cur}{invoice.total_zar:,.2f}".replace(",", " ")
    period = (
        f"{invoice.period_start.strftime('%d %b %Y')} – "
        f"{invoice.period_end.strftime('%d %b %Y')}"
    )
    if invoice.status == "paid":
        subject = f"{biz.name} receipt {invoice.invoice_number} — {total} paid"
        opener = (
            f"Thanks — we've received your payment of {total} for your "
            f"{invoice.tier.capitalize()} plan."
        )
    else:
        subject = f"{biz.name} invoice {invoice.invoice_number} — {total} due"
        opener = (
            f"Here is your invoice for the {invoice.tier.capitalize()} plan, "
            f"totalling {total}."
        )

    lines = [
        f"Hi{(' ' + invoice.customer_name) if invoice.customer_name else ''},",
        "",
        opener,
        "",
        f"Invoice number : {invoice.invoice_number}",
        f"Billing period : {period}",
        f"Subtotal       : {cur}{invoice.subtotal_zar:,.2f}".replace(",", " "),
    ]
    if biz.is_vat_registered:
        vat_pct = (invoice.vat_rate * 100).normalize()
        lines.append(f"VAT @ {vat_pct}%    : {cur}{invoice.vat_amount_zar:,.2f}".replace(",", " "))
    if invoice.wallet_applied_zar and invoice.wallet_applied_zar > 0:
        wa = f"{cur}{invoice.wallet_applied_zar:,.2f}".replace(",", " ")
        lines.append(f"Wallet credit  : -{wa}")
    lines += [
        f"Total          : {total}",
        "",
        "Your itemised invoice is attached as a PDF.",
    ]
    if invoice.wallet_applied_zar and invoice.wallet_applied_zar > 0:
        wa = f"{cur}{invoice.wallet_applied_zar:,.2f}".replace(",", " ")
        lines.append(
            f"({wa} of prepaid wallet credit was applied to this invoice. "
            f"Wallet top-ups are charged without VAT, so VAT — where applicable — "
            f"is charged on your full usage and the credit reduces the amount due.)"
        )
    lines += [
        "",
        f"Questions? Just reply to this email or contact {biz.email}.",
        "",
        f"— {biz.name}",
    ]
    return subject, "\n".join(lines)


def send_invoice(
    invoice: Invoice,
    pdf_bytes: bytes,
    *,
    sender: EmailSender | None = None,
    settings: Any | None = None,
) -> dict[str, Any]:
    """Email *invoice* (with its PDF attached) to the customer.

    Parameters
    ----------
    invoice:
        The invoice to send.  ``customer_email`` must be set.
    pdf_bytes:
        The rendered invoice PDF.
    sender:
        An :class:`EmailSender`; defaults to :func:`get_default_sender`.
    settings:
        App settings (for transport selection); defaults to the live singleton.

    Returns
    -------
    dict
        ``{to, subject, attachment_name, delivered}``.  ``delivered`` is
        ``False`` when a ``NullSender`` was used (no real transport configured).
    """
    from app.ee.billing.invoice_pdf import invoice_pdf_filename  # noqa: PLC0415

    if not invoice.customer_email:
        raise ValueError("invoice.customer_email is required to send an invoice")

    sender = sender or get_default_sender(settings)
    subject, body = _invoice_email_body(invoice)
    filename = invoice.pdf_filename or invoice_pdf_filename(invoice)

    sender.send(
        to=invoice.customer_email,
        subject=subject,
        body=body,
        attachment_name=filename,
        attachment_data=pdf_bytes,
    )
    return {
        "to": invoice.customer_email,
        "subject": subject,
        "attachment_name": filename,
        "delivered": not isinstance(sender, NullSender),
    }
