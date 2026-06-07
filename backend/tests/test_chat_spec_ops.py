"""Tests for the chat assistant's dashboard-editing tools + spec transforms.

Covers ``app.chat.spec_ops`` (pure transforms: id generation, free-spot
positioning, merges, errors) and ``app.chat.tools.execute_tool`` (the tool
dispatch that wraps them and surfaces the updated spec in *extra*).
"""

from __future__ import annotations

import pytest

from app.chat import spec_ops, tools
from app.queries.registry import reset_for_tests


# ---------------------------------------------------------------------------
# spec_ops — pure transforms
# ---------------------------------------------------------------------------


def test_ensure_spec_fills_defaults():
    out = spec_ops.ensure_spec(None)
    assert out["version"] == 1
    assert out["title"]
    assert out["layout"] == {"cols": 12, "row_height": 60}
    assert out["widgets"] == []
    assert out["variables"] == []


def test_add_widget_generates_unique_ids_and_does_not_mutate_input():
    spec = {"title": "D", "widgets": []}
    s1, id1 = spec_ops.add_widget(spec, type="kpi", query_id="demo_all")
    assert id1 == "w1"
    # input spec untouched (pure transform).
    assert spec["widgets"] == []
    s2, id2 = spec_ops.add_widget(s1, type="table", query_id="demo_all")
    assert id2 == "w2"
    assert [w["id"] for w in s2["widgets"]] == ["w1", "w2"]


def test_add_widget_id_fills_gap():
    spec = {"widgets": [{"id": "w1", "type": "kpi", "pos": {"x": 1, "y": 1, "w": 2, "h": 1}},
                        {"id": "w3", "type": "kpi", "pos": {"x": 3, "y": 1, "w": 2, "h": 1}}]}
    _, new_id = spec_ops.add_widget(spec, type="kpi", query_id="demo_all")
    assert new_id == "w2"


def test_free_spot_avoids_overlap():
    # A 12-col grid with one widget filling cols 1-12 at row 1 (h=2).
    spec = {
        "layout": {"cols": 12},
        "widgets": [{"id": "w1", "type": "chart", "pos": {"x": 1, "y": 1, "w": 12, "h": 2}}],
    }
    out, _ = spec_ops.add_widget(spec, type="kpi", query_id="demo_all")
    pos = out["widgets"][1]["pos"]
    # KPI default is 3x2; first free spot is row 3 (rows 1-2 are full).
    assert pos["y"] == 3
    assert pos["x"] == 1


def test_add_widget_respects_explicit_pos():
    out, _ = spec_ops.add_widget(
        {"widgets": []}, type="chart", query_id="q", chart_type="bar",
        encoding={"x": "a", "y": "b"}, pos={"x": 2, "y": 5, "w": 6, "h": 3},
    )
    w = out["widgets"][0]
    assert w["pos"] == {"x": 2, "y": 5, "w": 6, "h": 3}
    assert w["chart_type"] == "bar"
    assert w["encoding"] == {"x": "a", "y": "b"}


def test_add_widget_passthrough_filter_fields():
    out, _ = spec_ops.add_widget(
        {"widgets": []}, type="filter", subtype="select", target_var="region",
        options_query_id="demo_all",
    )
    w = out["widgets"][0]
    assert w["subtype"] == "select"
    assert w["target_var"] == "region"
    assert w["options_query_id"] == "demo_all"


def test_add_widget_rejects_unknown_type():
    with pytest.raises(spec_ops.SpecOpError):
        spec_ops.add_widget({"widgets": []}, type="gauge")


def test_update_widget_merges_dicts_replaces_scalars():
    spec, _ = spec_ops.add_widget(
        {"widgets": []}, type="chart", query_id="q1", chart_type="line",
        encoding={"x": "a", "y": "b"}, props={"label": "Old", "limit": 10},
    )
    wid = spec["widgets"][0]["id"]
    out = spec_ops.update_widget(spec, wid, {
        "chart_type": "bar",
        "props": {"label": "New"},          # merge → limit stays
        "encoding": {"color": "c"},          # merge → x,y stay
        "query_id": "q2",
    })
    w = out["widgets"][0]
    assert w["chart_type"] == "bar"
    assert w["query_id"] == "q2"
    assert w["props"] == {"label": "New", "limit": 10}
    assert w["encoding"] == {"x": "a", "y": "b", "color": "c"}


def test_update_widget_cannot_rename():
    spec, _ = spec_ops.add_widget({"widgets": []}, type="kpi", query_id="q")
    wid = spec["widgets"][0]["id"]
    out = spec_ops.update_widget(spec, wid, {"id": "hacked"})
    assert out["widgets"][0]["id"] == wid


def test_update_widget_missing_raises():
    with pytest.raises(spec_ops.SpecOpError):
        spec_ops.update_widget({"widgets": []}, "nope", {"props": {}})


def test_remove_widget():
    spec, _ = spec_ops.add_widget({"widgets": []}, type="kpi", query_id="q")
    wid = spec["widgets"][0]["id"]
    out = spec_ops.remove_widget(spec, wid)
    assert out["widgets"] == []


def test_remove_widget_missing_raises():
    with pytest.raises(spec_ops.SpecOpError):
        spec_ops.remove_widget({"widgets": []}, "nope")


def test_set_widget_style_merges_and_transparent_background():
    spec, _ = spec_ops.add_widget({"widgets": []}, type="kpi", query_id="q")
    wid = spec["widgets"][0]["id"]
    out = spec_ops.set_widget_style(spec, wid, {"background": "transparent"})
    out = spec_ops.set_widget_style(out, wid, {"border": "1px solid #333"})
    assert out["widgets"][0]["style"] == {"background": "transparent", "border": "1px solid #333"}


def test_set_layout_merges():
    out = spec_ops.set_layout({"layout": {"cols": 12, "row_height": 60}}, {"cols": 24, "compaction": "vertical"})
    assert out["layout"] == {"cols": 24, "row_height": 60, "compaction": "vertical"}


def test_set_background_string_and_object():
    out = spec_ops.set_background({}, "transparent")
    assert out["background"] == "transparent"
    out = spec_ops.set_background(out, {"type": "gradient", "from": "#111", "to": "#333"})
    assert out["background"] == {"type": "gradient", "from": "#111", "to": "#333"}


def test_add_variable_upserts():
    out = spec_ops.add_variable({}, "region", type="select", default="north")
    assert out["variables"] == [{"name": "region", "type": "select", "default": "north"}]
    out = spec_ops.add_variable(out, "region", type="text", default="south")
    assert len(out["variables"]) == 1
    assert out["variables"][0]["default"] == "south"


def test_set_drilldown_writes_props():
    spec, _ = spec_ops.add_widget({"widgets": []}, type="table", query_id="q")
    wid = spec["widgets"][0]["id"]
    out = spec_ops.set_drilldown(spec, wid, "region", "name")
    assert out["widgets"][0]["props"]["drilldown"] == {"target_var": "region", "value_field": "name"}


# ---------------------------------------------------------------------------
# tools.execute_tool — dispatch contract
# ---------------------------------------------------------------------------


def test_tool_specs_expose_all_tools():
    names = {t["name"] for t in tools.anthropic_tool_specs()}
    expected = {
        "propose_dashboard_spec", "list_registered_queries", "register_query",
        "add_widget", "update_widget", "remove_widget", "set_widget_style",
        "set_layout", "set_background", "add_variable", "set_drilldown",
    }
    assert expected <= names
    # Every tool carries a proper input_schema object.
    for t in tools.anthropic_tool_specs():
        assert t["input_schema"]["type"] == "object"
        assert t["description"]


def test_execute_add_widget_returns_spec_in_extra():
    output, extra = tools.execute_tool(
        "add_widget", {"spec": {"widgets": []}, "type": "kpi", "query_id": "demo_all"}
    )
    assert output["ok"] is True
    assert output["added_widget_id"] == "w1"
    assert "spec" in extra
    assert extra["spec"]["widgets"][0]["query_id"] == "demo_all"


def test_execute_spec_op_error_is_structured():
    output, extra = tools.execute_tool(
        "update_widget", {"spec": {"widgets": []}, "widget_id": "nope", "patch": {}}
    )
    assert "error" in output
    assert extra == {}


def test_execute_register_query_persists_to_registry():
    reset_for_tests()
    try:
        output, extra = tools.execute_tool(
            "register_query",
            {"name": "Monthly Revenue", "sql": "SELECT * FROM demo WHERE value > {{min}}",
             "params": [{"name": "min", "type": "number", "default": 0}]},
        )
        assert extra == {}
        assert output["id"] == "monthly_revenue"
        # The query is live in the registry and bindable by widgets.
        from app.queries.registry import get_query_registry
        rq = get_query_registry().get("monthly_revenue")
        assert rq is not None
        assert rq.params[0].name == "min"
    finally:
        reset_for_tests()


def test_execute_register_query_validates():
    output, _ = tools.execute_tool("register_query", {"name": "", "sql": "SELECT 1"})
    assert "error" in output


def test_chained_edits_accumulate_via_extra_spec():
    # Simulate the loop: feed each tool's returned spec into the next.
    _, extra1 = tools.execute_tool("add_widget", {"type": "kpi", "query_id": "demo_all"})
    spec1 = extra1["spec"]
    _, extra2 = tools.execute_tool("add_widget", {"spec": spec1, "type": "table", "query_id": "demo_all"})
    spec2 = extra2["spec"]
    _, extra3 = tools.execute_tool("set_background", {"spec": spec2, "background": "#0b0f1a"})
    final = extra3["spec"]
    assert [w["id"] for w in final["widgets"]] == ["w1", "w2"]
    assert final["background"] == "#0b0f1a"
