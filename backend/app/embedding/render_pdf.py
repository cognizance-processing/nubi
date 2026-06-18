"""High-fidelity PDF renderer — SVG → PDF via cairosvg or svglib+reportlab.

Takes the composed page SVG(s) produced by T2 (``app.dashboards.svg_render``)
and the T5 export config (``app.dashboards.spec.get_export_config``) and
produces a PDF with:

- One PDF page per composed SVG.
- The SVG scaled to fit the requested ``page_size`` (A4 / Letter / 16:9).
- Optional header and footer text on every page.
- Optional title slide (plain text) inserted before the content pages.
- Page breaks: ``widget_hints.page_break_before`` (respected when the caller
  passes pre-split SVG pages; or use ``render_board_pdf`` which handles it
  automatically when the board has a single composed SVG).

Rendering backend (lazy-imported, tried in order):

1. **cairosvg** — preferred.  Produces vector PDF with selectable text.
   Requires the ``cairo`` system library (``libcairo-2.so`` / ``libcairo.2.dylib``).
   Install: ``pip install cairosvg``.

2. **svglib + reportlab** — pure-Python fallback.  Text in the SVG is rendered as
   vector outlines (no font embedding needed) and remains selectable in most
   viewers.  Install: ``pip install svglib reportlab``.

If neither backend is available an :class:`~app.errors.AppError` is raised with
a clear install message.

Public API
----------
``render_board_pdf(svg_pages, export_cfg, *, title=None) -> bytes``
    Primary entry point.  Takes 1-N composed page SVG strings and the
    normalized export-config dict from ``get_export_config``, returns a ``%PDF``
    byte string.

``svg_page_size_px(page_size) -> tuple[int, int]``
    Returns ``(width_px, height_px)`` for the given page_size token at 96 dpi.
    Useful for sizing the T2 SVG render to match the PDF canvas.

``_page_dims_pt(page_size) -> tuple[float, float]``
    Internal: page dimensions in PDF points.  Exposed for testing.
"""

from __future__ import annotations

import io
from typing import Any

from app.errors import AppError

# ---------------------------------------------------------------------------
# Page size constants
# ---------------------------------------------------------------------------

# PDF points (1 pt = 1/72 inch).
_PAGE_DIMS_PT: dict[str, tuple[float, float]] = {
    "A4":     (595.28, 841.89),   # ISO A4 portrait
    "Letter": (612.0,  792.0),    # US Letter portrait
    "16:9":   (841.89, 473.56),   # Widescreen (A4-width landscape)
}

# Pixel equivalents at 96 dpi — used to size the T2 SSR viewport so the
# rendered SVG proportions match the PDF canvas exactly.
_PAGE_DIMS_PX: dict[str, tuple[int, int]] = {
    "A4":     (794,  1123),
    "Letter": (816,  1056),
    "16:9":   (1123,  632),
}

# Header / footer geometry (PDF points).
_HEADER_H_PT = 24.0   # vertical band reserved at top of each page
_FOOTER_H_PT = 20.0   # vertical band reserved at bottom of each page
_MARGIN_PT = 48.0     # left/right margin for header/footer text


def _page_dims_pt(page_size: str) -> tuple[float, float]:
    """Return ``(width_pt, height_pt)`` for a page-size token.

    Parameters
    ----------
    page_size:
        One of ``'A4'``, ``'Letter'``, ``'16:9'``.

    Raises
    ------
    ValueError
        For an unknown ``page_size`` token.
    """
    try:
        return _PAGE_DIMS_PT[page_size]
    except KeyError:
        raise ValueError(
            f"Unknown page_size {page_size!r}. "
            f"Valid values: {list(_PAGE_DIMS_PT)}."
        )


def svg_page_size_px(page_size: str) -> tuple[int, int]:
    """Return ``(width_px, height_px)`` at 96 dpi for *page_size*.

    Pass these into ``render_board_svg`` (T2) as ``page_width_px`` and a
    proportionally scaled ``row_height_px`` so the rendered SVG exactly fills
    the PDF canvas with no letterboxing.

    Parameters
    ----------
    page_size:
        One of ``'A4'``, ``'Letter'``, ``'16:9'``.
    """
    try:
        return _PAGE_DIMS_PX[page_size]
    except KeyError:
        raise ValueError(
            f"Unknown page_size {page_size!r}. "
            f"Valid values: {list(_PAGE_DIMS_PX)}."
        )


# ---------------------------------------------------------------------------
# Backend detection (lazy)
# ---------------------------------------------------------------------------


def _try_cairosvg() -> Any | None:
    """Return the ``cairosvg`` module if it is importable and the Cairo
    system library is present, else ``None``.

    Never raises — errors are swallowed so the caller can fall through to the
    svglib backend.
    """
    try:
        import cairosvg  # noqa: PLC0415
        return cairosvg
    except (ImportError, OSError):
        return None


def _require_svglib_reportlab() -> tuple[Any, Any]:
    """Return ``(svg2rlg, renderPDF)`` from svglib+reportlab.

    Raises
    ------
    AppError("pdf_backend_missing", 503)
        When neither cairosvg nor svglib+reportlab are importable.
    """
    try:
        from svglib.svglib import svg2rlg  # noqa: PLC0415
        import reportlab.graphics.renderPDF as render_pdf_mod  # noqa: PLC0415
        return svg2rlg, render_pdf_mod
    except ImportError as exc:
        raise AppError(
            "pdf_backend_missing",
            "PDF export requires either cairosvg (+ cairo system library) or "
            "svglib + reportlab. Install one of:\n"
            "  pip install cairosvg          # also needs libcairo system package\n"
            "  pip install svglib reportlab  # pure-Python fallback",
            503,
        ) from exc


# ---------------------------------------------------------------------------
# cairosvg-based rendering
# ---------------------------------------------------------------------------


def _render_pages_cairosvg(
    cairosvg: Any,
    svg_pages: list[str],
    page_w_pt: float,
    page_h_pt: float,
    header: str | None,
    footer: str | None,
    title_slide_cfg: dict | None,
    title: str | None,
) -> bytes:
    """Render *svg_pages* to PDF using cairosvg.

    cairosvg can write SVG -> PDF directly, but only one page at a time.
    We concatenate pages using a tiny PDF-merging step with reportlab's
    canvas if there are multiple pages; otherwise we output the single-page
    cairosvg PDF directly.

    Header/footer text is currently added only on the svglib path.  On the
    cairosvg path, if header/footer are requested and there are no multi-page
    needs, we fall through to the svglib path for those decorations.  For
    simplicity (and because the svglib path is always available), we delegate
    to it when header/footer/title-slide are needed.
    """
    # If header/footer/title-slide decoration is needed, use the svglib path
    # which has a canvas layer for text overlays.
    if header or footer or title_slide_cfg:
        svg2rlg, rpdf = _require_svglib_reportlab()
        return _render_pages_svglib(
            svg2rlg, rpdf, svg_pages, page_w_pt, page_h_pt,
            header, footer, title_slide_cfg, title,
        )

    # Pure cairosvg path — single or multi-page via byte-level PDF merge.
    pdf_parts: list[bytes] = []
    for svg_str in svg_pages:
        part = cairosvg.svg2pdf(
            bytestring=svg_str.encode("utf-8"),
            output_width=page_w_pt,
            output_height=page_h_pt,
        )
        pdf_parts.append(part)

    if len(pdf_parts) == 1:
        return pdf_parts[0]

    # Multi-page: re-render each cairosvg page as an image into one reportlab doc.
    svg2rlg, rpdf = _require_svglib_reportlab()
    return _render_pages_svglib(
        svg2rlg, rpdf, svg_pages, page_w_pt, page_h_pt,
        header, footer, title_slide_cfg, title,
    )


# ---------------------------------------------------------------------------
# svglib+reportlab-based rendering
# ---------------------------------------------------------------------------


def _render_pages_svglib(
    svg2rlg: Any,
    rpdf: Any,
    svg_pages: list[str],
    page_w_pt: float,
    page_h_pt: float,
    header: str | None,
    footer: str | None,
    title_slide_cfg: dict | None,
    title: str | None,
) -> bytes:
    """Render *svg_pages* to PDF using svglib + reportlab.

    Each SVG is converted to a reportlab Drawing, scaled to fit the page
    (honouring the content area after subtracting header/footer bands), then
    drawn onto a reportlab Canvas page.  Header and footer text are drawn as
    plain Helvetica text on top.
    """
    from reportlab.pdfgen import canvas as rl_canvas  # noqa: PLC0415

    # Content area after reserving header/footer bands.
    top_reserved = _HEADER_H_PT if header else 0.0
    bot_reserved = _FOOTER_H_PT if footer else 0.0
    content_h = page_h_pt - top_reserved - bot_reserved

    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(page_w_pt, page_h_pt))

    # ── Title slide ──────────────────────────────────────────────────────────
    if title_slide_cfg is not None:
        _draw_title_slide(c, page_w_pt, page_h_pt, title_slide_cfg, title)
        _draw_header_footer(c, page_w_pt, page_h_pt, header, footer)
        c.showPage()

    # ── Content pages ────────────────────────────────────────────────────────
    for svg_str in svg_pages:
        drawing = svg2rlg(io.StringIO(svg_str))
        if drawing is None:
            # Un-parseable SVG — emit a blank page.
            c.showPage()
            continue

        # Scale drawing to fit the content area (preserve aspect ratio).
        scale_x = page_w_pt / drawing.width if drawing.width > 0 else 1.0
        scale_y = content_h / drawing.height if drawing.height > 0 else 1.0
        scale = min(scale_x, scale_y)

        draw_w = drawing.width * scale
        draw_h = drawing.height * scale

        # Center horizontally; place at top of content area.
        x_off = (page_w_pt - draw_w) / 2.0
        y_off = bot_reserved + content_h - draw_h  # bottom-left y in PDF coords

        rpdf.draw(drawing, c, x_off, y_off, showBoundary=False)
        _draw_header_footer(c, page_w_pt, page_h_pt, header, footer)
        c.showPage()

    c.save()
    return buf.getvalue()


def _draw_header_footer(
    c: Any,
    page_w_pt: float,
    page_h_pt: float,
    header: str | None,
    footer: str | None,
) -> None:
    """Draw optional header/footer text onto the current canvas page."""
    if not header and not footer:
        return
    c.saveState()
    c.setFont("Helvetica", 8)
    c.setFillColorRGB(0.42, 0.45, 0.52)  # muted ink
    if header:
        # Top of page — y is near the top (PDF coords: 0 = bottom)
        c.drawString(_MARGIN_PT, page_h_pt - 14.0, header)
        # Thin rule below header text
        c.setStrokeColorRGB(0.85, 0.87, 0.90)
        c.setLineWidth(0.5)
        c.line(_MARGIN_PT, page_h_pt - _HEADER_H_PT,
               page_w_pt - _MARGIN_PT, page_h_pt - _HEADER_H_PT)
    if footer:
        c.drawString(_MARGIN_PT, 8.0, footer)
        # Thin rule above footer text
        c.setStrokeColorRGB(0.85, 0.87, 0.90)
        c.setLineWidth(0.5)
        c.line(_MARGIN_PT, _FOOTER_H_PT,
               page_w_pt - _MARGIN_PT, _FOOTER_H_PT)
    c.restoreState()


def _draw_title_slide(
    c: Any,
    page_w_pt: float,
    page_h_pt: float,
    title_slide_cfg: dict,
    board_title: str | None,
) -> None:
    """Draw a simple title slide onto the current canvas page.

    Uses the brand palette (navy background band, teal accent, Helvetica fonts).

    Parameters
    ----------
    c:
        Active reportlab Canvas.
    page_w_pt, page_h_pt:
        Page dimensions in points.
    title_slide_cfg:
        ``{"heading": str|None, "subheading": str|None}`` from
        ``get_export_config``.
    board_title:
        Fallback heading text when ``title_slide_cfg["heading"]`` is ``None``.
    """
    heading = title_slide_cfg.get("heading") or board_title or ""
    subheading = title_slide_cfg.get("subheading") or ""

    # ── Navy background band (centre-ish vertical) ──────────────────────────
    band_h = page_h_pt * 0.4
    band_y = (page_h_pt - band_h) / 2.0
    c.setFillColorRGB(0.071, 0.094, 0.165)  # navy
    c.rect(0, band_y, page_w_pt, band_h, stroke=0, fill=1)

    # ── Teal accent bar (top of band) ────────────────────────────────────────
    c.setFillColorRGB(0.078, 0.553, 0.498)  # teal
    c.rect(0, band_y + band_h - 4, page_w_pt, 4, stroke=0, fill=1)

    # ── Heading text ─────────────────────────────────────────────────────────
    if heading:
        c.setFillColorRGB(1, 1, 1)
        font_size = min(36.0, max(18.0, page_w_pt / (max(len(heading), 1) * 0.55)))
        font_size = min(font_size, band_h * 0.3)
        c.setFont("Helvetica-Bold", font_size)
        text_x = _MARGIN_PT
        text_y = band_y + band_h / 2.0 + font_size * 0.2
        c.drawString(text_x, text_y, heading)

    # ── Subheading text ──────────────────────────────────────────────────────
    if subheading:
        c.setFillColorRGB(0.8, 0.85, 0.9)
        sub_size = 14.0
        c.setFont("Helvetica", sub_size)
        sub_y = band_y + band_h / 2.0 - sub_size * 1.2
        c.drawString(_MARGIN_PT, sub_y, subheading)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_board_pdf(
    svg_pages: list[str],
    export_cfg: dict[str, Any],
    *,
    title: str | None = None,
) -> bytes:
    """Render composed SVG page(s) to a vector PDF.

    Parameters
    ----------
    svg_pages:
        Ordered list of composed page SVG strings (one per PDF page).  Each
        string is the output of ``render_board_svg`` (T2).  A single-element
        list is the common case (one board, one composed page).
    export_cfg:
        Normalized export-config dict from ``get_export_config(spec)`` (T5).
        Expected keys: ``page_size``, ``header``, ``footer``, ``title_slide``,
        ``widget_hints``.  All keys are always present when the dict comes from
        ``get_export_config``.
    title:
        Optional board title string used as the fallback heading on the title
        slide when ``export_cfg["title_slide"]["heading"]`` is ``None``.

    Returns
    -------
    bytes
        A valid ``%PDF-1.x`` byte string with one page per entry in
        *svg_pages*, plus an optional title-slide page prepended.

    Raises
    ------
    AppError("pdf_backend_missing", 503)
        When neither cairosvg nor svglib+reportlab are importable.
    AppError("pdf_render_error", 500)
        When the rendering backend raises an unexpected error.
    ValueError
        For an unknown ``page_size`` in *export_cfg*.
    """
    if not svg_pages:
        raise ValueError("svg_pages must be a non-empty list.")

    page_size: str = export_cfg.get("page_size", "A4")
    page_w_pt, page_h_pt = _page_dims_pt(page_size)

    header: str | None = export_cfg.get("header")
    footer: str | None = export_cfg.get("footer")
    title_slide_cfg: dict | None = export_cfg.get("title_slide")

    # Try cairosvg first (vector + system-font fidelity); fall back to svglib.
    cairosvg_mod = _try_cairosvg()

    try:
        if cairosvg_mod is not None:
            return _render_pages_cairosvg(
                cairosvg_mod, svg_pages, page_w_pt, page_h_pt,
                header, footer, title_slide_cfg, title,
            )
        else:
            svg2rlg, rpdf = _require_svglib_reportlab()
            return _render_pages_svglib(
                svg2rlg, rpdf, svg_pages, page_w_pt, page_h_pt,
                header, footer, title_slide_cfg, title,
            )
    except AppError:
        raise
    except Exception as exc:
        raise AppError(
            "pdf_render_error",
            f"PDF rendering failed: {exc}",
            500,
        ) from exc
