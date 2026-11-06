"""Tests for the MCP local-use hardening: output caps + board read/update tools.

Coverage
--------
1.  run_query caps `rows` while `row_count` still reports the TRUE total.
2.  run_query honours an explicit limit and clamps it to the hard max.
3.  list_queries paginates and reports total/has_more.
4.  list_queries filters on name_contains (id or display name).
5.  list_dashboards returns compact summaries, never full specs.
6.  get_dashboard returns the full spec for one board.
7.  update_dashboard round-trips a rename + spec edit.
8.  update_dashboard REFUSES an invalid spec and leaves the board untouched.
9.  update_dashboard is org-scoped: org B cannot touch org A's board.
10. update_dashboard rejects embed/restricted tokens.
11. delete_dashboard removes a board, and only with confirm=true.
12. delete_dashboard is org-scoped and rejects embed/restricted tokens.
13. The route's response guard shrinks an oversized payload to valid JSON.
"""

from __future__ import annotations

import json
import uuid

import pytest

from app.ai.tools import execute_tool
from app.errors import AppError
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo
from app.routes.mcp import _MAX_TOOL_RESULT_BYTES, _encode_tool_result


def _claims(org_id: str, user_id: str, kind: str = "access") -> dict:
    return {
        "kind": kind,
        "sub": user_id,
        "org": org_id,
        "policies": {},
        "scope": ["author:sql"],
    }


def _spec(title: str = "Probe") -> dict:
    return {
        "version": 1,
        "title": title,
        "widgets": [
            {"id": "t1", "type": "text", "title": "hello", "props": {"markdown": "hi"}}
        ],
        "layout": {"type": "grid", "items": [{"widgetId": "t1", "x": 0, "y": 0, "w": 4, "h": 2}]},
    }


@pytest.fixture
def repo():
    r = InMemoryRepo()
    set_repo(r)
    yield r
    set_repo(None)


@pytest.fixture
def ctx(repo):
    org_id, user_id = str(uuid.uuid4()), str(uuid.uuid4())
    return org_id, user_id, _claims(org_id, user_id)


# ── run_query output cap ────────────────────────────────────────────────────


def test_run_query_caps_rows_but_reports_true_total(ctx):
    """`rows` is a sample; `row_count` keeps meaning "rows the query produced"."""
    _, _, claims = ctx
    res = execute_tool("run_query", {"sql": "SELECT * FROM demo", "limit": 2}, claims)

    assert len(res["rows"]) == 2
    assert res["returned_row_count"] == 2
    # The cap must NOT corrupt the count a caller would report to a user.
    assert res["row_count"] == 5
    assert res["truncated"] is True


def test_run_query_untruncated_when_under_limit(ctx):
    _, _, claims = ctx
    res = execute_tool("run_query", {"sql": "SELECT * FROM demo"}, claims)

    assert res["truncated"] is False
    assert res["row_count"] == res["returned_row_count"] == len(res["rows"])


def test_run_query_clamps_limit_to_hard_max(ctx):
    """An absurd limit is clamped, not honoured."""
    from app.ai.tools import _RUN_QUERY_MAX_LIMIT

    _, _, claims = ctx
    res = execute_tool("run_query", {"sql": "SELECT * FROM demo", "limit": 10**9}, claims)
    assert len(res["rows"]) <= _RUN_QUERY_MAX_LIMIT


# ── list_queries pagination ─────────────────────────────────────────────────


def test_list_queries_paginates(ctx):
    _, _, claims = ctx
    page = execute_tool("list_queries", {"limit": 2, "offset": 0}, claims)

    assert page["returned"] <= 2
    assert page["total"] >= page["returned"]
    assert page["has_more"] == (page["offset"] + page["returned"] < page["total"])


def test_list_queries_name_contains_filters(ctx):
    _, _, claims = ctx
    everything = execute_tool("list_queries", {"limit": 1000}, claims)
    filtered = execute_tool("list_queries", {"name_contains": "demo"}, claims)

    assert filtered["total"] <= everything["total"]
    for q in filtered["queries"]:
        assert "demo" in (q["id"] + (q["name"] or "")).casefold()


# ── board read / update ─────────────────────────────────────────────────────


def test_list_dashboards_is_compact(ctx):
    """The listing must never carry full specs — that is get_dashboard's job."""
    _, _, claims = ctx
    execute_tool("save_dashboard", {"spec": _spec(), "name": "Board A"}, claims)

    listing = execute_tool("list_dashboards", {}, claims)
    assert listing["total"] == 1
    entry = listing["dashboards"][0]
    assert entry["name"] == "Board A"
    assert entry["widget_count"] == 1
    assert "spec" not in entry


def test_get_and_update_dashboard_round_trip(ctx):
    _, _, claims = ctx
    created = execute_tool("save_dashboard", {"spec": _spec(), "name": "Before"}, claims)
    bid = created["id"]

    updated = execute_tool(
        "update_dashboard",
        {"board_id": bid, "name": "After", "spec": _spec("Edited")},
        claims,
    )
    assert updated["saved"] is True

    back = execute_tool("get_dashboard", {"board_id": bid}, claims)
    assert back["name"] == "After"
    assert back["spec"]["title"] == "Edited"


def test_update_dashboard_refuses_invalid_spec_without_writing(ctx):
    """A bad edit must not be able to destroy a working board."""
    _, _, claims = ctx
    created = execute_tool("save_dashboard", {"spec": _spec("Good"), "name": "Board"}, claims)
    bid = created["id"]

    res = execute_tool(
        "update_dashboard",
        {"board_id": bid, "spec": {"widgets": [{"type": "not_a_widget"}]}},
        claims,
    )
    assert res["valid"] is False and res["saved"] is False
    assert res["issues"]

    # The stored board is unchanged.
    back = execute_tool("get_dashboard", {"board_id": bid}, claims)
    assert back["spec"]["title"] == "Good"


def test_board_tools_are_org_scoped(ctx):
    """Org B must not be able to read or update org A's board."""
    _, _, claims_a = ctx
    created = execute_tool("save_dashboard", {"spec": _spec(), "name": "A's board"}, claims_a)
    bid = created["id"]

    claims_b = _claims(str(uuid.uuid4()), str(uuid.uuid4()))

    assert execute_tool("list_dashboards", {}, claims_b)["total"] == 0

    with pytest.raises(AppError) as read_exc:
        execute_tool("get_dashboard", {"board_id": bid}, claims_b)
    assert read_exc.value.code == "board_not_found"

    with pytest.raises(AppError) as write_exc:
        execute_tool("update_dashboard", {"board_id": bid, "name": "hijacked"}, claims_b)
    assert write_exc.value.code == "board_not_found"

    # A's board still has its original name.
    assert execute_tool("get_dashboard", {"board_id": bid}, claims_a)["name"] == "A's board"


def test_update_dashboard_rejects_embed_tokens(ctx):
    org_id, user_id, claims = ctx
    created = execute_tool("save_dashboard", {"spec": _spec(), "name": "Board"}, claims)

    embed = _claims(org_id, user_id, kind="embed")
    with pytest.raises(AppError) as exc:
        execute_tool("update_dashboard", {"board_id": created["id"], "name": "no"}, embed)
    assert exc.value.code == "insufficient_scope"


# ── delete_dashboard ────────────────────────────────────────────────────────


def test_delete_dashboard_removes_the_board(ctx):
    _, _, claims = ctx
    created = execute_tool("save_dashboard", {"spec": _spec(), "name": "Doomed"}, claims)
    bid = created["id"]

    res = execute_tool("delete_dashboard", {"board_id": bid, "confirm": True}, claims)
    assert res == {"id": bid, "name": "Doomed", "deleted": True}

    assert execute_tool("list_dashboards", {}, claims)["total"] == 0
    with pytest.raises(AppError) as exc:
        execute_tool("get_dashboard", {"board_id": bid}, claims)
    assert exc.value.code == "board_not_found"


def test_delete_dashboard_without_confirm_deletes_nothing(ctx):
    """The flag is the only gate standing in for the UI's confirm dialog."""
    _, _, claims = ctx
    created = execute_tool("save_dashboard", {"spec": _spec(), "name": "Keep me"}, claims)

    with pytest.raises(AppError) as exc:
        execute_tool("delete_dashboard", {"board_id": created["id"], "confirm": False}, claims)
    assert exc.value.code == "invalid_tool_input"

    assert execute_tool("get_dashboard", {"board_id": created["id"]}, claims)["name"] == "Keep me"


def test_delete_dashboard_is_org_scoped(ctx):
    _, _, claims_a = ctx
    created = execute_tool("save_dashboard", {"spec": _spec(), "name": "A's board"}, claims_a)
    bid = created["id"]

    claims_b = _claims(str(uuid.uuid4()), str(uuid.uuid4()))
    with pytest.raises(AppError) as exc:
        execute_tool("delete_dashboard", {"board_id": bid, "confirm": True}, claims_b)
    assert exc.value.code == "board_not_found"

    assert execute_tool("get_dashboard", {"board_id": bid}, claims_a)["name"] == "A's board"


def test_delete_dashboard_rejects_embed_tokens(ctx):
    org_id, user_id, claims = ctx
    created = execute_tool("save_dashboard", {"spec": _spec(), "name": "Board"}, claims)

    embed = _claims(org_id, user_id, kind="embed")
    with pytest.raises(AppError) as exc:
        execute_tool("delete_dashboard", {"board_id": created["id"], "confirm": True}, embed)
    assert exc.value.code == "insufficient_scope"

    assert execute_tool("get_dashboard", {"board_id": created["id"]}, claims)["name"] == "Board"


def test_delete_dashboard_missing_board_is_404(ctx):
    _, _, claims = ctx
    with pytest.raises(AppError) as exc:
        execute_tool("delete_dashboard", {"board_id": "nope", "confirm": True}, claims)
    assert exc.value.code == "board_not_found"


# ── route response guard ────────────────────────────────────────────────────


def test_response_guard_shrinks_oversized_payload_to_valid_json():
    big = {"rows": [{"i": i, "pad": "x" * 200} for i in range(5000)], "columns": ["i", "pad"]}
    assert len(json.dumps(big)) > _MAX_TOOL_RESULT_BYTES

    encoded = _encode_tool_result(big)
    assert len(encoded) <= _MAX_TOOL_RESULT_BYTES * 1.2

    parsed = json.loads(encoded)  # must still be valid JSON
    assert parsed["_truncated"]["total"] == 5000
    assert parsed["_truncated"]["returned"] < 5000
    assert len(parsed["rows"]) == parsed["_truncated"]["returned"]


def test_response_guard_passes_small_payloads_through_untouched():
    small = {"rows": [{"i": 1}], "columns": ["i"]}
    assert json.loads(_encode_tool_result(small)) == small


# ── Boards too big to read in one response ───────────────────────────────────


def _nested_spec(title: str = "Big") -> dict:
    spec = _spec(title)
    spec["widgets"].append(
        {
            "id": "s1",
            "type": "stepper",
            "props": {
                "title": "Drill",
                "steps": [
                    {
                        "id": "a",
                        "label": "First",
                        "widget": {"id": "inner", "type": "text", "title": "deep", "props": {"markdown": "x", "keep": 1}},
                    }
                ],
            },
        }
    )
    return spec


@pytest.fixture
def board(ctx):
    _, _, claims = ctx
    created = execute_tool("save_dashboard", {"spec": _nested_spec(), "name": "Big"}, claims)
    return created["id"], claims


def test_get_dashboard_outline_lists_every_widget_without_its_config(board):
    bid, claims = board
    res = execute_tool("get_dashboard", {"board_id": bid, "outline": True}, claims)
    assert "spec" not in res
    by_id = {w["id"]: w for w in res["widgets"]}
    assert set(by_id) == {"t1", "s1", "inner"}
    assert by_id["inner"]["parent_id"] == "s1"
    assert by_id["inner"]["title"] == "deep"
    assert by_id["s1"]["title"] == "Drill"
    assert "props" not in by_id["t1"]


def test_get_dashboard_returns_one_widget_even_when_nested(board):
    bid, claims = board
    res = execute_tool("get_dashboard", {"board_id": bid, "widget_id": "inner"}, claims)
    assert res["widget"]["props"]["markdown"] == "x"
    assert "spec" not in res


def test_get_dashboard_unknown_widget_is_an_error(board):
    bid, claims = board
    with pytest.raises(AppError) as exc:
        execute_tool("get_dashboard", {"board_id": bid, "widget_id": "nope"}, claims)
    assert exc.value.code == "widget_not_found"


def test_a_big_board_stays_readable_within_the_mcp_guard(ctx):
    _, _, claims = ctx
    spec = _spec("Huge")
    spec["widgets"].extend(
        {"id": f"w{i}", "type": "text", "title": f"t{i}", "props": {"markdown": "x" * 5000}} for i in range(40)
    )
    bid = execute_tool("save_dashboard", {"spec": spec, "name": "Huge"}, claims)["id"]

    assert "_truncated" in json.loads(
        _encode_tool_result(execute_tool("get_dashboard", {"board_id": bid}, claims))
    )
    outline = json.loads(
        _encode_tool_result(execute_tool("get_dashboard", {"board_id": bid, "outline": True}, claims))
    )
    assert "_truncated" not in outline and len(outline["widgets"]) == 41


def test_edit_dashboard_by_board_id_saves_without_the_full_spec(board):
    bid, claims = board
    res = execute_tool(
        "edit_dashboard",
        {"board_id": bid, "op": {"action": "configure_widget", "widget_id": "t1", "updates": {"title": "new"}}},
        claims,
    )
    assert res["saved"] is True and res["valid"] is True and "spec" not in res
    back = execute_tool("get_dashboard", {"board_id": bid, "widget_id": "t1"}, claims)
    assert back["widget"]["title"] == "new"


def test_edit_dashboard_reaches_a_widget_inside_a_stepper_and_merges_props(board):
    bid, claims = board
    execute_tool(
        "edit_dashboard",
        {"board_id": bid, "op": {"action": "configure_widget", "widget_id": "inner", "updates": {"props": {"markdown": "y"}}}},
        claims,
    )
    props = execute_tool("get_dashboard", {"board_id": bid, "widget_id": "inner"}, claims)["widget"]["props"]
    assert props == {"markdown": "y", "keep": 1}


def test_edit_dashboard_refuses_an_unknown_widget_rather_than_reporting_a_save(board):
    bid, claims = board
    with pytest.raises(AppError) as exc:
        execute_tool(
            "edit_dashboard",
            {"board_id": bid, "op": {"action": "configure_widget", "widget_id": "nope", "updates": {"title": "x"}}},
            claims,
        )
    assert exc.value.code == "widget_not_found"


def test_edit_dashboard_needs_a_spec_or_a_board(ctx):
    _, _, claims = ctx
    with pytest.raises(AppError) as exc:
        execute_tool("edit_dashboard", {"op": {"action": "remove_widget", "widget_id": "t1"}}, claims)
    assert exc.value.code == "invalid_tool_input"


def test_edit_dashboard_by_board_id_is_org_scoped(board):
    bid, _claims_a = board
    claims_b = _claims(str(uuid.uuid4()), str(uuid.uuid4()))
    with pytest.raises(AppError) as exc:
        execute_tool(
            "edit_dashboard",
            {"board_id": bid, "op": {"action": "remove_widget", "widget_id": "t1"}},
            claims_b,
        )
    assert exc.value.code == "board_not_found"


def test_edit_dashboard_by_board_id_is_refused_for_an_embed_session(board):
    bid, claims = board
    embed = {**claims, "kind": "embed"}
    with pytest.raises(AppError) as exc:
        execute_tool(
            "edit_dashboard",
            {"board_id": bid, "op": {"action": "remove_widget", "widget_id": "t1"}},
            embed,
        )
    assert exc.value.code == "insufficient_scope"
