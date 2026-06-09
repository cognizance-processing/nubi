"""Dependency-free PDF builder (Python standard library only).

A small, reusable PDF generator that emits a *real* ``%PDF-1.4`` file (valid
xref table, openable by any reader) using only the 14 standard PDF fonts
(Helvetica / Helvetica-Bold) and raw content-stream operators — no reportlab,
weasyprint, or system libraries.  This keeps the OSS build and the test
environment dependency-light.

This lives in OSS core so any feature can render a PDF.  The EE invoice
renderer (``app.ee.billing.invoice_pdf``) and the scheduled-report renderer
(``app.jobs.report``) both build on it.  (Core never imports EE; EE may import
core, so the shared primitive belongs here.)

Public API
----------
``Pdf``                       — the multi-page content builder.
``esc`` / ``text_width``      — text helpers (PDF-literal escaping, metrics).
Page geometry + brand palette constants for A4 layouts.
"""

from __future__ import annotations

# ── Page geometry (A4, points) ──────────────────────────────────────────────
PAGE_W = 595.28
PAGE_H = 841.89
MARGIN = 48.0
CONTENT_W = PAGE_W - 2 * MARGIN

# ── Brand palette (0..1 RGB) ────────────────────────────────────────────────
NAVY = (0.071, 0.094, 0.165)
TEAL = (0.078, 0.553, 0.498)
INK = (0.13, 0.15, 0.20)
MUTED = (0.42, 0.45, 0.52)
HAIR = (0.85, 0.87, 0.90)
ZEBRA = (0.965, 0.972, 0.980)

# Helvetica AFM widths (1/1000 em) for the ASCII range we use — enough for
# accurate right-alignment of money/number columns.  Index = the character.
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


def text_width(text: str, size: float) -> float:
    """Approximate rendered width of *text* at *size* pt in Helvetica."""
    return sum(_char_width(c) for c in text) * size / 1000.0


# Common non-Latin-1 punctuation → ASCII so text renders cleanly.
_UNICODE_FOLD = {
    "—": "-", "–": "-", "−": "-",
    "‘": "'", "’": "'", "‚": ",",
    "“": '"', "”": '"',
    "…": "...", "•": "*", "·": "·",
    " ": " ",
}


def esc(text: str) -> str:
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


def truncate(text: str, max_w: float, size: float) -> str:
    """Truncate *text* with an ellipsis so it fits within *max_w* pt."""
    if text_width(text, size) <= max_w:
        return text
    ell = "..."
    while text and text_width(text + ell, size) > max_w:
        text = text[:-1]
    return text + ell


class Pdf:
    """Minimal multi-page PDF content builder (Helvetica / Helvetica-Bold).

    Coordinates are PDF user space: origin bottom-left, y grows upward.
    """

    def __init__(self) -> None:
        self._pages: list[list[str]] = []
        self._ops: list[str] = []
        self.new_page()

    def new_page(self) -> None:
        self._ops = []
        self._pages.append(self._ops)

    # ── primitives ──────────────────────────────────────────────────────────
    def text(self, x: float, y: float, size: float, s: str, *, bold: bool = False,
             color: tuple = INK) -> None:
        font = "F2" if bold else "F1"
        r, g, b = color
        self._ops.append(
            f"BT /{font} {size:.2f} Tf {r:.3f} {g:.3f} {b:.3f} rg "
            f"1 0 0 1 {x:.2f} {y:.2f} Tm ({esc(s)}) Tj ET"
        )

    def text_right(self, x_right: float, y: float, size: float, s: str, *,
                   bold: bool = False, color: tuple = INK) -> None:
        self.text(x_right - text_width(s, size), y, size, s, bold=bold, color=color)

    def rect_fill(self, x: float, y: float, w: float, h: float, color: tuple) -> None:
        r, g, b = color
        self._ops.append(f"{r:.3f} {g:.3f} {b:.3f} rg {x:.2f} {y:.2f} {w:.2f} {h:.2f} re f")

    def line(self, x1: float, y1: float, x2: float, y2: float, *, color: tuple = HAIR,
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

        font1 = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
        font2 = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"

        page_obj_nums: list[int] = []
        next_num = 5  # catalog=1, pages=2, f1=3, f2=4
        page_specs = []
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
                + f"{PAGE_W:.2f} {PAGE_H:.2f}".encode()
                + b"] /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> "
                b"/Contents " + str(content_num).encode() + b" 0 R >>"
            )
            add(page)        # page_num
            add(stream)      # content_num

        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = [0]
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
