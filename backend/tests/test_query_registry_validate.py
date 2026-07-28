"""POST /query/registry/validate — batch "will this query even run?" check.

A registered query whose stored SQL no longer parses is indistinguishable from
a healthy one in the query library: it fails only once the user opens it and
presses Run. Migrated estates carry many of those (a legacy filter variable
that rendered to the empty string leaves ``WHERE d BETWEEN  AND x``), so the
library needs to flag them up front. This endpoint is what it asks.

Test coverage
-------------
(1) A parseable query is reported valid.
(2) A query with a dangling BETWEEN is reported invalid, with a readable reason.
(3) Declared params are rendered with their DEFAULTS — the same thing POST
    /query does — so a template that is only valid once filled still passes.
(4) Unknown ids are omitted rather than reported (no existence oracle).
(5) Another org's query is omitted too — validate must not become a side
    channel around the list endpoint's scoping.
(6) The id list is capped so one call stays cheap.
(7) Unauthenticated -> 401.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# A query whose date-range filter var rendered to the empty string: the shape
# that made 228 of one migrated library's 1103 queries unrunnable.
DANGLING_BETWEEN_SQL = "SELECT * FROM t\nWHERE d BETWEEN \nAND x IS NOT NULL\n"


def _auth_headers(user_id: str) -> dict[str, str]:
    from app.auth.jwt import mint_access_token

    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


@pytest_asyncio.fixture
async def validate_client(app, fake_db):
    from app.repos.memory import InMemoryRepo
    from app.repos.provider import set_repo

    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    fake_db.users[user_id] = {
        "id": user_id,
        "email": "validate_tester@example.com",
        "name": "Validate Tester",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    repo = InMemoryRepo()
    repo.seed_org_member(org_id=org_id, user_id=user_id)
    set_repo(repo)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as ac:
        yield ac, user_id, org_id

    set_repo(None)


async def _register(client, headers, *, sql, params=None, name="q"):
    qid = str(uuid.uuid4())
    resp = await client.post(
        "/api/v1/query/registry",
        json={"id": qid, "name": name, "sql": sql, "params": params or []},
        headers=headers,
    )
    assert resp.status_code in (200, 201), resp.text
    return qid


@pytest.mark.asyncio
async def test_parseable_query_is_valid(validate_client):
    client, user_id, _ = validate_client
    headers = _auth_headers(user_id)
    qid = await _register(client, headers, sql="SELECT 1 AS n")

    resp = await client.post(
        "/api/v1/query/registry/validate", json={"ids": [qid]}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["results"][qid] == {"valid": True}


@pytest.mark.asyncio
async def test_unparseable_query_is_flagged_with_a_readable_reason(validate_client):
    client, user_id, _ = validate_client
    headers = _auth_headers(user_id)
    qid = await _register(client, headers, sql=DANGLING_BETWEEN_SQL, name="broken")

    resp = await client.post(
        "/api/v1/query/registry/validate", json={"ids": [qid]}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    entry = resp.json()["results"][qid]
    assert entry["valid"] is False
    # Readable: no ANSI escapes, no sqlglot internals — this string is rendered
    # verbatim as the badge tooltip in the query library.
    assert "\x1b" not in entry["error"]
    assert "<class" not in entry["error"]
    assert "BETWEEN" in entry["error"]


@pytest.mark.asyncio
async def test_params_are_rendered_with_defaults(validate_client):
    """Mirrors POST /query: an unsupplied param takes its declared default.

    Without this the check would flag every parameterised query as broken,
    because the bare template `WHERE r = {{ region }}` is not valid SQL.
    """
    client, user_id, _ = validate_client
    headers = _auth_headers(user_id)
    qid = await _register(
        client,
        headers,
        sql="SELECT * FROM t WHERE r = {{ region }}",
        params=[{"name": "region", "type": "text", "default": "WC"}],
    )

    resp = await client.post(
        "/api/v1/query/registry/validate", json={"ids": [qid]}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["results"][qid]["valid"] is True


@pytest.mark.asyncio
async def test_unknown_ids_are_omitted_not_reported(validate_client):
    """No existence oracle: an id we cannot see simply has no result row."""
    client, user_id, _ = validate_client
    headers = _auth_headers(user_id)
    known = await _register(client, headers, sql="SELECT 1")
    ghost = str(uuid.uuid4())

    resp = await client.post(
        "/api/v1/query/registry/validate",
        json={"ids": [known, ghost]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    results = resp.json()["results"]
    assert known in results
    assert ghost not in results


@pytest.mark.asyncio
async def test_other_orgs_query_is_not_validated(validate_client):
    """Validate must not widen what GET /query/registry is willing to show."""
    client, user_id, _org_id = validate_client
    from app.repos.provider import get_repo

    foreign_qid = str(uuid.uuid4())
    await get_repo().create(
        "queries",
        str(uuid.uuid4()),
        str(uuid.uuid4()),
        "Foreign query",
        {"sql": "SELECT secret FROM vault", "params": []},
        id=foreign_qid,
    )

    resp = await client.post(
        "/api/v1/query/registry/validate",
        json={"ids": [foreign_qid]},
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 200, resp.text
    assert foreign_qid not in resp.json()["results"]
    assert "secret" not in resp.text


@pytest.mark.asyncio
async def test_id_list_is_capped(validate_client):
    """One call stays bounded; the caller pages through a long library."""
    from app.routes.query import _REGISTRY_VALIDATE_MAX_IDS

    client, user_id, _ = validate_client
    headers = _auth_headers(user_id)
    qid = await _register(client, headers, sql="SELECT 1")

    # Pad well past the cap with ghosts, keeping the real id beyond it.
    ids = [str(uuid.uuid4()) for _ in range(_REGISTRY_VALIDATE_MAX_IDS + 50)] + [qid]
    resp = await client.post(
        "/api/v1/query/registry/validate", json={"ids": ids}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    # The real id fell outside the cap, so it was never looked at.
    assert qid not in resp.json()["results"]


@pytest.mark.asyncio
async def test_empty_id_list_is_a_noop(validate_client):
    client, user_id, _ = validate_client
    resp = await client.post(
        "/api/v1/query/registry/validate",
        json={"ids": []},
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"results": {}}


@pytest.mark.asyncio
async def test_unauthenticated_is_rejected(validate_client):
    client, _user_id, _ = validate_client
    resp = await client.post("/api/v1/query/registry/validate", json={"ids": []})
    assert resp.status_code == 401, resp.text
