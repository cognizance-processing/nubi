"""Connection strings and auth headers are credentials: encrypted at rest, never echoed."""

from __future__ import annotations

import json
import uuid

import pytest

from tests.security.conftest_helpers import as_user, make_tenant_app

ORG = str(uuid.uuid4())
DSN = "postgresql://svc:hunter2-secret@db.example.com:5432/app"
HEADERS = {"Authorization": "Bearer tok-very-secret"}


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.connectors.secret_store import InMemorySecretStore, set_secret_store_for_tests
    from app.repos.provider import set_repo

    store = InMemorySecretStore()
    set_secret_store_for_tests(store)
    app, repo, _users = make_tenant_app({"u": (ORG, "owner")}, modules=("app.routes.connectors",))
    with TestClient(app, raise_server_exceptions=False) as c:
        c.repo, c.secrets = repo, store  # type: ignore[attr-defined]
        yield c
    set_repo(None)
    set_secret_store_for_tests(None)


def test_dsn_and_headers_in_config_are_stored_as_secrets(client):
    import asyncio

    resp = client.post(
        "/api/v1/connectors",
        json={"name": "pg", "type": "postgres", "config": {"dsn": DSN, "sslmode": "require"}},
        headers=as_user("u"),
    )
    assert resp.status_code == 201, resp.text
    assert "hunter2-secret" not in resp.text
    cid = resp.json()["id"]
    row = asyncio.run(client.repo.get("datastores", ORG, cid))
    assert "dsn" not in row["config"] and row["config"]["sslmode"] == "require"
    assert asyncio.run(client.secrets.get(cid, ORG))["dsn"] == DSN

    resp = client.post(
        "/api/v1/connectors",
        json={"name": "api", "type": "http_json", "config": {"url": "https://api.example.com/x", "headers": HEADERS}},
        headers=as_user("u"),
    )
    assert resp.status_code == 201, resp.text
    assert "tok-very-secret" not in resp.text
    hid = resp.json()["id"]
    assert "headers" not in asyncio.run(client.repo.get("datastores", ORG, hid))["config"]

    listing = client.get("/api/v1/connectors", headers=as_user("u")).text
    assert "hunter2-secret" not in listing and "tok-very-secret" not in listing


def test_existing_plaintext_values_are_scrubbed_from_responses(client):
    import asyncio

    row = asyncio.run(
        client.repo.create(
            resource="datastores", org_id=ORG, created_by="u", name="old",
            config={"connector_type": "postgres", "dsn": DSN, "headers": HEADERS, "s3_secret": "s3-very-secret"},
        )
    )
    for path in ("/api/v1/connectors", f"/api/v1/connectors/{row['id']}"):
        text = client.get(path, headers=as_user("u")).text
        for needle in ("hunter2-secret", "tok-very-secret", "s3-very-secret"):
            assert needle not in text, path


@pytest.mark.parametrize(
    "ctype,secret,expect",
    [
        ("postgres", {"dsn": DSN}, lambda cfg: cfg["dsn"] == DSN),
        ("mysql", {"dsn": "mysql://u:p@h/db"}, lambda cfg: cfg["dsn"] == "mysql://u:p@h/db"),
        ("http_json", {"headers": HEADERS}, lambda cfg: cfg["headers"]["Authorization"] == "Bearer tok-very-secret"),
        ("http_json", {"token": "t1"}, lambda cfg: cfg["headers"]["Authorization"] == "Bearer t1"),
        ("postgres", {"password": "pw"}, lambda cfg: cfg["password"] == "pw"),
        ("duckdb", {"s3_secret": "s"}, lambda cfg: cfg["s3_secret"] == "s"),
    ],
)
def test_secrets_are_merged_back_when_a_connector_is_built(ctype, secret, expect):
    from app.connectors.resolve import merge_connector_secret

    cfg = {"connector_type": ctype, "headers": {"Accept": "application/json"}} if ctype == "http_json" else {}
    merge_connector_secret(cfg, ctype, secret)
    assert expect(cfg), cfg


def test_there_is_one_secret_merge_implementation():
    import inspect

    from app.flows import registry
    from app.routes import query

    for mod in (registry, query):
        src = inspect.getsource(mod)
        assert "merge_connector_secret" in src, mod.__name__
        assert '_headers["X-API-Key"]' not in src, mod.__name__


# ---------------------------------------------------------------------------
# Claim-templated datastore config
# ---------------------------------------------------------------------------


def test_template_claims_come_from_verified_sources_only():
    from app.connectors.resolve import template_claims

    plan_claims = {"policies": {"tenant": "acme", "org": "spoofed-org"}, "org": "also-spoofed"}
    claims = template_claims(plan_claims, ORG)
    assert claims["org"] == ORG  # always the server-resolved organisation
    assert claims["tenant"] == "acme"
    assert "policies" not in claims

    assert template_claims({}, ORG) == {"org": ORG}
    assert "org" not in template_claims({"policies": {"tenant": "t"}}, None)


@pytest.mark.asyncio
async def test_templated_datastore_resolves_with_the_callers_org(monkeypatch):
    from app.connectors import resolve
    from app.connectors.plan import PhysicalPlan
    from app.connectors.secret_store import InMemorySecretStore, set_secret_store_for_tests
    from app.repos.memory import InMemoryRepo

    set_secret_store_for_tests(InMemorySecretStore())
    built: dict = {}

    class _Registry:
        def get(self, ctype):
            return lambda cfg: built.setdefault("cfg", cfg) or object()

    monkeypatch.setattr(resolve, "get_connector_registry", lambda: _Registry())
    repo = InMemoryRepo()
    row = await repo.create(
        resource="datastores", org_id=ORG, created_by="u", name="t",
        config={
            "connector_type": "snowflake", "account": "acct",
            "template_config": {"database": "ks_{{ claims.org }}", "schema": "{{ claims.tenant }}"},
        },
    )
    plan = PhysicalPlan(sql="SELECT 1", params=[], cache_key="", rls_claims={"policies": {"tenant": "acme"}})
    try:
        await resolve.resolve_datastore_connector(plan, str(row["id"]), ORG, repo)
    except Exception:  # noqa: BLE001 — only the built config matters here
        pass
    finally:
        set_secret_store_for_tests(None)
    assert built["cfg"]["database"] == f"ks_{ORG}"
    assert built["cfg"]["schema"] == "acme"
