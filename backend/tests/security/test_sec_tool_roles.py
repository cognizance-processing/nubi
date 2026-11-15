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


# ---------------------------------------------------------------------------
# api_request: an explicit allowlist of what a delegated agent may call
# ---------------------------------------------------------------------------

ALLOWED = [
    ("GET", "/boards"), ("POST", "/boards"), ("PUT", "/boards/abc"), ("DELETE", "/boards/abc"),
    ("GET", "/queries/abc"), ("POST", "/queries"), ("POST", "/widgets"),
    ("POST", "/query"), ("POST", "/query/estimate"), ("GET", "/query/registry"), ("POST", "/query/registry"),
    ("GET", "/query/registry/q1/widgets"), ("POST", "/boards/b1/providers/p1/data"),
    ("GET", "/metrics"), ("POST", "/metrics/m1/query"), ("PUT", "/metrics/m1"),
    ("GET", "/connectors"), ("GET", "/connectors/c1"), ("GET", "/datastores"),
    ("GET", "/data/c1/tables/t/rows"), ("GET", "/projects"), ("POST", "/projects"), ("PATCH", "/projects/p1"),
    ("GET", "/orgs"), ("GET", "/orgs/o1"), ("GET", "/flows"), ("GET", "/flows/f1"),
    ("POST", "/dashboards/validate"), ("PUT", "/variables/k"), ("POST", "/images"), ("GET", "/features"),
    ("POST", "/versions/board/b1"), ("GET", "/versions/board/b1/3"),
]
REFUSED = [
    ("DELETE", "/orgs/o1"), ("PATCH", "/orgs/o1"), ("POST", "/orgs"), ("DELETE", "/projects/p1"),
    ("POST", "/orgs/o1/invites"), ("DELETE", "/orgs/o1/members/u1"), ("GET", "/orgs/o1/invites"),
    ("POST", "/connectors"), ("PUT", "/connectors/c1"), ("DELETE", "/connectors/c1"), ("POST", "/connectors/c1/test"),
    ("POST", "/datastores"), ("PUT", "/datastores/d1"), ("DELETE", "/datastores/d1"),
    ("GET", "/git/history"), ("POST", "/git/sync"), ("POST", "/git/connect"), ("POST", "/environments/e1/git/push"),
    ("GET", "/integrations"), ("POST", "/integrations"),
    ("POST", "/flows"), ("POST", "/flows/f1/run"), ("POST", "/flows/run-cell"), ("POST", "/flows/compile"),
    ("POST", "/flows/preview"), ("PUT", "/flows/f1"), ("DELETE", "/flows/f1"), ("POST", "/compute/run"),
    ("POST", "/images/from-url"), ("POST", "/import"), ("POST", "/projects/p1/import"), ("POST", "/apply"),
    ("GET", "/ee/billing"), ("POST", "/ee/billing/checkout"), ("GET", "/chat/conversations"), ("POST", "/chat/stream"),
    ("POST", "/ai/chat"), ("POST", "/ai/sql"), ("GET", "/auth/me"), ("POST", "/auth/api-keys"), ("GET", "/secrets"),
    ("GET", "/admin/orgs"), ("GET", "/security/jwt-issuers"), ("POST", "/mcp/servers"), ("POST", "/oauth/token"),
    ("POST", "/bridges/b1/tokens"), ("POST", "/embed/embed-token"), ("POST", "/access-grants"),
    ("POST", "/cache/invalidate"), ("POST", "/preagg/build"), ("POST", "/boards/b1/share"),
    ("POST", "/boards/b1/export/public"), ("PATCH", "/data/c1/tables/t/rows"), ("POST", "/webhooks"),
    ("POST", "/push/subscribe"), ("POST", "/jobs"), ("POST", "/watches/tick"), ("GET", "/something-new"),
]


@pytest.mark.parametrize("method,path", ALLOWED, ids=[f"{m} {p}" for m, p in ALLOWED])
def test_api_request_allows_the_working_surface(method, path):
    from app.routes.mcp import _api_allowed

    assert _api_allowed(method, path) is True


@pytest.mark.parametrize("method,path", REFUSED, ids=[f"{m} {p}" for m, p in REFUSED])
def test_api_request_refuses_everything_else(method, path):
    from app.routes.mcp import _api_allowed

    assert _api_allowed(method, path) is False


# ---------------------------------------------------------------------------
# External MCP servers are an org-wide trust decision
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role,expected", [("viewer", 403), ("member", 403), ("admin", 201), ("owner", 201)])
def test_only_owner_or_admin_register_external_mcp_servers(role, expected):
    from unittest.mock import AsyncMock, MagicMock, patch

    from fastapi.testclient import TestClient

    from app.repos.provider import set_repo

    store = MagicMock()
    store.create = AsyncMock(return_value={"id": str(uuid.uuid4()), "name": "ext", "url": "https://mcp.example.com/mcp"})
    store.get = AsyncMock(return_value=None)
    app, _repo, _users = make_tenant_app({"u": (ORG, role)}, modules=("app.routes.mcp",))
    try:
        with (
            patch("app.mcp.store.get_mcp_store", return_value=store),
            patch("app.routes.mcp.guard_url", lambda url: None),
            TestClient(app, raise_server_exceptions=False) as c,
        ):
            resp = c.post(
                "/api/v1/mcp/servers",
                json={"name": "ext", "url": "https://mcp.example.com/mcp"},
                headers=as_user("u"),
            )
            assert resp.status_code == expected, resp.text
            assert store.create.await_count == (1 if expected == 201 else 0)
            if role == "viewer":
                assert c.post(f"/api/v1/mcp/servers/{uuid.uuid4()}/test", headers=as_user("u")).status_code == 403
    finally:
        set_repo(None)
