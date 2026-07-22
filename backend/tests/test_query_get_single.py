"""GET /query/registry/{id} — open a single query by id (deep-link support).

The LIST endpoint excludes slug-only registry entries (embed-allowlist ids with
no persisted ``queries`` row). This endpoint lets the editor OPEN such a query
by id without widening that browse policy.

Test coverage
-------------
(1) A uuid-persisted query is returned with its sql + params.
(2) A slug-id query -- excluded from the LIST -- is still fetchable by id.
(3) An unknown id -> 404.
(4) Another org's persisted query is not readable -> 404.
(5) Unauthenticated request -> 401.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


def _auth_headers(user_id: str) -> dict[str, str]:
    from app.auth.jwt import mint_access_token
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


@pytest_asyncio.fixture
async def single_client(app, fake_db):
    """HTTPX client with a seeded user + org and an InMemoryRepo."""
    from app.repos.memory import InMemoryRepo
    from app.repos.provider import set_repo

    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    fake_db.users[user_id] = {
        "id": user_id,
        "email": "single_tester@example.com",
        "name": "Single Tester",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    repo = InMemoryRepo()
    repo.seed_org_member(org_id=org_id, user_id=user_id)
    set_repo(repo)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as ac:
        yield ac, user_id, org_id

    set_repo(None)


@pytest.mark.asyncio
async def test_uuid_query_is_fetchable_by_id(single_client):
    client, user_id, _org_id = single_client
    headers = _auth_headers(user_id)
    qid = str(uuid.uuid4())

    await client.post(
        "/api/v1/query/registry",
        json={
            "id": qid,
            "name": "Deep link me",
            "sql": "SELECT 1 AS n",
            "params": [{"name": "region", "type": "text"}],
        },
        headers=headers,
    )

    resp = await client.get(f"/api/v1/query/registry/{qid}", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == qid
    assert body["name"] == "Deep link me"
    assert body["sql"] == "SELECT 1 AS n"
    assert [p["name"] for p in body["params"]] == ["region"]


@pytest.mark.asyncio
async def test_slug_query_excluded_from_list_is_still_fetchable(single_client):
    """The whole point: slug ids are unlistable but must be openable by id."""
    client, user_id, _org_id = single_client
    headers = _auth_headers(user_id)
    slug_id = "q_revenue_by_region"

    await client.post(
        "/api/v1/query/registry",
        json={"id": slug_id, "name": "Migrated board query", "sql": "SELECT 2", "params": []},
        headers=headers,
    )

    # Confirm the premise: it is NOT in the browsable list...
    listed = await client.get("/api/v1/query/registry", headers=headers)
    assert listed.status_code == 200
    assert slug_id not in [q["id"] for q in listed.json()["queries"]]

    # ...but IS fetchable by id.
    resp = await client.get(f"/api/v1/query/registry/{slug_id}", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == slug_id
    assert resp.json()["sql"] == "SELECT 2"


@pytest.mark.asyncio
async def test_unknown_id_returns_404(single_client):
    client, user_id, _org_id = single_client
    resp = await client.get(
        "/api/v1/query/registry/q_does_not_exist", headers=_auth_headers(user_id)
    )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_other_orgs_persisted_query_is_not_readable(single_client):
    """A uuid query owned by another org must not leak through this route."""
    client, user_id, _org_id = single_client
    from app.repos.provider import get_repo

    other_org_id = str(uuid.uuid4())
    other_user_id = str(uuid.uuid4())
    foreign_qid = str(uuid.uuid4())
    await get_repo().create(
        "queries",
        other_org_id,
        other_user_id,
        "Foreign query",
        {"sql": "SELECT secret FROM vault", "params": []},
        id=foreign_qid,
    )

    resp = await client.get(
        f"/api/v1/query/registry/{foreign_qid}", headers=_auth_headers(user_id)
    )
    assert resp.status_code == 404, resp.text
    assert "secret" not in resp.text


@pytest.mark.asyncio
async def test_honours_x_org_id_for_a_non_default_org(single_client, fake_db):
    """Regression: the route must scope by X-Org-Id, not the user's DEFAULT org.

    Caught in the browser — the first implementation used `_resolve_caller_org`
    (which returns the user's first/default org and ignores the header), so a
    user viewing any *other* org they belong to got a 404 for queries that were
    plainly visible in that org's own board. GET /query/registry has always used
    the header-aware `resolve_org_id`; this must match it.
    """
    client, user_id, _default_org_id = single_client
    from app.repos.provider import get_repo

    # Same user, second org — the one they can "switch into" via the header.
    # (POST /query/registry always writes to the user's default org, so the
    # query below lands in the DEFAULT org regardless of the header.)
    second_org_id = str(uuid.uuid4())
    get_repo().seed_org_member(org_id=second_org_id, user_id=user_id)

    base = _auth_headers(user_id)
    slug_id = "q_default_org"
    reg = await client.post(
        "/api/v1/query/registry",
        json={"id": slug_id, "name": "Default org query", "sql": "SELECT 3", "params": []},
        headers=base,
    )
    assert reg.status_code == 201, reg.text

    # Visible while viewing the org that owns it.
    owned = await client.get(f"/api/v1/query/registry/{slug_id}", headers=base)
    assert owned.status_code == 200, owned.text

    # Switched into the OTHER org, the same query must NOT resolve. With the
    # original `_resolve_caller_org` implementation this returned 200 because
    # it silently used the user's default org and ignored the header.
    switched = await client.get(
        f"/api/v1/query/registry/{slug_id}",
        headers={**base, "X-Org-Id": second_org_id},
    )
    assert switched.status_code == 404, switched.text


@pytest.mark.asyncio
async def test_unauthenticated_request_returns_401(single_client):
    client, _user_id, _org_id = single_client
    resp = await client.get("/api/v1/query/registry/q_anything")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_running_a_query_honours_x_org_id(single_client):
    """Regression: POST /query gates on the org the caller is VIEWING.

    Found in the browser via the new error state. `_resolve_caller_org` used the
    user's DEFAULT org and ignored X-Org-Id, so a member of several orgs who
    switched workspaces got `query_not_registered` (403) for every registered
    query in the org they were looking at — the query is owned by org B, the
    allowlist gate checked org A. Whole boards failed in any non-default org,
    and the SAMPLE_TABLE fallback disguised it as "sample data".

    Asserted in the observable direction: a query owned by the DEFAULT org must
    stop resolving once the caller switches to another org. Pre-fix the gate
    ignored the header, so this returned something other than 403.
    """
    client, user_id, _default_org_id = single_client
    from app.repos.provider import get_repo

    second_org_id = str(uuid.uuid4())
    get_repo().seed_org_member(org_id=second_org_id, user_id=user_id)

    base = _auth_headers(user_id)
    slug_id = "q_run_scope"
    reg = await client.post(
        "/api/v1/query/registry",
        json={"id": slug_id, "name": "Run scope", "sql": "SELECT 1 AS n", "params": []},
        headers=base,
    )
    assert reg.status_code == 201, reg.text

    switched = await client.post(
        "/api/v1/query",
        json={"query_id": slug_id, "named_params": {}},
        headers={**base, "X-Org-Id": second_org_id},
    )
    assert switched.status_code == 403, switched.text
    assert "query_not_registered" in switched.text
