"""Tools reachable by an agent (MCP, AI chat) respect the caller's role and scope."""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi import Request

from tests.security.conftest_helpers import as_user, make_tenant_app

ORG = str(uuid.uuid4())
FULL = ["read:*", "edit:*", "author:sql", "author:metric"]


def _claims(role, scope=None, kind="access"):
    return {"kind": kind, "sub": str(uuid.uuid4()), "org": ORG, "role": role, "policies": {}, "scope": scope or FULL}


WRITE_CALLS = [
    ("run_query", {"sql": "SELECT 1"}),
    ("create_query", {"name": "x", "sql": "SELECT 1"}),
    ("delete_dashboard", {"dashboard_id": str(uuid.uuid4()), "confirm": True}),
    ("update_dashboard", {"dashboard_id": str(uuid.uuid4()), "spec": {"title": "t", "widgets": []}}),
    ("save_dashboard", {"spec": {"title": "t", "widgets": []}}),
]


def _refused(name, args, claims) -> bool:
    from app.ai.tools import execute_tool
    from app.errors import AppError

    try:
        out = execute_tool(name, args, claims)
    except AppError as exc:
        return exc.status == 403
    return isinstance(out, dict) and "error" in out and "permission" in json.dumps(out).lower()


@pytest.mark.parametrize("name,args", WRITE_CALLS, ids=[c[0] for c in WRITE_CALLS])
@pytest.mark.parametrize(
    "claims",
    [
        _claims("viewer"),
        _claims(None),
        {k: v for k, v in _claims("owner").items() if k != "role"},
        _claims("owner", scope=["read:*"]),
        _claims("owner", kind="embed"),
    ],
    ids=["viewer", "non-member", "no-role", "read-only-scope", "embed"],
)
def test_changing_and_sql_tools_need_a_writer(name, args, claims):
    assert _refused(name, args, claims)


def test_read_tools_still_work_for_viewers():
    from app.ai.tools import execute_tool

    assert "queries" in execute_tool("list_queries", {}, _claims("viewer", scope=["read:*"]))
    out = execute_tool("run_query", {"query_id": "demo_all"}, _claims("viewer", scope=["read:*"]))
    assert "error" not in out


def test_writer_passes_the_role_gate():
    from app.ai.tools import execute_tool

    out = execute_tool("run_query", {"sql": "SELECT 1 AS one"}, _claims("member"))
    assert "error" not in out


@pytest.mark.asyncio
async def test_tool_claims_follow_the_callers_role():
    from app.auth.roles import tool_claims
    from app.auth.verify import VerifiedIdentity
    from app.repos.memory import InMemoryRepo

    repo = InMemoryRepo()
    viewer, member = str(uuid.uuid4()), str(uuid.uuid4())
    repo.seed_org_member(ORG, viewer, "viewer")
    repo.seed_org_member(ORG, member, "member")

    def ident(uid):
        return VerifiedIdentity(
            kind="access", user_id=uid, org=None, project=None, roles=[], policies={"region": "x"},
            scope=list(FULL), embed_origin=None, datastore=None, raw_claims={},
        )

    c = await tool_claims(ident(viewer), ORG, repo)
    assert c["role"] == "viewer" and c["org"] == ORG and c["policies"] == {"region": "x"}
    assert not any(s.startswith(("edit:", "write:", "author:")) for s in c["scope"])
    c = await tool_claims(ident(member), ORG, repo)
    assert c["role"] == "member" and "author:sql" in c["scope"]
    c = await tool_claims(ident(str(uuid.uuid4())), ORG, repo)  # not a member
    assert c["role"] is None and "author:sql" not in c["scope"]


def test_ai_routes_do_not_hardcode_scopes():
    import inspect

    from app.routes import ai

    src = inspect.getsource(ai)
    assert '"write:*", "author:sql"' not in src


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.auth.deps import verified_identity
    from app.auth.verify import VerifiedIdentity
    from app.repos.provider import set_repo

    app, _repo, users = make_tenant_app(
        {"viewer": (ORG, "viewer"), "member": (ORG, "member")},
        modules=("app.routes.mcp", "app.routes.query"),
    )

    def _identity(request: Request):
        key = request.headers.get("x-test-user", "")
        return VerifiedIdentity(
            kind="access", user_id=users[key], org=None, project=None, roles=[], policies={},
            scope=list(FULL), embed_origin=None, datastore=None, raw_claims={},
        )

    app.dependency_overrides[verified_identity] = _identity
    from app.routes import mcp as mcp_routes

    def _caller(request: Request):
        return {"id": users[request.headers.get("x-test-user", "")]}, _identity(request)

    app.dependency_overrides[mcp_routes._mcp_caller] = _caller
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    set_repo(None)


def _mcp_call(client, user, name, arguments):
    resp = client.post(
        "/api/v1/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}},
        headers=as_user(user),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["result"]


@pytest.mark.parametrize("name,args", WRITE_CALLS[:3], ids=[c[0] for c in WRITE_CALLS[:3]])
def test_mcp_viewer_is_refused(client, name, args):
    assert _mcp_call(client, "viewer", name, args)["isError"] is True


def test_mcp_member_can_run_sql(client):
    result = _mcp_call(client, "member", "run_query", {"sql": "SELECT 1 AS one"})
    assert result["isError"] is False, result


def test_raw_sql_on_the_query_route_needs_a_writer(client):
    body = {"sql": "SELECT 1 AS one"}
    assert client.post("/api/v1/query", json=body, headers=as_user("viewer")).status_code == 403
    assert client.post("/api/v1/query", json=body, headers=as_user("member")).status_code == 200
    # A registered query stays readable for viewers.
    assert client.post("/api/v1/query", json={"query_id": "demo_all"}, headers=as_user("viewer")).status_code == 200
