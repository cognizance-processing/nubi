"""A stored dashboard spec never carries options a chart library renders as HTML or CSS."""

from __future__ import annotations

from tests.security import conftest_helpers  # noqa: F401  (env bootstrap)

PAYLOAD = '<img src=x onerror="alert(1)">'


def _widget(config):
    from app.dashboards.spec import validate_spec

    spec, _issues = validate_spec(
        {
            "title": "t",
            "widgets": [{"id": "w1", "type": "chart", "chart_type": "line", "query_id": "demo_all",
                         "encoding": {"x": "name", "y": "value"}, "config": config}],
        }
    )
    assert spec is not None
    return spec.widgets[0].config


def test_tooltip_html_sinks_are_dropped_on_validation():
    config = _widget(
        {
            "tooltip": {"formatter": PAYLOAD, "extraCssText": "position:fixed", "className": "x", "trigger": "item",
                        "textStyle": {"color": "#111", "rich": {"a": {}}}},
            "echarts": {
                "tooltip": {"formatter": PAYLOAD, "renderMode": "html"},
                "series": [{"tooltip": {"formatter": PAYLOAD, "appendToBody": True}}],
                "title": {"link": "javascript:alert(1)", "sublink": "https://example.com/ok"},
            },
            "palette": ["#111111"],
        }
    )
    import json

    text = json.dumps(config)
    assert "onerror" not in text and "javascript:" not in text
    assert config["tooltip"] == {"trigger": "item", "textStyle": {"color": "#111"}}
    assert config["echarts"]["title"] == {"sublink": "https://example.com/ok"}
    assert config["palette"] == ["#111111"]


def test_ordinary_config_is_untouched():
    cfg = {"legend": {"position": "bottom"}, "stack": True, "tooltip": {"trigger": "axis", "valueFormat": {"style": "number"}}}
    assert _widget(dict(cfg)) == cfg


def _nest(leaf, levels):
    node = leaf
    for _ in range(levels):
        node = {"children": [node]}
    return node


def test_tooltips_given_as_a_list_are_scrubbed():
    import json

    config = _widget(
        {"echarts": {"tooltip": [{"formatter": PAYLOAD, "extraCssText": "position:fixed", "trigger": "item"}],
                     "series": [{"tooltip": [{"formatter": PAYLOAD}]}]}}
    )
    assert "onerror" not in json.dumps(config)
    assert config["echarts"]["tooltip"] == [{"trigger": "item"}]


def test_component_tooltip_content_is_dropped():
    import json

    config = _widget(
        {"echarts": {"title": {"text": "t", "tooltip": {"show": True, "content": PAYLOAD}},
                     "legend": {"tooltip": {"show": True, "formatter": PAYLOAD}},
                     "toolbox": {"tooltip": {"content": PAYLOAD}, "feature": {"saveAsImage": {"lang": [PAYLOAD]}}}}}
    )
    assert "onerror" not in json.dumps(config)
    assert config["echarts"]["title"] == {"text": "t", "tooltip": {"show": True}}


def test_toolbox_data_view_is_dropped():
    import json

    config = _widget(
        {"echarts": {"toolbox": {"show": True, "feature": {
            "dataView": {"show": True, "title": PAYLOAD, "lang": [PAYLOAD, "x", "y"], "content": PAYLOAD},
            "restore": {"show": True}}}}}
    )
    assert "onerror" not in json.dumps(config)
    assert config["echarts"]["toolbox"]["feature"] == {"restore": {"show": True}}


def test_nesting_depth_does_not_exempt_a_value():
    import json

    for levels in (6, 13, 20, 40, 200):
        config = _widget({"echarts": {"series": [{"data": [_nest({"tooltip": {"formatter": PAYLOAD}}, levels)]}]}})
        assert "onerror" not in json.dumps(config), levels


def test_ordinary_nested_data_survives():
    tree = {"echarts": {"series": [{"type": "tree", "data": [_nest({"name": "leaf", "value": 1}, 10)]}]}}
    import copy

    assert _widget(copy.deepcopy(tree)) == tree
