"""Metrics routes — CRUD + compile + execute + governance (Wave C1).

Coverage
--------
(1)  POST /metrics then GET /metrics/{id} round-trips the definition.
(2)  POST /metrics/{id}/sql returns compiled SQL + params (no execution).
(3)  POST /metrics/{id}/query executes a SEEDED metric against the demo
     connector and returns Arrow rows.
(4)  POST /metrics/{id}/query for a freshly-created metric returns rows.
(5)  A MetricQuery with an unknown dimension → 400 (governance).
(6)  POST /metrics with a definition that has no source → 400.
(7)  Embed token rejected from POST /metrics → 403.
(8)  GET /metrics lists the seeded demo metric.
(9)  Unauthenticated POST /metrics → 401.

Notes
-----
The metric registry is a process-global singleton with a built-in ``demo_revenue``
seed (SUM(value) from the 5-row demo table). conftest does not reset it between
cases, so tests use the seed for execution and unique names for created metrics.
Real-datastore execution is exercised through the built-in demo DuckDB connector
(``base_table='demo'``) — no external warehouse is needed.
"""

from __future__ import annotations

import uuid
from io import BytesIO

import pyarrow.ipc as pa_ipc
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


def _auth_headers(user_id: str) -> dict[str, str]:
    from app.auth.jwt import mint_access_token

    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


def _embed_headers(user_id: str) -> dict[str, str]:
    import time

    import jwt

    from app.config import get_settings

    settings = get_settings()
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": user_id,
            "kind": "embed",
            "scope": ["read:query"],
            "iat": now,
            "exp": now + 900,
        },
        settings.JWT_SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def _parse_arrow(content: bytes):
    return pa_ipc.open_stream(BytesIO(content)).read_all()


@pytest_asyncio.fixture
async def m_client(app, fake_db):
    """HTTPX client with a seeded user for the metrics tests."""
    user_id = str(uuid.uuid4())
    fake_db.users[user_id] = {
        "id": user_id,
        "email": "metric_tester@example.com",
        "name": "Metric Tester",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as ac:
        yield ac, user_id


def _revenue_def(name: str) -> dict:
    """A valid metric definition over the built-in demo table."""
    return {
        "name": name,
        "measure": {"name": "revenue", "agg": "sum", "expr": "value"},
        "base_table": "demo",
        "dimensions": [
            {"name": "name", "type": "text"},
            {"name": "active", "type": "bool"},
        ],
    }


# ---------------------------------------------------------------------------
# (1) POST then GET round-trips the definition
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_then_get_metric(m_client):
    client, user_id = m_client
    headers = _auth_headers(user_id)

    name = f"Test Revenue {uuid.uuid4().hex[:8]}"
    resp = await client.post("/api/v1/metrics", json=_revenue_def(name), headers=headers)
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["name"] == name
    assert created["measure"]["agg"] == "sum"
    assert created["base_table"] == "demo"
    metric_id = created["id"]

    get_resp = await client.get(f"/api/v1/metrics/{metric_id}", headers=headers)
    assert get_resp.status_code == 200, get_resp.text
    fetched = get_resp.json()
    assert fetched["id"] == metric_id
    assert fetched["measure"]["expr"] == "value"
    assert {d["name"] for d in fetched["dimensions"]} == {"name", "active"}


# ---------------------------------------------------------------------------
# (2) /metrics/{id}/sql returns compiled SQL + params (no execution)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metric_sql_dry_compile(m_client):
    client, user_id = m_client
    headers = _auth_headers(user_id)

    resp = await client.post(
        "/api/v1/metrics/demo_revenue/sql",
        json={"dimensions": ["name"]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "sql" in body and "params" in body
    sql = body["sql"].lower()
    assert "sum" in sql
    assert "demo" in sql
    assert "group by" in sql


@pytest.mark.asyncio
async def test_metric_sql_binds_filter_params(m_client):
    """A user filter is bound as a {{param}} placeholder, not concatenated."""
    client, user_id = m_client
    headers = _auth_headers(user_id)

    resp = await client.post(
        "/api/v1/metrics/demo_revenue/sql",
        json={
            "dimensions": ["name"],
            "filters": [{"field": "active", "op": "=", "value": True}],
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The filter value rides in params, never inlined into the SQL text.
    assert any(v is True for v in body["params"].values())


# ---------------------------------------------------------------------------
# (3) /metrics/{id}/query executes the seeded metric → Arrow rows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_seeded_metric_returns_rows(m_client):
    client, user_id = m_client
    headers = _auth_headers(user_id)

    resp = await client.post(
        "/api/v1/metrics/demo_revenue/query",
        json={"dimensions": ["name"]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers.get("content-type", "").startswith(
        "application/vnd.apache.arrow.stream"
    )
    table = _parse_arrow(resp.content)
    # 5 distinct demo names → 5 grouped rows, with a `name` + `revenue` column.
    assert table.num_rows == 5
    assert "name" in table.schema.names
    assert "revenue" in table.schema.names


# ---------------------------------------------------------------------------
# (4) A freshly-created metric is immediately queryable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_created_metric_is_queryable(m_client):
    client, user_id = m_client
    headers = _auth_headers(user_id)

    name = f"Live Metric {uuid.uuid4().hex[:8]}"
    create = await client.post("/api/v1/metrics", json=_revenue_def(name), headers=headers)
    assert create.status_code == 201, create.text
    metric_id = create.json()["id"]

    run = await client.post(
        f"/api/v1/metrics/{metric_id}/query",
        json={"dimensions": ["name"]},
        headers=headers,
    )
    assert run.status_code == 200, run.text
    table = _parse_arrow(run.content)
    assert table.num_rows == 5


# ---------------------------------------------------------------------------
# (5) Unknown dimension → 400 (governance)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_dimension_returns_400(m_client):
    client, user_id = m_client
    headers = _auth_headers(user_id)

    resp = await client.post(
        "/api/v1/metrics/demo_revenue/query",
        json={"dimensions": ["definitely_not_a_dimension"]},
        headers=headers,
    )
    assert resp.status_code == 400, resp.text
    assert "error" in resp.json()


@pytest.mark.asyncio
async def test_unknown_dimension_sql_returns_400(m_client):
    """The /sql dry-compile path also governs unknown dimensions."""
    client, user_id = m_client
    headers = _auth_headers(user_id)

    resp = await client.post(
        "/api/v1/metrics/demo_revenue/sql",
        json={"dimensions": ["nope"]},
        headers=headers,
    )
    assert resp.status_code == 400, resp.text


# ---------------------------------------------------------------------------
# (6) Bad definition (no source) → 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_metric_without_source_returns_400(m_client):
    client, user_id = m_client
    headers = _auth_headers(user_id)

    bad = {
        "name": f"No Source {uuid.uuid4().hex[:8]}",
        "measure": {"name": "revenue", "agg": "sum", "expr": "value"},
        # no base_table and no base_sql
    }
    resp = await client.post("/api/v1/metrics", json=bad, headers=headers)
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "no_source"


@pytest.mark.asyncio
async def test_create_metric_with_empty_measure_returns_400(m_client):
    """A non-count measure that references nothing → 400."""
    client, user_id = m_client
    headers = _auth_headers(user_id)

    bad = {
        "name": f"Bad Measure {uuid.uuid4().hex[:8]}",
        "measure": {"name": "revenue", "agg": "sum", "expr": "*"},
        "base_table": "demo",
    }
    resp = await client.post("/api/v1/metrics", json=bad, headers=headers)
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "invalid_measure"


# ---------------------------------------------------------------------------
# (7) Embed token rejected from POST /metrics → 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_token_cannot_create_metric(m_client):
    client, user_id = m_client

    resp = await client.post(
        "/api/v1/metrics",
        json=_revenue_def(f"Embed Attempt {uuid.uuid4().hex[:8]}"),
        headers=_embed_headers(user_id),
    )
    assert resp.status_code in (401, 403), resp.text


# ---------------------------------------------------------------------------
# (8) GET /metrics lists the seeded demo metric
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_metrics_includes_seed(m_client):
    client, user_id = m_client
    headers = _auth_headers(user_id)

    resp = await client.get("/api/v1/metrics", headers=headers)
    assert resp.status_code == 200, resp.text
    metrics = resp.json()["metrics"]
    ids = [m["id"] for m in metrics]
    assert "demo_revenue" in ids
    seed = next(m for m in metrics if m["id"] == "demo_revenue")
    assert seed["measure"]["agg"] == "sum"
    assert "name" in seed["dimensions"]


# ---------------------------------------------------------------------------
# (9) Unauthenticated POST → 401
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unauthenticated_create_returns_401(m_client):
    client, _ = m_client

    resp = await client.post(
        "/api/v1/metrics", json=_revenue_def("Anon Metric")
    )
    assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------------------
# (10) DELETE unregisters the metric
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_metric_unregisters(m_client):
    client, user_id = m_client
    headers = _auth_headers(user_id)

    name = f"Doomed Metric {uuid.uuid4().hex[:8]}"
    create = await client.post("/api/v1/metrics", json=_revenue_def(name), headers=headers)
    assert create.status_code == 201, create.text
    metric_id = create.json()["id"]

    # Present before delete.
    assert (await client.get(f"/api/v1/metrics/{metric_id}", headers=headers)).status_code == 200

    delete = await client.delete(f"/api/v1/metrics/{metric_id}", headers=headers)
    assert delete.status_code == 200, delete.text
    assert delete.json()["deleted"] is True

    # Gone after delete (the persistence-free path returns 404 from the registry).
    after = await client.get(f"/api/v1/metrics/{metric_id}", headers=headers)
    assert after.status_code == 404, after.text
