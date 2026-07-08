"""Unit tests for app/chat/render.py (render_chart_png).

``TestRenderChartPng`` (below) covers the base cases — bar/line/pie/scatter/
empty/missing-columns return bytes starting with the PNG magic header. This
used to live in test_chat_gateway.py (the removed Slack/WhatsApp inbound
gateway module); render_chart_png itself is gateway-agnostic (Nubi ships it as
a standalone chart-to-PNG utility), so its tests moved here on gateway removal.

Additional cases
----------------
1. title field is rendered without error (smoke — no assertion on pixel content).
2. pyarrow Table input (``to_pylist()`` path) is accepted and returns PNG bytes.
3. Non-numeric y values coerce to 0.0 instead of raising.
4. Single-row dataset works for all four non-pie chart types.
5. Unrecognised chart type falls back to bar (still returns PNG).
6. Missing y column key in rows → all values coerce to 0 (fallback to 0).
7. Large dataset (>10 rows) exercises the xticklabels step-skip logic without error.

All tests are offline and require only matplotlib + pyarrow (already in requirements.txt).
"""

from __future__ import annotations


import pytest

from app.chat.render import render_chart_png

PNG_MAGIC = b"\x89PNG"

# ---------------------------------------------------------------------------
# Helper rows
# ---------------------------------------------------------------------------

SIMPLE_ROWS = [
    {"region": "EMEA", "revenue": 100},
    {"region": "APAC", "revenue": 200},
    {"region": "AMER", "revenue": 150},
]

SCATTER_ROWS = [
    {"x": 1.0, "y": 10.0},
    {"x": 2.0, "y": 20.0},
    {"x": 3.0, "y": 15.0},
]


# ---------------------------------------------------------------------------
# 0. Base coverage — moved from the removed test_chat_gateway.py
# ---------------------------------------------------------------------------


class TestRenderChartPng:
    """render_chart_png must return PNG bytes starting with the magic header."""

    def test_bar_chart_returns_png(self):
        rows = [
            {"region": "North", "revenue": 120},
            {"region": "South", "revenue": 95},
        ]
        spec = {"type": "bar", "title": "Test", "x": "region", "y": "revenue"}
        result = render_chart_png(spec, rows)
        assert isinstance(result, bytes)
        assert result[:4] == b"\x89PNG", f"Expected PNG magic, got {result[:4]!r}"
        assert len(result) > 100  # sanity — should be many bytes

    def test_line_chart_returns_png(self):
        rows = [{"x": i, "y": i * 2} for i in range(5)]
        spec = {"type": "line", "x": "x", "y": "y"}
        result = render_chart_png(spec, rows)
        assert result[:4] == b"\x89PNG"

    def test_pie_chart_returns_png(self):
        rows = [{"label": "A", "val": 30}, {"label": "B", "val": 70}]
        spec = {"type": "pie", "x": "label", "y": "val"}
        result = render_chart_png(spec, rows)
        assert result[:4] == b"\x89PNG"

    def test_scatter_chart_returns_png(self):
        rows = [{"x": float(i), "y": float(i) ** 2} for i in range(5)]
        spec = {"type": "scatter", "x": "x", "y": "y"}
        result = render_chart_png(spec, rows)
        assert result[:4] == b"\x89PNG"

    def test_empty_rows_returns_png(self):
        """An empty dataset should still produce a valid (possibly blank) chart."""
        spec = {"type": "bar", "x": "region", "y": "revenue"}
        result = render_chart_png(spec, [])
        assert result[:4] == b"\x89PNG"

    def test_missing_columns_fallback(self):
        """When x/y are absent from spec, falls back to first two column names."""
        rows = [{"alpha": 1, "beta": 2}, {"alpha": 3, "beta": 4}]
        spec = {"type": "bar"}
        result = render_chart_png(spec, rows)
        assert result[:4] == b"\x89PNG"

    def test_returns_bytes_not_str(self):
        rows = [{"x": "A", "y": 10}]
        spec = {"type": "bar", "x": "x", "y": "y"}
        result = render_chart_png(spec, rows)
        assert isinstance(result, bytes)


# ---------------------------------------------------------------------------
# 1. title field
# ---------------------------------------------------------------------------


class TestRenderChartPngTitle:
    def test_bar_with_title_returns_png(self):
        spec = {"type": "bar", "title": "Revenue by Region", "x": "region", "y": "revenue"}
        result = render_chart_png(spec, SIMPLE_ROWS)
        assert result[:4] == PNG_MAGIC, "expected PNG bytes when title is set"

    def test_line_with_long_title_returns_png(self):
        spec = {"type": "line", "title": "A" * 120, "x": "region", "y": "revenue"}
        result = render_chart_png(spec, SIMPLE_ROWS)
        assert result[:4] == PNG_MAGIC

    def test_title_empty_string_returns_png(self):
        spec = {"type": "bar", "title": "", "x": "region", "y": "revenue"}
        result = render_chart_png(spec, SIMPLE_ROWS)
        assert result[:4] == PNG_MAGIC


# ---------------------------------------------------------------------------
# 2. pyarrow Table input
# ---------------------------------------------------------------------------


class TestRenderChartPngPyarrowInput:
    def test_accepts_pyarrow_table(self):
        """If pyarrow is available, a Table with to_pylist() is accepted."""
        pa = pytest.importorskip("pyarrow")

        table = pa.table(
            {"region": ["EMEA", "APAC", "AMER"], "revenue": [100, 200, 150]}
        )
        spec = {"type": "bar", "x": "region", "y": "revenue"}
        result = render_chart_png(spec, table)
        assert result[:4] == PNG_MAGIC, "pyarrow Table input must return PNG bytes"

    def test_accepts_pyarrow_table_for_line(self):
        pa = pytest.importorskip("pyarrow")

        table = pa.table(
            {"month": ["Jan", "Feb", "Mar"], "sales": [10.0, 20.0, 15.0]}
        )
        spec = {"type": "line", "x": "month", "y": "sales"}
        result = render_chart_png(spec, table)
        assert result[:4] == PNG_MAGIC


# ---------------------------------------------------------------------------
# 3. Non-numeric y values coerce to 0.0
# ---------------------------------------------------------------------------


class TestRenderChartPngNonNumericY:
    def test_string_y_values_coerce_to_zero(self):
        rows = [
            {"label": "a", "value": "not-a-number"},
            {"label": "b", "value": None},
            {"label": "c", "value": ""},
        ]
        spec = {"type": "bar", "x": "label", "y": "value"}
        # Must not raise; should return valid PNG (possibly all-zero bars)
        result = render_chart_png(spec, rows)
        assert result[:4] == PNG_MAGIC

    def test_mixed_numeric_and_string_y_values(self):
        rows = [
            {"label": "a", "value": 100},
            {"label": "b", "value": "bad"},
            {"label": "c", "value": 200},
        ]
        spec = {"type": "bar", "x": "label", "y": "value"}
        result = render_chart_png(spec, rows)
        assert result[:4] == PNG_MAGIC


# ---------------------------------------------------------------------------
# 4. Single-row dataset
# ---------------------------------------------------------------------------


class TestRenderChartPngSingleRow:
    def test_single_row_bar(self):
        rows = [{"region": "EMEA", "revenue": 100}]
        spec = {"type": "bar", "x": "region", "y": "revenue"}
        result = render_chart_png(spec, rows)
        assert result[:4] == PNG_MAGIC

    def test_single_row_line(self):
        rows = [{"month": "Jan", "sales": 50}]
        spec = {"type": "line", "x": "month", "y": "sales"}
        result = render_chart_png(spec, rows)
        assert result[:4] == PNG_MAGIC

    def test_single_row_scatter(self):
        rows = [{"x": 1.0, "y": 1.0}]
        spec = {"type": "scatter", "x": "x", "y": "y"}
        result = render_chart_png(spec, rows)
        assert result[:4] == PNG_MAGIC

    def test_single_row_pie(self):
        rows = [{"label": "only", "val": 100}]
        spec = {"type": "pie", "x": "label", "y": "val"}
        result = render_chart_png(spec, rows)
        assert result[:4] == PNG_MAGIC


# ---------------------------------------------------------------------------
# 5. Unrecognised chart type falls back to bar
# ---------------------------------------------------------------------------


class TestRenderChartPngUnknownType:
    def test_unknown_type_returns_png(self):
        spec = {"type": "heatmap", "x": "region", "y": "revenue"}
        result = render_chart_png(spec, SIMPLE_ROWS)
        assert result[:4] == PNG_MAGIC, "unrecognised type must fall back to bar"

    def test_none_type_returns_png(self):
        spec = {"type": None, "x": "region", "y": "revenue"}
        result = render_chart_png(spec, SIMPLE_ROWS)
        assert result[:4] == PNG_MAGIC, "None type must fall back to bar"

    def test_absent_type_returns_png(self):
        spec = {"x": "region", "y": "revenue"}
        result = render_chart_png(spec, SIMPLE_ROWS)
        assert result[:4] == PNG_MAGIC, "absent type must fall back to bar"


# ---------------------------------------------------------------------------
# 6. Missing y key in rows → coerce to 0
# ---------------------------------------------------------------------------


class TestRenderChartPngMissingYKey:
    def test_missing_y_key_coerces_to_zero(self):
        rows = [
            {"region": "EMEA"},  # 'revenue' key absent
            {"region": "APAC", "revenue": 200},
        ]
        spec = {"type": "bar", "x": "region", "y": "revenue"}
        result = render_chart_png(spec, rows)
        assert result[:4] == PNG_MAGIC


# ---------------------------------------------------------------------------
# 7. Large dataset — step-skip logic
# ---------------------------------------------------------------------------


class TestRenderChartPngLargeDataset:
    def _large_rows(self, n: int):
        return [{"idx": str(i), "val": float(i)} for i in range(n)]

    def test_large_bar_chart(self):
        rows = self._large_rows(50)
        spec = {"type": "bar", "x": "idx", "y": "val"}
        result = render_chart_png(spec, rows)
        assert result[:4] == PNG_MAGIC

    def test_large_line_chart(self):
        rows = self._large_rows(50)
        spec = {"type": "line", "x": "idx", "y": "val"}
        result = render_chart_png(spec, rows)
        assert result[:4] == PNG_MAGIC
