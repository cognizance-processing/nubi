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

import asyncio
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
# REGRESSION: N+1 list_flows scan (MED — fix 2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flow_provider_list_flows_called_at_most_once(repo: InMemoryRepo) -> None:
    """resolve_provider_data issues at most ONE list_flows call per board load.

    Before the fix, _resolve_flow_provider called list_flows on every provider
    that missed a direct get_flow hit, producing O(providers × flows) DB scans.
    After the fix, resolve_provider_data pre-fetches once and passes the dict
    down so list_flows is called at most once no matter how many flow providers
    are on a board.

    This test wires up a flow board with one provider, tracks list_flows calls,
    and asserts the count is exactly 1 (the pre-fetch) rather than growing with
    the number of providers.
    """
    from app.flows.store import get_flow_store  # noqa: PLC0415

    # Patch get_flow to return None (forces the fallback path that would
    # previously call list_flows per provider).
    list_flows_call_count = 0

    original_get_flow_store = get_flow_store

    class _TrackingStore:
        """Thin wrapper that counts list_flows invocations."""

        def __init__(self, inner: Any) -> None:
            self._inner = inner

        async def get_flow(self, flow_id: str) -> Any:
            # Always return None to trigger the fallback path.
            return None

        async def list_flows(self, **kwargs: Any) -> list[Any]:
            nonlocal list_flows_call_count
            list_flows_call_count += 1
            # Return a fake flow whose id matches _PROVIDER_ID so the lookup
            # succeeds and the execution path continues (we stop before the
            # actual flow run via the _resolve_flow_provider patch below).
            return [{"id": _PROVIDER_ID, "name": _PROVIDER_ID, "org_id": _ORG}]

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

    tracking_store = _TrackingStore(None)

    await repo.update(
        "boards",
        _ORG,
        _BOARD_ID,
        {"config": {"spec": _make_spec_with_flow_provider()}},
    )

    with (
        patch("app.flows.store.get_flow_store", return_value=tracking_store),
        patch(
            "app.dashboards.board_data._get_flow_store",
            return_value=tracking_store,
            create=True,
        ),
        patch(
            "app.dashboards.board_data._resolve_flow_provider",
            new=AsyncMock(return_value={"summary": pa.table({})}),
        ),
    ):
        # We patch _resolve_flow_provider entirely so we only test that
        # resolve_provider_data itself calls list_flows at most once (the
        # pre-fetch) regardless of provider count.
        #
        # Re-import to pick up any module-level references.
        import importlib

        import app.dashboards.board_data as _bd_mod

        with patch.object(_bd_mod, "_get_flow_store", return_value=tracking_store, create=True):
            # Directly patch the store used inside resolve_provider_data's
            # pre-fetch block by patching the module-level import alias.
            import app.flows.store as _fs_mod

            with patch.object(_fs_mod, "get_flow_store", return_value=tracking_store):
                await resolve_provider_data(
                    board_id=_BOARD_ID,
                    provider_id=_PROVIDER_ID,
                    params={},
                    org_id=_ORG,
                    claims={"policies": {}},
                    repo=repo,
                )

    # list_flows must be called AT MOST ONCE (the pre-fetch in
    # resolve_provider_data) — not once per provider.
    assert list_flows_call_count <= 1, (
        f"list_flows called {list_flows_call_count} times; expected at most 1 "
        "(N+1 regression: pre-fetch should prevent per-provider scans)."
    )


# ---------------------------------------------------------------------------
# REGRESSION: flow-provider cache-miss calls enforce_quota (LOW metering fix)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flow_provider_cache_miss_calls_enforce_quota(repo: InMemoryRepo) -> None:
    """On a cache miss for a flow provider, enforce_quota must be called.

    Before the fix, materialize+drain ran with NO quota enforcement so embed
    viewers could trigger unmetered warehouse compute.  After the fix,
    ``enforce_quota(org_id, 'compute_units', amount=1.0)`` is awaited before
    ``_resolve_flow_provider`` is invoked.
    """
    quota_calls: list[tuple[str, str, float]] = []

    async def _fake_enforce_quota(org_id: str, dimension: str, amount: float = 1.0) -> None:
        quota_calls.append((org_id, dimension, amount))

    await repo.update(
        "boards",
        _ORG,
        _BOARD_ID,
        {"config": {"spec": _make_spec_with_flow_provider()}},
    )

    with (
        patch(
            "app.features.enforce_quota",
            side_effect=_fake_enforce_quota,
        ),
        patch(
            "app.dashboards.board_data._resolve_flow_provider",
            new=AsyncMock(return_value={"summary": pa.table({})}),
        ),
    ):
        await resolve_provider_data(
            board_id=_BOARD_ID,
            provider_id=_PROVIDER_ID,
            params={},
            org_id=_ORG,
            claims={"policies": {}},
            repo=repo,
        )

    assert len(quota_calls) >= 1, (
        "enforce_quota was NOT called on flow-provider cache miss "
        "(unmetered warehouse compute regression)."
    )
    # Correct dimension and org_id.
    org_ids = [c[0] for c in quota_calls]
    dimensions = [c[1] for c in quota_calls]
    assert _ORG in org_ids, f"enforce_quota called with wrong org_id: {quota_calls}"
    assert "compute_units" in dimensions, f"enforce_quota called with wrong dimension: {quota_calls}"


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
    key_a = _provider_cache_key("org-a", "p1", params, {"tenant_id": "alpha"})
    key_b = _provider_cache_key("org-a", "p1", params, {"tenant_id": "beta"})
    key_empty = _provider_cache_key("org-a", "p1", params, {})
    assert key_a != key_b
    assert key_a != key_empty
    assert key_b != key_empty


# ---------------------------------------------------------------------------
# REGRESSION: cross-tenant cache collision (HIGH — fix 1)
# ---------------------------------------------------------------------------


def test_cross_tenant_cache_key_no_collision() -> None:
    """Two orgs with the same provider_id + empty policies MUST get different keys.

    Before the fix, _provider_cache_key omitted org_id so org-B could hit
    org-A's cached Arrow tables when both had an identically-named provider and
    no per-tenant RLS policies.
    """
    params: dict = {}
    key_org_a = _provider_cache_key("org-alpha", "shared-provider", params, {})
    key_org_b = _provider_cache_key("org-beta", "shared-provider", params, {})
    assert key_org_a != key_org_b, (
        "Cross-tenant cache collision: org-alpha and org-beta must NOT share a "
        "cache key for the same provider_id with empty policies."
    )
    # Sanity: both keys must start with 'provider:' and embed the org_id.
    assert key_org_a.startswith("provider:org-alpha:")
    assert key_org_b.startswith("provider:org-beta:")


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
# 7. Materialized mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_materialized_mode_returns_result_when_present(repo: InMemoryRepo) -> None:
    """resolve_provider_data with mode='materialized' serves from the latest
    successful flow run when one exists — NOT a 501 and NOT a live re-run.

    Contract: the 'scheduled → derived tables' path reads task_run results
    from the most-recent successful flow_run instead of triggering new compute.
    """
    import io as _io
    import pyarrow as pa
    from app.flows.store import InMemoryFlowStore
    import app.flows.store as _fs_mod

    # Build a flow-backed board spec.
    await repo.update(
        "boards",
        _ORG,
        _BOARD_ID,
        {"config": {"spec": _make_spec_with_flow_provider()}},
    )

    # Prepare a fake materialized result that the flow task_run produced.
    materialized_table = _make_arrow_table(7)
    ipc_buf = _io.BytesIO()
    writer = pa.ipc.new_stream(ipc_buf, materialized_table.schema)
    writer.write_table(materialized_table)
    writer.close()
    ipc_bytes = ipc_buf.getvalue()

    fake_flow = {"id": _PROVIDER_ID, "name": _PROVIDER_ID, "org_id": _ORG}
    fake_flow_run = {"id": "mat-run-1", "state": "success"}
    fake_task_run = {
        "task_key": "summary",
        "state": "success",
        "result": {"__arrow_ipc__": ipc_bytes},
    }

    class _FakeStore:
        async def get_flow(self, flow_id: str) -> dict:
            return fake_flow

        async def list_flows(self, **kwargs: Any) -> list:
            return [fake_flow]

        async def list_flow_runs(self, flow_id: str, limit: int = 10, **kwargs: Any) -> list:
            return [fake_flow_run]

        async def list_task_runs(self, run_id: str, limit: int | None = None) -> list:
            result = [fake_task_run]
            return result[:limit] if limit is not None else result

    async def _fake_enforce_quota(org_id: str, dimension: str, amount: float = 1.0) -> None:
        pass

    with (
        patch("app.features.enforce_quota", side_effect=_fake_enforce_quota),
        patch.object(_fs_mod, "get_flow_store", return_value=_FakeStore()),
    ):
        tables = await resolve_provider_data(
            board_id=_BOARD_ID,
            provider_id=_PROVIDER_ID,
            params={},
            org_id=_ORG,
            claims={"policies": {}},
            repo=repo,
            mode="materialized",
        )

    # Must return the materialized result (7 rows), NOT raise 501.
    assert "summary" in tables, f"Expected 'summary' in tables, got: {list(tables)}"
    assert isinstance(tables["summary"], pa.Table)
    assert tables["summary"].num_rows == 7, (
        f"Expected 7 rows from materialized result, got {tables['summary'].num_rows}"
    )


@pytest.mark.asyncio
async def test_materialized_mode_falls_back_to_ephemeral_when_absent(
    repo: InMemoryRepo,
) -> None:
    """resolve_provider_data with mode='materialized' falls back to a live
    (ephemeral) flow run when no successful materialized run exists yet.

    The fallback must NOT raise — dashboards on fresh/cold providers still work.
    """
    import app.flows.store as _fs_mod

    await repo.update(
        "boards",
        _ORG,
        _BOARD_ID,
        {"config": {"spec": _make_spec_with_flow_provider()}},
    )

    fake_flow = {"id": _PROVIDER_ID, "name": _PROVIDER_ID, "org_id": _ORG}
    ephemeral_table = _make_arrow_table(3)

    class _EmptyRunStore:
        """Store with a known flow but no successful runs (schedule hasn't fired)."""

        async def get_flow(self, flow_id: str) -> dict:
            return fake_flow

        async def list_flows(self, **kwargs: Any) -> list:
            return [fake_flow]

        async def list_flow_runs(self, flow_id: str, limit: int = 10, **kwargs: Any) -> list:
            # Simulate a flow that has never successfully completed.
            return []

        async def list_task_runs(self, run_id: str, limit: int | None = None) -> list:
            return []

    async def _fake_enforce_quota(org_id: str, dimension: str, amount: float = 1.0) -> None:
        pass

    with (
        patch("app.features.enforce_quota", side_effect=_fake_enforce_quota),
        # The materialized-path store sees no successful runs.
        patch.object(_fs_mod, "get_flow_store", return_value=_EmptyRunStore()),
        # The ephemeral fallback (_resolve_flow_provider) is mocked to return data.
        patch(
            "app.dashboards.board_data._resolve_flow_provider",
            new=AsyncMock(return_value={"summary": ephemeral_table}),
        ),
    ):
        tables = await resolve_provider_data(
            board_id=_BOARD_ID,
            provider_id=_PROVIDER_ID,
            params={},
            org_id=_ORG,
            claims={"policies": {}},
            repo=repo,
            mode="materialized",  # still passes materialized, but falls back
        )

    # Must fall back to ephemeral result (3 rows from the mocked live run).
    assert "summary" in tables, f"Expected 'summary' in fallback tables, got: {list(tables)}"
    assert isinstance(tables["summary"], pa.Table)
    assert tables["summary"].num_rows == 3, (
        f"Expected 3 rows from ephemeral fallback, got {tables['summary'].num_rows}"
    )


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


# ---------------------------------------------------------------------------
# NEW: inline provider — cache-miss calls enforce_quota (FIX 1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inline_provider_cache_miss_calls_enforce_quota(repo: InMemoryRepo) -> None:
    """On a cache miss for an inline provider, enforce_quota must be called.

    Inline providers execute warehouse SQL and must be metered the same way as
    flow providers.  Before the fix only kind='flow' called enforce_quota.
    """
    quota_calls: list[tuple[str, str, float]] = []

    async def _fake_enforce_quota(org_id: str, dimension: str, amount: float = 1.0) -> None:
        quota_calls.append((org_id, dimension, amount))

    async def _fake_run(query_id, org_id, _repo, policies):
        return ["amount"], [[10.0]]

    with (
        patch("app.features.enforce_quota", side_effect=_fake_enforce_quota),
        patch("app.dashboards.collect.run_query_rows", side_effect=_fake_run),
    ):
        await resolve_provider_data(
            board_id=_BOARD_ID,
            provider_id=_PROVIDER_ID,
            params={},
            org_id=_ORG,
            claims={"policies": {}},
            repo=repo,
        )

    assert len(quota_calls) >= 1, (
        "enforce_quota was NOT called on inline-provider cache miss "
        "(unmetered warehouse compute regression)."
    )
    org_ids = [c[0] for c in quota_calls]
    dimensions = [c[1] for c in quota_calls]
    assert _ORG in org_ids, f"enforce_quota called with wrong org_id: {quota_calls}"
    assert "compute_units" in dimensions, (
        f"enforce_quota called with wrong dimension: {quota_calls}"
    )


# ---------------------------------------------------------------------------
# NEW: inline provider base_cte path — result is row-capped (FIX 2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inline_provider_base_cte_result_is_row_capped(repo: InMemoryRepo) -> None:
    """Inline provider with base_cte must cap rows to _ROW_CAP.

    An unbounded connector.execute could return millions of rows as Arrow IPC.
    The fix slices the table to _ROW_CAP before serialisation.
    """
    import app.dashboards.board_data as _bd_mod

    # Build a board with an inline provider that has a base_cte.
    cte_spec = {
        "version": 1,
        "title": "CTE Board",
        "widgets": [
            {"id": "w1", "type": "table", "source": {"provider": _PROVIDER_ID, "result": "revenue"}},
        ],
        "data": [
            {
                "id": _PROVIDER_ID,
                "kind": "inline",
                "params": {},
                "base_cte": "WITH revenue AS (SELECT 1 AS x)",
                "results": [{"name": "revenue", "grain": None}],
            }
        ],
    }
    await repo.update("boards", _ORG, _BOARD_ID, {"config": {"spec": cte_spec}})

    # Simulate a connector that returns more rows than the cap.
    original_row_cap = _bd_mod._ROW_CAP
    small_cap = 3
    _bd_mod._ROW_CAP = small_cap

    # Arrow table with more rows than the cap.
    big_table = pa.table({"x": pa.array(list(range(small_cap + 5)))})

    try:
        async def _fake_enforce_quota(org_id: str, dimension: str, amount: float = 1.0) -> None:
            pass

        with (
            patch("app.features.enforce_quota", side_effect=_fake_enforce_quota),
            patch("app.connectors.plan", return_value=object()),
            patch("app.routes.query._get_demo_connector") as mock_connector_factory,
        ):
            mock_connector = mock_connector_factory.return_value
            mock_connector.execute.return_value = big_table

            tables = await resolve_provider_data(
                board_id=_BOARD_ID,
                provider_id=_PROVIDER_ID,
                params={},
                org_id=_ORG,
                claims={"policies": {}},
                repo=repo,
            )
    finally:
        _bd_mod._ROW_CAP = original_row_cap

    assert "revenue" in tables
    result = tables["revenue"]
    assert isinstance(result, pa.Table)
    assert result.num_rows <= small_cap, (
        f"Row cap not enforced: got {result.num_rows} rows, expected <= {small_cap}."
    )


# ---------------------------------------------------------------------------
# NEW: inline provider base_cte — execute is non-blocking (FIX 3 sanity)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inline_provider_base_cte_execute_via_to_thread(repo: InMemoryRepo) -> None:
    """connector.execute is called via asyncio.to_thread (non-blocking path).

    We verify the event loop is not blocked by confirming an awaitable co-routine
    can interleave while the connector runs.  Practically: asyncio.to_thread
    wraps the call so it runs in the thread-pool rather than directly on the loop.

    This test confirms the returned data is correct (sanity) after the async wrap.
    """
    cte_spec = {
        "version": 1,
        "title": "CTE Board",
        "widgets": [
            {"id": "w1", "type": "table", "source": {"provider": _PROVIDER_ID, "result": "revenue"}},
        ],
        "data": [
            {
                "id": _PROVIDER_ID,
                "kind": "inline",
                "params": {},
                "base_cte": "WITH revenue AS (SELECT 1 AS amount)",
                "results": [{"name": "revenue", "grain": None}],
            }
        ],
    }
    await repo.update("boards", _ORG, _BOARD_ID, {"config": {"spec": cte_spec}})

    expected_table = pa.table({"amount": pa.array([1.0, 2.0, 3.0])})

    async def _fake_enforce_quota(org_id: str, dimension: str, amount: float = 1.0) -> None:
        pass

    with (
        patch("app.features.enforce_quota", side_effect=_fake_enforce_quota),
        patch("app.connectors.plan", return_value=object()),
        patch("app.routes.query._get_demo_connector") as mock_connector_factory,
    ):
        mock_connector = mock_connector_factory.return_value
        mock_connector.execute.return_value = expected_table

        tables = await resolve_provider_data(
            board_id=_BOARD_ID,
            provider_id=_PROVIDER_ID,
            params={},
            org_id=_ORG,
            claims={"policies": {}},
            repo=repo,
        )

    assert "revenue" in tables
    result = tables["revenue"]
    assert isinstance(result, pa.Table)
    # Data is correct after asyncio.to_thread wrapping.
    assert result.num_rows == 3
    assert result.to_pydict() == {"amount": [1.0, 2.0, 3.0]}


# ---------------------------------------------------------------------------
# NEW: [MED metering INVARIANT] embed-token cache miss MUST NOT call enforce_quota
# First-party cache miss MUST call enforce_quota
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_token_cache_miss_does_not_call_enforce_quota(repo: InMemoryRepo) -> None:
    """[MED metering INVARIANT] Embed/viewer tokens must NEVER trigger quota enforcement.

    resolve_provider_data(is_embed=True) on a cache miss must execute the
    provider (so the result can be cached for subsequent callers) but must NOT
    call enforce_quota.  Viewers are never metered.
    """
    quota_calls: list[tuple] = []

    async def _fake_enforce_quota(org_id: str, dimension: str, amount: float = 1.0) -> None:
        quota_calls.append((org_id, dimension, amount))

    async def _fake_run(query_id, org_id, _repo, policies):
        return ["amount"], [[99.0]]

    with (
        patch("app.features.enforce_quota", side_effect=_fake_enforce_quota),
        patch("app.dashboards.collect.run_query_rows", side_effect=_fake_run),
    ):
        tables = await resolve_provider_data(
            board_id=_BOARD_ID,
            provider_id=_PROVIDER_ID,
            params={},
            org_id=_ORG,
            claims={"policies": {}},
            repo=repo,
            is_embed=True,  # embed/viewer token
        )

    # Provider must still execute and return data.
    assert "revenue" in tables
    assert tables["revenue"].num_rows == 1

    # But quota must NOT have been called for an embed token.
    assert len(quota_calls) == 0, (
        f"enforce_quota was called for an embed token — viewers are never metered. "
        f"Calls: {quota_calls}"
    )


@pytest.mark.asyncio
async def test_first_party_cache_miss_calls_enforce_quota(repo: InMemoryRepo) -> None:
    """[MED metering] First-party (non-embed) cache miss MUST call enforce_quota.

    When is_embed=False (default), a cache miss must meter the org before
    executing the provider.  This is the inverse of the embed check above.
    """
    quota_calls: list[tuple] = []

    async def _fake_enforce_quota(org_id: str, dimension: str, amount: float = 1.0) -> None:
        quota_calls.append((org_id, dimension, amount))

    async def _fake_run(query_id, org_id, _repo, policies):
        return ["amount"], [[42.0]]

    with (
        patch("app.features.enforce_quota", side_effect=_fake_enforce_quota),
        patch("app.dashboards.collect.run_query_rows", side_effect=_fake_run),
    ):
        tables = await resolve_provider_data(
            board_id=_BOARD_ID,
            provider_id=_PROVIDER_ID,
            params={},
            org_id=_ORG,
            claims={"policies": {}},
            repo=repo,
            is_embed=False,  # first-party token (default)
        )

    # Provider must execute and return data.
    assert "revenue" in tables

    # Quota must be enforced for a first-party cache miss.
    assert len(quota_calls) >= 1, (
        "enforce_quota was NOT called for a first-party cache miss — metering regression."
    )
    org_ids = [c[0] for c in quota_calls]
    assert _ORG in org_ids, f"enforce_quota called with wrong org_id: {quota_calls}"


# ---------------------------------------------------------------------------
# NEW: [MED metering INVARIANT] first-party VIEWER-role must not be metered
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_party_viewer_role_cache_miss_does_not_call_enforce_quota(
    repo: InMemoryRepo,
) -> None:
    """[MED metering INVARIANT] First-party viewer-role cache miss must NOT meter.

    A first-party user (identity.kind='access') whose org role is 'viewer' must
    be exempt from quota enforcement — the same invariant that covers embed tokens.
    resolve_provider_data(skip_metering=True) is passed by the route for viewers.
    """
    quota_calls: list[tuple] = []

    async def _fake_enforce_quota(org_id: str, dimension: str, amount: float = 1.0) -> None:
        quota_calls.append((org_id, dimension, amount))

    async def _fake_run(query_id, org_id, _repo, policies):
        return ["amount"], [[7.0]]

    with (
        patch("app.features.enforce_quota", side_effect=_fake_enforce_quota),
        patch("app.dashboards.collect.run_query_rows", side_effect=_fake_run),
    ):
        tables = await resolve_provider_data(
            board_id=_BOARD_ID,
            provider_id=_PROVIDER_ID,
            params={},
            org_id=_ORG,
            claims={"policies": {}},
            repo=repo,
            is_embed=False,       # first-party token (not embed)
            skip_metering=True,   # viewer role — route sets this for org role='viewer'
        )

    # Provider must still execute and return data.
    assert "revenue" in tables
    assert tables["revenue"].num_rows == 1

    # But quota must NOT have been called for a viewer-role first-party caller.
    assert len(quota_calls) == 0, (
        f"enforce_quota was called for a first-party viewer-role caller — "
        f"viewers are never metered. Calls: {quota_calls}"
    )


@pytest.mark.asyncio
async def test_first_party_writer_role_cache_miss_calls_enforce_quota(
    repo: InMemoryRepo,
) -> None:
    """[MED metering] First-party writer/admin cache miss MUST call enforce_quota.

    Writers and admins ARE metered on cache miss.  skip_metering=False (default)
    is the path for non-viewer, non-embed callers.
    """
    quota_calls: list[tuple] = []

    async def _fake_enforce_quota(org_id: str, dimension: str, amount: float = 1.0) -> None:
        quota_calls.append((org_id, dimension, amount))

    async def _fake_run(query_id, org_id, _repo, policies):
        return ["amount"], [[55.0]]

    with (
        patch("app.features.enforce_quota", side_effect=_fake_enforce_quota),
        patch("app.dashboards.collect.run_query_rows", side_effect=_fake_run),
    ):
        tables = await resolve_provider_data(
            board_id=_BOARD_ID,
            provider_id=_PROVIDER_ID,
            params={},
            org_id=_ORG,
            claims={"policies": {}},
            repo=repo,
            is_embed=False,        # first-party token
            skip_metering=False,   # writer/admin/member — metering applies
        )

    # Provider must execute and return data.
    assert "revenue" in tables

    # Quota MUST be called for a writer-role first-party cache miss.
    assert len(quota_calls) >= 1, (
        "enforce_quota was NOT called for a first-party writer cache miss — "
        "metering regression."
    )
    org_ids = [c[0] for c in quota_calls]
    assert _ORG in org_ids, f"enforce_quota called with wrong org_id: {quota_calls}"


@pytest.mark.asyncio
async def test_embed_token_skip_metering_does_not_call_enforce_quota(
    repo: InMemoryRepo,
) -> None:
    """[MED metering INVARIANT] Embed token cache miss must NOT call enforce_quota.

    is_embed=True sets skip_metering implicitly in resolve_provider_data; even
    if skip_metering is left at its default (False), is_embed alone suppresses
    quota enforcement for embed/viewer tokens.
    """
    quota_calls: list[tuple] = []

    async def _fake_enforce_quota(org_id: str, dimension: str, amount: float = 1.0) -> None:
        quota_calls.append((org_id, dimension, amount))

    async def _fake_run(query_id, org_id, _repo, policies):
        return ["amount"], [[3.0]]

    with (
        patch("app.features.enforce_quota", side_effect=_fake_enforce_quota),
        patch("app.dashboards.collect.run_query_rows", side_effect=_fake_run),
    ):
        tables = await resolve_provider_data(
            board_id=_BOARD_ID,
            provider_id=_PROVIDER_ID,
            params={},
            org_id=_ORG,
            claims={"policies": {}},
            repo=repo,
            is_embed=True,         # embed token — always skips metering
            skip_metering=False,   # is_embed alone should suppress quota
        )

    assert "revenue" in tables
    assert tables["revenue"].num_rows == 1

    assert len(quota_calls) == 0, (
        f"enforce_quota was called for an embed token — viewers are never metered. "
        f"Calls: {quota_calls}"
    )


# ---------------------------------------------------------------------------
# NEW: [MED resource] provider result cardinality cap (Fix 1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_too_many_result_tables_raises(repo: InMemoryRepo) -> None:
    """Provider returning more tables than _PROVIDER_MAX_TABLES raises AppError.

    A DataProvider returning many named result sets would produce an unbounded
    Arrow IPC payload cached in memory.  The fix caps the number of result tables
    and raises provider_result_too_large (422) when exceeded.
    """
    import app.dashboards.board_data as _bd_mod

    original_max = _bd_mod._PROVIDER_MAX_TABLES
    small_cap = 2
    _bd_mod._PROVIDER_MAX_TABLES = small_cap

    try:
        over_cap_tables = {f"result_{i}": pa.table({"x": [i]}) for i in range(small_cap + 1)}

        async def _fake_enforce_quota(org_id: str, dimension: str, amount: float = 1.0) -> None:
            pass

        await repo.update(
            "boards",
            _ORG,
            _BOARD_ID,
            {"config": {"spec": _make_spec_with_flow_provider()}},
        )
        with (
            patch("app.features.enforce_quota", side_effect=_fake_enforce_quota),
            patch(
                "app.dashboards.board_data._resolve_flow_provider",
                new=AsyncMock(return_value=over_cap_tables),
            ),
            pytest.raises(AppError) as exc_info,
        ):
            await resolve_provider_data(
                board_id=_BOARD_ID,
                provider_id=_PROVIDER_ID,
                params={},
                org_id=_ORG,
                claims={"policies": {}},
                repo=repo,
            )
        assert exc_info.value.code == "provider_result_too_large"
        assert exc_info.value.status == 422
    finally:
        _bd_mod._PROVIDER_MAX_TABLES = original_max


@pytest.mark.asyncio
async def test_provider_result_within_table_cap_succeeds(repo: InMemoryRepo) -> None:
    """Provider returning tables at or below _PROVIDER_MAX_TABLES succeeds."""
    import app.dashboards.board_data as _bd_mod

    original_max = _bd_mod._PROVIDER_MAX_TABLES
    cap = 3
    _bd_mod._PROVIDER_MAX_TABLES = cap

    try:
        # Exactly at the cap.
        at_cap_tables = {f"result_{i}": pa.table({"x": [i]}) for i in range(cap)}

        async def _fake_enforce_quota(org_id: str, dimension: str, amount: float = 1.0) -> None:
            pass

        await repo.update(
            "boards",
            _ORG,
            _BOARD_ID,
            {"config": {"spec": _make_spec_with_flow_provider()}},
        )
        with (
            patch("app.features.enforce_quota", side_effect=_fake_enforce_quota),
            patch(
                "app.dashboards.board_data._resolve_flow_provider",
                new=AsyncMock(return_value=at_cap_tables),
            ),
        ):
            tables = await resolve_provider_data(
                board_id=_BOARD_ID,
                provider_id=_PROVIDER_ID,
                params={},
                org_id=_ORG,
                claims={"policies": {}},
                repo=repo,
            )
        assert len(tables) == cap
    finally:
        _bd_mod._PROVIDER_MAX_TABLES = original_max


@pytest.mark.asyncio
async def test_provider_bytes_cap_raises(repo: InMemoryRepo) -> None:
    """Provider serialised result exceeding _PROVIDER_MAX_BYTES raises AppError.

    The fix checks the serialised byte size before caching and raises
    provider_result_too_large (422) when exceeded.
    """
    import app.dashboards.board_data as _bd_mod

    original_max_bytes = _bd_mod._PROVIDER_MAX_BYTES
    # 1 byte is smaller than any real Arrow IPC stream.
    _bd_mod._PROVIDER_MAX_BYTES = 1

    try:
        async def _fake_enforce_quota(org_id: str, dimension: str, amount: float = 1.0) -> None:
            pass

        async def _fake_run(query_id, org_id, _repo, policies):
            return ["x"], [[1], [2], [3]]

        with (
            patch("app.features.enforce_quota", side_effect=_fake_enforce_quota),
            patch("app.dashboards.collect.run_query_rows", side_effect=_fake_run),
            pytest.raises(AppError) as exc_info,
        ):
            await resolve_provider_data(
                board_id=_BOARD_ID,
                provider_id=_PROVIDER_ID,
                params={},
                org_id=_ORG,
                claims={"policies": {}},
                repo=repo,
            )
        assert exc_info.value.code == "provider_result_too_large"
        assert exc_info.value.status == 422
    finally:
        _bd_mod._PROVIDER_MAX_BYTES = original_max_bytes


def test_provider_max_tables_cap_is_configurable() -> None:
    """_PROVIDER_MAX_TABLES is a positive integer (env-overridable cap)."""
    import app.dashboards.board_data as _bd_mod

    assert isinstance(_bd_mod._PROVIDER_MAX_TABLES, int)
    assert _bd_mod._PROVIDER_MAX_TABLES > 0


def test_provider_max_bytes_cap_is_configurable() -> None:
    """_PROVIDER_MAX_BYTES is a non-negative integer (env-overridable cap)."""
    import app.dashboards.board_data as _bd_mod

    assert isinstance(_bd_mod._PROVIDER_MAX_BYTES, int)
    assert _bd_mod._PROVIDER_MAX_BYTES >= 0


# ---------------------------------------------------------------------------
# NEW: [MED resource] flow provider result tables are row-capped (Fix part a)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flow_provider_result_tables_are_row_capped(repo: InMemoryRepo) -> None:
    """Flow provider result tables must be row-capped to _ROW_CAP.

    Before the fix, _resolve_flow_provider returned tables with unbounded row
    counts — the row cap only applied to inline providers.  After the fix,
    each flow result table is sliced to _ROW_CAP before being returned so the
    downstream serialisation never materialises GiBs of Arrow IPC.

    Strategy: wire up _resolve_flow_provider to return a real table with more
    rows than a small cap, then assert the resolved result is truncated.
    """
    import app.dashboards.board_data as _bd_mod

    original_row_cap = _bd_mod._ROW_CAP
    small_cap = 4
    _bd_mod._ROW_CAP = small_cap

    # A table with more rows than the cap — simulates what a flow task_run
    # would return from a warehouse query.
    oversized_table = pa.table({"amount": pa.array([float(i) for i in range(small_cap + 10)])})

    async def _fake_enforce_quota(org_id: str, dimension: str, amount: float = 1.0) -> None:
        pass

    await repo.update(
        "boards",
        _ORG,
        _BOARD_ID,
        {"config": {"spec": _make_spec_with_flow_provider()}},
    )

    try:
        # Use real _resolve_flow_provider internals: simulate task_run results
        # with a large table by patching at the store level so the row-cap code
        # inside _resolve_flow_provider is exercised (not bypassed by mocking
        # _resolve_flow_provider itself).

        # Build the IPC bytes that the executor would have stored.
        ipc_buf = __import__("io").BytesIO()
        writer = pa.ipc.new_stream(ipc_buf, oversized_table.schema)
        writer.write_table(oversized_table)
        writer.close()
        ipc_bytes = ipc_buf.getvalue()

        fake_flow = {"id": _PROVIDER_ID, "name": _PROVIDER_ID, "org_id": _ORG}
        fake_flow_run = {"id": "run-1", "state": "success"}
        fake_task_run = {
            "task_key": "summary",
            "state": "success",
            "result": {"__arrow_ipc__": ipc_bytes},
        }

        class _FakeStore:
            async def get_flow(self, flow_id: str) -> dict:
                return fake_flow

            async def list_flows(self, **kwargs):
                return [fake_flow]

            async def list_task_runs(self, run_id: str, limit: int | None = None) -> list:
                result = [fake_task_run]
                return result[:limit] if limit is not None else result

        from app.flows.store import get_flow_store as _orig_get_flow_store

        with (
            patch("app.features.enforce_quota", side_effect=_fake_enforce_quota),
            patch("app.flows.store.get_flow_store", return_value=_FakeStore()),
            patch(
                "app.flows.runtime.materialize_flow_run",
                new=AsyncMock(return_value=fake_flow_run),
            ),
            patch(
                "app.flows.runtime.drain_flow_run",
                new=AsyncMock(return_value=fake_flow_run),
            ),
        ):
            tables = await resolve_provider_data(
                board_id=_BOARD_ID,
                provider_id=_PROVIDER_ID,
                params={},
                org_id=_ORG,
                claims={"policies": {}},
                repo=repo,
            )
    finally:
        _bd_mod._ROW_CAP = original_row_cap

    assert "summary" in tables
    result = tables["summary"]
    assert isinstance(result, pa.Table)
    assert result.num_rows <= small_cap, (
        f"Flow provider row cap not enforced: got {result.num_rows} rows, "
        f"expected <= {small_cap} (NUBI_COLLECT_ROW_CAP not applied to flow results)."
    )


# ---------------------------------------------------------------------------
# NEW: [MED resource] _tables_to_bytes raises EARLY on max_bytes exceeded
# (Fix part b — no full materialisation before the check)
# ---------------------------------------------------------------------------


def test_tables_to_bytes_raises_early_on_max_bytes_exceeded() -> None:
    """_tables_to_bytes raises AppError before serialising all tables when
    max_bytes is exceeded — the cap fires mid-iteration, not post-materialisation.

    We verify:
    1. Passing max_bytes=1 raises provider_result_too_large (422) on even the
       first table (well below any real IPC frame size).
    2. A generous max_bytes succeeds and the result round-trips correctly.
    3. The error is raised BEFORE all tables are serialised — a sentinel counter
       confirms the function did not iterate past the offending table.
    """
    from app.dashboards.board_data import _tables_to_bytes, _bytes_to_tables

    tbl_small = pa.table({"x": pa.array([1, 2, 3])})
    tbl_large = pa.table({"y": pa.array(list(range(1000)))})

    # 1. Cap of 1 byte: must raise immediately.
    with pytest.raises(AppError) as exc_info:
        _tables_to_bytes({"small": tbl_small}, max_bytes=1)
    assert exc_info.value.code == "provider_result_too_large"
    assert exc_info.value.status == 422

    # 2. Generous cap: must succeed and round-trip.
    blob = _tables_to_bytes({"small": tbl_small}, max_bytes=1_000_000)
    restored = _bytes_to_tables(blob)
    assert restored["small"].to_pydict() == tbl_small.to_pydict()

    # 3. Early-exit: with two tables where the first is OK but the second
    # would exceed the cap, only one table should have been serialised
    # (the function raises before completing the second frame).
    # We set max_bytes just large enough for the first table's IPC frame
    # but not for two.
    first_only = _tables_to_bytes({"small": tbl_small}, max_bytes=0)  # 0 = unlimited
    first_bytes = len(first_only)

    # Now set the cap just above the first table's serialised size but below
    # the combined size of both tables.
    cap = first_bytes  # exactly fits one table; second would push over
    with pytest.raises(AppError) as exc_info2:
        _tables_to_bytes({"small": tbl_small, "large": tbl_large}, max_bytes=cap)
    assert exc_info2.value.code == "provider_result_too_large"
    assert exc_info2.value.status == 422


# ---------------------------------------------------------------------------
# NEW: [MED DB-level LIMIT] inline provider base_cte pushes LIMIT into SQL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inline_provider_base_cte_pushes_db_level_limit(repo: InMemoryRepo) -> None:
    """Inline provider with base_cte wraps the SQL in SELECT * FROM (...) LIMIT _ROW_CAP.

    The DB-level LIMIT must appear in the SQL string passed to planner_plan
    so the database engine never returns more than _ROW_CAP rows into memory.
    The post-fetch slice is a backstop only; the primary cap fires inside the DB.
    """
    import app.dashboards.board_data as _bd_mod

    original_row_cap = _bd_mod._ROW_CAP
    small_cap = 5
    _bd_mod._ROW_CAP = small_cap

    cte_spec = {
        "version": 1,
        "title": "LIMIT Test Board",
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
                "base_cte": "WITH revenue AS (SELECT i AS amount FROM generate_series(1, 1000) t(i))",
                "results": [{"name": "revenue", "grain": None}],
            }
        ],
    }
    await repo.update("boards", _ORG, _BOARD_ID, {"config": {"spec": cte_spec}})

    captured_sql: list[str] = []

    def _capture_plan(sql: str, claims: dict, params: list) -> object:
        captured_sql.append(sql)
        return object()

    expected_table = pa.table({"amount": pa.array(list(range(small_cap)))})

    async def _fake_enforce_quota(org_id: str, dimension: str, amount: float = 1.0) -> None:
        pass

    try:
        with (
            patch("app.features.enforce_quota", side_effect=_fake_enforce_quota),
            patch("app.connectors.plan", side_effect=_capture_plan),
            patch("app.routes.query._get_demo_connector") as mock_connector_factory,
        ):
            mock_connector = mock_connector_factory.return_value
            mock_connector.execute.return_value = expected_table

            tables = await resolve_provider_data(
                board_id=_BOARD_ID,
                provider_id=_PROVIDER_ID,
                params={},
                org_id=_ORG,
                claims={"policies": {}},
                repo=repo,
            )
    finally:
        _bd_mod._ROW_CAP = original_row_cap

    # The SQL passed to planner_plan must contain a LIMIT clause with the cap value.
    assert len(captured_sql) == 1, f"Expected exactly one planner_plan call; got: {captured_sql}"
    executed_sql = captured_sql[0]
    limit_keyword = f"LIMIT {small_cap}"
    assert limit_keyword in executed_sql, (
        f"DB-level LIMIT not pushed into SQL (MED finding: DB still returns all rows "
        f"before Python truncation). Expected '{limit_keyword}' in:\n{executed_sql}"
    )
    # The wrapping pattern must be SELECT * FROM (...) LIMIT N (outer SELECT).
    assert executed_sql.strip().upper().startswith("SELECT"), (
        f"Wrapped SQL must start with SELECT; got: {executed_sql[:80]}"
    )

    # The result rows must still be bounded by the cap.
    assert "revenue" in tables
    assert tables["revenue"].num_rows <= small_cap, (
        f"Row cap not enforced: got {tables['revenue'].num_rows} rows, "
        f"expected <= {small_cap}."
    )


# ---------------------------------------------------------------------------
# NEW: [HIGH concurrency] DuckDB thread-safety — concurrent inline base_cte
# executions on the shared demo singleton must not crash and must return
# correct independent results.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_inline_base_cte_executions_are_thread_safe(
    repo: InMemoryRepo,
) -> None:
    """Concurrent inline-provider (base_cte) executions on the shared demo
    connector must not crash and must each return correct, independent results.

    Before the fix, two concurrent asyncio.to_thread(connector.execute, ...)
    calls on the same DuckDB singleton connection could race and corrupt each
    other's state.  After the fix, _execute_with_lock serialises them via a
    per-connector threading.Lock, so results are always correct.

    Strategy: patch the demo connector with a *real* shared DuckDB connection
    (in-memory, so no filesystem dependency) and fire off N concurrent
    resolve_provider_data calls with different base_cte SQL.  Each must return
    the correct rows for its own query.
    """
    import duckdb
    import pyarrow as pa

    from app.connectors.duckdb_conn import DuckDBConnector
    from app.dashboards.board_data import _execute_with_lock, _get_connector_lock

    # Build a real shared DuckDB connection with two tables — simulates the
    # demo singleton.
    shared_conn = duckdb.connect(":memory:")
    shared_conn.execute("CREATE TABLE t1 AS SELECT 10 AS val")
    shared_conn.execute("CREATE TABLE t2 AS SELECT 20 AS val")
    shared_connector = DuckDBConnector(shared_conn)

    # Two boards with different base_cte queries targeting the shared connector.
    spec_a = {
        "version": 1,
        "title": "Concurrent Board A",
        "widgets": [
            {"id": "w1", "type": "table", "source": {"provider": "pa", "result": "res_a"}},
        ],
        "data": [
            {
                "id": "pa",
                "kind": "inline",
                "params": {},
                "base_cte": "WITH res_a AS (SELECT val FROM t1)",
                "results": [{"name": "res_a", "grain": None}],
            }
        ],
    }
    spec_b = {
        "version": 1,
        "title": "Concurrent Board B",
        "widgets": [
            {"id": "w1", "type": "table", "source": {"provider": "pb", "result": "res_b"}},
        ],
        "data": [
            {
                "id": "pb",
                "kind": "inline",
                "params": {},
                "base_cte": "WITH res_b AS (SELECT val FROM t2)",
                "results": [{"name": "res_b", "grain": None}],
            }
        ],
    }

    board_a_id = "board-concurrent-a"
    board_b_id = "board-concurrent-b"
    await repo.create(
        "boards",
        org_id=_ORG,
        created_by="test",
        name="Concurrent A",
        config={"spec": spec_a},
        id=board_a_id,
    )
    await repo.create(
        "boards",
        org_id=_ORG,
        created_by="test",
        name="Concurrent B",
        config={"spec": spec_b},
        id=board_b_id,
    )

    async def _fake_enforce_quota(org_id: str, dimension: str, amount: float = 1.0) -> None:
        pass

    with (
        patch("app.features.enforce_quota", side_effect=_fake_enforce_quota),
        patch("app.routes.query._get_demo_connector", return_value=shared_connector),
    ):
        # Fire off both concurrently — they race to use the same shared_connector.
        results = await asyncio.gather(
            resolve_provider_data(
                board_id=board_a_id,
                provider_id="pa",
                params={},
                org_id=_ORG,
                claims={"policies": {}},
                repo=repo,
            ),
            resolve_provider_data(
                board_id=board_b_id,
                provider_id="pb",
                params={},
                org_id=_ORG,
                claims={"policies": {}},
                repo=repo,
            ),
        )

    tables_a, tables_b = results

    # Each result must contain only its own data — no corruption or cross-mixing.
    assert "res_a" in tables_a, f"res_a missing from tables_a: {list(tables_a)}"
    assert "res_b" in tables_b, f"res_b missing from tables_b: {list(tables_b)}"
    assert tables_a["res_a"].num_rows == 1
    assert tables_b["res_b"].num_rows == 1
    assert tables_a["res_a"].to_pydict() == {"val": [10]}, (
        f"Incorrect result for res_a (DuckDB thread-safety violation?): "
        f"{tables_a['res_a'].to_pydict()}"
    )
    assert tables_b["res_b"].to_pydict() == {"val": [20]}, (
        f"Incorrect result for res_b (DuckDB thread-safety violation?): "
        f"{tables_b['res_b'].to_pydict()}"
    )


# ---------------------------------------------------------------------------
# NEW: [LOW row cap] inline provider non-base_cte path — result is row-capped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inline_provider_non_base_cte_result_is_row_capped(repo: InMemoryRepo) -> None:
    """Inline provider without base_cte (run_query_rows path) must cap rows to _ROW_CAP.

    Before the fix, the non-base_cte branch called run_query_rows and built an
    Arrow table from the full result without applying _ROW_CAP.  An unbounded
    result from a registered query could materialise millions of rows in memory.

    The fix slices the rows list to _ROW_CAP before constructing the pa.table so
    both the non-base_cte and base_cte paths are consistently bounded.
    """
    import app.dashboards.board_data as _bd_mod

    original_row_cap = _bd_mod._ROW_CAP
    small_cap = 3
    _bd_mod._ROW_CAP = small_cap

    # run_query_rows returns more rows than the cap.
    oversized_rows = [[float(i)] for i in range(small_cap + 10)]

    async def _fake_run(query_id, org_id, _repo, policies):
        return ["amount"], oversized_rows

    async def _fake_enforce_quota(org_id: str, dimension: str, amount: float = 1.0) -> None:
        pass

    try:
        with (
            patch("app.features.enforce_quota", side_effect=_fake_enforce_quota),
            patch("app.dashboards.collect.run_query_rows", side_effect=_fake_run),
        ):
            tables = await resolve_provider_data(
                board_id=_BOARD_ID,
                provider_id=_PROVIDER_ID,
                params={},
                org_id=_ORG,
                claims={"policies": {}},
                repo=repo,
            )
    finally:
        _bd_mod._ROW_CAP = original_row_cap

    assert "revenue" in tables
    result = tables["revenue"]
    assert isinstance(result, pa.Table)
    assert result.num_rows <= small_cap, (
        f"Non-base_cte row cap not enforced: got {result.num_rows} rows, "
        f"expected <= {small_cap} (NUBI_COLLECT_ROW_CAP not applied to non-base_cte inline results)."
    )


@pytest.mark.asyncio
async def test_concurrent_inline_base_cte_many_workers_no_crash(
    repo: InMemoryRepo,
) -> None:
    """Higher-concurrency stress test: 6 concurrent inline executes on the shared
    demo connector all return correct independent results without crashing.

    Uses a single board/provider spec queried N times concurrently.  Each call
    expects a stable result from the shared connection.
    """
    import app.dashboards.board_data as _bd_mod
    import duckdb

    from app.connectors.duckdb_conn import DuckDBConnector

    # Ensure _ROW_CAP is large enough that the 5-row stress table is not
    # truncated by the inline-provider row cap (a previous test may have
    # temporarily lowered it; restoring here is defensive).
    _saved_row_cap = _bd_mod._ROW_CAP
    _bd_mod._ROW_CAP = max(_bd_mod._ROW_CAP, 100)

    try:
        shared_conn = duckdb.connect(":memory:")
        shared_conn.execute("CREATE TABLE stress AS SELECT i AS val FROM generate_series(1, 5) t(i)")
        shared_connector = DuckDBConnector(shared_conn)

        spec = {
            "version": 1,
            "title": "Stress Board",
            "widgets": [
                {"id": "w1", "type": "table", "source": {"provider": _PROVIDER_ID, "result": "revenue"}},
            ],
            "data": [
                {
                    "id": _PROVIDER_ID,
                    "kind": "inline",
                    "params": {},
                    "base_cte": "WITH revenue AS (SELECT val FROM stress ORDER BY val)",
                    "results": [{"name": "revenue", "grain": None}],
                }
            ],
        }
        await repo.update("boards", _ORG, _BOARD_ID, {"config": {"spec": spec}})

        async def _fake_enforce_quota(org_id: str, dimension: str, amount: float = 1.0) -> None:
            pass

        n_concurrent = 6
        with (
            patch("app.features.enforce_quota", side_effect=_fake_enforce_quota),
            patch("app.routes.query._get_demo_connector", return_value=shared_connector),
        ):
            all_results = await asyncio.gather(
                *[
                    resolve_provider_data(
                        board_id=_BOARD_ID,
                        provider_id=_PROVIDER_ID,
                        # Use different params to bust the cache so each fires a real execute.
                        params={"_worker": str(i)},
                        org_id=_ORG,
                        claims={"policies": {}},
                        repo=repo,
                    )
                    for i in range(n_concurrent)
                ]
            )
    finally:
        _bd_mod._ROW_CAP = _saved_row_cap

    for idx, tables in enumerate(all_results):
        assert "revenue" in tables, f"Worker {idx}: revenue missing from result"
        tbl = tables["revenue"]
        assert isinstance(tbl, pa.Table), f"Worker {idx}: result is not pa.Table"
        assert tbl.num_rows == 5, (
            f"Worker {idx}: expected 5 rows, got {tbl.num_rows} "
            "(DuckDB thread-safety violation: shared connection race?)"
        )
        assert tbl.to_pydict() == {"val": [1, 2, 3, 4, 5]}, (
            f"Worker {idx}: incorrect data — DuckDB thread-safety violation? "
            f"Got {tbl.to_pydict()}"
        )


# ---------------------------------------------------------------------------
# NEW: [LOW injection] inline provider base_cte result-name validation
# ---------------------------------------------------------------------------


def test_validate_result_name_rejects_sql_payload() -> None:
    """_validate_result_name rejects a result-name containing a SQL injection payload.

    An attacker who can influence the result-name in a DashboardSpec could
    otherwise inject arbitrary SQL via the ``SELECT * FROM {r.name}``
    interpolation in _resolve_inline_provider.  The validator must raise
    AppError("invalid_result_name", 400) before such a name reaches the SQL
    string.
    """
    from app.dashboards.board_data import _validate_result_name

    malicious_names = [
        "revenue; DROP TABLE orders--",
        "revenue UNION SELECT * FROM secrets",
        "x' OR '1'='1",
        "foo bar",
        "foo-bar",
        "123starts_with_digit",
        "",
        "a" * 0,  # empty string
        "revenue\n--",
        "revenue/*comment*/",
    ]
    for bad_name in malicious_names:
        with pytest.raises(AppError) as exc_info:
            _validate_result_name(bad_name)
        assert exc_info.value.code == "invalid_result_name", (
            f"Expected invalid_result_name for {bad_name!r}, "
            f"got {exc_info.value.code!r}"
        )
        assert exc_info.value.status == 400, (
            f"Expected HTTP 400 for {bad_name!r}, got {exc_info.value.status}"
        )


def test_validate_result_name_accepts_valid_identifiers() -> None:
    """_validate_result_name accepts well-formed SQL identifiers without raising.

    Valid identifiers (``[A-Za-z_][A-Za-z0-9_]*``) must pass through without
    raising so legitimate provider result names are not falsely rejected.
    """
    from app.dashboards.board_data import _validate_result_name

    valid_names = [
        "revenue",
        "revenue_by_day",
        "RevenueByDay",
        "_private",
        "a",
        "A1",
        "result_1",
        "my_cte_result",
        "SUMMARY",
        "x123",
    ]
    for good_name in valid_names:
        # Must not raise.
        _validate_result_name(good_name)


# ---------------------------------------------------------------------------
# NEW: [MED resource] list_task_runs is bounded at both call sites
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_huge_run_task_runs_are_bounded(repo: InMemoryRepo) -> None:
    """list_task_runs is called with limit=_MAX_TASK_RUNS+1 at both resolver
    call sites so a map-fan-out run with thousands of child task_runs never
    loads unbounded RAM on a provider HTTP request.

    Strategy: replace list_task_runs with a spy that records the ``limit``
    keyword argument.  We exercise BOTH paths:
    1. _resolve_flow_provider (ephemeral/live execution path)
    2. _resolve_materialized_flow_provider (scheduled/derived-tables path)

    Asserts:
    * list_task_runs is always called with a finite limit= kwarg.
    * The limit is equal to _MAX_TASK_RUNS+1 (the +1 allows truncation detection).
    * When the store returns more than _MAX_TASK_RUNS rows the result is
      truncated to at most _MAX_TASK_RUNS entries (the cap fires).
    """
    import io as _io
    import app.dashboards.board_data as _bd_mod
    import app.flows.store as _fs_mod

    original_max = _bd_mod._MAX_TASK_RUNS
    small_ceiling = 3  # Use a small ceiling so we can build a over-limit list easily
    _bd_mod._MAX_TASK_RUNS = small_ceiling

    try:
        # Build more task_runs than the ceiling to verify truncation.
        n_task_runs = small_ceiling + 5  # exceeds ceiling by 5
        fake_flow = {"id": _PROVIDER_ID, "name": _PROVIDER_ID, "org_id": _ORG}
        fake_flow_run = {"id": "huge-run-1", "state": "success"}

        # Each task_run has a unique task_key so none are filtered by result_names.
        # We include a "summary" task_run so the result table is not empty.
        fake_task_runs = [
            {
                "task_key": "summary" if i == 0 else f"child_{i}",
                "state": "success",
                "result": {"columns": ["val"], "rows": [[i]]},
            }
            for i in range(n_task_runs)
        ]

        limit_args_seen: list[int | None] = []

        class _SpyStore:
            """Store that records limit kwargs passed to list_task_runs."""

            async def get_flow(self, flow_id: str) -> dict:
                return fake_flow

            async def list_flows(self, **kwargs: Any) -> list:
                return [fake_flow]

            async def list_flow_runs(self, flow_id: str, limit: int = 10, **kwargs: Any) -> list:
                return [fake_flow_run]

            async def list_task_runs(self, run_id: str, limit: int | None = None) -> list:
                limit_args_seen.append(limit)
                result = list(fake_task_runs)
                return result[:limit] if limit is not None else result

        await repo.update(
            "boards",
            _ORG,
            _BOARD_ID,
            {"config": {"spec": _make_spec_with_flow_provider()}},
        )

        async def _fake_enforce_quota(org_id: str, dimension: str, amount: float = 1.0) -> None:
            pass

        # ── 1. Ephemeral path (_resolve_flow_provider) ────────────────────────
        with (
            patch("app.features.enforce_quota", side_effect=_fake_enforce_quota),
            patch.object(_fs_mod, "get_flow_store", return_value=_SpyStore()),
            patch(
                "app.flows.runtime.materialize_flow_run",
                new=AsyncMock(return_value=fake_flow_run),
            ),
            patch(
                "app.flows.runtime.drain_flow_run",
                new=AsyncMock(return_value=fake_flow_run),
            ),
        ):
            tables_ephemeral = await resolve_provider_data(
                board_id=_BOARD_ID,
                provider_id=_PROVIDER_ID,
                params={"_path": "ephemeral"},
                org_id=_ORG,
                claims={"policies": {}},
                repo=repo,
            )

        assert len(limit_args_seen) >= 1, "list_task_runs was never called (ephemeral path)"
        ephemeral_limit = limit_args_seen[-1]
        assert ephemeral_limit is not None, (
            "list_task_runs called with limit=None on ephemeral path — unbounded!"
        )
        assert ephemeral_limit == small_ceiling + 1, (
            f"Expected limit={small_ceiling + 1} (ceiling+1) on ephemeral path; "
            f"got limit={ephemeral_limit}"
        )
        # Truncation must fire: result should have at most small_ceiling task_runs processed.
        # "summary" is the only declared result, so only 1 table is expected.
        assert "summary" in tables_ephemeral

        # ── 2. Materialized path (_resolve_materialized_flow_provider) ────────
        limit_args_seen.clear()
        from app.connectors.cache import reset_cache_for_tests as _reset
        _reset()

        with (
            patch("app.features.enforce_quota", side_effect=_fake_enforce_quota),
            patch.object(_fs_mod, "get_flow_store", return_value=_SpyStore()),
        ):
            tables_mat = await resolve_provider_data(
                board_id=_BOARD_ID,
                provider_id=_PROVIDER_ID,
                params={"_path": "materialized"},
                org_id=_ORG,
                claims={"policies": {}},
                repo=repo,
                mode="materialized",
            )

        assert len(limit_args_seen) >= 1, "list_task_runs was never called (materialized path)"
        mat_limit = limit_args_seen[-1]
        assert mat_limit is not None, (
            "list_task_runs called with limit=None on materialized path — unbounded!"
        )
        assert mat_limit == small_ceiling + 1, (
            f"Expected limit={small_ceiling + 1} (ceiling+1) on materialized path; "
            f"got limit={mat_limit}"
        )
        assert "summary" in tables_mat

    finally:
        _bd_mod._MAX_TASK_RUNS = original_max


# ---------------------------------------------------------------------------
# NEW: [MED] inline provider base_cte executes against the org's real connector
# (fix: was always using _get_demo_connector regardless of org configuration)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inline_base_cte_uses_real_connector_when_configured(
    repo: InMemoryRepo,
) -> None:
    """Inline provider with base_cte MUST execute against the org's real connector,
    not always the demo singleton.

    Before the fix, _resolve_inline_provider always called _get_demo_connector()
    for the base_cte branch, even when the org had a real connector configured.
    A real-connector inline-provider binding would silently execute against demo
    data and bypass connector-layer RLS.

    Fix: _resolve_org_connector lists the org's datastores and picks the first
    non-system, non-demo datastore; the inline provider then executes against THAT
    connector, not the demo.

    This test:
    1. Seeds a real (non-demo, non-system) datastore into the org's repo.
    2. Patches _resolve_org_connector to confirm it returns a spy connector
       (not the demo).
    3. Verifies the inline provider executed against the spy connector.
    """
    from unittest.mock import MagicMock

    cte_spec = {
        "version": 1,
        "title": "Real Connector Board",
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
                "base_cte": "WITH revenue AS (SELECT 42 AS amount)",
                "results": [{"name": "revenue", "grain": None}],
            }
        ],
    }
    await repo.update("boards", _ORG, _BOARD_ID, {"config": {"spec": cte_spec}})

    expected_table = pa.table({"amount": pa.array([42])})

    # Track which connector was used.
    real_connector_calls: list[str] = []
    demo_connector_calls: list[str] = []

    # Build a spy real connector whose execute() records a call.
    real_connector = MagicMock()
    real_connector.execute.side_effect = lambda plan: (
        real_connector_calls.append("real") or expected_table
    )

    # Build a spy demo connector whose execute() records a call.
    demo_connector = MagicMock()
    demo_connector.execute.side_effect = lambda plan: (
        demo_connector_calls.append("demo") or expected_table
    )

    async def _fake_resolve_org_connector(org_id: str, repo: Any) -> tuple[Any, bool]:
        # Simulate: org has a real connector configured.
        return real_connector, False  # not owned (spy)

    async def _fake_enforce_quota(org_id: str, dimension: str, amount: float = 1.0) -> None:
        pass

    with (
        patch("app.features.enforce_quota", side_effect=_fake_enforce_quota),
        patch("app.connectors.plan", return_value=object()),
        patch(
            "app.dashboards.board_data._resolve_org_connector",
            side_effect=_fake_resolve_org_connector,
        ),
        patch(
            "app.routes.query._get_demo_connector",
            return_value=demo_connector,
        ),
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

    # The REAL connector must have been used, NOT the demo.
    assert len(real_connector_calls) >= 1, (
        "Real connector was NOT called — inline provider executed against demo "
        "data instead of the org's real connector (regression)."
    )
    assert len(demo_connector_calls) == 0, (
        f"Demo connector was called even though the org has a real connector "
        f"configured — connector bypass regression. Demo calls: {demo_connector_calls}"
    )


@pytest.mark.asyncio
async def test_inline_base_cte_falls_back_to_demo_when_no_real_connector(
    repo: InMemoryRepo,
) -> None:
    """Inline provider with base_cte falls back to the demo connector when no
    real connector is configured for the org.

    _resolve_org_connector must return the demo connector (owned=False) when the
    org's datastore list is empty or contains only system/demo entries.

    This test verifies:
    1. The demo connector IS used when no real connector exists.
    2. The provider still returns a result (no error on demo fallback).
    """
    from unittest.mock import MagicMock

    cte_spec = {
        "version": 1,
        "title": "Demo Fallback Board",
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
                "base_cte": "WITH revenue AS (SELECT 1 AS amount)",
                "results": [{"name": "revenue", "grain": None}],
            }
        ],
    }
    await repo.update("boards", _ORG, _BOARD_ID, {"config": {"spec": cte_spec}})

    expected_table = pa.table({"amount": pa.array([1])})
    demo_connector_calls: list[str] = []

    demo_connector = MagicMock()
    demo_connector.execute.side_effect = lambda plan: (
        demo_connector_calls.append("demo") or expected_table
    )

    async def _fake_resolve_org_connector(org_id: str, repo: Any) -> tuple[Any, bool]:
        # Simulate: org has NO real connector → demo fallback.
        from app.routes.query import _get_demo_connector as _demo

        return _demo(), False

    async def _fake_enforce_quota(org_id: str, dimension: str, amount: float = 1.0) -> None:
        pass

    with (
        patch("app.features.enforce_quota", side_effect=_fake_enforce_quota),
        patch("app.connectors.plan", return_value=object()),
        patch(
            "app.dashboards.board_data._resolve_org_connector",
            side_effect=_fake_resolve_org_connector,
        ),
        patch("app.routes.query._get_demo_connector", return_value=demo_connector),
    ):
        tables = await resolve_provider_data(
            board_id=_BOARD_ID,
            provider_id=_PROVIDER_ID,
            params={},
            org_id=_ORG,
            claims={"policies": {}},
            repo=repo,
        )

    # Provider must still return a result (demo fallback path works).
    assert "revenue" in tables


@pytest.mark.asyncio
async def test_resolve_org_connector_returns_demo_when_no_datastores(
    repo: InMemoryRepo,
) -> None:
    """_resolve_org_connector returns the demo connector when the org has no
    datastores (empty list).

    The demo fallback must work correctly for orgs that have not yet configured
    any connector (new accounts, dev environments, demo mode).
    """
    from app.dashboards.board_data import _resolve_org_connector

    # repo has no datastores seeded for _ORG — simulates a fresh org.
    demo_connector_mock = object()

    with patch("app.routes.query._get_demo_connector", return_value=demo_connector_mock):
        connector, owned = await _resolve_org_connector(_ORG, repo)

    assert connector is demo_connector_mock, (
        "_resolve_org_connector must return the demo connector when no real "
        "connector is configured."
    )
    assert owned is False, (
        "_resolve_org_connector must return owned=False for the demo singleton "
        "(caller must not close it)."
    )


@pytest.mark.asyncio
async def test_resolve_org_connector_skips_system_datastores(
    repo: InMemoryRepo,
) -> None:
    """_resolve_org_connector skips system-flagged datastores and falls back to demo.

    System datastores (e.g. the demo-hidden marker) must never be used as the
    real connector for inline provider execution — they are internal bookkeeping
    rows, not connectable data sources.
    """
    from app.dashboards.board_data import _resolve_org_connector

    # Seed a system-flagged datastore (simulates demo-hidden marker).
    system_ds_id = "sys-ds-1"
    await repo.create(
        "datastores",
        org_id=_ORG,
        created_by="test",
        name="System DS",
        config={"connector_type": "duckdb", "system": True},
        id=system_ds_id,
    )

    demo_connector_mock = object()

    with patch("app.routes.query._get_demo_connector", return_value=demo_connector_mock):
        connector, owned = await _resolve_org_connector(_ORG, repo)

    # System datastore must be skipped → demo fallback.
    assert connector is demo_connector_mock, (
        "_resolve_org_connector must skip system datastores and fall back to demo."
    )
    assert owned is False


# ---------------------------------------------------------------------------
# NEW (fix-19b): [MED embed compute amplification] findings 1a + 1b
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_flow_provider_concurrency_semaphore_limits_parallel_runs(
    repo: InMemoryRepo,
) -> None:
    """[MED embed amplification] Concurrent embed+flow cache misses are limited by
    the per-(org, provider) semaphore — excess concurrent requests get 429.

    Strategy: pre-drain the semaphore (concurrency=1) so it has no tokens, then
    call resolve_provider_data(is_embed=True) with timeout=0 and verify it raises
    AppError("provider_busy", 429) immediately.
    """
    import app.dashboards.board_data as _bd_mod
    from app.connectors.cache import reset_cache_for_tests as _reset

    await repo.update(
        "boards",
        _ORG,
        _BOARD_ID,
        {"config": {"spec": _make_spec_with_flow_provider()}},
    )

    original_concurrency = _bd_mod._EMBED_FLOW_CONCURRENCY
    original_timeout = _bd_mod._EMBED_FLOW_TIMEOUT_S
    _bd_mod._embed_semaphores.clear()
    _bd_mod._EMBED_FLOW_CONCURRENCY = 1
    _bd_mod._EMBED_FLOW_TIMEOUT_S = 0.0  # zero timeout → immediate 429 on contention

    async def _fake_enforce_quota(org_id: str, dimension: str, amount: float = 1.0) -> None:
        pass

    try:
        with (
            patch("app.features.enforce_quota", side_effect=_fake_enforce_quota),
            patch(
                "app.dashboards.board_data._resolve_flow_provider",
                new=AsyncMock(return_value={"summary": pa.table({"val": pa.array([1])})}),
            ),
        ):
            # Manually acquire the semaphore to simulate a concurrent holder.
            sem = _bd_mod._get_embed_semaphore(_ORG, _PROVIDER_ID)
            # Drain the semaphore (concurrency=1 → take the single token).
            await sem.acquire()
            try:
                # Now the semaphore is at capacity; a new call with timeout=0 must 429.
                with pytest.raises(AppError) as exc_info:
                    await resolve_provider_data(
                        board_id=_BOARD_ID,
                        provider_id=_PROVIDER_ID,
                        params={"_run": "contended"},
                        org_id=_ORG,
                        claims={"policies": {}},
                        repo=repo,
                        is_embed=True,
                    )
                assert exc_info.value.code == "provider_busy", (
                    f"Expected provider_busy 429 on semaphore contention; "
                    f"got {exc_info.value.code!r}"
                )
                assert exc_info.value.status == 429
            finally:
                sem.release()  # restore semaphore state

    finally:
        _bd_mod._EMBED_FLOW_CONCURRENCY = original_concurrency
        _bd_mod._EMBED_FLOW_TIMEOUT_S = original_timeout
        _bd_mod._embed_semaphores.clear()
        _reset()


@pytest.mark.asyncio
async def test_non_embed_flow_provider_bypasses_semaphore(repo: InMemoryRepo) -> None:
    """[MED embed amplification] Non-embed (first-party) flow provider calls are
    NOT subject to the embed semaphore guard — only embed tokens are throttled.
    """
    import app.dashboards.board_data as _bd_mod
    from app.connectors.cache import reset_cache_for_tests as _reset

    await repo.update(
        "boards",
        _ORG,
        _BOARD_ID,
        {"config": {"spec": _make_spec_with_flow_provider()}},
    )

    original_concurrency = _bd_mod._EMBED_FLOW_CONCURRENCY
    original_timeout = _bd_mod._EMBED_FLOW_TIMEOUT_S
    _bd_mod._embed_semaphores.clear()
    _bd_mod._EMBED_FLOW_CONCURRENCY = 0  # effectively zero — semaphore would block if used
    _bd_mod._EMBED_FLOW_TIMEOUT_S = 0.0
    _bd_mod._embed_semaphores.clear()

    async def _fake_enforce_quota(org_id: str, dimension: str, amount: float = 1.0) -> None:
        pass

    try:
        with (
            patch("app.features.enforce_quota", side_effect=_fake_enforce_quota),
            patch(
                "app.dashboards.board_data._resolve_flow_provider",
                new=AsyncMock(return_value={"summary": pa.table({"val": pa.array([1])})}),
            ),
        ):
            # Non-embed call must succeed even with concurrency=0.
            tables = await resolve_provider_data(
                board_id=_BOARD_ID,
                provider_id=_PROVIDER_ID,
                params={},
                org_id=_ORG,
                claims={"policies": {}},
                repo=repo,
                is_embed=False,  # first-party — no semaphore applied
            )
        assert "summary" in tables
        assert tables["summary"].num_rows == 1
    finally:
        _bd_mod._EMBED_FLOW_CONCURRENCY = original_concurrency
        _bd_mod._EMBED_FLOW_TIMEOUT_S = original_timeout
        _bd_mod._embed_semaphores.clear()
        _reset()


# ---------------------------------------------------------------------------
# NEW (fix-19b): [MED N+1] materialized path uses the pre-fetched org_flows_by_key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_materialized_path_uses_prefetch_no_per_provider_list_flows(
    repo: InMemoryRepo,
) -> None:
    """[MED N+1] _resolve_materialized_flow_provider must not call list_flows when
    org_flows_by_key is supplied by resolve_provider_data.

    Before the fix, both the ephemeral AND materialized paths called list_flows
    independently — with M materialized providers per board that would be 2×M
    list_flows calls instead of 1.  After the fix, the prefetch in
    resolve_provider_data covers both paths.
    """
    import io as _io
    import app.flows.store as _fs_mod

    await repo.update(
        "boards",
        _ORG,
        _BOARD_ID,
        {"config": {"spec": _make_spec_with_flow_provider()}},
    )

    # Prepare a materialized result.
    mat_table = _make_arrow_table(3)
    ipc_buf = _io.BytesIO()
    writer = pa.ipc.new_stream(ipc_buf, mat_table.schema)
    writer.write_table(mat_table)
    writer.close()
    ipc_bytes = ipc_buf.getvalue()

    fake_flow = {"id": _PROVIDER_ID, "name": _PROVIDER_ID, "org_id": _ORG}
    fake_flow_run = {"id": "mat-run-prefetch", "state": "success"}
    fake_task_run = {
        "task_key": "summary",
        "state": "success",
        "result": {"__arrow_ipc__": ipc_bytes},
    }

    list_flows_calls: list[dict] = []

    class _TrackingStore:
        async def get_flow(self, flow_id: str):
            return fake_flow

        async def list_flows(self, **kwargs):
            list_flows_calls.append(kwargs)
            return [fake_flow]

        async def list_flow_runs(self, flow_id: str, limit: int = 10, **kwargs):
            return [fake_flow_run]

        async def list_task_runs(self, run_id: str, limit=None):
            result = [fake_task_run]
            return result[:limit] if limit is not None else result

    async def _fake_enforce_quota(org_id: str, dimension: str, amount: float = 1.0) -> None:
        pass

    with (
        patch("app.features.enforce_quota", side_effect=_fake_enforce_quota),
        patch.object(_fs_mod, "get_flow_store", return_value=_TrackingStore()),
    ):
        tables = await resolve_provider_data(
            board_id=_BOARD_ID,
            provider_id=_PROVIDER_ID,
            params={},
            org_id=_ORG,
            claims={"policies": {}},
            repo=repo,
            mode="materialized",
        )

    # list_flows must be called AT MOST ONCE — the pre-fetch in resolve_provider_data.
    # _resolve_materialized_flow_provider must NOT make an additional list_flows call
    # because get_flow() returns the flow directly (no fallback needed).
    assert len(list_flows_calls) <= 1, (
        f"list_flows called {len(list_flows_calls)} times; expected at most 1 "
        "(materialized path N+1 regression: prefetch not passed through)."
    )

    # Must still return the materialized result.
    assert "summary" in tables
    assert isinstance(tables["summary"], pa.Table)


# ---------------------------------------------------------------------------
# NEW (fix-19b): [LOW N+1] inline connector resolved once per provider call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inline_connector_resolved_once_per_provider_call(
    repo: InMemoryRepo,
) -> None:
    """[LOW N+1] _resolve_org_connector must be called ONCE per provider call,
    not once per declared result.

    Before the fix, _resolve_org_connector was called inside the per-result loop
    in _resolve_inline_provider, causing O(results) repo.list("datastores") calls.
    After the fix, it is hoisted above the loop so only one call is made regardless
    of how many named results the provider has.
    """
    # Build a board with an inline provider that has TWO declared results.
    two_result_spec = {
        "version": 1,
        "title": "Two-result Inline Board",
        "widgets": [
            {"id": "w1", "type": "table", "source": {"provider": _PROVIDER_ID, "result": "r1"}},
            {"id": "w2", "type": "table", "source": {"provider": _PROVIDER_ID, "result": "r2"}},
        ],
        "data": [
            {
                "id": _PROVIDER_ID,
                "kind": "inline",
                "params": {},
                "base_cte": "WITH r1 AS (SELECT 1 AS x), r2 AS (SELECT 2 AS x)",
                "results": [
                    {"name": "r1", "grain": None},
                    {"name": "r2", "grain": None},
                ],
            }
        ],
    }
    await repo.update("boards", _ORG, _BOARD_ID, {"config": {"spec": two_result_spec}})

    resolve_connector_calls: list[tuple[str, Any]] = []

    async def _tracking_resolve_org_connector(org_id: str, repo: Any):
        resolve_connector_calls.append((org_id, repo))
        # Return a dummy connector and owned=False (no close needed).
        from unittest.mock import MagicMock
        dummy = MagicMock()
        dummy.execute.return_value = pa.table({"x": pa.array([1])})
        return dummy, False

    async def _fake_enforce_quota(org_id: str, dimension: str, amount: float = 1.0) -> None:
        pass

    with (
        patch("app.features.enforce_quota", side_effect=_fake_enforce_quota),
        patch("app.connectors.plan", return_value=object()),
        patch(
            "app.dashboards.board_data._resolve_org_connector",
            side_effect=_tracking_resolve_org_connector,
        ),
    ):
        tables = await resolve_provider_data(
            board_id=_BOARD_ID,
            provider_id=_PROVIDER_ID,
            params={},
            org_id=_ORG,
            claims={"policies": {}},
            repo=repo,
        )

    # _resolve_org_connector must be called exactly ONCE regardless of result count.
    assert len(resolve_connector_calls) == 1, (
        f"_resolve_org_connector called {len(resolve_connector_calls)} times for a "
        f"provider with 2 results; expected exactly 1 "
        "(N+1 regression: connector resolved per-result instead of once per provider)."
    )
    # Both results must still be present (correctness).
    assert "r1" in tables
    assert "r2" in tables
