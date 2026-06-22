"""Tests for BET-3: composite board DataProvider resolver + route.

Coverage
--------
1. resolve_provider_data — flow kind (mocked drain_flow_run)
   a. Returns named Arrow tables for declared results.
   b. Missing task_run result yields an empty table (not an error).

2. resolve_provider_data — inline kind
   a. Calls run_query_rows per result; returns Arrow tables.
   b. A query error for one result yields an empty table (best-effort).

3. RLS / org-scoping
   a. Different policies → different cache keys (no cross-tenant collision).
   b. Different org → board_not_found (org isolation).
   c. Provider not in spec → provider_not_found.

4. Cache reuse by (pid, params, rls_hash)
   a. Second call with same (provider_id, params, policies) hits cache.
   b. Changed params → cache miss → re-execution.

5. Legacy query_id board is unaffected (no DataProvider spec).

6. POST /boards/{id}/providers/{pid}/data route
   a. 200 with correct Content-Type for a valid call.
   b. 401 without auth.
   c. 404 when board does not exist.
   d. 404 when provider not in spec.

7. Materialized mode raises 501.
"""

from __future__ import annotations

import io
import os
import struct
import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pyarrow as pa
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault(
    "JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef"
)

from app.auth.jwt import mint_access_token
from app.connectors.cache import reset_cache_for_tests
from app.dashboards.board_data import (
    _bytes_to_tables,
    _provider_cache_key,
    _tables_to_bytes,
    resolve_provider_data,
    tables_to_multi_ipc_stream,
)
from app.dashboards.spec import DataProvider, DashboardSpec, ProviderResult, Widget, WidgetSource
from app.errors import AppError
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ORG = "org-bet3-test"
_ORG_B = "org-bet3-other"
_BOARD_ID = "board-bet3-1"
_BOARD_ID_LEGACY = "board-bet3-legacy"
_PROVIDER_ID = "p1"
_USER_ID = str(uuid.uuid4())


def _make_spec_with_inline_provider() -> dict[str, Any]:
    """Board spec with an inline DataProvider."""
    return {
        "version": 1,
        "title": "BET-3 Test Board",
        "widgets": [
            {
                "id": "w1",
                "type": "table",
                "source": {"provider": _PROVIDER_ID, "result": "revenue"},
            }
        ],
        "data": [
            {
                "id": _PROVIDER_ID,
                "kind": "inline",
                "params": {},
                "base_cte": None,
                "results": [{"name": "revenue", "grain": None}],
            }
        ],
    }


def _make_spec_with_flow_provider() -> dict[str, Any]:
    """Board spec with a flow DataProvider."""
    return {
        "version": 1,
        "title": "BET-3 Flow Board",
        "widgets": [
            {
                "id": "w1",
                "type": "table",
                "source": {"provider": _PROVIDER_ID, "result": "summary"},
            }
        ],
        "data": [
            {
                "id": _PROVIDER_ID,
                "kind": "flow",
                "params": {},
                "base_cte": None,
                "results": [{"name": "summary", "grain": None}],
            }
        ],
    }


def _make_legacy_spec() -> dict[str, Any]:
    """Legacy board spec with only query_id widgets — no DataProvider."""
    return {
        "version": 1,
        "title": "Legacy Board",
        "widgets": [
            {
                "id": "w1",
                "type": "kpi",
                "query_id": "demo_all",
                "encoding": {"value": "id"},
                "pos": {"x": 1, "y": 1, "w": 4, "h": 2},
            }
        ],
    }


async def _make_repo_async(org: str = _ORG) -> InMemoryRepo:
    """Create an InMemoryRepo with seeded boards (async version)."""
    r = InMemoryRepo()
    set_repo(r)
    await r.create(
        "boards",
        org_id=org,
        created_by="test",
        name="BET-3 Test Board",
        config={"spec": _make_spec_with_inline_provider()},
        id=_BOARD_ID,
    )
    await r.create(
        "boards",
        org_id=org,
        created_by="test",
        name="Legacy Board",
        config={"spec": _make_legacy_spec()},
        id=_BOARD_ID_LEGACY,
    )
    return r


def _make_arrow_table(n: int = 3) -> pa.Table:
    return pa.table({"amount": pa.array([float(i * 10) for i in range(n)])})


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_cache():
    """Reset the Arrow IPC cache between tests."""
    reset_cache_for_tests()
    yield
    reset_cache_for_tests()


@pytest_asyncio.fixture()
async def repo() -> InMemoryRepo:
    r = await _make_repo_async()
    yield r
    set_repo(None)


# ---------------------------------------------------------------------------
# 1. resolve_provider_data — flow kind
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flow_provider_returns_named_tables(repo: InMemoryRepo) -> None:
    """A flow provider returns one Arrow table per declared result name."""
    summary_table = _make_arrow_table(5)

    # Patch _resolve_flow_provider at the module level so we bypass the
    # full flows runtime (which needs a real store + executor).
    expected_tables = {
        "summary": summary_table,
    }

    with patch(
        "app.dashboards.board_data._resolve_flow_provider",
        new=AsyncMock(return_value=expected_tables),
    ):
        # Update the board spec to use a flow provider.
        await repo.update(
            "boards",
            _ORG,
            _BOARD_ID,
            {"config": {"spec": _make_spec_with_flow_provider()}},
        )

        tables = await resolve_provider_data(
            board_id=_BOARD_ID,
            provider_id=_PROVIDER_ID,
            params={},
            org_id=_ORG,
            claims={"policies": {}},
            repo=repo,
        )

    assert "summary" in tables
    tbl = tables["summary"]
    assert isinstance(tbl, pa.Table)
    assert tbl.num_rows == 5
    assert "amount" in tbl.schema.names


@pytest.mark.asyncio
async def test_flow_provider_missing_result_yields_empty_table(repo: InMemoryRepo) -> None:
    """When a declared result has no matching task_run, an empty table is returned."""
    # _resolve_flow_provider returns the empty table for the declared result.
    with patch(
        "app.dashboards.board_data._resolve_flow_provider",
        new=AsyncMock(return_value={"summary": pa.table({})}),
    ):
        await repo.update(
            "boards",
            _ORG,
            _BOARD_ID,
            {"config": {"spec": _make_spec_with_flow_provider()}},
        )
        tables = await resolve_provider_data(
            board_id=_BOARD_ID,
            provider_id=_PROVIDER_ID,
            params={},
            org_id=_ORG,
            claims={"policies": {}},
            repo=repo,
        )

    assert "summary" in tables
    assert isinstance(tables["summary"], pa.Table)
    assert tables["summary"].num_rows == 0


# ---------------------------------------------------------------------------
# 2. resolve_provider_data — inline kind
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inline_provider_returns_named_tables(repo: InMemoryRepo) -> None:
    """Inline provider: run_query_rows called per result; Arrow tables returned."""
    revenue_table = _make_arrow_table(4)

    async def _fake_run_query_rows(query_id, org_id, _repo, policies):
        assert org_id == _ORG
        return revenue_table.schema.names, [
            [row.get(c) for c in revenue_table.schema.names]
            for row in revenue_table.to_pylist()
        ]

    with patch(
        "app.dashboards.collect.run_query_rows",
        side_effect=_fake_run_query_rows,
    ):
        tables = await resolve_provider_data(
            board_id=_BOARD_ID,
            provider_id=_PROVIDER_ID,
            params={},
            org_id=_ORG,
            claims={"policies": {}},
            repo=repo,
        )

    assert "revenue" in tables
    tbl = tables["revenue"]
    assert isinstance(tbl, pa.Table)
    assert tbl.num_rows == 4


@pytest.mark.asyncio
async def test_inline_provider_error_yields_empty_table(repo: InMemoryRepo) -> None:
    """When run_query_rows raises, the result is an empty table (best-effort)."""
    async def _fail_query(*args, **kwargs):
        raise AppError("query_not_registered", "No query found.", 404)

    with patch(
        "app.dashboards.collect.run_query_rows",
        side_effect=_fail_query,
    ):
        tables = await resolve_provider_data(
            board_id=_BOARD_ID,
            provider_id=_PROVIDER_ID,
            params={},
            org_id=_ORG,
            claims={"policies": {}},
            repo=repo,
        )

    assert "revenue" in tables
    assert tables["revenue"].num_rows == 0


# ---------------------------------------------------------------------------
# 3. RLS / org-scoping enforcement
# ---------------------------------------------------------------------------


def test_different_policies_produce_different_cache_keys() -> None:
    """Different RLS policies must produce different cache keys (no cross-tenant)."""
    params = {"date": "2024-01"}
    key_a = _provider_cache_key("p1", params, {"tenant_id": "alpha"})
    key_b = _provider_cache_key("p1", params, {"tenant_id": "beta"})
    key_empty = _provider_cache_key("p1", params, {})
    assert key_a != key_b
    assert key_a != key_empty
    assert key_b != key_empty


@pytest.mark.asyncio
async def test_different_org_raises_board_not_found(repo: InMemoryRepo) -> None:
    """A board belonging to org A must not be accessible from org B."""
    with pytest.raises(AppError) as exc_info:
        await resolve_provider_data(
            board_id=_BOARD_ID,
            provider_id=_PROVIDER_ID,
            params={},
            org_id=_ORG_B,  # wrong org
            claims={"policies": {}},
            repo=repo,
        )
    assert exc_info.value.code == "board_not_found"


@pytest.mark.asyncio
async def test_unknown_provider_raises_provider_not_found(repo: InMemoryRepo) -> None:
    """An unknown provider_id raises provider_not_found."""
    with pytest.raises(AppError) as exc_info:
        await resolve_provider_data(
            board_id=_BOARD_ID,
            provider_id="nonexistent-provider",
            params={},
            org_id=_ORG,
            claims={"policies": {}},
            repo=repo,
        )
    assert exc_info.value.code == "provider_not_found"


# ---------------------------------------------------------------------------
# 4. Cache reuse by (pid, params, rls_hash)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_reuse_same_key(repo: InMemoryRepo) -> None:
    """Second call with the same (provider_id, params, policies) hits the cache."""
    call_count = 0

    async def _fake_run(query_id, org_id, _repo, policies):
        nonlocal call_count
        call_count += 1
        return ["x"], [[float(call_count)]]

    with patch(
        "app.dashboards.collect.run_query_rows",
        side_effect=_fake_run,
    ):
        tables1 = await resolve_provider_data(
            board_id=_BOARD_ID,
            provider_id=_PROVIDER_ID,
            params={"d": "2024"},
            org_id=_ORG,
            claims={"policies": {"tid": "t1"}},
            repo=repo,
        )
        # Second call — should hit cache, not call run_query_rows again.
        tables2 = await resolve_provider_data(
            board_id=_BOARD_ID,
            provider_id=_PROVIDER_ID,
            params={"d": "2024"},
            org_id=_ORG,
            claims={"policies": {"tid": "t1"}},
            repo=repo,
        )

    # run_query_rows called only once (cache on second call).
    assert call_count == 1
    # Both results structurally identical.
    assert tables1["revenue"].num_rows == tables2["revenue"].num_rows


@pytest.mark.asyncio
async def test_cache_miss_on_changed_params(repo: InMemoryRepo) -> None:
    """Changed params produce a different cache key — re-execution happens."""
    call_count = 0

    async def _fake_run(query_id, org_id, _repo, policies):
        nonlocal call_count
        call_count += 1
        return ["x"], [[float(call_count)]]

    with patch(
        "app.dashboards.collect.run_query_rows",
        side_effect=_fake_run,
    ):
        await resolve_provider_data(
            board_id=_BOARD_ID,
            provider_id=_PROVIDER_ID,
            params={"d": "2024-01"},
            org_id=_ORG,
            claims={"policies": {}},
            repo=repo,
        )
        await resolve_provider_data(
            board_id=_BOARD_ID,
            provider_id=_PROVIDER_ID,
            params={"d": "2024-02"},  # different param
            org_id=_ORG,
            claims={"policies": {}},
            repo=repo,
        )

    assert call_count == 2


# ---------------------------------------------------------------------------
# 5. Legacy query_id board is unaffected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_board_raises_provider_not_found(repo: InMemoryRepo) -> None:
    """A legacy board (no DataProvider spec) raises provider_not_found."""
    with pytest.raises(AppError) as exc_info:
        await resolve_provider_data(
            board_id=_BOARD_ID_LEGACY,
            provider_id="p1",
            params={},
            org_id=_ORG,
            claims={"policies": {}},
            repo=repo,
        )
    assert exc_info.value.code == "provider_not_found"


# ---------------------------------------------------------------------------
# 6. POST /boards/{id}/providers/{pid}/data route
# ---------------------------------------------------------------------------


def _auth_headers(user_id: str) -> dict[str, str]:
    token = mint_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


def _make_user(user_id: str) -> dict[str, Any]:
    return {
        "id": user_id,
        "email": "bet3-test@example.com",
        "name": "BET-3 Tester",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


@pytest_asyncio.fixture
async def route_client(app, fake_db):
    """HTTPX async client with a pre-seeded user + board for route tests."""
    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    fake_db.users[user_id] = _make_user(user_id)

    # Use InMemoryRepo with the board seeded.
    r = InMemoryRepo()
    r.seed_org_member(org_id=org_id, user_id=user_id)

    async def _seed() -> None:
        await r.create(
            "boards",
            org_id=org_id,
            created_by=user_id,
            name="Route Test Board",
            config={"spec": _make_spec_with_inline_provider()},
            id=_BOARD_ID,
        )

    await _seed()

    from app.repos.provider import set_repo as _set_repo

    _set_repo(r)
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
            follow_redirects=False,
        ) as ac:
            yield ac, user_id, org_id, r
    finally:
        _set_repo(None)
        reset_cache_for_tests()


@pytest.mark.asyncio
async def test_route_requires_auth(route_client) -> None:
    """POST /boards/{id}/providers/{pid}/data requires authentication."""
    ac, _, _, _ = route_client
    resp = await ac.post(
        f"/api/v1/boards/{_BOARD_ID}/providers/{_PROVIDER_ID}/data",
        json={"params": {}},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_route_200_with_valid_call(route_client) -> None:
    """A valid authenticated call returns 200 with Arrow IPC content type."""
    ac, user_id, org_id, repo = route_client

    async def _fake_run(query_id, org_id, _repo, policies):
        return ["amount"], [[1.0], [2.0], [3.0]]

    with patch(
        "app.dashboards.collect.run_query_rows",
        side_effect=_fake_run,
    ):
        resp = await ac.post(
            f"/api/v1/boards/{_BOARD_ID}/providers/{_PROVIDER_ID}/data",
            json={"params": {}},
            headers=_auth_headers(user_id),
        )

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/vnd.apache.arrow.stream")
    # The response body must be non-empty binary (valid IPC frame).
    assert len(resp.content) > 0


@pytest.mark.asyncio
async def test_route_404_board_not_found(route_client) -> None:
    """Returns 404 when the board does not exist."""
    ac, user_id, _, _ = route_client

    async def _fake_run(query_id, org_id, _repo, policies):
        return [], []

    with patch(
        "app.dashboards.collect.run_query_rows",
        side_effect=_fake_run,
    ):
        resp = await ac.post(
            "/api/v1/boards/nonexistent-board/providers/p1/data",
            json={"params": {}},
            headers=_auth_headers(user_id),
        )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_route_404_provider_not_found(route_client) -> None:
    """Returns 404 when the provider_id is not in the spec."""
    ac, user_id, _, _ = route_client

    async def _fake_run(query_id, org_id, _repo, policies):
        return [], []

    with patch(
        "app.dashboards.collect.run_query_rows",
        side_effect=_fake_run,
    ):
        resp = await ac.post(
            f"/api/v1/boards/{_BOARD_ID}/providers/no-such-provider/data",
            json={"params": {}},
            headers=_auth_headers(user_id),
        )

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 7. Materialized mode raises 501
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_materialized_mode_raises_501(repo: InMemoryRepo) -> None:
    """resolve_provider_data with mode='materialized' raises AppError 501."""
    with pytest.raises(AppError) as exc_info:
        await resolve_provider_data(
            board_id=_BOARD_ID,
            provider_id=_PROVIDER_ID,
            params={},
            org_id=_ORG,
            claims={"policies": {}},
            repo=repo,
            mode="materialized",
        )
    assert exc_info.value.code == "provider_mode_unsupported"
    assert exc_info.value.status == 501


# ---------------------------------------------------------------------------
# Serialisation round-trip helpers
# ---------------------------------------------------------------------------


def test_tables_roundtrip() -> None:
    """_tables_to_bytes / _bytes_to_tables round-trips correctly."""
    tbl_a = pa.table({"x": pa.array([1, 2, 3]), "y": pa.array([4.0, 5.0, 6.0])})
    tbl_b = pa.table({"name": pa.array(["alice", "bob"])})
    original = {"result_a": tbl_a, "result_b": tbl_b}

    blob = _tables_to_bytes(original)
    restored = _bytes_to_tables(blob)

    assert set(restored.keys()) == {"result_a", "result_b"}
    assert restored["result_a"].to_pydict() == tbl_a.to_pydict()
    assert restored["result_b"].to_pydict() == tbl_b.to_pydict()


def test_tables_to_multi_ipc_stream_non_empty() -> None:
    """tables_to_multi_ipc_stream produces non-empty bytes."""
    tbl = pa.table({"v": pa.array([1, 2])})
    blob = tables_to_multi_ipc_stream({"result": tbl})
    assert isinstance(blob, bytes)
    assert len(blob) > 8  # at least the header
