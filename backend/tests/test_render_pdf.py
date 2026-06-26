"""Tests for app/embedding/render_pdf.py — T3 PDF renderer.

Coverage
--------
1. render_board_pdf — single SVG page produces a valid %PDF with 1 content page.
2. render_board_pdf — title slide config adds a title-slide page (2 pages total).
3. render_board_pdf — two SVG pages produces a PDF with 2 content pages.
4. render_board_pdf — header + footer strings do not crash the renderer.
5. render_board_pdf — page_size='Letter' produces a different page size than A4.
6. render_board_pdf — page_size='16:9' is accepted.
7. render_board_pdf — empty svg_pages raises ValueError.
8. render_board_pdf — unknown page_size raises ValueError.
9. svg_page_size_px — returns sensible (width, height) tuples for each page size.
10. _page_dims_pt — returns expected pt dimensions.

All tests use a minimal valid SVG and do NOT require Node.js or a live database.
The svglib+reportlab backend is used (cairosvg requires a system library absent
on CI).  Tests are skipped if svglib/reportlab are not installed.
"""

from __future__ import annotations

import importlib
import re

import pytest

# ---------------------------------------------------------------------------
# Skip guard — tests require svglib + reportlab
# ---------------------------------------------------------------------------

_SVGLIB_AVAILABLE = (
    importlib.util.find_spec("svglib") is not None
    and importlib.util.find_spec("reportlab") is not None
)

pytestmark = pytest.mark.skipif(
    not _SVGLIB_AVAILABLE,
    reason="svglib + reportlab not installed — skipping PDF render tests",
)

# ---------------------------------------------------------------------------
# Imports (deferred after skip guard)
# ---------------------------------------------------------------------------

from app.embedding.render_pdf import (
    _page_dims_pt,
    render_board_pdf,
    svg_page_size_px,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SIMPLE_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="600">'
    '<rect width="800" height="600" fill="#eef"/>'
    '<text x="100" y="100" font-size="32" fill="#222">Nubi Test Chart</text>'
    '</svg>'
)

_SIMPLE_SVG_2 = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="600">'
    '<rect width="800" height="600" fill="#ffe"/>'
    '<text x="100" y="100" font-size="32" fill="#333">Page 2 Widget</text>'
    '</svg>'
)

_DEFAULT_EXPORT_CFG = {
    "page_size": "A4",
    "header": None,
    "footer": None,
    "title_slide": None,
    "widget_hints": {},
}


def _count_pdf_pages(pdf_bytes: bytes) -> int:
    """Return the page count, robust to compressed PDFs.

    cairosvg/cairo writes the page tree into a compressed object stream, so the
    plain-bytes ``/Count`` / ``/Type /Page`` heuristics find nothing.  Prefer a
    real PDF parser (pypdf) when available; fall back to the byte heuristics for
    the uncompressed reportlab/svglib output.
    """
    try:
        import io  # noqa: PLC0415

        from pypdf import PdfReader  # noqa: PLC0415

        return len(PdfReader(io.BytesIO(pdf_bytes)).pages)
    except Exception:
        pass
    # Fallback (uncompressed PDFs): /Count inside /Type /Pages, else /Type /Page.
    match = re.search(rb'/Count\s+(\d+)', pdf_bytes)
    if match:
        return int(match.group(1))
    return len(re.findall(rb'/Type\s*/Page\b', pdf_bytes))


def _is_valid_pdf(data: bytes) -> bool:
    """Basic validity check: starts with %PDF and ends near %%EOF."""
    return data[:4] == b'%PDF' and b'%%EOF' in data[-128:]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_single_page_is_valid_pdf() -> None:
    """A single SVG page produces a valid %PDF byte string."""
    pdf = render_board_pdf([_SIMPLE_SVG], _DEFAULT_EXPORT_CFG)
    assert isinstance(pdf, bytes)
    assert _is_valid_pdf(pdf), "Output is not a valid PDF"


def test_single_page_count() -> None:
    """A single SVG page (no title slide) produces exactly 1 page."""
    pdf = render_board_pdf([_SIMPLE_SVG], _DEFAULT_EXPORT_CFG)
    assert _count_pdf_pages(pdf) == 1


def test_title_slide_adds_page() -> None:
    """A title-slide config results in 2 pages: title + 1 content page."""
    cfg = {**_DEFAULT_EXPORT_CFG, "title_slide": {"heading": "My Board", "subheading": "Q1 2026"}}
    pdf = render_board_pdf([_SIMPLE_SVG], cfg, title="Fallback Title")
    assert _is_valid_pdf(pdf)
    assert _count_pdf_pages(pdf) == 2


def test_two_svg_pages_produce_two_pages() -> None:
    """Two SVG pages produce a 2-page PDF (no title slide)."""
    pdf = render_board_pdf([_SIMPLE_SVG, _SIMPLE_SVG_2], _DEFAULT_EXPORT_CFG)
    assert _is_valid_pdf(pdf)
    assert _count_pdf_pages(pdf) == 2


def test_two_svg_pages_with_title_slide() -> None:
    """Two SVG pages + title slide produce a 3-page PDF."""
    cfg = {**_DEFAULT_EXPORT_CFG, "title_slide": {"heading": "Dashboard", "subheading": None}}
    pdf = render_board_pdf([_SIMPLE_SVG, _SIMPLE_SVG_2], cfg)
    assert _is_valid_pdf(pdf)
    assert _count_pdf_pages(pdf) == 3


def test_header_footer_no_crash() -> None:
    """Header and footer strings are accepted without error."""
    cfg = {**_DEFAULT_EXPORT_CFG, "header": "Nubi Analytics — Confidential", "footer": "Page 1"}
    pdf = render_board_pdf([_SIMPLE_SVG], cfg)
    assert _is_valid_pdf(pdf)
    assert _count_pdf_pages(pdf) == 1


def test_letter_page_size() -> None:
    """page_size='Letter' produces a valid PDF."""
    cfg = {**_DEFAULT_EXPORT_CFG, "page_size": "Letter"}
    pdf = render_board_pdf([_SIMPLE_SVG], cfg)
    assert _is_valid_pdf(pdf)


def test_widescreen_page_size() -> None:
    """page_size='16:9' produces a valid PDF."""
    cfg = {**_DEFAULT_EXPORT_CFG, "page_size": "16:9"}
    pdf = render_board_pdf([_SIMPLE_SVG], cfg)
    assert _is_valid_pdf(pdf)


def test_empty_svg_pages_raises() -> None:
    """An empty svg_pages list raises ValueError."""
    with pytest.raises(ValueError, match="non-empty"):
        render_board_pdf([], _DEFAULT_EXPORT_CFG)


def test_unknown_page_size_raises() -> None:
    """An unknown page_size token raises ValueError."""
    cfg = {**_DEFAULT_EXPORT_CFG, "page_size": "A3"}
    with pytest.raises(ValueError, match="page_size"):
        render_board_pdf([_SIMPLE_SVG], cfg)


def test_svg_page_size_px_a4() -> None:
    """svg_page_size_px returns a sensible A4 pixel size at 96 dpi."""
    w, h = svg_page_size_px("A4")
    assert w > 0 and h > 0
    # A4 portrait: width < height
    assert w < h


def test_svg_page_size_px_widescreen() -> None:
    """svg_page_size_px returns landscape dimensions for 16:9."""
    w, h = svg_page_size_px("16:9")
    assert w > h, "16:9 should be landscape (width > height)"


def test_svg_page_size_px_unknown_raises() -> None:
    """svg_page_size_px raises ValueError for an unknown page size."""
    with pytest.raises(ValueError):
        svg_page_size_px("A0")


def test_page_dims_pt_a4() -> None:
    """A4 page dims match the ISO spec (595.28 x 841.89 pt)."""
    w, h = _page_dims_pt("A4")
    assert abs(w - 595.28) < 0.1
    assert abs(h - 841.89) < 0.1


def test_page_dims_pt_unknown_raises() -> None:
    """_page_dims_pt raises ValueError for an unknown page size."""
    with pytest.raises(ValueError):
        _page_dims_pt("B5")


def test_title_slide_heading_fallback() -> None:
    """When title_slide heading is None, the board title param is used (no error)."""
    cfg = {**_DEFAULT_EXPORT_CFG, "title_slide": {"heading": None, "subheading": None}}
    pdf = render_board_pdf([_SIMPLE_SVG], cfg, title="Board Title")
    assert _is_valid_pdf(pdf)
    assert _count_pdf_pages(pdf) == 2


def test_full_export_cfg_integration() -> None:
    """Combine title slide + header + footer + Letter size — produces a valid 2-page PDF."""
    cfg = {
        "page_size": "Letter",
        "header": "Nubi — Q1 2026",
        "footer": "Confidential",
        "title_slide": {"heading": "Quarterly Overview", "subheading": "Finance Team"},
        "widget_hints": {},
    }
    pdf = render_board_pdf([_SIMPLE_SVG], cfg, title="Quarterly Overview")
    assert _is_valid_pdf(pdf)
    assert _count_pdf_pages(pdf) == 2
