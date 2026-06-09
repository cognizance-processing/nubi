"""Dependency-free invoice PDF generator.

Produces a clean, professional A4 invoice PDF for an
:class:`~app.ee.billing.invoice.Invoice` using only the Python standard
library — no reportlab / weasyprint / system libraries.  This keeps the OSS
build and the test environment dependency-light while still emitting a *real*
PDF (valid ``%PDF-1.4`` with xref table) that any reader can open.

The generator uses the 14 standard PDF fonts (Helvetica / Helvetica-Bold),
which never need embedding, and lays out text + simple vector graphics
(rules, filled header band, the totals box) via raw content-stream operators.
It paginates automatically if an invoice has many line items.

Public API
----------
``render_invoice_pdf(invoice) -> bytes``  — the only entry point callers need.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.ee.billing.invoice import Invoice

# ── Page geometry (A4, points) ──────────────────────────────────────────────
_PAGE_W = 595.28
_PAGE_H = 841.89
_MARGIN = 48.0
_CONTENT_W = _PAGE_W - 2 * _MARGIN

# ── Brand palette (0..1 RGB) ────────────────────────────────────────────────
_NAVY = (0.071, 0.094, 0.165)     # #12182a-ish
_TEAL = (0.078, 0.553, 0.498)     # #148d7f-ish
_INK = (0.13, 0.15, 0.20)
_MUTED = (0.42, 0.45, 0.52)
_HAIR = (0.85, 0.87, 0.90)
_ZEBRA = (0.965, 0.972, 0.980)

# Helvetica AFM widths (1/1000 em) for the ASCII range we use — enough for
# accurate right-alignment of money columns.  Index = ord(char).
_HELV_WIDTHS = {
    " ": 278, "!": 278, '"': 355, "#": 556, "$": 556, "%": 889, "&": 667,
    "'": 191, "(": 333, ")": 333, "*": 389, "+": 584, ",": 278, "-": 333,
    ".": 278, "/": 278, "0": 556, "1": 556, "2": 556, "3": 556, "4": 556,
    "5": 556, "6": 556, "7": 556, "8": 556, "9": 556, ":": 278, ";": 278,
    "<": 584, "=": 584, ">": 584, "?": 556, "@": 1015, "[": 278, "\\": 278,
    "]": 278, "^": 469, "_": 556, "`": 333, "{": 334, "|": 260, "}": 334,
    "~": 584,
}
_HELV_UPPER = 667  # rough avg for A-Z
_HELV_LOWER = 540  # rough avg for a-z


def _char_width(ch: str) -> int:
    if ch in _HELV_WIDTHS:
        return _HELV_WIDTHS[ch]
    if ch.isupper():
        return _HELV_UPPER
    if ch.islower():
        return _HELV_LOWER
    return 556


def _text_width(text: str, size: float) -> float:
    return sum(_char_width(c) for c in text) * size / 1000.0


# Common non-Latin-1 punctuation → ASCII so descriptions render cleanly.
_UNICODE_FOLD = {
    "—": "-", "–": "-", "−": "-",   # em / en dash, minus
    "‘": "'", "’": "'", "‚": ",",   # curly single quotes
    "“": '"', "”": '"',                   # curly double quotes
    "…": "...", "•": "*", "·": "·",  # ellipsis, bullet, middot
    " ": " ",                                   # nbsp
}


def _esc(text: str) -> str:
    """Escape a string for a PDF literal, folding common Unicode to ASCII."""
    out = []
    for ch in text:
        ch = _UNICODE_FOLD.get(ch, ch)
        if ch in "()\\":
            out.append("\\" + ch)
        elif ord(ch) < 32 or ord(ch) > 255:
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def _fmt_zar(amount: Decimal | str, currency: str = "R") -> str:
    d = Decimal(str(amount))
    sign = "-" if d < 0 else ""
    d = abs(d)
    whole, frac = divmod(int((d * 100).to_integral_value()), 100)
    grouped = f"{whole:,}".replace(",", " ")
    return f"{sign}{currency}{grouped}.{frac:02d}"


class _Pdf:
    """Minimal multi-page PDF content builder (Helvetica / Helvetica-Bold)."""

    def __init__(self) -> None:
        self._pages: list[list[str]] = []
        self._ops: list[str] = []
        self.new_page()

    def new_page(self) -> None:
        self._ops = []
        self._pages.append(self._ops)

    # ── primitives ──────────────────────────────────────────────────────────
    def text(self, x: float, y: float, size: float, s: str, *, bold: bool = False,
             color: tuple = _INK) -> None:
        font = "F2" if bold else "F1"
        r, g, b = color
        self._ops.append(
            f"BT /{font} {size:.2f} Tf {r:.3f} {g:.3f} {b:.3f} rg "
            f"1 0 0 1 {x:.2f} {y:.2f} Tm ({_esc(s)}) Tj ET"
        )

    def text_right(self, x_right: float, y: float, size: float, s: str, *,
                   bold: bool = False, color: tuple = _INK) -> None:
        self.text(x_right - _text_width(s, size), y, size, s, bold=bold, color=color)

    def rect_fill(self, x: float, y: float, w: float, h: float, color: tuple) -> None:
        r, g, b = color
        self._ops.append(f"{r:.3f} {g:.3f} {b:.3f} rg {x:.2f} {y:.2f} {w:.2f} {h:.2f} re f")

    def line(self, x1: float, y1: float, x2: float, y2: float, *, color: tuple = _HAIR,
             width: float = 0.75) -> None:
        r, g, b = color
        self._ops.append(
            f"{width:.2f} w {r:.3f} {g:.3f} {b:.3f} RG "
            f"{x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S"
        )

    # ── serialise ─────────────────────────────────────────────────────────────
    def to_bytes(self) -> bytes:
        objects: list[bytes] = []

        def add(obj: bytes) -> int:
            objects.append(obj)
            return len(objects)  # 1-based object number

        # Reserve: 1=Catalog, 2=Pages, 3=F1, 4=F2 then page+content pairs.
        font1 = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
        font2 = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"

        page_obj_nums: list[int] = []
        # Pre-compute object numbers: catalog=1, pages=2, f1=3, f2=4
        next_num = 5
        page_specs = []  # (page_num, content_num, content_bytes)
        for ops in self._pages:
            content = ("\n".join(ops)).encode("latin-1", "replace")
            stream = (
                b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n"
                + content + b"\nendstream"
            )
            page_num = next_num
            content_num = next_num + 1
            next_num += 2
            page_specs.append((page_num, content_num, stream))
            page_obj_nums.append(page_num)

        kids = " ".join(f"{n} 0 R" for n in page_obj_nums)
        catalog = b"<< /Type /Catalog /Pages 2 0 R >>"
        pages = (
            b"<< /Type /Pages /Kids [" + kids.encode() + b"] /Count "
            + str(len(page_obj_nums)).encode() + b" >>"
        )

        add(catalog)   # 1
        add(pages)     # 2
        add(font1)     # 3
        add(font2)     # 4
        for page_num, content_num, stream in page_specs:
            page = (
                b"<< /Type /Page /Parent 2 0 R "
                b"/MediaBox [0 0 "
                + f"{_PAGE_W:.2f} {_PAGE_H:.2f}".encode()
                + b"] /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> "
                b"/Contents " + str(content_num).encode() + b" 0 R >>"
            )
            add(page)        # page_num
            add(stream)      # content_num

        # Assemble file with xref.
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = [0]  # object 0 is the free head
        for i, obj in enumerate(objects, start=1):
            offsets.append(len(out))
            out += str(i).encode() + b" 0 obj\n" + obj + b"\nendobj\n"

        xref_pos = len(out)
        n = len(objects) + 1
        out += b"xref\n0 " + str(n).encode() + b"\n"
        out += b"0000000000 65535 f \n"
        for off in offsets[1:]:
            out += f"{off:010d} 00000 n \n".encode()
        out += (
            b"trailer\n<< /Size " + str(n).encode() + b" /Root 1 0 R >>\n"
            b"startxref\n" + str(xref_pos).encode() + b"\n%%EOF"
        )
        return bytes(out)


def _status_label(status: str) -> tuple[str, tuple]:
    return {
        "paid": ("PAID", _TEAL),
        "pending": ("DUE", (0.85, 0.55, 0.10)),
        "past_due": ("PAST DUE", (0.80, 0.20, 0.20)),
        "draft": ("DRAFT", _MUTED),
        "void": ("VOID", _MUTED),
    }.get(status, (status.upper(), _MUTED))


def render_invoice_pdf(invoice: Invoice) -> bytes:
    """Render *invoice* to PDF bytes (valid PDF-1.4, no external deps)."""
    biz = invoice.business
    cur = "R" if invoice.currency == "ZAR" else invoice.currency + " "
    pdf = _Pdf()
    y = _PAGE_H - _MARGIN

    # ── Header band ──
    pdf.rect_fill(0, _PAGE_H - 96, _PAGE_W, 96, _NAVY)
    pdf.text(_MARGIN, _PAGE_H - 52, 24, biz.name, bold=True, color=(1, 1, 1))
    label = "TAX INVOICE" if biz.is_vat_registered else "INVOICE"
    pdf.text_right(_PAGE_W - _MARGIN, _PAGE_H - 44, 15, label, bold=True, color=(0.62, 0.92, 0.86))
    sub_bits = [b for b in (biz.website, biz.email) if b]
    pdf.text_right(_PAGE_W - _MARGIN, _PAGE_H - 64, 9, "  ·  ".join(sub_bits), color=(0.70, 0.74, 0.82))
    pdf.text(_MARGIN, _PAGE_H - 74, 9, biz.legal_name, color=(0.70, 0.74, 0.82))

    y = _PAGE_H - 128

    # ── From / Bill-to columns ──
    col2 = _MARGIN + _CONTENT_W * 0.56
    pdf.text(_MARGIN, y, 8.5, "FROM", bold=True, color=_MUTED)
    pdf.text(col2, y, 8.5, "BILL TO", bold=True, color=_MUTED)
    y -= 15
    from_lines = [biz.legal_name]
    from_lines += [ln for ln in biz.address.split("\n") if ln.strip()]
    if biz.reg_number:
        from_lines.append(f"Reg: {biz.reg_number}")
    if biz.is_vat_registered:
        from_lines.append(f"VAT: {biz.vat_number}")
    to_lines = [invoice.customer_name or invoice.customer_email]
    if invoice.customer_name and invoice.customer_email:
        to_lines.append(invoice.customer_email)
    to_lines.append(f"Org: {invoice.org_id}")

    fy = y
    for ln in from_lines:
        pdf.text(_MARGIN, fy, 9.5, ln, color=_INK)
        fy -= 13
    ty = y
    for ln in to_lines:
        pdf.text(col2, ty, 9.5, ln, color=_INK)
        ty -= 13
    y = min(fy, ty) - 10

    # ── Invoice meta row ──
    pdf.line(_MARGIN, y, _PAGE_W - _MARGIN, y)
    y -= 18
    meta = [
        ("Invoice no.", invoice.invoice_number or invoice.id[:8]),
        ("Issued", invoice.issued_at.strftime("%d %b %Y")),
        ("Billing period", f"{invoice.period_start.strftime('%d %b')} - {invoice.period_end.strftime('%d %b %Y')}"),
        ("Plan", invoice.tier.capitalize()),
    ]
    mw = _CONTENT_W / len(meta)
    for i, (k, v) in enumerate(meta):
        x = _MARGIN + i * mw
        pdf.text(x, y, 8.5, k.upper(), bold=True, color=_MUTED)
        pdf.text(x, y - 14, 10.5, v, bold=True, color=_INK)
    status_txt, status_col = _status_label(invoice.status)
    pdf.text_right(_PAGE_W - _MARGIN, y - 14, 12, status_txt, bold=True, color=status_col)
    y -= 36

    # ── Line-item table header ──
    desc_x = _MARGIN + 4
    qty_x = _MARGIN + _CONTENT_W * 0.58
    unit_x = _MARGIN + _CONTENT_W * 0.78
    amt_x = _PAGE_W - _MARGIN - 4
    pdf.rect_fill(_MARGIN, y - 6, _CONTENT_W, 22, _NAVY)
    pdf.text(desc_x, y, 8.5, "DESCRIPTION", bold=True, color=(1, 1, 1))
    pdf.text_right(qty_x + 24, y, 8.5, "QTY", bold=True, color=(1, 1, 1))
    pdf.text_right(unit_x + 30, y, 8.5, "UNIT", bold=True, color=(1, 1, 1))
    pdf.text_right(amt_x, y, 8.5, "AMOUNT", bold=True, color=(1, 1, 1))
    y -= 22

    def ensure_space(needed: float) -> None:
        nonlocal y
        if y - needed < _MARGIN + 140:
            pdf.new_page()
            y = _PAGE_H - _MARGIN

    # Wallet credit is a payment applied after VAT (see Invoice.recompute),
    # so it is shown in the totals box rather than as a taxable line item.
    table_items = [li for li in invoice.line_items if li.kind != "wallet"]
    wallet_credit = -sum(
        (li.amount_zar for li in invoice.line_items if li.kind == "wallet"), Decimal("0")
    )

    row_h = 22.0
    for i, li in enumerate(table_items):
        ensure_space(row_h)
        if i % 2 == 1:
            pdf.rect_fill(_MARGIN, y - 6, _CONTENT_W, row_h, _ZEBRA)
        pdf.text(desc_x, y, 9.5, li.description, color=_INK)
        if li.quantity is not None:
            q = li.quantity
            if q == q.to_integral_value():
                qstr = f"{int(q):,}".replace(",", " ")
            else:
                qstr = f"{q.normalize():f}"
            pdf.text_right(qty_x + 24, y, 9.5, qstr, color=_MUTED)
        if li.unit_price_zar is not None:
            unit_lbl = _fmt_zar(li.unit_price_zar, cur)
            if li.unit:
                unit_lbl += f"/{li.unit}"
            pdf.text_right(unit_x + 30, y, 8.5, unit_lbl, color=_MUTED)
        pdf.text_right(amt_x, y, 9.5, _fmt_zar(li.amount_zar, cur), color=_INK)
        y -= row_h

    pdf.line(_MARGIN, y + 2, _PAGE_W - _MARGIN, y + 2)
    y -= 14

    # ── Totals box (right-aligned) ──
    ensure_space(120)
    box_x = _MARGIN + _CONTENT_W * 0.52
    box_w = _PAGE_W - _MARGIN - box_x
    label_r = box_x + box_w * 0.55
    val_r = _PAGE_W - _MARGIN - 6

    def total_row(label: str, value: str, *, bold: bool = False, color: tuple = _INK, size: float = 10.0) -> None:
        nonlocal y
        pdf.text(box_x + 4, y, size, label, bold=bold, color=color)
        pdf.text_right(val_r, y, size, value, bold=bold, color=color)
        y -= 18

    total_row("Subtotal", _fmt_zar(invoice.subtotal_zar, cur))
    if biz.is_vat_registered:
        vat_pct = (invoice.vat_rate * 100).quantize(Decimal("0.1"))
        total_row(f"VAT @ {vat_pct.normalize()}%", _fmt_zar(invoice.vat_amount_zar, cur))
    else:
        total_row("VAT", "Not VAT-registered", color=_MUTED, size=9.0)
    if wallet_credit > 0:
        total_row("Prepaid wallet credit", _fmt_zar(-wallet_credit, cur))
    pdf.line(box_x, y + 4, _PAGE_W - _MARGIN, y + 4, color=_NAVY, width=1.0)
    y -= 6
    pdf.rect_fill(box_x, y - 6, box_w, 24, _TEAL)
    pdf.text(box_x + 4, y, 12, "Total due" if invoice.status != "paid" else "Total paid",
             bold=True, color=(1, 1, 1))
    pdf.text_right(val_r, y, 12, _fmt_zar(invoice.total_zar, cur), bold=True, color=(1, 1, 1))
    y -= 30

    # ── Notes: wallet credit, FX, payment ──
    notes: list[str] = []
    if invoice.wallet_applied_zar and invoice.wallet_applied_zar > 0:
        notes.append(
            f"{_fmt_zar(invoice.wallet_applied_zar, cur)} of prepaid wallet credit was applied "
            f"to this invoice. Wallet top-ups are charged without VAT, so VAT (where applicable) "
            f"is charged on the full usage above and the credit reduces the amount due."
        )
    if invoice.fx_rate:
        notes.append(
            f"Prices are anchored in USD and billed in ZAR at R{invoice.fx_rate}/USD "
            f"(+2% buffer). The ZAR amount may vary slightly between cycles as the rate changes."
        )
    if invoice.paystack_reference:
        notes.append(f"Payment reference: {invoice.paystack_reference} (collected via Paystack).")
    if not biz.is_vat_registered:
        notes.append("No VAT charged: the issuing entity is not VAT-registered.")

    if notes:
        ensure_space(20 + 14 * len(notes))
        pdf.text(_MARGIN, y, 8.5, "NOTES", bold=True, color=_MUTED)
        y -= 14
        for note in notes:
            for wrapped in _wrap(note, 92):
                pdf.text(_MARGIN, y, 8.5, wrapped, color=_MUTED)
                y -= 12
            y -= 2

    # ── Footer ──
    pdf.line(_MARGIN, _MARGIN + 24, _PAGE_W - _MARGIN, _MARGIN + 24)
    foot = f"Thank you for your business.  Questions? {biz.email}"
    pdf.text(_MARGIN, _MARGIN + 10, 8.5, foot, color=_MUTED)
    pdf.text_right(_PAGE_W - _MARGIN, _MARGIN + 10, 8.5, biz.name, bold=True, color=_NAVY)

    return pdf.to_bytes()


def _wrap(text: str, width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


def invoice_pdf_filename(invoice: Invoice) -> str:
    """Stable, human-friendly filename for an invoice PDF."""
    base = invoice.invoice_number or f"invoice-{invoice.id[:8]}"
    return f"{base}.pdf".replace("/", "-").replace(" ", "_")


def render_invoice_pdf_from_row(row: dict[str, Any]) -> bytes:
    """Render a PDF from a stored invoice row dict (for on-demand download)."""
    from app.ee.billing.invoice_store import invoice_from_row  # noqa: PLC0415

    return render_invoice_pdf(invoice_from_row(row))
