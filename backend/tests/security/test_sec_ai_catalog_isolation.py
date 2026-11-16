"""AI grounding only ever sees the caller organisation's catalog."""

from __future__ import annotations

import json
import uuid

import pytest

from tests.security.conftest_helpers import as_user, make_tenant_app

ORG_A = str(uuid.uuid4())
ORG_B = str(uuid.uuid4())
SECRET_TABLE = "private_ledger_zq"
SECRET_COLUMN = "margin_secret_zq"


@pytest.fixture
def org_a_query():
    from app.queries.registry import get_query_registry

    reg = get_query_registry()
    qid = f"qa_{uuid.uuid4().hex[:10]}"
    unowned = f"qu_{uuid.uuid4().hex[:10]}"
    reg.register(
        id=qid, sql=f"SELECT {SECRET_COLUMN}, region FROM {SECRET_TABLE}", name="Ledger zq", owner_org_id=ORG_A
    )
    reg.register(id=unowned, sql=f"SELECT {SECRET_COLUMN} FROM {SECRET_TABLE}", name="Unattributed zq")
    yield qid, unowned
    reg.unregister(qid)
    reg.unregister(unowned)


def _leaks(payload, ids) -> list[str]:
    text = payload if isinstance(payload, str) else json.dumps(payload, default=str)
    return [needle for needle in (SECRET_TABLE, SECRET_COLUMN, "zq", *ids) if needle in text]


def test_catalog_requires_an_org_and_is_scoped_to_it(org_a_query):
    from app.ai.grounding import ALL_ORGS, build_catalog

    with pytest.raises(TypeError):
        build_catalog()  # type: ignore[call-arg]

    assert _leaks(build_catalog(ORG_B), org_a_query) == []
    assert _leaks(build_catalog(None), org_a_query) == []
    own = build_catalog(ORG_A)
    assert SECRET_TABLE in own["tables"]
    assert org_a_query[0] in {q["id"] for q in own["queries"]}
    assert org_a_query[1] not in {q["id"] for q in own["queries"]}
    # Built-in demo queries stay visible to everyone.
    assert build_catalog(ORG_B)["queries"]
    # The explicit operator view still spans everything.
    assert SECRET_TABLE in build_catalog(ALL_ORGS)["tables"]


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.repos.provider import set_repo

    app, _repo, _users = make_tenant_app(
        {"a": (ORG_A, "owner"), "b": (ORG_B, "owner")}, modules=("app.routes.ai",)
    )
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    set_repo(None)


@pytest.mark.parametrize(
    "path,body",
    [
        ("/api/v1/ai/ask", {"question": "show the margin for the private ledger by region"}),
        ("/api/v1/ai/sql", {"question": "show the margin for the private ledger by region"}),
        ("/api/v1/ai/dashboard", {"question": "dashboard of the private ledger margin by region"}),
    ],
)
def test_ai_routes_ground_on_the_callers_org_only(client, org_a_query, monkeypatch, path, body):
    import app.ai.grounding as grounding

    seen: list = []
    real = grounding.build_catalog

    def _spy(org_id):
        seen.append(org_id)
        return real(org_id)

    monkeypatch.setattr(grounding, "build_catalog", _spy)
    for mod in ("app.routes.ai", "app.ai.tools"):
        import importlib

        m = importlib.import_module(mod)
        if hasattr(m, "build_catalog"):
            monkeypatch.setattr(m, "build_catalog", _spy)

    resp = client.post(path, json=body, headers=as_user("b"))
    assert resp.status_code < 500, resp.text
    assert seen and all(o == ORG_B for o in seen), seen
    assert _leaks(resp.json(), org_a_query) == []


def test_ai_tools_ground_on_the_callers_org_only(org_a_query):
    from app.ai.tools import execute_tool

    claims = {"sub": str(uuid.uuid4()), "org": ORG_B, "scope": ["*"]}
    for name, args in (
        ("create_dashboard", {"question": "overview of the private ledger margin"}),
        ("generate_sql", {"question": "total margin by region for the private ledger"}),
        ("list_queries", {}),
    ):
        try:
            out = execute_tool(name, args, claims)
        except Exception as exc:  # noqa: BLE001 — a refusal is fine; a leak is not
            out = {"error": str(exc)}
        assert _leaks(out, org_a_query) == [], name


@pytest.mark.parametrize("path", ["/api/v1/ai/context"])
def test_context_for_an_org_the_caller_is_not_in_is_refused(client, org_a_query, path):
    resp = client.get(path, headers={**as_user("b"), "X-Org-Id": ORG_A})
    assert resp.status_code == 403, resp.text
    resp = client.get(path, headers=as_user("b"))
    assert resp.status_code == 200, resp.text
    assert _leaks(resp.json(), org_a_query) == []
