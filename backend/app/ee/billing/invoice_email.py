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

import smtplib
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any

from app.ee.billing.invoice import Invoice
from app.jobs.report import EmailSender, NullSender


class SmtpEmailSender:
    """Synchronous SMTP sender implementing the :class:`EmailSender` protocol."""

    def __init__(
        self,
        *,
        host: str,
        port: int = 587,
        username: str = "",
        password: str = "",
        use_tls: bool = True,
        from_addr: str = "",
        from_name: str = "",
        timeout: float = 20.0,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.use_tls = use_tls
        self.from_addr = from_addr
        self.from_name = from_name
        self.timeout = timeout

    def send(
        self,
        to: str,
        subject: str,
        body: str,
        attachment_name: str,
        attachment_data: bytes | str,
    ) -> None:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = formataddr((self.from_name or self.from_addr, self.from_addr))
        msg["To"] = to
        msg.set_content(body)

        data = attachment_data.encode("utf-8") if isinstance(attachment_data, str) else attachment_data
        maintype, subtype = ("application", "pdf") if attachment_name.endswith(".pdf") else ("application", "octet-stream")
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=attachment_name)

        if self.port == 465:
            with smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout) as srv:
                self._auth_and_send(srv, msg)
        else:
            with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as srv:
                if self.use_tls:
                    srv.starttls()
                self._auth_and_send(srv, msg)

    def _auth_and_send(self, srv: smtplib.SMTP, msg: EmailMessage) -> None:
        if self.username:
            srv.login(self.username, self.password)
        srv.send_message(msg)


def get_default_sender(settings: Any | None = None) -> EmailSender:
    """Return the configured email transport.

    ``SmtpEmailSender`` when ``SMTP_HOST`` is set, otherwise a ``NullSender``
    (no real delivery — invoices are still generated and recorded).
    """
    if settings is None:
        from app.config import settings as _settings  # noqa: PLC0415

        settings = _settings

    host = (getattr(settings, "SMTP_HOST", "") or "").strip()
    if not host:
        return NullSender()

    from_addr = (
        (getattr(settings, "SMTP_FROM", "") or "").strip()
        or (getattr(settings, "BILLING_EMAIL", "") or "").strip()
        or (getattr(settings, "COMPANY_EMAIL", "") or "").strip()
    )
    return SmtpEmailSender(
        host=host,
        port=int(getattr(settings, "SMTP_PORT", 587) or 587),
        username=(getattr(settings, "SMTP_USERNAME", "") or "").strip(),
        password=getattr(settings, "SMTP_PASSWORD", "") or "",
        use_tls=bool(getattr(settings, "SMTP_USE_TLS", True)),
        from_addr=from_addr,
        from_name=(getattr(settings, "COMPANY_NAME", "") or "Nubi").strip(),
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
