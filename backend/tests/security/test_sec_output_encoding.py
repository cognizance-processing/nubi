"""Values that end up in HTML, SVG or spreadsheets are encoded for that destination."""

from __future__ import annotations

import pytest

from tests.security import conftest_helpers  # noqa: F401  (env bootstrap)

BREAKOUT = "</script><script>alert(document.domain)</script>"


def test_public_export_page_cannot_be_broken_out_of():
    from app.embedding.public_export import _render_html

    html = _render_html(
        board_title="Board",
        spec={"title": BREAKOUT, "widgets": [{"id": "w", "type": "text", "props": {"text": "<!-- " + BREAKOUT}}]},
        snapshot_descriptor={"artifact": {"tables": [{"name": BREAKOUT}]}, "policy_fingerprint": "abc"},
        snapshot_public_url="https://cdn.example/s.duckdb",
        exported_at="2026-01-01T00:00:00Z",
    )
    assert BREAKOUT not in html
    assert "</script><script>" not in html
    assert "<!--" not in html.split("<script type=\"module\">", 1)[1]


@pytest.mark.parametrize(
    "value,expected",
    [
        ("=HYPERLINK(\"http://evil\",\"x\")", "'=HYPERLINK(\"http://evil\",\"x\")"),
        ("+1+1", "'+1+1"),
        ("-2+3", "'-2+3"),
        ("@SUM(A1)", "'@SUM(A1)"),
        ("\t=1", "'\t=1"),
        ("\r=1", "'\r=1"),
        ("  =cmd|' /C calc'!A0", "'  =cmd|' /C calc'!A0"),
        ("plain text", "plain text"),
        ("-5", "-5"),
        ("-12.50", "-12.50"),
        ("+27 82 000 0000", "'+27 82 000 0000"),
        (-5, -5),
        (3.2, 3.2),
        (None, ""),
        (True, True),
    ],
)
def test_csv_cells_cannot_start_a_formula(value, expected):
    from app.routes.export_share import csv_safe_cell

    assert csv_safe_cell(value) == expected


def test_csv_export_uses_the_cell_guard():
    import inspect

    from app.routes import export_share

    src = inspect.getsource(export_share)
    assert src.count("csv_safe_cell(") >= 3  # definition + header row + data rows


def test_avatars_cannot_be_svg():
    from app.assets import service

    assert "image/svg+xml" not in service._ALLOWED_CONTENT_TYPES
    assert ".svg" not in service._MIME_TO_EXT.values()
