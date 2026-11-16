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


# ---------------------------------------------------------------------------
# A board saved through the REST API is stored without them too
# ---------------------------------------------------------------------------


def _hostile_board(extra_widget=None):
    chart = {
        "id": "w1", "type": "chart", "chart_type": "bar", "query_id": "demo_all",
        "encoding": {"x": "name", "y": "value"}, "pos": {"x": 0, "y": 0, "w": 6, "h": 4},
        "config": {
            "legend": {"position": "bottom"},
            "echarts": {
                "tooltip": [{"formatter": PAYLOAD, "trigger": "item"}],
                "title": {"text": "t", "link": "javascript:alert(1)", "tooltip": {"content": PAYLOAD}},
                "toolbox": {"feature": {"dataView": {"lang": [PAYLOAD]}, "restore": {}}},
            },
        },
    }
    widgets = [chart] + ([extra_widget] if extra_widget else [])
    return {"spec": {"title": "Board", "widgets": widgets}, "folderId": "f1"}


def test_boards_saved_over_rest_are_stored_scrubbed():
    import json
    import uuid

    from fastapi.testclient import TestClient

    from app.repos.provider import set_repo
    from tests.security.conftest_helpers import as_user, make_tenant_app

    org = str(uuid.uuid4())
    app, _repo, _users = make_tenant_app({"u": (org, "owner")}, modules=("app.routes.resources",))
    nested = {
        "id": "s1", "type": "stepper", "pos": {"x": 0, "y": 4, "w": 6, "h": 4},
        "children": [{"id": "c1", "type": "chart", "config": {"tooltip": {"formatter": PAYLOAD}}}],
        "config": {"note": "kept"},
    }
    from unittest.mock import AsyncMock, patch

    try:
        with (
            patch("app.routes.resources.resolve_project_id_for_create", new=AsyncMock(return_value=None)),
            TestClient(app, raise_server_exceptions=False) as c,
        ):
            made = c.post("/api/v1/boards", json={"name": "b", "config": _hostile_board(nested)}, headers=as_user("u"))
            assert made.status_code in (200, 201), made.text
            board_id = made.json()["id"]
            stored = c.get(f"/api/v1/boards/{board_id}", headers=as_user("u")).json()
            text = json.dumps(stored)
            assert "onerror" not in text and "javascript:" not in text and "dataView" not in text

            widget = stored["config"]["spec"]["widgets"][0]
            assert widget["config"]["legend"] == {"position": "bottom"}  # ordinary options kept
            assert widget["config"]["echarts"]["tooltip"] == [{"trigger": "item"}]
            assert widget["config"]["echarts"]["toolbox"]["feature"] == {"restore": {}}
            assert stored["config"]["folderId"] == "f1"  # the rest of the board is untouched
            assert stored["config"]["spec"]["widgets"][1]["config"] == {"note": "kept"}

            updated = c.put(
                f"/api/v1/boards/{board_id}", json={"config": _hostile_board()}, headers=as_user("u")
            )
            assert updated.status_code == 200, updated.text
            text = json.dumps(c.get(f"/api/v1/boards/{board_id}", headers=as_user("u")).json())
            assert "onerror" not in text and "javascript:" not in text and "dataView" not in text
    finally:
        set_repo(None)


def test_only_widget_configs_are_touched():
    from app.dashboards.spec import scrub_board_config

    board = {
        "spec": {"title": "t", "link": "/d/other-board",
                 "widgets": [{"id": "w", "type": "text", "config": {"link": "/d/other-board", "tooltip": {"formatter": PAYLOAD}}}]},
        "tooltip": {"formatter": "kept: not a widget config"},
    }
    out = scrub_board_config(board)
    assert out["tooltip"] == {"formatter": "kept: not a widget config"}
    assert out["spec"]["link"] == "/d/other-board"
    assert out["spec"]["widgets"][0]["config"] == {"tooltip": {}}  # same rule as spec validation
    assert scrub_board_config(None) is None and scrub_board_config("x") == "x"
