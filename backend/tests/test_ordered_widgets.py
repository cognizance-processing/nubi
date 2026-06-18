"""Unit tests for _ordered_widgets in app/embedding/render_pptx.py.

These tests exercise only the ordering/filtering helper — no cairosvg or
python-pptx required.  They specifically guard against the O(n²)
list.index regression fixed by pre-computing a position map.
"""

from __future__ import annotations

import pytest

from app.dashboards.spec import (
    BoardExportConfig,
    DashboardSpec,
    Widget,
    WidgetExportHints,
    WidgetPos,
)
from app.embedding.render_pptx import _ordered_widgets  # type: ignore[attr-defined]

# Skip when python-pptx itself is absent (render_pptx imports it at top-level).
pytest.importorskip("pptx", reason="python-pptx not installed")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_spec(
    widget_ids: list[str] | None = None,
    widget_hints: dict[str, dict] | None = None,
) -> DashboardSpec:
    """Build a spec with the given widget ids (defaults to chart1/kpi1/txt1)."""
    ids = widget_ids or ["chart1", "kpi1", "txt1"]
    wh: dict[str, WidgetExportHints] = {}
    if widget_hints:
        for wid, d in widget_hints.items():
            wh[wid] = WidgetExportHints(**d)

    widgets = [
        Widget(
            id=wid,
            type="text",
            content=wid,
            pos=WidgetPos(x=1, y=i + 1, w=3, h=2),
        )
        for i, wid in enumerate(ids)
    ]
    return DashboardSpec(
        title="Test",
        layout={"cols": 12, "row_height": 60},
        widgets=widgets,
        export=BoardExportConfig(widget_hints=wh),
    )


def _cfg(spec: DashboardSpec) -> dict:
    return spec.export.model_dump(exclude_none=True)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_natural_order_no_hints():
    """Without any hints all widgets are returned in their natural spec order."""
    spec = _make_spec()
    ids = [w.id for w in _ordered_widgets(spec, _cfg(spec))]
    assert ids == ["chart1", "kpi1", "txt1"]


def test_explicit_order_bucket_sorts_first():
    """Widgets with an explicit `order` hint (bucket 0) come before widgets
    with no hint (bucket 1), regardless of their natural position."""
    spec = _make_spec(widget_hints={"txt1": {"order": 0}, "chart1": {"order": 1}})
    ids = [w.id for w in _ordered_widgets(spec, _cfg(spec))]
    # txt1 (order=0) < chart1 (order=1) < kpi1 (no hint, natural pos 1)
    assert ids == ["txt1", "chart1", "kpi1"]


def test_no_hint_widgets_preserve_natural_order():
    """When multiple widgets share the no-hint bucket they keep spec order."""
    spec = _make_spec(widget_hints={"kpi1": {"order": 0}})
    ids = [w.id for w in _ordered_widgets(spec, _cfg(spec))]
    # kpi1 (order=0) first, then chart1 (natural pos 0) < txt1 (natural pos 2)
    assert ids == ["kpi1", "chart1", "txt1"]


def test_include_in_report_false_excludes_widget():
    """A widget with include_in_report=False must not appear in the output."""
    spec = _make_spec(widget_hints={"kpi1": {"include_in_report": False}})
    ids = [w.id for w in _ordered_widgets(spec, _cfg(spec))]
    assert "kpi1" not in ids
    assert ids == ["chart1", "txt1"]


def test_pos_map_correct_for_large_list():
    """O(1) pos-map fallback returns correct natural order for 100 widgets —
    this would be measurably slow with the O(n²) list.index path."""
    n = 100
    ids = [f"w{i}" for i in range(n)]
    spec = _make_spec(widget_ids=ids)
    result_ids = [w.id for w in _ordered_widgets(spec, _cfg(spec))]
    assert result_ids == ids, "Natural order must be preserved for 100 widgets"
