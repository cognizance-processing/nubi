"""Tests for the first-class ``placement`` field on dashboard Widgets.

Coverage
--------
1. Widget model:
   - ``placement`` defaults to 'grid'.
   - ``effective_placement`` resolves the legacy ``drawer=True`` (with the
     default placement) to 'drawer', while an explicit ``placement`` wins.
2. validate_spec (pure):
   - a header-placed filter widget validates with no hard errors.
   - a non-filter/text widget placed in the header emits a soft '[warn]'.
3. spec_to_html (pure):
   - emits a ``nubi-filter-bar`` container (before the grid) holding the header
     widgets ordered by ``order``.
   - OMITS the bar entirely when there are no header widgets (byte-identical to
     the prior render).
   - drawer widgets still render (drawer behavior unchanged).
   - tab scoping: a header filter with a ``tab_id`` shows in that tab's bar.

Mirrors test_dashboard_validate.py / test_widget_metric_binding.py
(validate_spec + spec_to_html pure tests, no app/db needed).
"""

from __future__ import annotations

from typing import Any

from app.dashboards.spec import (
    Widget,
    effective_placement,
    spec_to_html,
    validate_spec,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pos() -> dict[str, int]:
    return {"x": 1, "y": 1, "w": 4, "h": 3}


def _filter_widget(
    wid: str = "f1",
    *,
    placement: str | None = None,
    drawer: bool | None = None,
    order: int = 0,
    tab_id: str | None = None,
) -> dict[str, Any]:
    w: dict[str, Any] = {
        "id": wid,
        "type": "filter",
        "subtype": "select",
        "target_var": "region",
        "pos": _pos(),
        "order": order,
    }
    if placement is not None:
        w["placement"] = placement
    if drawer is not None:
        w["drawer"] = drawer
    if tab_id is not None:
        w["tab_id"] = tab_id
    return w


def _text_widget(wid: str, content: str = "hello") -> dict[str, Any]:
    return {"id": wid, "type": "text", "content": content, "pos": _pos()}


def _kpi_widget(wid: str = "k1", **extra: Any) -> dict[str, Any]:
    w: dict[str, Any] = {
        "id": wid,
        "type": "kpi",
        "query_id": "q1",
        "pos": _pos(),
    }
    w.update(extra)
    return w


def _spec(widgets: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"version": 1, "title": "Placement", "widgets": widgets}
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# 1. Widget model — field default + effective_placement
# ---------------------------------------------------------------------------


class TestPlacementField:
    def test_defaults_to_grid(self):
        w = Widget(id="w1", type="kpi", query_id="q1", pos=_pos())
        assert w.placement == "grid"
        assert w.effective_placement() == "grid"
        assert effective_placement(w) == "grid"

    def test_legacy_drawer_true_resolves_to_drawer(self):
        # drawer=True + default placement => effective 'drawer'.
        w = Widget(
            id="w1", type="filter", subtype="select", target_var="r",
            pos=_pos(), drawer=True,
        )
        assert w.placement == "grid"  # stored value is untouched
        assert w.effective_placement() == "drawer"
        assert effective_placement(w) == "drawer"

    def test_explicit_placement_wins_over_drawer(self):
        # An explicit placement is honoured even if drawer were True.
        w = Widget(
            id="w1", type="filter", subtype="select", target_var="r",
            pos=_pos(), drawer=True, placement="header",
        )
        assert w.effective_placement() == "header"

    def test_explicit_drawer_placement(self):
        w = Widget(
            id="w1", type="filter", subtype="select", target_var="r",
            pos=_pos(), placement="drawer",
        )
        assert w.effective_placement() == "drawer"


# ---------------------------------------------------------------------------
# 2. validate_spec — header filter validates; non-filter header warns
# ---------------------------------------------------------------------------


class TestPlacementValidation:
    def test_header_filter_validates(self):
        spec, issues = validate_spec(
            _spec([_filter_widget("f1", placement="header")])
        )
        assert spec is not None
        # No hard errors; no header soft-warning for a filter widget.
        assert [i for i in issues if not i.startswith("[warn]")] == []
        assert not any("placement 'header'" in i for i in issues)

    def test_header_text_validates(self):
        w = _text_widget("t1")
        w["placement"] = "header"
        spec, issues = validate_spec(_spec([w]))
        assert spec is not None
        assert not any("placement 'header'" in i for i in issues)

    def test_non_filter_header_emits_soft_warning(self):
        spec, issues = validate_spec(
            _spec([_kpi_widget("k1", placement="header")])
        )
        assert spec is not None  # not a hard failure
        warn = next(i for i in issues if "placement 'header'" in i)
        assert warn.startswith("[warn]")
        assert "k1" in warn


# ---------------------------------------------------------------------------
# 3. spec_to_html — filter bar emission, omission, drawer + tab scoping
# ---------------------------------------------------------------------------


class TestPlacementHtml:
    def test_filter_bar_emitted_with_header_widgets(self):
        spec, _ = validate_spec(
            _spec([_filter_widget("f1", placement="header")])
        )
        html_out = spec_to_html(spec)
        assert 'class="nubi-filter-bar"' in html_out
        assert "nubi-filter-bar__item" in html_out
        assert "<nubi-filter" in html_out

    def test_no_bar_when_no_header_widgets_byte_identical(self):
        widgets = [_kpi_widget("k1")]
        spec_with, _ = validate_spec(_spec(widgets))
        html_with = spec_to_html(spec_with)
        # No header widget => no filter bar at all.
        assert "nubi-filter-bar" not in html_with

    def test_header_widgets_ordered_by_order(self):
        # Two header filters with distinct target-vars; the one with the lower
        # 'order' must render first regardless of document order.
        late = _filter_widget("late", placement="header", order=5)
        late["target_var"] = "zzz_var"
        early = _filter_widget("early", placement="header", order=1)
        early["target_var"] = "aaa_var"
        spec, _ = validate_spec(_spec([late, early]))
        html_out = spec_to_html(spec)
        assert html_out.index('target-var="aaa_var"') < html_out.index(
            'target-var="zzz_var"'
        )

    def test_filter_bar_precedes_grid(self):
        spec, _ = validate_spec(
            _spec([_filter_widget("f1", placement="header"), _kpi_widget("k1")])
        )
        html_out = spec_to_html(spec)
        assert html_out.index("nubi-filter-bar") < html_out.index("nubi-kpi")

    def test_drawer_widget_still_rendered(self):
        # Legacy drawer=True filter still renders (drawer behavior unchanged) and
        # is NOT placed in a filter bar.
        spec, _ = validate_spec(
            _spec(
                [
                    _filter_widget("f1", drawer=True, placement=None),
                    _kpi_widget("k1"),
                ]
            )
        )
        html_out = spec_to_html(spec)
        assert "nubi-filter-bar" not in html_out
        assert "<nubi-filter" in html_out  # drawer filter still emitted

    def test_grid_unchanged_for_plain_spec(self):
        spec, _ = validate_spec(_spec([_kpi_widget("k1")]))
        html_out = spec_to_html(spec)
        assert 'class="nubi-widget nubi-widget--kpi"' in html_out
        assert "nubi-filter-bar" not in html_out

    def test_header_widget_tab_scoped(self):
        # A header filter bound to tab 't2' must render in t2's section, not t1.
        widgets = [
            _filter_widget("f2", placement="header", tab_id="t2"),
            _kpi_widget("k1", tab_id="t1"),
        ]
        spec, issues = validate_spec(
            _spec(
                widgets,
                tabs=[
                    {"id": "t1", "label": "One"},
                    {"id": "t2", "label": "Two"},
                ],
            )
        )
        assert spec is not None
        # No header soft-warning (the filter is a valid header widget); the only
        # issue is the unknown-query forward-ref warning for the kpi.
        assert not any("placement 'header'" in i for i in issues)
        html_out = spec_to_html(spec)
        # The filter bar appears after the t2 section heading, after t1's content.
        t2_idx = html_out.index('data-tab-id="t2"')
        bar_idx = html_out.index("nubi-filter-bar")
        assert bar_idx > t2_idx
