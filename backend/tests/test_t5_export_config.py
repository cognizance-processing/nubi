"""Tests for T5 — per-board + per-widget export/report layout config.

Coverage
--------
1. Default values are applied when spec.export is absent.
2. Default WidgetExportHints values are applied for widgets with no explicit hint.
3. Explicit values round-trip through model_dump / model_validate.
4. BoardExportConfig validation: invalid page_size rejected; valid ones accepted.
5. WidgetExportHints validation: invalid render_as rejected.
6. get_export_config returns a fully normalised dict (all keys present).
7. get_export_config widget_hints contains one entry per widget in spec.widgets.
8. Per-widget hints override defaults correctly.
9. title_slide: None when absent; serialised dict when present.
10. Backward compat: existing specs without 'export' key still validate.
11. Spec JSON schema exposes 'export'.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.dashboards.spec import (
    BoardExportConfig,
    DashboardSpec,
    TitleSlideConfig,
    Widget,
    WidgetExportHints,
    WidgetPos,
    get_export_config,
    validate_spec,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_spec(**extra: Any) -> dict[str, Any]:
    """Minimal valid DashboardSpec dict (kpi widget, no export key)."""
    base: dict[str, Any] = {
        "version": 1,
        "title": "Export Test",
        "widgets": [
            {
                "id": "w1",
                "type": "kpi",
                "query_id": "demo_all",
                "encoding": {"value": "id"},
                "pos": {"x": 1, "y": 1, "w": 4, "h": 2},
            }
        ],
    }
    base.update(extra)
    return base


def _two_widget_spec() -> dict[str, Any]:
    return {
        "version": 1,
        "title": "Two Widgets",
        "widgets": [
            {
                "id": "w1",
                "type": "kpi",
                "query_id": "demo_all",
                "encoding": {"value": "id"},
                "pos": {"x": 1, "y": 1, "w": 4, "h": 2},
            },
            {
                "id": "w2",
                "type": "table",
                "query_id": "demo_all",
                "pos": {"x": 5, "y": 1, "w": 8, "h": 2},
            },
        ],
    }


# ---------------------------------------------------------------------------
# 1. Defaults when export is absent
# ---------------------------------------------------------------------------


class TestExportDefaults:
    """Absent spec.export → all defaults applied."""

    def test_spec_without_export_validates(self):
        data = _minimal_spec()
        spec, issues = validate_spec(data)
        hard_issues = [i for i in issues if "not in the registered" not in i]
        assert spec is not None, f"Expected valid spec; issues: {issues}"
        assert hard_issues == []

    def test_export_defaults_to_board_export_config(self):
        data = _minimal_spec()
        spec, _ = validate_spec(data)
        assert spec is not None
        assert isinstance(spec.export, BoardExportConfig)

    def test_default_title_slide_is_none(self):
        data = _minimal_spec()
        spec, _ = validate_spec(data)
        assert spec is not None
        assert spec.export.title_slide is None

    def test_default_header_is_none(self):
        data = _minimal_spec()
        spec, _ = validate_spec(data)
        assert spec is not None
        assert spec.export.header is None

    def test_default_footer_is_none(self):
        data = _minimal_spec()
        spec, _ = validate_spec(data)
        assert spec is not None
        assert spec.export.footer is None

    def test_default_page_size_is_a4(self):
        data = _minimal_spec()
        spec, _ = validate_spec(data)
        assert spec is not None
        assert spec.export.page_size == "A4"

    def test_default_widget_hints_empty(self):
        data = _minimal_spec()
        spec, _ = validate_spec(data)
        assert spec is not None
        assert spec.export.widget_hints == {}


# ---------------------------------------------------------------------------
# 2. Default WidgetExportHints values
# ---------------------------------------------------------------------------


class TestWidgetExportHintsDefaults:
    """WidgetExportHints defaults: include_in_report=True, render_as='auto', etc."""

    def test_include_in_report_default_true(self):
        h = WidgetExportHints()
        assert h.include_in_report is True

    def test_order_default_none(self):
        h = WidgetExportHints()
        assert h.order is None

    def test_page_break_before_default_false(self):
        h = WidgetExportHints()
        assert h.page_break_before is False

    def test_caption_default_none(self):
        h = WidgetExportHints()
        assert h.caption is None

    def test_render_as_default_auto(self):
        h = WidgetExportHints()
        assert h.render_as == "auto"


# ---------------------------------------------------------------------------
# 3. Round-trip: explicit values survive model_dump / model_validate
# ---------------------------------------------------------------------------


class TestRoundTrip:
    """Explicit export config survives serialisation round-trip."""

    def _full_export_spec(self) -> dict[str, Any]:
        return {
            **_two_widget_spec(),
            "export": {
                "title_slide": {"heading": "Q1 Report", "subheading": "Confidential"},
                "header": "Nubi Analytics",
                "footer": "© 2026",
                "page_size": "Letter",
                "widget_hints": {
                    "w1": {
                        "include_in_report": False,
                        "order": 2,
                        "page_break_before": True,
                        "caption": "KPI overview",
                        "render_as": "chart",
                    },
                    "w2": {
                        "render_as": "table",
                    },
                },
            },
        }

    def test_explicit_page_size_preserved(self):
        spec, issues = validate_spec(self._full_export_spec())
        hard_issues = [i for i in issues if "not in the registered" not in i]
        assert spec is not None, f"Issues: {issues}"
        assert hard_issues == []
        assert spec.export.page_size == "Letter"

    def test_explicit_header_preserved(self):
        spec, _ = validate_spec(self._full_export_spec())
        assert spec is not None
        assert spec.export.header == "Nubi Analytics"

    def test_explicit_footer_preserved(self):
        spec, _ = validate_spec(self._full_export_spec())
        assert spec is not None
        assert spec.export.footer == "© 2026"

    def test_explicit_title_slide_preserved(self):
        spec, _ = validate_spec(self._full_export_spec())
        assert spec is not None
        ts = spec.export.title_slide
        assert ts is not None
        assert ts.heading == "Q1 Report"
        assert ts.subheading == "Confidential"

    def test_widget_hints_include_in_report_false(self):
        spec, _ = validate_spec(self._full_export_spec())
        assert spec is not None
        w1_hints = spec.export.widget_hints["w1"]
        assert w1_hints.include_in_report is False

    def test_widget_hints_order_preserved(self):
        spec, _ = validate_spec(self._full_export_spec())
        assert spec is not None
        assert spec.export.widget_hints["w1"].order == 2

    def test_widget_hints_page_break_before_preserved(self):
        spec, _ = validate_spec(self._full_export_spec())
        assert spec is not None
        assert spec.export.widget_hints["w1"].page_break_before is True

    def test_widget_hints_caption_preserved(self):
        spec, _ = validate_spec(self._full_export_spec())
        assert spec is not None
        assert spec.export.widget_hints["w1"].caption == "KPI overview"

    def test_widget_hints_render_as_preserved(self):
        spec, _ = validate_spec(self._full_export_spec())
        assert spec is not None
        assert spec.export.widget_hints["w1"].render_as == "chart"
        assert spec.export.widget_hints["w2"].render_as == "table"

    def test_model_dump_round_trip(self):
        spec, _ = validate_spec(self._full_export_spec())
        assert spec is not None
        dumped = spec.model_dump()
        re_spec = DashboardSpec.model_validate(dumped)
        assert re_spec.export.page_size == "Letter"
        assert re_spec.export.header == "Nubi Analytics"
        assert re_spec.export.footer == "© 2026"
        assert re_spec.export.title_slide is not None
        assert re_spec.export.title_slide.heading == "Q1 Report"
        assert re_spec.export.widget_hints["w1"].include_in_report is False
        assert re_spec.export.widget_hints["w1"].order == 2


# ---------------------------------------------------------------------------
# 4. BoardExportConfig validation
# ---------------------------------------------------------------------------


class TestBoardExportConfigValidation:
    """BoardExportConfig rejects invalid values; valid ones are accepted."""

    def test_valid_page_size_a4(self):
        cfg = BoardExportConfig(page_size="A4")
        assert cfg.page_size == "A4"

    def test_valid_page_size_letter(self):
        cfg = BoardExportConfig(page_size="Letter")
        assert cfg.page_size == "Letter"

    def test_valid_page_size_16_9(self):
        cfg = BoardExportConfig(page_size="16:9")
        assert cfg.page_size == "16:9"

    def test_invalid_page_size_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            BoardExportConfig(page_size="A3")

    def test_invalid_page_size_string_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            BoardExportConfig(page_size="custom")

    def test_title_slide_none_accepted(self):
        cfg = BoardExportConfig(title_slide=None)
        assert cfg.title_slide is None

    def test_title_slide_config_accepted(self):
        cfg = BoardExportConfig(
            title_slide=TitleSlideConfig(heading="H", subheading="S")
        )
        assert cfg.title_slide is not None
        assert cfg.title_slide.heading == "H"


# ---------------------------------------------------------------------------
# 5. WidgetExportHints validation
# ---------------------------------------------------------------------------


class TestWidgetExportHintsValidation:
    """WidgetExportHints rejects invalid render_as; valid ones accepted."""

    def test_render_as_auto(self):
        h = WidgetExportHints(render_as="auto")
        assert h.render_as == "auto"

    def test_render_as_chart(self):
        h = WidgetExportHints(render_as="chart")
        assert h.render_as == "chart"

    def test_render_as_table(self):
        h = WidgetExportHints(render_as="table")
        assert h.render_as == "table"

    def test_invalid_render_as_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            WidgetExportHints(render_as="svg")

    def test_include_in_report_bool(self):
        h = WidgetExportHints(include_in_report=False)
        assert h.include_in_report is False

    def test_order_int(self):
        h = WidgetExportHints(order=5)
        assert h.order == 5

    def test_order_none(self):
        h = WidgetExportHints(order=None)
        assert h.order is None


# ---------------------------------------------------------------------------
# 6. get_export_config returns fully normalised dict
# ---------------------------------------------------------------------------


class TestGetExportConfig:
    """get_export_config always returns a complete, normalised dict."""

    def test_returns_dict(self):
        spec = DashboardSpec(title="T", widgets=[])
        result = get_export_config(spec)
        assert isinstance(result, dict)

    def test_all_top_level_keys_present(self):
        spec = DashboardSpec(title="T", widgets=[])
        result = get_export_config(spec)
        for key in ("title_slide", "header", "footer", "page_size", "widget_hints"):
            assert key in result, f"Missing key: {key!r}"

    def test_default_spec_title_slide_is_none(self):
        spec = DashboardSpec(title="T", widgets=[])
        result = get_export_config(spec)
        assert result["title_slide"] is None

    def test_default_spec_header_is_none(self):
        spec = DashboardSpec(title="T", widgets=[])
        result = get_export_config(spec)
        assert result["header"] is None

    def test_default_spec_footer_is_none(self):
        spec = DashboardSpec(title="T", widgets=[])
        result = get_export_config(spec)
        assert result["footer"] is None

    def test_default_spec_page_size_a4(self):
        spec = DashboardSpec(title="T", widgets=[])
        result = get_export_config(spec)
        assert result["page_size"] == "A4"

    def test_title_slide_serialised_as_dict_when_set(self):
        spec = DashboardSpec(
            title="T",
            widgets=[],
            export=BoardExportConfig(
                title_slide=TitleSlideConfig(heading="My Report", subheading=None)
            ),
        )
        result = get_export_config(spec)
        ts = result["title_slide"]
        assert ts is not None
        assert isinstance(ts, dict)
        assert ts["heading"] == "My Report"
        assert ts["subheading"] is None

    def test_header_and_footer_passed_through(self):
        spec = DashboardSpec(
            title="T",
            widgets=[],
            export=BoardExportConfig(header="HDR", footer="FTR"),
        )
        result = get_export_config(spec)
        assert result["header"] == "HDR"
        assert result["footer"] == "FTR"

    def test_page_size_passed_through(self):
        spec = DashboardSpec(
            title="T",
            widgets=[],
            export=BoardExportConfig(page_size="16:9"),
        )
        result = get_export_config(spec)
        assert result["page_size"] == "16:9"


# ---------------------------------------------------------------------------
# 7. get_export_config widget_hints has one entry per widget
# ---------------------------------------------------------------------------


class TestGetExportConfigWidgetHints:
    """widget_hints in get_export_config output contains one entry per widget."""

    def _spec_with_widgets(self, n: int = 2) -> DashboardSpec:
        widgets = [
            Widget(
                id=f"w{i}",
                type="kpi",
                query_id="demo_all",
                encoding={"value": "id"},
                pos=WidgetPos(x=1, y=i, w=4, h=1),
            )
            for i in range(1, n + 1)
        ]
        return DashboardSpec(title="T", widgets=widgets)

    def test_empty_spec_returns_empty_widget_hints(self):
        spec = DashboardSpec(title="T", widgets=[])
        result = get_export_config(spec)
        assert result["widget_hints"] == {}

    def test_one_widget_returns_one_hint(self):
        spec = self._spec_with_widgets(1)
        result = get_export_config(spec)
        assert set(result["widget_hints"].keys()) == {"w1"}

    def test_two_widgets_returns_two_hints(self):
        spec = self._spec_with_widgets(2)
        result = get_export_config(spec)
        assert set(result["widget_hints"].keys()) == {"w1", "w2"}

    def test_hint_has_all_fields(self):
        spec = self._spec_with_widgets(1)
        result = get_export_config(spec)
        hint = result["widget_hints"]["w1"]
        for key in ("include_in_report", "order", "page_break_before", "caption", "render_as"):
            assert key in hint, f"Missing hint key: {key!r}"

    def test_hint_defaults_applied_for_unmentioned_widget(self):
        spec = self._spec_with_widgets(1)
        result = get_export_config(spec)
        hint = result["widget_hints"]["w1"]
        assert hint["include_in_report"] is True
        assert hint["order"] is None
        assert hint["page_break_before"] is False
        assert hint["caption"] is None
        assert hint["render_as"] == "auto"


# ---------------------------------------------------------------------------
# 8. Per-widget hints override defaults
# ---------------------------------------------------------------------------


class TestPerWidgetHintOverride:
    """Explicit widget_hints override defaults; unset fields stay default."""

    def test_explicit_include_in_report_false(self):
        spec = DashboardSpec(
            title="T",
            widgets=[
                Widget(
                    id="w1",
                    type="kpi",
                    query_id="demo_all",
                    encoding={"value": "id"},
                    pos=WidgetPos(x=1, y=1, w=4, h=1),
                )
            ],
            export=BoardExportConfig(
                widget_hints={"w1": WidgetExportHints(include_in_report=False)}
            ),
        )
        result = get_export_config(spec)
        assert result["widget_hints"]["w1"]["include_in_report"] is False

    def test_unset_fields_stay_default_when_partially_specified(self):
        """Partial hint: only render_as set → other fields remain defaults."""
        spec = DashboardSpec(
            title="T",
            widgets=[
                Widget(
                    id="w1",
                    type="table",
                    query_id="demo_all",
                    pos=WidgetPos(x=1, y=1, w=12, h=3),
                )
            ],
            export=BoardExportConfig(
                widget_hints={"w1": WidgetExportHints(render_as="table")}
            ),
        )
        result = get_export_config(spec)
        hint = result["widget_hints"]["w1"]
        # Explicitly set:
        assert hint["render_as"] == "table"
        # Defaults for unset fields:
        assert hint["include_in_report"] is True
        assert hint["order"] is None
        assert hint["page_break_before"] is False
        assert hint["caption"] is None

    def test_caption_override(self):
        spec = DashboardSpec(
            title="T",
            widgets=[
                Widget(
                    id="w1",
                    type="kpi",
                    query_id="demo_all",
                    encoding={"value": "id"},
                    pos=WidgetPos(x=1, y=1, w=4, h=1),
                )
            ],
            export=BoardExportConfig(
                widget_hints={"w1": WidgetExportHints(caption="Figure 1: Total Count")}
            ),
        )
        result = get_export_config(spec)
        assert result["widget_hints"]["w1"]["caption"] == "Figure 1: Total Count"

    def test_page_break_before_override(self):
        spec = DashboardSpec(
            title="T",
            widgets=[
                Widget(
                    id="w1",
                    type="kpi",
                    query_id="demo_all",
                    encoding={"value": "id"},
                    pos=WidgetPos(x=1, y=1, w=4, h=1),
                )
            ],
            export=BoardExportConfig(
                widget_hints={"w1": WidgetExportHints(page_break_before=True)}
            ),
        )
        result = get_export_config(spec)
        assert result["widget_hints"]["w1"]["page_break_before"] is True

    def test_order_override(self):
        spec = DashboardSpec(
            title="T",
            widgets=[
                Widget(
                    id="w1",
                    type="kpi",
                    query_id="demo_all",
                    encoding={"value": "id"},
                    pos=WidgetPos(x=1, y=1, w=4, h=1),
                )
            ],
            export=BoardExportConfig(
                widget_hints={"w1": WidgetExportHints(order=10)}
            ),
        )
        result = get_export_config(spec)
        assert result["widget_hints"]["w1"]["order"] == 10


# ---------------------------------------------------------------------------
# 9. title_slide serialisation
# ---------------------------------------------------------------------------


class TestTitleSlideSerialisation:
    """title_slide: None when absent; dict with heading/subheading when set."""

    def test_absent_title_slide_gives_none(self):
        spec = DashboardSpec(title="T", widgets=[])
        result = get_export_config(spec)
        assert result["title_slide"] is None

    def test_explicit_none_title_slide_gives_none(self):
        spec = DashboardSpec(
            title="T", widgets=[], export=BoardExportConfig(title_slide=None)
        )
        result = get_export_config(spec)
        assert result["title_slide"] is None

    def test_title_slide_heading_and_subheading(self):
        spec = DashboardSpec(
            title="T",
            widgets=[],
            export=BoardExportConfig(
                title_slide=TitleSlideConfig(heading="Annual Report", subheading="2026")
            ),
        )
        result = get_export_config(spec)
        ts = result["title_slide"]
        assert ts == {"heading": "Annual Report", "subheading": "2026"}

    def test_title_slide_heading_none(self):
        """heading=None is valid (export renderer falls back to board title)."""
        spec = DashboardSpec(
            title="T",
            widgets=[],
            export=BoardExportConfig(title_slide=TitleSlideConfig()),
        )
        result = get_export_config(spec)
        ts = result["title_slide"]
        assert ts is not None
        assert ts["heading"] is None
        assert ts["subheading"] is None


# ---------------------------------------------------------------------------
# 10. Backward compat: existing specs without 'export' still validate
# ---------------------------------------------------------------------------


class TestBackwardCompat:
    """Existing specs without 'export' key parse and behave as before."""

    def test_spec_without_export_key_validates(self):
        data = _minimal_spec()
        assert "export" not in data
        spec, issues = validate_spec(data)
        hard_issues = [i for i in issues if "not in the registered" not in i]
        assert spec is not None
        assert hard_issues == []

    def test_spec_without_export_key_has_default_export(self):
        data = _minimal_spec()
        spec, _ = validate_spec(data)
        assert spec is not None
        assert isinstance(spec.export, BoardExportConfig)
        assert spec.export.page_size == "A4"
        assert spec.export.title_slide is None

    def test_spec_with_empty_export_dict_validates(self):
        data = {**_minimal_spec(), "export": {}}
        spec, issues = validate_spec(data)
        hard_issues = [i for i in issues if "not in the registered" not in i]
        assert spec is not None
        assert hard_issues == []
        assert spec.export.page_size == "A4"

    def test_dashboardspec_model_dump_includes_export(self):
        spec = DashboardSpec(title="T", widgets=[])
        dumped = spec.model_dump()
        assert "export" in dumped

    def test_model_dump_round_trip_preserves_export_defaults(self):
        spec = DashboardSpec(title="T", widgets=[])
        dumped = spec.model_dump()
        re_spec = DashboardSpec.model_validate(dumped)
        assert re_spec.export.page_size == "A4"
        assert re_spec.export.title_slide is None
        assert re_spec.export.header is None
        assert re_spec.export.footer is None


# ---------------------------------------------------------------------------
# 11. JSON schema exposes 'export'
# ---------------------------------------------------------------------------


class TestSchemaExposesExport:
    """spec_json_schema includes the 'export' property."""

    def test_schema_has_export_property(self):
        from app.dashboards.spec import spec_json_schema
        schema = spec_json_schema()
        props = schema.get("properties", {})
        assert "export" in props, (
            f"Schema should expose 'export'; props: {list(props.keys())}"
        )
