"""Heavy-query pool forwarding (cloud "warehouse machine class").

A datastore flagged ``config.query_pool="heavy"`` gets its cache-MISS queries
proxied to ``NUBI_HEAVY_QUERY_URL``; the pool runs the SAME code on bigger
machines.  This suite verifies the forwarding contract WITHOUT a real pool by
mocking ``httpx.AsyncClient`` inside the route's lazy import.

Coverage
--------
(1) Heavy datastore + pool URL → request forwarded verbatim (auth + loop-guard
    headers), pool's Arrow bytes streamed back with X-Nubi-Pool: heavy.
(2) The forwarded result is cached locally under the same cache key — an
    identical second request is a local HIT, no second forward.
(3) Loop guard #1: NUBI_QUERY_POOL=heavy → never forwards (executes locally).
(4) Loop guard #2: inbound X-Nubi-Forwarded → never re-forwards.
(5) Pool unreachable (transport error) → falls back to local execution.
(6) Pool HTTP error (e.g. 402 quota_exceeded) → propagated verbatim,
    NOT retried locally (fallback would bypass the pool's quota gate).
(7) Non-heavy datastore → never forwarded even when a pool URL is set.
"""

from __future__ import annotations

import json
import uuid
from io import BytesIO
from unittest.mock import patch

import httpx
import pyarrow as pa
import pyarrow.ipc as pa_ipc
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.connectors.arrow_io import table_to_ipc_bytes
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo

_ARROW = "application/vnd.apache.arrow.stream"

# Canned Arrow payload the fake pool returns.
_POOL_TABLE = pa.table({"warehouse": pa.array([1, 2, 3], type=pa.int64())})
_POOL_BYTES = table_to_ipc_bytes(_POOL_TABLE)


def _auth_headers(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


def _parse_arrow(content: bytes) -> pa.Table:
    return pa_ipc.open_stream(BytesIO(content)).read_all()


class _FakePoolClient:
    """Stands in for httpx.AsyncClient inside _forward_heavy_query.

    Records outbound calls; returns a configurable response or raises.
    """

    calls: list[dict] = []
    response: httpx.Response | None = None
    raise_exc: Exception | None = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, content=None, headers=None):
        _FakePoolClient.calls.append(
            {"url": url, "content": content, "headers": dict(headers or {})}
        )
        if _FakePoolClient.raise_exc is not None:
            raise _FakePoolClient.raise_exc
        assert _FakePoolClient.response is not None
        return _FakePoolClient.response

    @classmethod
    def reset(
        cls,
        response: httpx.Response | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        cls.calls = []
        cls.response = response
        cls.raise_exc = raise_exc


def _ok_pool_response() -> httpx.Response:
    return httpx.Response(
        200,
        content=_POOL_BYTES,
        headers={"content-type": _ARROW, "x-nubi-cache": "MISS"},
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def conn_app(app):
    repo = InMemoryRepo()
    set_repo(repo)
    yield app, repo
    set_repo(None)


@pytest_asyncio.fixture
async def conn_client(conn_app, fake_db):
    app, repo = conn_app

    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    fake_db.users[user_id] = {
        "id": user_id,
        "email": "pool-tester@example.com",
        "name": "Pool Tester",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    repo.seed_org_member(org_id=org_id, user_id=user_id)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as ac:
        yield ac, user_id, org_id, repo


@pytest.fixture(autouse=True)
def _clear_cache():
    from app.connectors.cache import get_cache

    get_cache().clear()
    yield
    get_cache().clear()


@pytest.fixture(autouse=True)
def _pool_env(monkeypatch):
    """Default: a pool URL is configured; this process is NOT the pool."""
    monkeypatch.setenv("NUBI_HEAVY_QUERY_URL", "http://query.internal:8000")
    monkeypatch.delenv("NUBI_QUERY_POOL", raising=False)
    yield


async def _seed_heavy_datastore(repo, org_id: str, user_id: str) -> str:
    ds = await repo.create(
        "datastores",
        org_id=org_id,
        created_by=user_id,
        name="Big table",
        config={"type": "duckdb", "query_pool": "heavy"},
    )
    return ds["id"]


# ---------------------------------------------------------------------------
# (1) Forwarding contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_heavy_datastore_forwards_to_pool(conn_client):
    client, user_id, org_id, repo = conn_client
    ds_id = await _seed_heavy_datastore(repo, org_id, user_id)
    _FakePoolClient.reset(response=_ok_pool_response())

    with patch("httpx.AsyncClient", _FakePoolClient):
        resp = await client.post(
            "/api/v1/query",
            json={"query_id": "demo_points_10k", "datastore_id": ds_id},
            headers=_auth_headers(user_id),
        )

    assert resp.status_code == 200, resp.text
    assert resp.headers.get("x-nubi-pool") == "heavy"
    assert _parse_arrow(resp.content).column("warehouse").to_pylist() == [1, 2, 3]

    assert len(_FakePoolClient.calls) == 1
    call = _FakePoolClient.calls[0]
    assert call["url"] == "http://query.internal:8000/api/v1/query"
    # Loop guard + auth forwarded; body carries the original query.
    assert call["headers"]["x-nubi-forwarded"] == "1"
    assert call["headers"]["authorization"].startswith("Bearer ")
    sent = json.loads(call["content"])
    assert sent["query_id"] == "demo_points_10k"
    assert sent["datastore_id"] == ds_id


# ---------------------------------------------------------------------------
# (2) Forwarded result is cached locally
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_forwarded_result_cached_locally(conn_client):
    client, user_id, org_id, repo = conn_client
    ds_id = await _seed_heavy_datastore(repo, org_id, user_id)
    _FakePoolClient.reset(response=_ok_pool_response())
    body = {"query_id": "demo_points_10k", "datastore_id": ds_id}

    with patch("httpx.AsyncClient", _FakePoolClient):
        first = await client.post(
            "/api/v1/query", json=body, headers=_auth_headers(user_id)
        )
        second = await client.post(
            "/api/v1/query", json=body, headers=_auth_headers(user_id)
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.headers.get("x-nubi-cache") == "HIT"
    # Only ONE forward — the second request was served from the local cache.
    assert len(_FakePoolClient.calls) == 1


# ---------------------------------------------------------------------------
# (3) + (4) Loop guards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pool_process_never_forwards(conn_client, monkeypatch):
    client, user_id, org_id, repo = conn_client
    ds_id = await _seed_heavy_datastore(repo, org_id, user_id)
    monkeypatch.setenv("NUBI_QUERY_POOL", "heavy")
    _FakePoolClient.reset(response=_ok_pool_response())

    with patch("httpx.AsyncClient", _FakePoolClient):
        resp = await client.post(
            "/api/v1/query",
            json={"query_id": "demo_points_10k", "datastore_id": ds_id},
            headers=_auth_headers(user_id),
        )

    assert resp.status_code == 200, resp.text
    assert _FakePoolClient.calls == []
    # Executed locally — 10k rows from the registered demo query.
    assert _parse_arrow(resp.content).num_rows == 10_000


@pytest.mark.asyncio
async def test_already_forwarded_request_executes_locally(conn_client):
    client, user_id, org_id, repo = conn_client
    ds_id = await _seed_heavy_datastore(repo, org_id, user_id)
    _FakePoolClient.reset(response=_ok_pool_response())

    with patch("httpx.AsyncClient", _FakePoolClient):
        resp = await client.post(
            "/api/v1/query",
            json={"query_id": "demo_points_10k", "datastore_id": ds_id},
            headers={**_auth_headers(user_id), "X-Nubi-Forwarded": "1"},
        )

    assert resp.status_code == 200, resp.text
    assert _FakePoolClient.calls == []
    assert _parse_arrow(resp.content).num_rows == 10_000


# ---------------------------------------------------------------------------
# (5) Pool unreachable → local fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pool_unreachable_falls_back_to_local(conn_client):
    client, user_id, org_id, repo = conn_client
    ds_id = await _seed_heavy_datastore(repo, org_id, user_id)
    _FakePoolClient.reset(raise_exc=httpx.ConnectError("pool is scaled to 0"))

    with patch("httpx.AsyncClient", _FakePoolClient):
        resp = await client.post(
            "/api/v1/query",
            json={"query_id": "demo_points_10k", "datastore_id": ds_id},
            headers=_auth_headers(user_id),
        )

    assert resp.status_code == 200, resp.text
    assert len(_FakePoolClient.calls) == 1  # tried the pool first
    assert _parse_arrow(resp.content).num_rows == 10_000  # then ran locally


# ---------------------------------------------------------------------------
# (6) Pool HTTP errors propagate — no local retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pool_error_response_propagates(conn_client):
    client, user_id, org_id, repo = conn_client
    ds_id = await _seed_heavy_datastore(repo, org_id, user_id)
    _FakePoolClient.reset(
        response=httpx.Response(
            402,
            json={"error": {"code": "quota_exceeded", "message": "Upgrade."}},
            headers={"content-type": "application/json"},
        )
    )

    with patch("httpx.AsyncClient", _FakePoolClient):
        resp = await client.post(
            "/api/v1/query",
            json={"query_id": "demo_points_10k", "datastore_id": ds_id},
            headers=_auth_headers(user_id),
        )

    assert resp.status_code == 402
    assert "quota_exceeded" in resp.text
    assert len(_FakePoolClient.calls) == 1


# ---------------------------------------------------------------------------
# (7) Non-heavy datastore never forwards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_normal_datastore_not_forwarded(conn_client):
    client, user_id, org_id, repo = conn_client
    ds = await repo.create(
        "datastores",
        org_id=org_id,
        created_by=user_id,
        name="Normal",
        config={"type": "duckdb"},
    )
    _FakePoolClient.reset(response=_ok_pool_response())

    with patch("httpx.AsyncClient", _FakePoolClient):
        resp = await client.post(
            "/api/v1/query",
            json={"query_id": "demo_points_10k", "datastore_id": ds["id"]},
            headers=_auth_headers(user_id),
        )

    assert resp.status_code == 200, resp.text
    assert _FakePoolClient.calls == []


# ---------------------------------------------------------------------------
# (8) Warehouse quota gate at the route (core hook, no EE import)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_warehouse_quota_denied_returns_402_before_forward(conn_client):
    from app.features import register_quota_checker

    client, user_id, org_id, repo = conn_client
    ds_id = await _seed_heavy_datastore(repo, org_id, user_id)
    _FakePoolClient.reset(response=_ok_pool_response())

    def _checker(*, org_id: str, dimension: str, amount: float = 1.0):
        if dimension == "warehouse":
            return False, "Warehouse queries require the Pro plan."
        return True, ""

    register_quota_checker(_checker)
    try:
        with patch("httpx.AsyncClient", _FakePoolClient):
            resp = await client.post(
                "/api/v1/query",
                json={"query_id": "demo_points_10k", "datastore_id": ds_id},
                headers=_auth_headers(user_id),
            )
    finally:
        register_quota_checker(None)

    assert resp.status_code == 402, resp.text
    assert "Pro plan" in resp.text
    assert _FakePoolClient.calls == []  # denied BEFORE any forward


# ---------------------------------------------------------------------------
# (9) Pool process meters CUs at the multiplier with a :warehouse tier suffix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pool_meters_cu_multiplier(conn_client, monkeypatch):
    from app.compute.metering import InMemorySink, set_sink

    client, user_id, org_id, repo = conn_client
    ds_id = await _seed_heavy_datastore(repo, org_id, user_id)
    # This process IS the pool: executes locally, bills at 4×.
    monkeypatch.setenv("NUBI_QUERY_POOL", "heavy")
    monkeypatch.setenv("NUBI_CU_MULTIPLIER", "4")
    sink = InMemorySink()
    set_sink(sink)
    try:
        resp = await client.post(
            "/api/v1/query",
            json={"query_id": "demo_points_10k", "datastore_id": ds_id},
            headers=_auth_headers(user_id),
        )
        assert resp.status_code == 200, resp.text
        events = [e for e in sink.get_events() if e["kind"] == "compute"]
        assert len(events) == 1
        evt = events[0]
        assert evt["tier"] == "duckdb:warehouse"
        assert evt["org_id"] == org_id
        # units = compute-seconds × multiplier
        assert evt["units"] == pytest.approx((evt["elapsed_ms"] / 1000.0) * 4.0)
    finally:
        set_sink(None)
