"""Over MCP, changing or deleting a SAVED dashboard needs an owner or admin.

An external MCP client is a delegated agent with no in-app confirmation step,
so the destructive board operations are limited to the roles that approve
things in a workspace.  Creating boards, and everything read-only, is unchanged.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import Request

from tests.security.conftest_helpers import as_user, make_tenant_app

ORG = str(uuid.uuid4())
FULL = ["read:*", "edit:*", "author:sql", "author:metric"]
BOARD = str(uuid.uuid4())

SAVED_BOARD_CALLS = [
    ("delete_dashboard", {"board_id": BOARD, "confirm": True}),
    ("update_dashboard", {"board_id": BOARD, "spec": {"title": "t", "widgets": []}}),
    ("edit_dashboard", {"board_id": BOARD, "op": {"action": "remove_widget", "widget_id": "w1"}}),
]
OTHER_CALLS = [
    ("edit_dashboard", {"spec": {"title": "t", "widgets": []}, "op": {"action": "remove_widget", "widget_id": "w1"}}),
    ("save_dashboard", {"spec": {"title": "t", "widgets": []}}),
    ("list_dashboards", {}),
]


class _FakeResponse:
    status_code = 200
    headers = {"content-type": "application/json"}
    content = b"{}"
    text = "{}"

    def json(self):
        return {"ok": True}


@pytest.fixture
def mcp(monkeypatch):
    from fastapi.testclient import TestClient

    from app.auth.deps import verified_identity
    from app.auth.verify import VerifiedIdentity
    from app.repos.provider import set_repo
    from app.routes import mcp as mcp_routes

    app, _repo, users = make_tenant_app(
        {"viewer": (ORG, "viewer"), "member": (ORG, "member"), "admin": (ORG, "admin"), "owner": (ORG, "owner")},
        modules=("app.routes.mcp",),
    )

    def _identity(request: Request):
        return VerifiedIdentity(
            kind="access", user_id=users[request.headers.get("x-test-user", "")], org=None, project=None,
            roles=[], policies={}, scope=list(FULL), embed_origin=None, datastore=None, raw_claims={},
        )

    app.dependency_overrides[verified_identity] = _identity
    def _caller(request: Request):
        return {"id": users[request.headers.get("x-test-user", "")]}, _identity(request)

    app.dependency_overrides[mcp_routes._mcp_caller] = _caller

    ran: list[tuple[str, dict]] = []
    dispatched: list[tuple[str, str]] = []

    def _execute(name, arguments, claims):
        ran.append((name, arguments))
        return {"ok": True}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, url, **kwargs):
            dispatched.append((method, url))
            return _FakeResponse()

    import httpx

    monkeypatch.setattr("app.ai.tools.execute_tool", _execute)
    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    def call(user, name, arguments):
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/api/v1/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                      "params": {"name": name, "arguments": arguments}},
                headers=as_user(user),
            )
        assert resp.status_code == 200, resp.text
        return resp.json()["result"]

    yield call, ran, dispatched
    set_repo(None)


@pytest.mark.parametrize("name,args", SAVED_BOARD_CALLS, ids=[c[0] for c in SAVED_BOARD_CALLS])
@pytest.mark.parametrize("role", ["member", "viewer"])
def test_saved_board_tools_are_refused_below_admin(mcp, role, name, args):
    call, ran, _dispatched = mcp
    result = call(role, name, args)
    assert result["isError"] is True
    assert "owner or admin" in result["content"][0]["text"]
    assert ran == []  # nothing ran, whatever `confirm` said


@pytest.mark.parametrize("name,args", SAVED_BOARD_CALLS, ids=[c[0] for c in SAVED_BOARD_CALLS])
@pytest.mark.parametrize("role", ["admin", "owner"])
def test_saved_board_tools_run_for_owner_and_admin(mcp, role, name, args):
    call, ran, _dispatched = mcp
    result = call(role, name, args)
    assert result["isError"] is False, result
    assert [r[0] for r in ran] == [name]


@pytest.mark.parametrize("name,args", OTHER_CALLS, ids=["edit-in-hand", "save-new", "list"])
def test_members_keep_everything_that_does_not_touch_a_saved_board(mcp, name, args):
    call, ran, _dispatched = mcp
    result = call("member", name, args)
    assert result["isError"] is False, result
    assert [r[0] for r in ran] == [name]


API_SAVED_BOARD = [
    ("PUT", f"/boards/{BOARD}"), ("PATCH", f"/boards/{BOARD}"), ("DELETE", f"/boards/{BOARD}"),
    ("POST", f"/versions/board/{BOARD}/3/restore"), ("POST", f"/versions/boards/{BOARD}/3/restore"),
]
API_OTHER = [
    ("GET", f"/boards/{BOARD}"), ("GET", "/boards"), ("POST", "/boards"),
    ("POST", f"/versions/board/{BOARD}"), ("POST", f"/versions/query/{BOARD}/3/restore"),
    ("PUT", f"/queries/{BOARD}"),
]


@pytest.mark.parametrize("method,path", API_SAVED_BOARD, ids=[f"{m} {p.split('/')[1]}" + str(i) for i, (m, p) in enumerate(API_SAVED_BOARD)])
def test_api_request_cannot_change_a_saved_board_below_admin(mcp, method, path):
    call, _ran, dispatched = mcp
    result = call("member", "api_request", {"method": method, "path": path, "body": {"name": "x"}})
    assert result["isError"] is True
    assert "owner or admin" in result["content"][0]["text"]
    assert dispatched == []

    result = call("admin", "api_request", {"method": method, "path": path, "body": {"name": "x"}})
    assert result["isError"] is False, result
    assert dispatched == [(method, "/api/v1" + path)]


@pytest.mark.parametrize("method,path", API_OTHER, ids=[f"{m} {p.split('/')[1]}" + str(i) for i, (m, p) in enumerate(API_OTHER)])
def test_api_request_is_otherwise_unchanged_for_members(mcp, method, path):
    call, _ran, dispatched = mcp
    args = {"method": method, "path": path}
    if method != "GET":
        args["body"] = {"name": "x"}
    result = call("member", "api_request", args)
    assert result["isError"] is False, result
    assert dispatched == [(method, "/api/v1" + path)]


def test_one_rule_names_what_changes_a_saved_board():
    from app.routes.mcp import changes_saved_board_api, changes_saved_board_tool

    assert changes_saved_board_tool("delete_dashboard", {}) is True
    assert changes_saved_board_tool("update_dashboard", {"board_id": "b"}) is True
    assert changes_saved_board_tool("edit_dashboard", {"board_id": "b"}) is True
    assert changes_saved_board_tool("edit_dashboard", {"spec": {}}) is False
    assert changes_saved_board_tool("save_dashboard", {}) is False
    assert changes_saved_board_api("DELETE", "/boards/b1") is True
    assert changes_saved_board_api("POST", "/boards") is False
    assert changes_saved_board_api("GET", "/boards/b1") is False
