"""Browsing raw tables is an authoring capability and never sidesteps row policies."""

from __future__ import annotations

import uuid

import pytest
from fastapi import Request

from tests.security.conftest_helpers import as_user, make_tenant_app

ORG = str(uuid.uuid4())
DS = str(uuid.uuid4())

DATASTORE_ROUTES = [
    f"/api/v1/data/{DS}/tables",
    f"/api/v1/data/{DS}/tables/orders/columns",
    f"/api/v1/data/{DS}/tables/orders/rows",
    f"/api/v1/datastores/{DS}/tables",
    f"/api/v1/datastores/{DS}/tables/orders/preview",
]
DEMO_ROUTES = ["/api/v1/data/tables", "/api/v1/data/tables/budget/rows"]


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.auth.deps import verified_identity
    from app.auth.verify import VerifiedIdentity
    from app.repos.provider import set_repo

    app, _repo, users = make_tenant_app(
        {"viewer": (ORG, "viewer"), "member": (ORG, "member"), "scoped": (ORG, "member")},
        modules=("app.routes.data_browser", "app.routes.query_tools"),
    )

    def _identity(request: Request):
        key = request.headers.get("x-test-user", "")
        return VerifiedIdentity(
            kind=request.headers.get("x-test-kind", "access"), user_id=users[key], org=None, project=None,
            roles=[], policies={"region": "EU"} if key == "scoped" else {},
            scope=request.headers.get("x-test-scope", "read:* edit:*").split(),
            embed_origin=None, datastore=None, raw_claims={},
        )

    app.dependency_overrides[verified_identity] = _identity
    # main.py mounts this router itself (it does not self-register).
    from app.routes.query_tools import router as query_tools_router

    app.include_router(query_tools_router, prefix="/api/v1")
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    set_repo(None)


@pytest.mark.parametrize("path", DATASTORE_ROUTES)
def test_read_only_members_cannot_browse_raw_tables(client, path):
    assert client.get(path, headers=as_user("viewer")).status_code == 403


@pytest.mark.parametrize("path", DATASTORE_ROUTES + DEMO_ROUTES)
def test_policy_restricted_identities_cannot_browse_raw_tables(client, path):
    assert client.get(path, headers=as_user("scoped")).status_code == 403


@pytest.mark.parametrize("path", DATASTORE_ROUTES + DEMO_ROUTES)
def test_embed_and_scopeless_credentials_cannot_browse(client, path):
    assert client.get(path, headers={**as_user("member"), "X-Test-Kind": "embed"}).status_code == 403
    assert client.get(path, headers={**as_user("member"), "X-Test-Scope": "exec:kernel"}).status_code == 403


@pytest.mark.parametrize("path", DATASTORE_ROUTES)
def test_writers_pass_the_guard(client, path):
    # The datastore does not exist, so the guard passing shows up as 404.
    assert client.get(path, headers=as_user("member")).status_code == 404


def test_demo_tables_stay_browsable_for_any_member(client):
    resp = client.get("/api/v1/data/tables", headers=as_user("viewer"))
    assert resp.status_code == 200, resp.text


def test_tick_secret_is_compared_in_constant_time():
    import inspect

    from app.routes import flows, watches

    for fn in (watches.watches_tick, flows.flows_tick):
        src = inspect.getsource(fn)
        assert "compare_digest" in src, fn.__name__
