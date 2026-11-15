"""Every route that changes something declares who may call it.

A non-GET route must carry a role guard dependency (writer / approver /
superadmin) unless it is listed in ``EXEMPT`` with the reason it is safe
without one.  A new mutation route therefore fails this test until someone
either guards it or records why it does not need a guard.
"""

from __future__ import annotations

import uuid

import pytest

from tests.security.conftest_helpers import as_user, make_tenant_app

SELF = "acts only on the caller's own account or session"
READ = "read-only computation (POST carries a body, changes nothing)"
IN_HANDLER = "role is checked inside the handler"
PUBLIC = "unauthenticated by design; protected by its own secret, signature or flow"
TOOLS = "tool calls are role-gated in the tool layer"

EXEMPT: dict[tuple[str, str], str] = {
    # ── the caller's own account / session ──
    ("POST", "/auth/login"): PUBLIC, ("POST", "/auth/register"): PUBLIC,
    ("POST", "/auth/refresh"): PUBLIC, ("POST", "/auth/logout"): PUBLIC,
    ("PATCH", "/auth/me"): SELF, ("POST", "/auth/api-keys"): SELF, ("DELETE", "/auth/api-keys/{key_id}"): SELF,
    ("POST", "/notifications/read_all"): SELF, ("POST", "/notifications/{notification_id}/read"): SELF,
    ("POST", "/push/subscribe"): SELF, ("POST", "/push/unsubscribe"): SELF,
    ("DELETE", "/oauth/connections/{grant_id}"): SELF, ("POST", "/oauth/requests/{request_id}/decision"): SELF,
    # ── OAuth protocol endpoints, webhooks and scheduler ticks ──
    ("POST", "/oauth/register"): PUBLIC, ("POST", "/oauth/revoke"): PUBLIC, ("POST", "/oauth/token"): PUBLIC,
    ("POST", "/ee/billing/webhook"): PUBLIC, ("POST", "/flows/tick"): PUBLIC, ("POST", "/watches/tick"): PUBLIC,
    ("DELETE", "/mcp"): "always 405",
    # ── role checked in the handler ──
    ("POST", "/orgs"): "any signed-in user may create their own organisation",
    ("POST", "/orgs/invites/{token}/accept"): SELF,
    ("DELETE", "/orgs/{org_id}"): IN_HANDLER, ("PATCH", "/orgs/{org_id}"): IN_HANDLER,
    ("POST", "/orgs/{org_id}/invites"): IN_HANDLER, ("DELETE", "/orgs/{org_id}/invites/{invite_id}"): IN_HANDLER,
    ("DELETE", "/orgs/{org_id}/members/{member_id}"): IN_HANDLER,
    ("PUT", "/orgs/{org_id}/members/{member_id}"): IN_HANDLER,
    ("POST", "/bridges/{bridge_id}/tokens"): IN_HANDLER,
    ("DELETE", "/bridges/{bridge_id}/tokens/{token_id}"): IN_HANDLER,
    ("POST", "/bridges/{bridge_id}/tokens/{token_id}/rotate"): IN_HANDLER,
    ("POST", "/ai/keys"): IN_HANDLER, ("DELETE", "/ai/keys"): IN_HANDLER,
    ("POST", "/ee/billing/checkout"): IN_HANDLER, ("POST", "/ee/billing/wallet/topup"): IN_HANDLER,
    ("PUT", "/ee/billing/wallet/autotopup"): IN_HANDLER,
    ("POST", "/cache/invalidate"): IN_HANDLER,
    ("POST", "/compute/run"): IN_HANDLER,
    ("POST", "/embed/embed-token"): IN_HANDLER,
    # ── reads expressed as POST ──
    ("POST", "/query"): "raw SQL is role-gated in the handler; registered queries are reads",
    ("POST", "/query/estimate"): READ, ("POST", "/query/render"): READ, ("POST", "/query/validate"): READ,
    ("POST", "/query/complete"): READ, ("POST", "/query/registry/validate"): READ,
    ("POST", "/metrics/{metric_id}/query"): READ, ("POST", "/metrics/{metric_id}/sql"): READ,
    ("POST", "/boards/{board_id}/providers/{provider_id}/data"): READ,
    ("POST", "/dashboards/validate"): READ, ("POST", "/transpile"): READ,
    ("POST", "/flows/codegen"): READ, ("POST", "/flows/validate"): READ, ("POST", "/flows/{flow_id}/codegen"): READ,
    # ── agent surfaces ──
    ("POST", "/mcp"): TOOLS,
}


def _mutation_routes() -> list[tuple[str, str, bool]]:
    """``(method, path, guarded)`` for every non-GET API route."""
    import main
    from app.auth import roles, superadmin
    from tests.security.conftest_helpers import iter_api_routes, route_dependency_calls

    guards = {
        roles.require_writer, roles.require_writer_default,
        roles.require_approver, roles.require_approver_default,
        superadmin.require_superadmin,
    }
    found = []
    for path, methods, dependant in iter_api_routes(main.app):
        if not path.startswith("/api/v1"):
            continue
        guarded = bool(route_dependency_calls(dependant) & guards)
        for method in sorted(methods - {"GET", "HEAD", "OPTIONS"}):
            found.append((method, path[len("/api/v1"):], guarded))
    return found


def _unguarded() -> list[tuple[str, str]]:
    return [(m, p) for m, p, guarded in _mutation_routes() if not guarded]


def test_the_route_table_is_actually_being_inspected():
    routes = _mutation_routes()
    assert len(routes) > 150, f"only {len(routes)} mutation routes found — the walk is missing routers"
    assert sum(1 for _m, _p, guarded in routes if guarded) > 100


def test_every_mutation_route_is_guarded_or_explicitly_exempt():
    unexplained = sorted(set(_unguarded()) - set(EXEMPT))
    assert unexplained == [], (
        "Mutation routes with no role guard and no recorded exemption:\n  "
        + "\n  ".join(f"{m} {p}" for m, p in unexplained)
    )


def test_exemptions_are_not_stale():
    stale = sorted(set(EXEMPT) - set(_unguarded()))
    assert stale == [], f"exempt entries that are now guarded or gone (remove them): {stale}"


ORG = str(uuid.uuid4())
VIEWER_WRITES = [
    ("POST", "/api/v1/integrations", {"name": "x", "kind": "slack", "config": {}}),
    ("PUT", "/api/v1/integrations/i1", {"name": "x"}),
    ("DELETE", "/api/v1/integrations/i1", None),
    ("POST", "/api/v1/integrations/i1/test", None),
    ("POST", "/api/v1/watches", {"name": "w"}),
    ("PUT", "/api/v1/watches/w1", {"name": "w"}),
    ("DELETE", "/api/v1/watches/w1", None),
    ("POST", "/api/v1/watches/w1/evaluate", None),
    ("POST", "/api/v1/metrics", {"id": "m", "name": "m"}),
    ("PUT", "/api/v1/metrics/m1", {"name": "m"}),
    ("DELETE", "/api/v1/metrics/m1", None),
    ("POST", "/api/v1/boards/b1/snapshot", {}),
    ("POST", "/api/v1/boards/b1/export/public", {}),
    ("POST", "/api/v1/boards/b1/share", {}),
    ("POST", "/api/v1/connectors/c1/test", None),
    ("POST", "/api/v1/bridges/b1/heartbeat", None),
    ("POST", "/api/v1/query/registry", {"name": "q", "sql": "SELECT 1"}),
    ("POST", "/api/v1/compute/run", {"code": "result = None"}),
    ("POST", "/api/v1/images/from-url", {"url": "https://example.com/a.png"}),
    ("POST", "/api/v1/projects/sample/remove", None),
]


@pytest.fixture
def viewer_client():
    from fastapi.testclient import TestClient

    from app.repos.provider import set_repo

    app, _repo, _users = make_tenant_app(
        {"viewer": (ORG, "viewer")},
        modules=(
            "app.routes.integrations", "app.routes.watches", "app.routes.metrics", "app.routes.snapshot",
            "app.routes.export_share", "app.routes.connectors", "app.routes.bridges", "app.routes.query",
            "app.routes.compute", "app.routes.images", "app.routes.projects",
        ),
    )
    from app.auth.deps import verified_identity
    from app.auth.verify import VerifiedIdentity

    app.dependency_overrides[verified_identity] = lambda: VerifiedIdentity(
        kind="access", user_id=_users["viewer"], org=None, project=None, roles=[], policies={},
        scope=["read:*", "edit:*", "author:sql", "author:metric"], embed_origin=None, datastore=None,
        raw_claims={},
    )
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    set_repo(None)


@pytest.mark.parametrize("method,path,body", VIEWER_WRITES, ids=[f"{m} {p}" for m, p, _ in VIEWER_WRITES])
def test_viewers_cannot_write(viewer_client, method, path, body):
    resp = viewer_client.request(method, path, json=body, headers=as_user("viewer"))
    assert resp.status_code == 403, f"{resp.status_code} {resp.text[:200]}"
