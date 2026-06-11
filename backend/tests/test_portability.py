"""Tests for the portable export/import spec format (app/portability.py + route).

Strategy mirrors tests/test_resources.py:
- InMemoryRepo injected via set_repo() — no live DB.
- Seed org membership on the repo; seed the user in FakeDB; mint a real JWT.

Coverage
--------
- Dashboard export → import round-trip (export then import = update no-op).
- Query export → import round-trip.
- Import without metadata.id creates a new resource.
- Cross-org export → 404.
- Unknown kind → 404.
- YAML and JSON formats both round-trip.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
import yaml
from httpx import ASGITransport, AsyncClient

# Ensure the portability router is registered on api_router for the test app,
# regardless of whether main.py imports it yet (it is being wired separately).
import app.routes.portability  # noqa: F401
from app.auth.jwt import mint_access_token
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo


def _make_user(user_id: str, email: str = "alice@example.com") -> dict[str, Any]:
    return {
        "id": user_id,
        "email": email,
        "name": "Alice",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


def _auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


@pytest_asyncio.fixture
async def port_client(app, fake_db):
    repo = InMemoryRepo()
    set_repo(repo)

    alice_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    fake_db.users[alice_id] = _make_user(alice_id)
    repo.seed_org_member(org_id=org_id, user_id=alice_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as ac:
        yield ac, alice_id, org_id, repo

    set_repo(None)


# ── Dashboard round-trip ────────────────────────────────────────────────────

_DASHBOARD_SPEC = {
    "version": 1,
    "title": "Sales Overview",
    "layout": {"cols": 12, "row_height": 60},
    "variables": [],
    "widgets": [
        {
            "id": "w1",
            "type": "kpi",
            "query_id": "demo_all",
            "encoding": {"value": "value"},
            "props": {"label": "Total"},
            "pos": {"x": 1, "y": 1, "w": 3, "h": 2},
        }
    ],
}


@pytest.mark.asyncio
async def test_dashboard_export_import_roundtrip(port_client):
    client, alice_id, org_id, repo = port_client

    # Seed a board whose config nests the spec under 'spec'.
    created = await repo.create(
        resource="boards",
        org_id=org_id,
        created_by=alice_id,
        name="Sales Overview",
        config={"spec": _DASHBOARD_SPEC},
    )
    board_id = created["id"]

    # Export (YAML default).
    resp = await client.get(
        f"/api/v1/export/dashboard/{board_id}", headers=_auth(alice_id)
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/yaml")
    assert "attachment; filename=" in resp.headers["content-disposition"]

    doc = resp.text
    env = yaml.safe_load(doc)
    assert env["kind"] == "dashboard"
    assert env["apiVersion"] == "nubi/v1"
    assert env["metadata"]["id"] == board_id
    assert env["metadata"]["name"] == "Sales Overview"
    assert env["spec"]["title"] == "Sales Overview"

    # Re-import the exported document — id present → update in place (no-op).
    imp = await client.post(
        "/api/v1/import",
        content=doc,
        headers={**_auth(alice_id), "Content-Type": "application/yaml"},
    )
    assert imp.status_code == 200, imp.text
    body = imp.json()
    assert body["id"] == board_id  # same row updated, NOT a new one
    assert body["config"]["spec"]["title"] == "Sales Overview"

    # Still exactly one board in the org.
    boards = await repo.list("boards", org_id)
    assert len(boards) == 1


@pytest.mark.asyncio
async def test_dashboard_import_without_id_creates_new(port_client):
    client, alice_id, org_id, repo = port_client

    env = {
        "kind": "dashboard",
        "apiVersion": "nubi/v1",
        "metadata": {"name": "Fresh Board"},
        "spec": _DASHBOARD_SPEC,
    }
    imp = await client.post(
        "/api/v1/import",
        content=yaml.safe_dump(env),
        headers={**_auth(alice_id), "Content-Type": "application/yaml"},
    )
    assert imp.status_code == 200, imp.text
    body = imp.json()
    assert body["name"] == "Fresh Board"
    assert body["org_id"] == org_id
    assert body["config"]["spec"]["title"] == "Sales Overview"


# ── Query round-trip ────────────────────────────────────────────────────────

_QUERY_SPEC = {
    "name": "Active rows",
    "sql": "SELECT * FROM demo WHERE active = true",
    "params": [
        {"name": "region", "type": "text", "default": None, "required": False}
    ],
    "datastore_id": None,
}


@pytest.mark.asyncio
async def test_query_export_import_roundtrip_json(port_client):
    client, alice_id, org_id, repo = port_client

    created = await repo.create(
        resource="queries",
        org_id=org_id,
        created_by=alice_id,
        name="Active rows",
        config=_QUERY_SPEC,
    )
    qid = created["id"]

    # Export as JSON.
    resp = await client.get(
        f"/api/v1/export/query/{qid}?format=json", headers=_auth(alice_id)
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/json")

    env = resp.json()
    assert env["kind"] == "query"
    assert env["metadata"]["id"] == qid
    assert env["spec"]["sql"] == "SELECT * FROM demo WHERE active = true"
    assert env["spec"]["params"][0]["name"] == "region"

    # Re-import as JSON body → update in place.
    imp = await client.post(
        "/api/v1/import",
        json=env,
        headers=_auth(alice_id),
    )
    assert imp.status_code == 200, imp.text
    body = imp.json()
    assert body["id"] == qid
    assert body["config"]["sql"] == "SELECT * FROM demo WHERE active = true"

    queries = await repo.list("queries", org_id)
    assert len(queries) == 1


# ── Error cases ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_export_cross_org_returns_404(port_client):
    client, alice_id, org_id, repo = port_client

    other_org = str(uuid.uuid4())
    row = await repo.create(
        resource="boards",
        org_id=other_org,
        created_by=str(uuid.uuid4()),
        name="Secret",
        config={"spec": _DASHBOARD_SPEC},
    )
    resp = await client.get(
        f"/api/v1/export/dashboard/{row['id']}", headers=_auth(alice_id)
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_export_unknown_kind_returns_404(port_client):
    client, alice_id, org_id, repo = port_client
    resp = await client.get(
        f"/api/v1/export/connector/{uuid.uuid4()}", headers=_auth(alice_id)
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_import_invalid_query_spec_returns_400(port_client):
    client, alice_id, org_id, repo = port_client
    env = {
        "kind": "query",
        "apiVersion": "nubi/v1",
        "metadata": {"name": "Bad"},
        "spec": {"sql": "", "params": []},  # empty sql → validation failure
    }
    imp = await client.post(
        "/api/v1/import",
        content=yaml.safe_dump(env),
        headers={**_auth(alice_id), "Content-Type": "application/yaml"},
    )
    assert imp.status_code == 400, imp.text


# ── Connector round-trip (NON-SECRET config only) ───────────────────────────
# Connectors are portable as NON-SECRET config exclusively. These tests prove:
#   - export scrubs every _SECRET_KEYS field and never reads the secret store,
#   - import upserts datastores.config only and never touches connector_secrets,
#   - cross-tenant id access is denied (404 / fresh create),
#   - export → import is a stable round-trip.


async def _seed_connector(repo, org_id, *, name="Prod Postgres", config=None):
    """Create a connector datastore row directly on the repo (no secret store)."""
    cfg = config or {
        "connector_type": "postgres",
        "host": "db.internal",
        "port": 5432,
        "database": "analytics",
        "user": "readonly",
        "sslmode": "require",
        "network_mode": "direct",
        "bridge_id": None,
    }
    return await repo.create(
        resource="datastores",
        org_id=org_id,
        created_by=str(uuid.uuid4()),
        name=name,
        config=cfg,
    )


@pytest.mark.asyncio
async def test_connector_export_scrubs_secrets(port_client):
    from app.routes.connectors import _SECRET_KEYS

    client, alice_id, org_id, repo = port_client

    # Seed a connector whose config *also* (illegally) carries secret keys —
    # export must scrub them, proving no secret can leak even from a dirty row.
    created = await _seed_connector(
        repo,
        org_id,
        config={
            "connector_type": "postgres",
            "host": "db.internal",
            "port": 5432,
            "database": "analytics",
            "user": "readonly",
            "sslmode": "require",
            # Secret material that must NEVER appear in the envelope:
            "password": "s3cr3t",
            "service_account_json": '{"type":"service_account"}',
            "api_key": "ak_live_123",
        },
    )
    cid = created["id"]

    resp = await client.get(
        f"/api/v1/export/connector/{cid}", headers=_auth(alice_id)
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/yaml")

    doc = resp.text
    env = yaml.safe_load(doc)
    assert env["kind"] == "connector"
    assert env["apiVersion"] == "nubi/v1"
    assert env["metadata"]["id"] == cid
    assert env["metadata"]["name"] == "Prod Postgres"

    spec = env["spec"]
    assert spec["connector_type"] == "postgres"
    assert spec["host"] == "db.internal"
    assert spec["user"] == "readonly"

    # No secret key anywhere in the spec — nor in the raw serialised document.
    for key in _SECRET_KEYS:
        assert key not in spec, f"secret {key!r} leaked into spec"
    assert "s3cr3t" not in doc
    assert "ak_live_123" not in doc
    assert "service_account" not in doc


@pytest.mark.asyncio
async def test_connector_export_import_roundtrip(port_client):
    client, alice_id, org_id, repo = port_client

    created = await _seed_connector(repo, org_id)
    cid = created["id"]

    resp = await client.get(
        f"/api/v1/export/connector/{cid}", headers=_auth(alice_id)
    )
    assert resp.status_code == 200, resp.text
    doc = resp.text

    # Re-import the exported document — id present → update in place (no-op).
    imp = await client.post(
        "/api/v1/import",
        content=doc,
        headers={**_auth(alice_id), "Content-Type": "application/yaml"},
    )
    assert imp.status_code == 200, imp.text
    body = imp.json()
    assert body["id"] == cid  # same row updated, NOT a new one
    assert body["config"]["connector_type"] == "postgres"
    assert body["config"]["host"] == "db.internal"

    # Still exactly one connector datastore row in the org.
    rows = [
        r for r in await repo.list("datastores", org_id)
        if isinstance(r.get("config"), dict)
        and "connector_type" in r["config"]
    ]
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_connector_import_never_touches_secret_store(port_client):
    """Importing a connector envelope must not write any connector secret."""
    from app.connectors import secret_store as ss_mod

    client, alice_id, org_id, repo = port_client

    created = await _seed_connector(repo, org_id)
    cid = created["id"]

    # Spy on the secret store: any put() during import is a contract violation.
    store = ss_mod.get_secret_store()
    calls: list[tuple] = []
    orig_put = store.put

    async def _spy_put(datastore_id, org, secret):  # noqa: ANN001
        calls.append((datastore_id, org, secret))
        return await orig_put(datastore_id, org, secret)

    store.put = _spy_put  # type: ignore[assignment]
    try:
        env = {
            "kind": "connector",
            "apiVersion": "nubi/v1",
            "metadata": {"name": "Prod Postgres", "id": cid},
            "spec": {
                "connector_type": "postgres",
                "host": "db.internal",
                "port": 5432,
                "database": "analytics",
                "user": "readonly",
            },
        }
        imp = await client.post(
            "/api/v1/import", json=env, headers=_auth(alice_id)
        )
        assert imp.status_code == 200, imp.text
    finally:
        store.put = orig_put  # type: ignore[assignment]

    assert calls == [], "import must NEVER write to the connector secret store"


@pytest.mark.asyncio
async def test_connector_import_with_secret_in_spec_rejected(port_client):
    """A connector envelope carrying a secret key is rejected (400)."""
    client, alice_id, org_id, repo = port_client

    env = {
        "kind": "connector",
        "apiVersion": "nubi/v1",
        "metadata": {"name": "Sneaky"},
        "spec": {
            "connector_type": "postgres",
            "host": "db.internal",
            "password": "s3cr3t",  # secret in the envelope → must be rejected
        },
    }
    imp = await client.post(
        "/api/v1/import", json=env, headers=_auth(alice_id)
    )
    assert imp.status_code == 400, imp.text
    assert "password" in imp.text


@pytest.mark.asyncio
async def test_connector_import_without_id_creates_new(port_client):
    client, alice_id, org_id, repo = port_client

    env = {
        "kind": "connector",
        "apiVersion": "nubi/v1",
        "metadata": {"name": "Fresh Connector"},
        "spec": {
            "connector_type": "mysql",
            "host": "mysql.internal",
            "port": 3306,
            "database": "app",
            "user": "svc",
        },
    }
    imp = await client.post(
        "/api/v1/import", json=env, headers=_auth(alice_id)
    )
    assert imp.status_code == 200, imp.text
    body = imp.json()
    assert body["name"] == "Fresh Connector"
    assert body["org_id"] == org_id
    assert body["config"]["connector_type"] == "mysql"
    assert body["config"]["host"] == "mysql.internal"


@pytest.mark.asyncio
async def test_connector_export_cross_org_returns_404(port_client):
    client, alice_id, org_id, repo = port_client

    other_org = str(uuid.uuid4())
    row = await _seed_connector(repo, other_org, name="Other Org DB")
    resp = await client.get(
        f"/api/v1/export/connector/{row['id']}", headers=_auth(alice_id)
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_connector_import_cross_org_id_creates_new_not_mutates(port_client):
    """Import with another org's connector id must not mutate that row."""
    client, alice_id, org_id, repo = port_client

    other_org = str(uuid.uuid4())
    foreign = await _seed_connector(repo, other_org, name="Other Org DB")
    foreign_id = foreign["id"]

    env = {
        "kind": "connector",
        "apiVersion": "nubi/v1",
        "metadata": {"name": "Hijack Attempt", "id": foreign_id},
        "spec": {
            "connector_type": "postgres",
            "host": "evil.internal",
            "user": "attacker",
        },
    }
    imp = await client.post(
        "/api/v1/import", json=env, headers=_auth(alice_id)
    )
    assert imp.status_code == 200, imp.text
    body = imp.json()
    # A fresh row was created in the caller's org — the foreign row is untouched.
    assert body["org_id"] == org_id
    assert body["id"] != foreign_id

    foreign_after = await repo.get("datastores", other_org, foreign_id)
    assert foreign_after is not None
    assert foreign_after["config"]["host"] == "db.internal"  # unchanged
    assert foreign_after["name"] == "Other Org DB"


@pytest.mark.asyncio
async def test_connector_export_rejects_non_connector_datastore(port_client):
    """A datastore row without a connector_type is not exportable as connector."""
    client, alice_id, org_id, repo = port_client

    row = await repo.create(
        resource="datastores",
        org_id=org_id,
        created_by=alice_id,
        name="not a connector",
        config={"something": "else"},  # no connector_type
    )
    resp = await client.get(
        f"/api/v1/export/connector/{row['id']}", headers=_auth(alice_id)
    )
    assert resp.status_code == 404


# ── Connector KindHandler unit tests (no DB) ─────────────────────────────────


def test_kind_registry_has_connector_with_folder():
    from app.portability import KIND_REGISTRY

    assert "connector" in KIND_REGISTRY
    assert KIND_REGISTRY["connector"].folder == "connectors"
    assert KIND_REGISTRY["connector"].resource == "datastores"


def test_connector_spec_from_row_scrubs_secret_keys():
    from app.portability import KIND_REGISTRY
    from app.routes.connectors import _SECRET_KEYS

    h = KIND_REGISTRY["connector"]
    row = {
        "id": "c-1",
        "name": "PG",
        "config": {
            "connector_type": "postgres",
            "host": "h",
            "password": "leak",
            "token": "leak2",
        },
    }
    spec = h.spec_from_row(row)
    assert spec["connector_type"] == "postgres"
    assert spec["host"] == "h"
    assert not (_SECRET_KEYS & set(spec.keys()))


def test_connector_validate_requires_type_and_rejects_secrets():
    from app.portability import KIND_REGISTRY

    h = KIND_REGISTRY["connector"]
    assert h.validate({"host": "h"})  # missing connector_type → issue
    assert h.validate({"connector_type": "nope"})  # unknown type → issue
    assert h.validate({"connector_type": "postgres", "password": "x"})  # secret
    assert h.validate({"connector_type": "postgres", "host": "h"}) == []


# ---------------------------------------------------------------------------
# Flow KindHandler unit tests (no DB) — registry-driven sync round-trip (A2/A3)
# ---------------------------------------------------------------------------


def test_kind_registry_has_flow_with_folder():
    from app.portability import KIND_REGISTRY

    assert "flow" in KIND_REGISTRY
    # Folders are centralised so push AND pull iterate the same set.
    assert KIND_REGISTRY["flow"].folder == "flows"
    assert KIND_REGISTRY["dashboard"].folder == "dashboards"
    assert KIND_REGISTRY["query"].folder == "queries"


def test_flow_spec_from_row_and_row_fields_roundtrip():
    from app.portability import KIND_REGISTRY

    h = KIND_REGISTRY["flow"]
    spec = {
        "name": "nightly",
        "tasks": [{"key": "a", "kind": "python", "config": {"code": "x = 1"}}],
    }
    row = {"id": "f-1", "name": "nightly", "spec": spec}

    extracted = h.spec_from_row(row)
    assert extracted == spec

    env = {"metadata": {"name": "nightly", "id": "f-1"}, "spec": extracted}
    fields = h.row_fields(env)
    assert fields["name"] == "nightly"
    assert fields["spec"] == spec
    assert fields["id"] == "f-1"


def test_flow_validate_rejects_bad_spec_and_accepts_good():
    from app.portability import KIND_REGISTRY

    h = KIND_REGISTRY["flow"]
    # Missing tasks / wrong shape → hard issues.
    assert h.validate({"tasks": [{"key": "a", "kind": "python", "config": {}}]})
    # Valid minimal flow → no HARD issues ([warn] forward-refs allowed).
    good = {
        "name": "ok",
        "tasks": [{"key": "a", "kind": "python", "config": {"code": "y = 2"}}],
    }
    hard = [i for i in h.validate(good) if not str(i).startswith("[warn]")]
    assert hard == []
