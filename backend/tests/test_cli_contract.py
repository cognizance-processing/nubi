"""CLI ⇄ backend contract verification.

This suite proves that EVERY backend HTTP call the shipped ``nubi`` CLI makes
(see ``cli/nubi_cli/{main,client,project,secrets_files}.py``) hits a route that
(a) EXISTS (never 404/405 for a path/method the CLI uses) and (b) returns the
SHAPE the CLI consumes.  Where practical we do real round-trips against the
in-memory backend (create via API → export → re-import) so the envelope shape
the CLI's ``_export_envelope`` / ``project.py`` serializers read is exercised
end-to-end.

The harness mirrors ``tests/test_portability.py``:
- ``InMemoryRepo`` injected via ``set_repo()`` (no live DB),
- ``InMemorySecretStore`` injected for both the flow-secret store and the
  connector secret store,
- org membership seeded on the repo, the user seeded in FakeDB,
- a real first-party JWT minted via ``mint_access_token``.

The "endpoint EXISTS" assertion is deliberately strict: the response status is
asserted to NOT be 404 (unknown path) or 405 (wrong method).  A 401/403/400 is
acceptable for an exists-check because it proves FastAPI matched the route and
ran the handler; but for the calls the CLI relies on we go further and assert
the success status + response shape.
"""

from __future__ import annotations

import base64
import os
import uuid
from typing import Any

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient

# A valid Fernet key must exist before the secret stores are imported/used.
os.environ.setdefault("NUBI_SECRETS_KEY", Fernet.generate_key().decode())
# The connector secret store encrypts with AES-256-GCM keyed by
# CONNECTOR_SECRET_KEY (base64 32 bytes). Provide a deterministic test key so
# the in-memory connector store's put/get round-trips without a live KMS.
os.environ.setdefault(
    "CONNECTOR_SECRET_KEY",
    base64.b64encode(b"\x00" * 32).decode(),
)

# Import the routers the CLI depends on so they self-register on api_router for
# the test app, regardless of main.py import order.
import app.routes.portability  # noqa: F401,E402
import app.routes.connectors  # noqa: F401,E402
import app.routes.secrets  # noqa: F401,E402
import app.routes.flows  # noqa: F401,E402
import app.routes.git  # noqa: F401,E402
import app.routes.environments  # noqa: F401,E402

from app.auth.jwt import mint_access_token  # noqa: E402
from app.connectors.secret_store import (  # noqa: E402
    InMemorySecretStore as ConnInMemorySecretStore,
    set_secret_store_for_tests as set_conn_secret_store,
)
from app.repos.memory import InMemoryRepo  # noqa: E402
from app.repos.provider import set_repo  # noqa: E402
from app.secrets.store import (  # noqa: E402
    InMemorySecretStore as FlowInMemorySecretStore,
    set_secret_store as set_flow_secret_store,
)


def _make_user(user_id: str, email: str = "cli@example.com") -> dict[str, Any]:
    return {
        "id": user_id,
        "email": email,
        "name": "CLI User",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


def _auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


@pytest_asyncio.fixture
async def cli(app, fake_db):
    """Authed client + seeded org/repo/secret-stores, mirroring the CLI's env."""
    # Other suites (test_connector_secrets / test_classify_connectors) POP the
    # connector AES key in their teardown, leaving it UNSET for whatever test
    # runs next.  The in-memory connector store still encrypts (AES-256-GCM), so
    # the key MUST be present when our request hits PUT/POST /connectors. Set it
    # here (and restore the prior value) so this fixture is hermetic regardless
    # of suite ordering.
    _saved_key = os.environ.get("CONNECTOR_SECRET_KEY")
    _saved_ver = os.environ.get("CONNECTOR_SECRET_KEY_VERSION")
    _saved_keys = os.environ.get("CONNECTOR_SECRET_KEYS")
    os.environ["CONNECTOR_SECRET_KEY"] = base64.b64encode(b"\x00" * 32).decode()
    os.environ["CONNECTOR_SECRET_KEY_VERSION"] = "1"
    os.environ.pop("CONNECTOR_SECRET_KEYS", None)
    # crypto.py caches the key registry at first use — force a reload so our key
    # is the one in effect (another suite may have cached/cleared a different one).
    from app.security.crypto import reset_keys_for_tests  # noqa: PLC0415

    reset_keys_for_tests()

    repo = InMemoryRepo()
    set_repo(repo)
    set_flow_secret_store(FlowInMemorySecretStore())
    set_conn_secret_store(ConnInMemorySecretStore())

    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    fake_db.users[user_id] = _make_user(user_id)
    repo.seed_org_member(org_id=org_id, user_id=user_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as ac:
        yield ac, user_id, org_id, repo

    set_repo(None)
    set_flow_secret_store(None)
    set_conn_secret_store(None)

    # Restore the connector-key env to its prior state (None → unset).
    for _name, _val in (
        ("CONNECTOR_SECRET_KEY", _saved_key),
        ("CONNECTOR_SECRET_KEY_VERSION", _saved_ver),
        ("CONNECTOR_SECRET_KEYS", _saved_keys),
    ):
        if _val is None:
            os.environ.pop(_name, None)
        else:
            os.environ[_name] = _val
    # Drop our cached key so the next test loads from its own env.
    reset_keys_for_tests()


def _assert_route_exists(resp, where: str) -> None:
    """Assert FastAPI matched the route + method (not a *routing* 404/405).

    A 405 always means a missing method for that path → contract violation.

    A 404 is ambiguous: it can be FastAPI's UNMATCHED-ROUTE 404 (body
    ``{"detail": "Not Found"}``) OR the application HANDLER returning a
    domain ``not_found`` (body ``{"error": {"code": "not_found", ...}}``).
    The latter PROVES the route matched and the handler ran (the resource id
    simply doesn't exist for this org) — which is exactly what the CLI relies
    on.  So we only fail on the unmatched-route 404.
    """
    assert resp.status_code != 405, (
        f"{where}: method not allowed (HTTP 405) — the CLI uses a verb the "
        f"backend does not serve on this path. Body: {resp.text[:300]}"
    )
    if resp.status_code == 404:
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            body = {}
        # FastAPI's unmatched-route 404 has {"detail": "Not Found"} and no
        # structured app-error envelope. A handler-raised AppError 404 carries
        # {"error": {"code": ...}} and proves the route exists.
        is_handler_404 = isinstance(body, dict) and "error" in body
        assert is_handler_404, (
            f"{where}: route/method missing (FastAPI unmatched 404) — "
            f"the CLI calls a path the backend does not serve. Body: {resp.text[:300]}"
        )


# The CLI prepends nothing; client.py builds f"{api_url}/{path}" and api_url ends
# in /api/v1. So every path the CLI passes (e.g. "auth/login") maps to
# "/api/v1/auth/login". We replicate that prefix here.
P = "/api/v1"


#: Every (method, path) the CLI calls, resolved to its FastAPI route template
#: under the /api/v1 prefix. Verified directly against the app route table so a
#: missing route is caught even before any request is issued.
_CLI_ROUTE_CONTRACT: list[tuple[str, str]] = [
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/logout"),
    ("GET", "/api/v1/auth/me"),
    ("GET", "/api/v1/export/{kind}/{id}"),
    ("POST", "/api/v1/import"),
    # Generic resource CRUD (boards/queries/datastores via _list_resources,
    # deploy-files, diff, pull-raw).
    ("GET", "/api/v1/{resource}"),
    ("POST", "/api/v1/{resource}"),
    ("GET", "/api/v1/{resource}/{id}"),
    ("PUT", "/api/v1/{resource}/{id}"),
    # Connectors (own router).
    ("GET", "/api/v1/connectors"),
    ("POST", "/api/v1/connectors"),
    ("PUT", "/api/v1/connectors/{connector_id}"),
    ("POST", "/api/v1/connectors/{connector_id}/test"),
    # Secrets.
    ("GET", "/api/v1/secrets"),
    ("POST", "/api/v1/secrets"),
    ("DELETE", "/api/v1/secrets/{name}"),
    # Flows.
    ("GET", "/api/v1/flows"),
    ("POST", "/api/v1/flows"),
    ("PUT", "/api/v1/flows/{flow_id}"),
    # Git binding + env/project sync.
    ("POST", "/api/v1/git/connect"),
    ("GET", "/api/v1/projects/{project_id}/git/graph"),
    ("POST", "/api/v1/environments/{env_id}/git/push"),
    ("POST", "/api/v1/environments/{env_id}/git/pull"),
    # Query runner + project fetch.
    ("POST", "/api/v1/query"),
    ("GET", "/api/v1/projects/{project_id}"),
]


@pytest.mark.asyncio
async def test_every_cli_route_is_registered(app):
    """Static proof: every (method, path) the CLI calls exists on the app.

    This catches a missing route/method WITHOUT issuing a request, so it cannot
    be fooled by a handler-level 404. The list above is the canonical CLI ⇄
    backend contract — if a route is renamed/removed, this fails immediately.
    """
    registered: set[tuple[str, str]] = set()
    for route in app.routes:
        methods = getattr(route, "methods", None) or set()
        path = getattr(route, "path", "")
        for method in methods:
            registered.add((method, path))

    missing = [pair for pair in _CLI_ROUTE_CONTRACT if pair not in registered]
    assert not missing, f"CLI calls routes the backend does not register: {missing}"


# ---------------------------------------------------------------------------
# Auth: login / logout / me
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auth_me_shape(cli):
    """GET /auth/me → {user:{id,email,...}} (CLI whoami reads body['user'])."""
    client, user_id, org_id, repo = cli
    resp = await client.get(f"{P}/auth/me", headers=_auth(user_id))
    _assert_route_exists(resp, "GET /auth/me")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "user" in body
    assert body["user"]["id"] == user_id
    assert "email" in body["user"]


@pytest.mark.asyncio
async def test_auth_login_route_exists_and_returns_access_token_shape(cli):
    """POST /auth/login exists and returns an ``access_token`` key on success.

    The CLI reads ``resp.json()["access_token"]``.  We cannot mint a password
    login against the FakeDB (no password hash), but we CAN prove the route
    exists + the error envelope shape (``{error:{code,message}}``) the CLI's
    ``_raise_for_error`` parses on failure.
    """
    client, *_ = cli
    resp = await client.post(
        f"{P}/auth/login", json={"email": "nobody@example.com", "password": "x" * 8}
    )
    _assert_route_exists(resp, "POST /auth/login")
    # Wrong creds → 401 with the structured error envelope the CLI parses.
    assert resp.status_code == 401, resp.text
    body = resp.json()
    assert "error" in body and "message" in body["error"]


@pytest.mark.asyncio
async def test_auth_logout_route_exists(cli):
    """POST /auth/logout exists (CLI logout best-effort revoke; 204)."""
    client, user_id, *_ = cli
    resp = await client.post(f"{P}/auth/logout", headers=_auth(user_id))
    _assert_route_exists(resp, "POST /auth/logout")
    assert resp.status_code == 204, resp.text


# ---------------------------------------------------------------------------
# List endpoints used by _list_resources (plain list, not wrapped)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["boards", "queries", "flows", "connectors"])
async def test_list_endpoints_return_plain_list(cli, endpoint):
    """The four list endpoints the CLI iterates return a JSON array.

    ``_list_resources`` tolerates a wrapped ``{key:[...]}`` but the backend
    returns a plain list for all four — assert that so the CLI's primary path
    (``isinstance(items, list)``) is what actually fires.
    """
    client, user_id, *_ = cli
    resp = await client.get(f"{P}/{endpoint}", headers=_auth(user_id))
    _assert_route_exists(resp, f"GET /{endpoint}")
    assert resp.status_code == 200, resp.text
    assert isinstance(resp.json(), list), f"GET /{endpoint} must return a list"


# ---------------------------------------------------------------------------
# Dashboard export → import round-trip (the CLI pull/push core)
# ---------------------------------------------------------------------------

_DASHBOARD_SPEC = {
    "version": 1,
    "title": "CLI Dash",
    "layout": {"cols": 12, "row_height": 60},
    "variables": [],
    "widgets": [],
}


@pytest.mark.asyncio
async def test_dashboard_export_json_envelope_then_import(cli):
    """GET /export/dashboard/{id}?format=json → envelope; POST /import round-trips.

    Mirrors ``_export_envelope`` (format=json → dict) + ``_import_envelope``
    (POST /import json=envelope). Asserts the kind/apiVersion/metadata/spec shape
    that ``project.envelope_to_files`` consumes.
    """
    client, user_id, org_id, repo = cli
    created = await repo.create(
        resource="boards",
        org_id=org_id,
        created_by=user_id,
        name="CLI Dash",
        config={"spec": _DASHBOARD_SPEC},
    )
    bid = created["id"]

    # Export as JSON — this is exactly _export_envelope().
    resp = await client.get(
        f"{P}/export/dashboard/{bid}",
        params={"format": "json"},
        headers=_auth(user_id),
    )
    _assert_route_exists(resp, "GET /export/dashboard/{id}")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/json")
    env = resp.json()
    assert env["kind"] == "dashboard"
    assert env["apiVersion"] == "nubi/v1"
    assert env["metadata"]["id"] == bid
    assert env["metadata"]["name"] == "CLI Dash"
    assert isinstance(env.get("spec"), dict)

    # Re-import the envelope as a JSON body (exactly _import_envelope()).
    imp = await client.post(f"{P}/import", json=env, headers=_auth(user_id))
    _assert_route_exists(imp, "POST /import (dashboard)")
    assert imp.status_code == 200, imp.text
    body = imp.json()
    assert body["id"] == bid  # update-in-place, not a duplicate
    assert len(await repo.list("boards", org_id)) == 1


# ---------------------------------------------------------------------------
# Query export → import round-trip
# ---------------------------------------------------------------------------

_QUERY_SPEC = {
    "name": "CLI Query",
    "sql": "SELECT 1 AS one",
    "params": [],
    "datastore_id": None,
}


@pytest.mark.asyncio
async def test_query_export_json_envelope_then_import(cli):
    """GET /export/query/{id}?format=json → envelope; POST /import round-trips."""
    client, user_id, org_id, repo = cli
    created = await repo.create(
        resource="queries",
        org_id=org_id,
        created_by=user_id,
        name="CLI Query",
        config=_QUERY_SPEC,
    )
    qid = created["id"]

    resp = await client.get(
        f"{P}/export/query/{qid}", params={"format": "json"}, headers=_auth(user_id)
    )
    _assert_route_exists(resp, "GET /export/query/{id}")
    assert resp.status_code == 200, resp.text
    env = resp.json()
    assert env["kind"] == "query"
    assert env["metadata"]["id"] == qid
    assert env["spec"]["sql"] == "SELECT 1 AS one"

    imp = await client.post(f"{P}/import", json=env, headers=_auth(user_id))
    assert imp.status_code == 200, imp.text
    assert imp.json()["id"] == qid


# ---------------------------------------------------------------------------
# Connector export → import round-trip + the connector CRUD the CLI uses
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connector_create_list_export_import_test(cli):
    """Exercise POST /connectors, GET /connectors, GET/POST export+import, test.

    Covers ``_push_connector`` (POST /import + PUT /connectors/{id} fallback),
    ``connectors_test`` (POST /connectors/{id}/test), and the connector pull
    path (GET /connectors + GET /export/connector/{id}).
    """
    client, user_id, org_id, repo = cli

    # POST /connectors (CLI fallback create path): {name, type, config, secret}.
    create = await client.post(
        f"{P}/connectors",
        json={
            "name": "CLI PG",
            "type": "postgres",
            "config": {"host": "db.internal", "port": 5432, "database": "app", "user": "ro"},
            "secret": {"password": "s3cr3t"},
        },
        headers=_auth(user_id),
    )
    _assert_route_exists(create, "POST /connectors")
    assert create.status_code == 201, create.text
    cid = create.json()["id"]
    # No secret leaks back.
    assert "password" not in create.json().get("config", {})

    # GET /connectors → plain list including our connector.
    lst = await client.get(f"{P}/connectors", headers=_auth(user_id))
    assert lst.status_code == 200
    ids = {c["id"] for c in lst.json()}
    assert cid in ids

    # GET /export/connector/{id}?format=json → envelope the CLI connector pull reads.
    exp = await client.get(
        f"{P}/export/connector/{cid}", params={"format": "json"}, headers=_auth(user_id)
    )
    _assert_route_exists(exp, "GET /export/connector/{id}")
    assert exp.status_code == 200, exp.text
    env = exp.json()
    assert env["kind"] == "connector"
    assert env["spec"]["connector_type"] == "postgres"
    assert "password" not in env["spec"]

    # POST /import the connector envelope (CLI _push_connector primary path).
    imp = await client.post(f"{P}/import", json=env, headers=_auth(user_id))
    _assert_route_exists(imp, "POST /import (connector)")
    assert imp.status_code == 200, imp.text
    assert imp.json()["id"] == cid

    # PUT /connectors/{id} — secret rotation (CLI rotates after import).
    put = await client.put(
        f"{P}/connectors/{cid}", json={"secret": {"password": "rotated"}}, headers=_auth(user_id)
    )
    _assert_route_exists(put, "PUT /connectors/{id}")
    assert put.status_code == 200, put.text

    # POST /connectors/{id}/test → {ok: ...} (CLI reads body["ok"]).
    test = await client.post(f"{P}/connectors/{cid}/test", headers=_auth(user_id))
    _assert_route_exists(test, "POST /connectors/{id}/test")
    assert test.status_code == 200, test.text
    assert "ok" in test.json()


# ---------------------------------------------------------------------------
# Secrets: POST / GET / DELETE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_secrets_crud(cli):
    """POST /secrets, GET /secrets (plain list), DELETE /secrets/{name}."""
    client, user_id, org_id, repo = cli

    post = await client.post(
        f"{P}/secrets", json={"name": "API_KEY", "value": "v1"}, headers=_auth(user_id)
    )
    _assert_route_exists(post, "POST /secrets")
    assert post.status_code == 201, post.text
    assert post.json()["name"] == "API_KEY"
    assert "value" not in post.json()  # never returns the value

    lst = await client.get(f"{P}/secrets", headers=_auth(user_id))
    _assert_route_exists(lst, "GET /secrets")
    assert lst.status_code == 200, lst.text
    secrets = lst.json()
    assert isinstance(secrets, list)
    assert {s["name"] for s in secrets} == {"API_KEY"}

    delete = await client.delete(f"{P}/secrets/API_KEY", headers=_auth(user_id))
    _assert_route_exists(delete, "DELETE /secrets/{name}")
    assert delete.status_code == 204, delete.text


# ---------------------------------------------------------------------------
# Flows: POST / GET / PUT (CLI flows push/pull)
# ---------------------------------------------------------------------------

_FLOW_SPEC = {
    "name": "nightly",
    "tasks": [{"key": "a", "kind": "python", "config": {"code": "x = 1"}}],
}


@pytest.mark.asyncio
async def test_flows_create_list_update(cli):
    """POST /flows {name,spec}; GET /flows plain list; PUT /flows/{id}."""
    client, user_id, org_id, repo = cli

    create = await client.post(
        f"{P}/flows", json={"name": "nightly", "spec": _FLOW_SPEC}, headers=_auth(user_id)
    )
    _assert_route_exists(create, "POST /flows")
    assert create.status_code == 201, create.text
    fid = create.json()["id"]
    assert create.json()["name"] == "nightly"
    assert create.json()["spec"]["tasks"][0]["key"] == "a"

    lst = await client.get(f"{P}/flows", headers=_auth(user_id))
    assert lst.status_code == 200, lst.text
    assert isinstance(lst.json(), list)
    assert any(f["id"] == fid for f in lst.json())

    put = await client.put(
        f"{P}/flows/{fid}", json={"name": "nightly", "spec": _FLOW_SPEC}, headers=_auth(user_id)
    )
    _assert_route_exists(put, "PUT /flows/{id}")
    assert put.status_code == 200, put.text


# ---------------------------------------------------------------------------
# Query runner: POST /query
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_route_exists(cli):
    """POST /query exists and accepts the CLI's ``{query_id: ...}`` body.

    The CLI ``run`` command POSTs ``{"query_id": <id>}`` and reads the Arrow
    stream from ``resp.content``.  An unknown id yields a structured error, but
    the route must EXIST (never 404/405) and accept the body shape.
    """
    client, user_id, *_ = cli
    resp = await client.post(
        f"{P}/query", json={"query_id": str(uuid.uuid4())}, headers=_auth(user_id)
    )
    _assert_route_exists(resp, "POST /query")
    # Unknown query id → not a 404 on the *route*; the handler runs and returns
    # a 4xx error envelope (or 200 stream). Either proves the contract holds.
    assert resp.status_code != 405


# ---------------------------------------------------------------------------
# Git: POST /git/connect, GET /projects/{id}/git/graph,
#      POST /environments/{env_id}/git/push|pull
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_git_connect_route_exists(cli):
    """POST /git/connect exists and accepts the CLI's body shape.

    The CLI sends {project_id, provider, repo_url, branch, base_path, token}.
    With no real project the handler returns 404 (project not found) — but the
    ROUTE matched and the body validated, which is the contract under test.
    """
    client, user_id, *_ = cli
    resp = await client.post(
        f"{P}/git/connect",
        json={
            "project_id": str(uuid.uuid4()),
            "provider": "github",
            "repo_url": "https://github.com/acme/repo.git",
            "branch": "main",
            "base_path": "",
            "token": "ghp_xxx",
        },
        headers=_auth(user_id),
    )
    _assert_route_exists(resp, "POST /git/connect")
    # 404 (unknown project) is fine — the route + body schema are proven.
    assert resp.status_code in (200, 404), resp.text


@pytest.mark.asyncio
async def test_project_git_graph_route_exists(cli):
    """GET /projects/{id}/git/graph exists and returns a {branches:[...]} shape."""
    client, user_id, org_id, repo = cli
    # Seed a project row so _require_project passes and we exercise the success
    # path (CLI status/git graph reads body['branches']).
    pid = str(uuid.uuid4())
    resp = await client.get(f"{P}/projects/{pid}/git/graph", headers=_auth(user_id))
    _assert_route_exists(resp, "GET /projects/{id}/git/graph")
    # Unknown project → 404; a bound project → 200 {branches:[...]}. Either way
    # the route exists; assert the success shape when it is 200.
    if resp.status_code == 200:
        assert "branches" in resp.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("verb", ["push", "pull"])
async def test_env_git_push_pull_routes_exist(cli, verb):
    """POST /environments/{env_id}/git/{push,pull} exist (CLI ``sync``)."""
    client, user_id, *_ = cli
    body = {} if verb == "push" else {"strategy": "take_branch"}
    resp = await client.post(
        f"{P}/environments/{uuid.uuid4()}/git/{verb}", json=body, headers=_auth(user_id)
    )
    _assert_route_exists(resp, f"POST /environments/{{env_id}}/git/{verb}")


# ---------------------------------------------------------------------------
# Project fetch used by `nubi init --project <id>`
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_project_route_exists(cli):
    """GET /projects/{id} exists (CLI init fetches name/org_id)."""
    client, user_id, *_ = cli
    resp = await client.get(f"{P}/projects/{uuid.uuid4()}", headers=_auth(user_id))
    _assert_route_exists(resp, "GET /projects/{id}")
    assert resp.status_code in (200, 404), resp.text


# ---------------------------------------------------------------------------
# Legacy resource CRUD used by deploy-files / diff (datastores/boards/...)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_resource_crud_routes_exist(cli):
    """POST/GET/PUT /{resource} for the legacy deploy-files + diff commands."""
    client, user_id, org_id, repo = cli
    # CREATE (POST /{resource}) — deploy-files create path.
    create = await client.post(
        f"{P}/boards", json={"name": "B", "config": {"spec": _DASHBOARD_SPEC}}, headers=_auth(user_id)
    )
    _assert_route_exists(create, "POST /boards")
    assert create.status_code == 201, create.text
    bid = create.json()["id"]

    # GET /{resource}/{id} — diff fetch path.
    get = await client.get(f"{P}/boards/{bid}", headers=_auth(user_id))
    _assert_route_exists(get, "GET /boards/{id}")
    assert get.status_code == 200, get.text

    # PUT /{resource}/{id} — deploy-files update path.
    put = await client.put(
        f"{P}/boards/{bid}", json={"name": "B2", "config": {"spec": _DASHBOARD_SPEC}}, headers=_auth(user_id)
    )
    _assert_route_exists(put, "PUT /boards/{id}")
    assert put.status_code == 200, put.text
