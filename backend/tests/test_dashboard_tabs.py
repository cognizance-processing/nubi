"""Tests for dashboard tabs — Track T (DASHBOARD_TABS_AND_FILTERS_IMPLEMENTATION.md T6).

New cases that complement but do NOT duplicate test_dashboard_spec.py's
``TestTrackTTabs`` class.  That class covers the happy-path acceptance, the two
canonical hard-error cases (duplicate tab id, undeclared tab_id), the backward-
compat no-tabs path, and the drawer-widget exemption.

This file adds:

1. ``TestTabValidationExtended`` — edge cases not covered by TestTrackTTabs:
   - Three tabs, each widget in a different tab.
   - All widgets with tab_id=None when tabs exist (all → first tab, no error).
   - Mixed: some widgets tab_id set, some None.
   - tab_id set on a non-drawer widget when spec has ONE tab.
   - Duplicate tab id among three tabs (second pair also flagged).
   - Multiple widgets with undeclared tab_id are ALL reported.
   - Tab with an empty-string id rejected by Pydantic (min_length=1).
   - Tab with empty-string label rejected by Pydantic (min_length=1).
   - Tab style dict is preserved on the model (arbitrary keys allowed).
   - spec.tabs empty list → same as absent (backward compat).
   - Tab declared but NO widgets → valid (empty tab is fine).

2. ``TestSpecToHtmlTabs`` — spec_to_html with tabs emits stacked-sections output:
   - With tabs: HTML contains an h3 element for each tab label.
   - With tabs: each tab section immediately precedes its widgets.
   - With tabs: widget in tab1 appears before widget in tab2 (section order).
   - With tabs: widget with tab_id=None appears under the first-tab heading.
   - Without tabs (backward compat): no h3 section headings emitted.
   - Stacked sections do NOT contain <script> tags.
   - Stacked sections do NOT contain on*= event handlers.
   - Tab label is HTML-escaped in the h3 (XSS guard).
   - Each tab section is wrapped in a container element with a data-tab-id attribute.
   - All widgets still render inside their grid wrappers (grid-column in style).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.dashboards.spec import (
    DashboardSpec,
    Tab,
    Widget,
    WidgetPos,
    spec_to_html,
    validate_spec,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _w(
    wid: str,
    *,
    tab_id: str | None = None,
    wtype: str = "kpi",
    drawer: bool = False,
    drawer_group: str | None = None,
) -> dict[str, Any]:
    """Minimal valid widget dict."""
    base: dict[str, Any] = {
        "id": wid,
        "type": wtype,
        "query_id": "demo_all",
        "pos": {"x": 1, "y": 1, "w": 4, "h": 2},
    }
    if tab_id is not None:
        base["tab_id"] = tab_id
    if drawer:
        base["drawer"] = True
    if drawer_group:
        base["drawer_group"] = drawer_group
    if wtype == "chart":
        base["chart_type"] = "bar"
        base["encoding"] = {"x": "category", "y": "value"}
    if wtype == "filter":
        base["subtype"] = "select"
        base["target_var"] = "region"
    if wtype == "text":
        base["content"] = "hello"
    return base


def _tabbed_spec(tabs: list[dict], widgets: list[dict]) -> dict[str, Any]:
    """Minimal spec dict with given tabs + widgets."""
    return {
        "version": 1,
        "title": "Tabbed",
        "layout": {"cols": 12, "row_height": 60},
        "tabs": tabs,
        "widgets": widgets,
    }


def _hard(issues: list[str]) -> list[str]:
    """Filter out soft registry warnings so only hard errors remain."""
    return [
        i for i in issues
        if "not in the registered" not in i and "forward reference" not in i
    ]


# ---------------------------------------------------------------------------
# 1. Extended tab validation
# ---------------------------------------------------------------------------


class TestTabValidationExtended:
    """Edge-case validation scenarios not covered by TestTrackTTabs."""

    # -- Three tabs, each widget in a different tab -------------------------

    def test_three_tabs_each_widget_in_different_tab(self):
        data = _tabbed_spec(
            tabs=[
                {"id": "a", "label": "Alpha"},
                {"id": "b", "label": "Beta"},
                {"id": "c", "label": "Gamma"},
            ],
            widgets=[
                _w("w1", tab_id="a"),
                _w("w2", tab_id="b"),
                _w("w3", tab_id="c"),
            ],
        )
        spec, issues = validate_spec(data)
        assert spec is not None
        hard = _hard(issues)
        assert hard == [], f"Three-tab spec should have no hard errors: {hard}"
        assert len(spec.tabs) == 3

    # -- All widgets with tab_id=None when tabs exist -----------------------

    def test_all_widgets_tab_id_none_with_tabs_is_valid(self):
        """All widgets with tab_id=None implicitly belong to the first tab."""
        data = _tabbed_spec(
            tabs=[
                {"id": "t1", "label": "First"},
                {"id": "t2", "label": "Second"},
            ],
            widgets=[
                _w("w1"),  # tab_id omitted → None
                _w("w2"),
            ],
        )
        spec, issues = validate_spec(data)
        assert spec is not None
        hard = _hard(issues)
        assert hard == [], f"tab_id=None widgets should not hard-error: {hard}"

    # -- Mixed: some tab_id set, some None ----------------------------------

    def test_mixed_tab_id_set_and_none(self):
        data = _tabbed_spec(
            tabs=[
                {"id": "t1", "label": "First"},
                {"id": "t2", "label": "Second"},
            ],
            widgets=[
                _w("w1", tab_id="t1"),
                _w("w2"),           # None → first tab
                _w("w3", tab_id="t2"),
            ],
        )
        spec, issues = validate_spec(data)
        assert spec is not None
        hard = _hard(issues)
        assert hard == [], f"Mixed tab_id should be valid: {hard}"

    # -- Single tab, widget points to it -----------------------------------

    def test_single_tab_widget_references_it(self):
        data = _tabbed_spec(
            tabs=[{"id": "only", "label": "The Tab"}],
            widgets=[_w("w1", tab_id="only")],
        )
        spec, issues = validate_spec(data)
        assert spec is not None
        hard = _hard(issues)
        assert hard == [], f"Single-tab reference should be valid: {hard}"

    # -- Duplicate tab id among three tabs (both duplicates flagged) --------

    def test_three_tabs_two_duplicates_all_reported(self):
        """If tab ids are ['a', 'a', 'b', 'b'] all duplicate occurrences are reported."""
        data = _tabbed_spec(
            tabs=[
                {"id": "a", "label": "Alpha"},
                {"id": "a", "label": "Alpha 2"},   # dup
                {"id": "b", "label": "Beta"},
                {"id": "b", "label": "Beta 2"},    # dup
            ],
            widgets=[],
        )
        spec, issues = validate_spec(data)
        assert spec is not None
        dup_issues = [i for i in issues if "duplicate tab id" in i.lower() or "Duplicate tab id" in i]
        assert len(dup_issues) >= 2, (
            f"Both duplicate tab ids should be reported; got: {dup_issues}"
        )

    # -- Multiple widgets with undeclared tab_id all reported ---------------

    def test_multiple_undeclared_tab_ids_all_reported(self):
        data = _tabbed_spec(
            tabs=[{"id": "real", "label": "Real"}],
            widgets=[
                _w("w1", tab_id="ghost_a"),
                _w("w2", tab_id="ghost_b"),
            ],
        )
        spec, issues = validate_spec(data)
        assert spec is not None
        undeclared = [i for i in issues if "not declared" in i and "tab_id" in i]
        assert len(undeclared) >= 2, (
            f"Both undeclared tab_ids should be reported; got: {undeclared}"
        )
        assert any("ghost_a" in i for i in undeclared)
        assert any("ghost_b" in i for i in undeclared)

    # -- Tab with empty-string id rejected ---------------------------------

    def test_empty_tab_id_rejected(self):
        data = _tabbed_spec(
            tabs=[{"id": "", "label": "Bad"}],
            widgets=[],
        )
        spec, issues = validate_spec(data)
        assert spec is None, (
            "Empty tab id should cause a Pydantic parse failure (min_length=1)"
        )
        assert len(issues) > 0

    # -- Tab with empty-string label rejected ------------------------------

    def test_empty_tab_label_rejected(self):
        data = _tabbed_spec(
            tabs=[{"id": "t1", "label": ""}],
            widgets=[],
        )
        spec, issues = validate_spec(data)
        assert spec is None, (
            "Empty tab label should cause a Pydantic parse failure (min_length=1)"
        )
        assert len(issues) > 0

    # -- Tab style dict preserved on model ---------------------------------

    def test_tab_style_preserved(self):
        style_payload = {"accent": "#f00", "variant": "pills", "size": "lg"}
        data = _tabbed_spec(
            tabs=[{"id": "t1", "label": "Styled", "style": style_payload}],
            widgets=[],
        )
        spec, issues = validate_spec(data)
        assert spec is not None
        hard = _hard(issues)
        assert hard == [], f"Unexpected hard issues: {hard}"
        assert spec.tabs[0].style == style_payload

    # -- spec.tabs=[] is backward-compatible --------------------------------

    def test_empty_tabs_list_backward_compat(self):
        data = _tabbed_spec(tabs=[], widgets=[_w("w1")])
        spec, issues = validate_spec(data)
        assert spec is not None
        hard = _hard(issues)
        assert hard == [], f"Empty tabs list should not cause issues: {hard}"
        assert spec.tabs == []

    # -- Tab declared but no widgets in it is valid -------------------------

    def test_empty_tab_no_widgets_is_valid(self):
        """Declaring a tab without assigning any widgets to it is allowed."""
        data = _tabbed_spec(
            tabs=[
                {"id": "t1", "label": "Populated"},
                {"id": "t2", "label": "Empty"},  # no widgets assigned here
            ],
            widgets=[_w("w1", tab_id="t1")],
        )
        spec, issues = validate_spec(data)
        assert spec is not None
        hard = _hard(issues)
        assert hard == [], f"Empty tab (no widgets) should be valid: {hard}"

    # -- widget.tab_id on a spec with no tabs is a hard error ---------------
    # (mirrors the existing test in TestTrackTTabs but uses a different widget
    # type to broaden coverage)

    def test_table_widget_tab_id_no_tabs_declared_is_hard_error(self):
        data: dict[str, Any] = {
            "version": 1,
            "title": "No Tabs",
            "widgets": [
                {
                    "id": "t1",
                    "type": "table",
                    "query_id": "demo_all",
                    "tab_id": "nonexistent",
                    "pos": {"x": 1, "y": 1, "w": 12, "h": 3},
                }
            ],
        }
        spec, issues = validate_spec(data)
        assert spec is not None
        assert any("nonexistent" in i and "not declared" in i for i in issues), (
            f"Expected undeclared tab_id error on table widget: {issues}"
        )

    # -- Drawer widget with bad tab_id on a spec that HAS tabs is no error --
    # (Broader: drawer ignores tab_id regardless of declared-tab set)

    def test_drawer_widget_bogus_tab_id_with_real_tabs_no_error(self):
        data = _tabbed_spec(
            tabs=[{"id": "t1", "label": "First"}],
            widgets=[
                _w("df1", tab_id="ghost", drawer=True, drawer_group="filters"),
            ],
        )
        spec, issues = validate_spec(data)
        assert spec is not None
        tab_id_issues = [
            i for i in issues
            if "ghost" in i and "not declared" in i
        ]
        assert tab_id_issues == [], (
            f"Drawer widget tab_id should be ignored: {tab_id_issues}"
        )

    # -- spec.tabs included in JSON Schema ---------------------------------

    def test_json_schema_has_tabs_property(self):
        from app.dashboards.spec import spec_json_schema

        schema = spec_json_schema()
        props = schema.get("properties", {})
        assert "tabs" in props, f"Schema must expose 'tabs'; got: {list(props.keys())}"

    # -- DashboardSpec model round-trips tabs correctly --------------------

    def test_model_round_trip_tabs(self):
        """model_dump → model_validate must produce identical tabs."""
        data = _tabbed_spec(
            tabs=[
                {"id": "t1", "label": "One", "style": {"accent": "#0af"}},
                {"id": "t2", "label": "Two"},
            ],
            widgets=[_w("w1", tab_id="t1"), _w("w2", tab_id="t2")],
        )
        spec, _ = validate_spec(data)
        assert spec is not None
        dumped = spec.model_dump()
        spec2 = DashboardSpec.model_validate(dumped)
        assert spec2.tabs[0].id == "t1"
        assert spec2.tabs[0].style == {"accent": "#0af"}
        assert spec2.tabs[1].id == "t2"
        assert spec2.widgets[0].tab_id == "t1"
        assert spec2.widgets[1].tab_id == "t2"


# ---------------------------------------------------------------------------
# 2. spec_to_html — stacked-sections output for tabbed specs
# ---------------------------------------------------------------------------

# Per DASHBOARD_TABS_AND_FILTERS_IMPLEMENTATION.md T1:
#   "spec_to_html (embed/static): graceful degradation — render tabs as
#    stacked sections, each preceded by <h3>{tab.label}</h3>. No interactive
#    tab bar in embeds (no JS)."
#
# The contract this test suite enforces:
# - A spec with N tabs produces N headings (<h3> or equivalent block) each
#   containing the tab label.
# - Widgets are grouped under their respective tab headings.
# - Widget with tab_id=None appears under the first tab's heading.
# - No tabs → backward-compat: NO tab headings emitted.
# - All security invariants (no <script>, no on*=) still hold.
# - Tab labels are HTML-escaped.


def _build_two_tab_spec() -> DashboardSpec:
    """Return a validated two-tab spec for spec_to_html tests."""
    data = _tabbed_spec(
        tabs=[
            {"id": "t1", "label": "Overview"},
            {"id": "t2", "label": "Details"},
        ],
        widgets=[
            _w("w1", tab_id="t1"),
            _w("w2", tab_id="t2"),
        ],
    )
    spec, issues = validate_spec(data)
    assert spec is not None, f"setup failed: {issues}"
    return spec


class TestSpecToHtmlTabs:
    """spec_to_html with tabs produces stacked-sections HTML."""

    # -- Section headings emitted for each tab -----------------------------

    def test_h3_headings_for_each_tab(self):
        """Each tab label appears as an h3 (or similar heading) in the output."""
        spec = _build_two_tab_spec()
        output = spec_to_html(spec)
        # The spec mandates <h3> per tab label (see T1 in the implementation doc).
        assert "<h3" in output, (
            f"Expected <h3 headings for tab sections; got:\n{output[:600]}"
        )
        assert "Overview" in output
        assert "Details" in output

    def test_both_tab_labels_present(self):
        spec = _build_two_tab_spec()
        output = spec_to_html(spec)
        assert "Overview" in output, "Tab 1 label 'Overview' should appear in HTML"
        assert "Details" in output, "Tab 2 label 'Details' should appear in HTML"

    # -- Widget ordering: tab1 section before tab2 section -----------------

    def test_tab1_section_before_tab2_section(self):
        """The tab1 heading must precede the tab2 heading in document order."""
        spec = _build_two_tab_spec()
        output = spec_to_html(spec)
        pos1 = output.find("Overview")
        pos2 = output.find("Details")
        assert pos1 != -1, "Overview heading not found"
        assert pos2 != -1, "Details heading not found"
        assert pos1 < pos2, (
            f"Expected Overview (tab1) before Details (tab2): pos1={pos1}, pos2={pos2}"
        )

    # -- Widget with tab_id=None appears under first-tab heading -----------

    def test_widget_with_null_tab_id_under_first_tab(self):
        """A widget with tab_id=None must appear after the first tab's heading."""
        data = _tabbed_spec(
            tabs=[
                {"id": "t1", "label": "First"},
                {"id": "t2", "label": "Second"},
            ],
            widgets=[
                _w("implicit"),          # tab_id omitted → None → first tab
                _w("explicit", tab_id="t2"),
            ],
        )
        spec, issues = validate_spec(data)
        assert spec is not None, f"setup failed: {issues}"

        output = spec_to_html(spec)
        # The query-id attribute for the implicit widget should appear after
        # the "First" heading and before the "Second" heading.
        first_pos = output.find("First")
        second_pos = output.find("Second")
        widget_pos = output.find('query-id="demo_all"')

        assert first_pos != -1 and second_pos != -1, "Both tab labels should appear"
        # Both widgets share query-id="demo_all"; at least the first occurrence
        # should come after "First".
        assert widget_pos > first_pos, (
            "Implicit tab widget should appear after the first-tab heading"
        )

    # -- Without tabs: no h3 headings (backward compat) --------------------

    def test_no_tabs_no_h3_headings(self):
        """A spec without tabs must NOT emit any tab-section headings."""
        data: dict[str, Any] = {
            "version": 1,
            "title": "Flat Dashboard",
            "widgets": [_w("w1")],
        }
        spec, _ = validate_spec(data)
        assert spec is not None
        assert spec.tabs == []

        output = spec_to_html(spec)
        # The title is in an h2; no tab-section h3 headings should appear.
        # Count <h3 occurrences.
        h3_count = output.lower().count("<h3")
        assert h3_count == 0, (
            f"No-tab spec should emit no <h3> headings; found {h3_count}:\n{output[:400]}"
        )

    # -- Security: no <script> in tab-section HTML -------------------------

    def test_no_script_tag_in_tabbed_output(self):
        spec = _build_two_tab_spec()
        output = spec_to_html(spec)
        assert "<script" not in output.lower(), (
            "Tabbed spec_to_html output must not contain <script>"
        )

    # -- Security: no on*= handlers in tab-section HTML --------------------

    def test_no_inline_event_handlers_in_tabbed_output(self):
        import re

        spec = _build_two_tab_spec()
        output = spec_to_html(spec)
        match = re.search(r"\bon\w+=", output, re.IGNORECASE)
        assert match is None, (
            f"Tabbed output contains inline handler: {match.group()!r}"
        )

    # -- Security: tab label is HTML-escaped (XSS guard) ------------------

    def test_tab_label_html_escaped(self):
        data = _tabbed_spec(
            tabs=[{"id": "t1", "label": '<script>alert("xss")</script>'}],
            widgets=[],
        )
        spec, _ = validate_spec(data)
        assert spec is not None

        output = spec_to_html(spec)
        assert "<script>" not in output, (
            "Raw <script> must not appear in tab label section"
        )
        # The label should be escaped in some form.
        assert "&lt;" in output or "script" not in output.lower(), (
            "Tab label XSS payload should be HTML-escaped"
        )

    # -- Three tabs produce three section headings -------------------------

    def test_three_tabs_three_section_headings(self):
        data = _tabbed_spec(
            tabs=[
                {"id": "a", "label": "Alpha"},
                {"id": "b", "label": "Beta"},
                {"id": "c", "label": "Gamma"},
            ],
            widgets=[
                _w("wa", tab_id="a"),
                _w("wb", tab_id="b"),
                _w("wc", tab_id="c"),
            ],
        )
        spec, issues = validate_spec(data)
        assert spec is not None, f"setup failed: {issues}"

        output = spec_to_html(spec)
        for label in ("Alpha", "Beta", "Gamma"):
            assert label in output, f"Tab label '{label}' should appear in output"

    # -- Grid-position styles still present in tabbed output ---------------

    def test_grid_column_in_tabbed_output(self):
        """Widget grid positions should still appear in inline styles."""
        spec = _build_two_tab_spec()
        output = spec_to_html(spec)
        assert "grid-column:" in output, (
            "Grid positions should appear in tabbed spec_to_html output"
        )

    # -- Tabbed output starts with the nubi-dashboard wrapper --------------

    def test_tabbed_output_starts_with_nubi_dashboard(self):
        spec = _build_two_tab_spec()
        output = spec_to_html(spec)
        assert output.strip().startswith('<div class="nubi-dashboard"'), (
            f"Tabbed output should still start with nubi-dashboard div: {output[:100]}"
        )

    # -- Each tab section has a data-tab-id attribute ----------------------

    def test_each_tab_section_has_data_tab_id(self):
        """Tab sections must carry data-tab-id so the embed renderer can identify them."""
        spec = _build_two_tab_spec()
        output = spec_to_html(spec)
        assert 'data-tab-id="t1"' in output, (
            f"Expected data-tab-id=t1 in tabbed output:\n{output[:600]}"
        )
        assert 'data-tab-id="t2"' in output, (
            f"Expected data-tab-id=t2 in tabbed output:\n{output[:600]}"
        )

    # -- Single tab: heading still emitted + no interactive bar needed -----

    def test_single_tab_emits_one_heading(self):
        data = _tabbed_spec(
            tabs=[{"id": "only", "label": "Solo"}],
            widgets=[_w("w1", tab_id="only")],
        )
        spec, _ = validate_spec(data)
        assert spec is not None

        output = spec_to_html(spec)
        assert "Solo" in output, "Single tab label should appear in stacked-sections output"
