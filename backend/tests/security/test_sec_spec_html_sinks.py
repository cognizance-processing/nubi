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
