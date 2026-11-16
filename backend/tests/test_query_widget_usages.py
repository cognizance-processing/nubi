"""GET /query/registry/{id}/widgets — find every widget referencing a query.

Test coverage
-------------
(1) A widget referencing the query via `query_id` is found.
(2) A filter widget referencing the query via `options_query_id` is found.
(3) A query with no widgets returns an empty list.
(4) Widgets belonging to a different org are never returned.
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
async def usage_client(app, fake_db):
    """HTTPX client with a seeded user + org, InMemoryRepo for board scans."""
    from app.repos.memory import InMemoryRepo
    from app.repos.provider import set_repo

    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    fake_db.users[user_id] = {
        "id": user_id,
        "email": "usage_tester@example.com",
        "name": "Usage Tester",
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
        yield ac, user_id, org_id, repo

    set_repo(None)


async def _seed_board(repo, org_id: str, user_id: str, name: str, widgets: list[dict]):
    return await repo.create(
        "boards",
        org_id,
        user_id,
        name,
        {"spec": {"title": name, "widgets": widgets}},
    )


@pytest.mark.asyncio
async def test_widget_referencing_query_id_is_found(usage_client):
    client, user_id, org_id, repo = usage_client
    headers = _auth_headers(user_id)
    qid = "q_target"

    await _seed_board(
        repo, org_id, user_id, "Board A",
        [{"id": "w1", "type": "table", "query_id": qid}],
    )

    resp = await client.get(f"/api/v1/query/registry/{qid}/widgets", headers=headers)
    assert resp.status_code == 200, resp.text
    widgets = resp.json()["widgets"]
    assert len(widgets) == 1
    assert widgets[0]["widget_id"] == "w1"
    assert widgets[0]["widget_type"] == "table"
    assert widgets[0]["board_name"] == "Board A"


@pytest.mark.asyncio
async def test_filter_widget_options_query_id_is_found(usage_client):
    client, user_id, org_id, repo = usage_client
    headers = _auth_headers(user_id)
    qid = "opt_target"

    await _seed_board(
        repo, org_id, user_id, "Board B",
        [{"id": "w2", "type": "filter", "options_query_id": qid}],
    )

    resp = await client.get(f"/api/v1/query/registry/{qid}/widgets", headers=headers)
    assert resp.status_code == 200, resp.text
    widgets = resp.json()["widgets"]
    assert len(widgets) == 1
    assert widgets[0]["widget_id"] == "w2"


@pytest.mark.asyncio
async def test_query_shared_by_multiple_widgets(usage_client):
    client, user_id, org_id, repo = usage_client
    headers = _auth_headers(user_id)
    qid = "opt_shared"

    await _seed_board(
        repo, org_id, user_id, "Overall",
        [{"id": "f1", "type": "filter", "options_query_id": qid}],
    )
    await _seed_board(
        repo, org_id, user_id, "Distributor",
        [{"id": "f2", "type": "filter", "options_query_id": qid}],
    )

    resp = await client.get(f"/api/v1/query/registry/{qid}/widgets", headers=headers)
    assert resp.status_code == 200, resp.text
    widgets = resp.json()["widgets"]
    assert {w["widget_id"] for w in widgets} == {"f1", "f2"}


@pytest.mark.asyncio
async def test_query_with_no_widgets_returns_empty_list(usage_client):
    client, user_id, org_id, repo = usage_client
    headers = _auth_headers(user_id)

    resp = await client.get("/api/v1/query/registry/q_unused/widgets", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["widgets"] == []


@pytest.mark.asyncio
async def test_widgets_from_other_org_are_not_returned(usage_client):
    client, user_id, org_id, repo = usage_client
    headers = _auth_headers(user_id)
    qid = "q_target"

    other_org_id = str(uuid.uuid4())
    other_user_id = str(uuid.uuid4())
    await _seed_board(
        repo, other_org_id, other_user_id, "Other Org Board",
        [{"id": "w_other", "type": "table", "query_id": qid}],
    )

    resp = await client.get(f"/api/v1/query/registry/{qid}/widgets", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["widgets"] == []


@pytest.mark.asyncio
async def test_unauthenticated_request_returns_401(usage_client):
    client, _user_id, _org_id, _repo = usage_client
    resp = await client.get("/api/v1/query/registry/q_target/widgets")
    assert resp.status_code == 401
