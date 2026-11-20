"""GET /boards?view=card summaries, and the guard that stops one being saved."""

from __future__ import annotations

import pytest

from app.dashboards.card_summary import (
    CARD_SUMMARY_MARKER,
    card_summary_config,
    card_summary_rows,
    reject_card_summary,
)
from app.errors import AppError

FULL = {
    "folderId": "f1",
    "spec": {
        "title": "Ops",
        "layout": {"cols": 12, "row_height": 60},
        "tabs": [{"id": "t1"}],
        "surfaces": {"grid": {"w1": {"x": 1, "y": 1, "w": 4, "h": 2}}},
        "variables": [{"name": "region", "options_sql": "SELECT ..." * 50}],
        "widgets": [
            {
                "id": "w1", "type": "chart", "chart_type": "line", "tab_id": "t1",
                "pos": {"x": 1, "y": 1, "w": 4, "h": 2}, "placement": "grid",
                "query_id": "q1", "params": {"a": 1}, "style": {"bg": "#000"},
                "props": {"title": "Visits"}, "encoding": {"x": "d", "y": "n"},
                "html": "<div>big</div>",
            },
            {"id": "w2", "type": "filter", "placement": "header", "drawer": False},
        ],
    },
}


def test_summary_keeps_only_what_a_card_draws() -> None:
    out = card_summary_config(FULL)
    assert out["folderId"] == "f1"
    assert out[CARD_SUMMARY_MARKER] is True
    spec = out["spec"]
    assert "variables" not in spec
    assert spec["layout"] == FULL["spec"]["layout"]
    assert spec["tabs"] == FULL["spec"]["tabs"]
    assert spec["surfaces"] == FULL["spec"]["surfaces"]
    assert spec["widgets"][0] == {
        "id": "w1", "type": "chart", "chart_type": "line", "tab_id": "t1",
        "pos": {"x": 1, "y": 1, "w": 4, "h": 2}, "placement": "grid",
    }
    assert spec["widgets"][1] == {"id": "w2", "type": "filter", "placement": "header", "drawer": False}
    # The input is not mutated.
    assert "variables" in FULL["spec"] and "query_id" in FULL["spec"]["widgets"][0]


def test_html_board_keeps_only_the_flag() -> None:
    out = card_summary_config({"html": "<html>" + "x" * 10_000})
    assert out["html"] is True


def test_rows_are_marked_partial() -> None:
    rows = card_summary_rows([{"id": "b1", "name": "B", "config": FULL}])
    assert rows[0]["config_partial"] is True
    assert rows[0]["name"] == "B"


def test_saving_a_summary_is_refused() -> None:
    with pytest.raises(AppError) as err:
        reject_card_summary(card_summary_config(FULL))
    assert err.value.status == 409
    reject_card_summary(FULL)  # a full config passes
    reject_card_summary(None)
