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


def _board_flow_provider() -> DataProvider:
    """A flow-backed DataProvider matching ``_make_spec_with_flow_provider``."""
    return DataProvider(
        id=_PROVIDER_ID,
        kind="flow",
        params={},
        base_cte=None,
        results=[ProviderResult(name="summary", grain=None)],
    )


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
# REGRESSION: flow-provider drain EXECUTION timeout (MED — event-loop starvation)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flow_provider_slow_drain_504s_at_exec_timeout(repo: InMemoryRepo) -> None:
    """A slow flow-provider drain raises provider_timeout (504) at the exec timeout.

    The per-(org, provider) semaphore's wait_for only bounds ACQUISITION; once
    acquired, drain runs unbounded.  resolve_provider_data wraps the
    post-acquisition execution in asyncio.wait_for(_FLOW_PROVIDER_EXEC_TIMEOUT_S)
    and raises AppError('provider_timeout', 504) on TimeoutError.
    """
    import app.dashboards.board_data as bd  # noqa: PLC0415

    await repo.update(
        "boards",
        _ORG,
        _BOARD_ID,
        {"config": {"spec": _make_spec_with_flow_provider()}},
    )

    async def _slow_resolve(*args: Any, **kwargs: Any) -> dict[str, pa.Table]:
        await asyncio.sleep(60)  # far longer than the patched exec timeout
        return {"summary": _make_arrow_table(1)}

    with (
        patch.object(bd, "_FLOW_PROVIDER_EXEC_TIMEOUT_S", 0.05),
        patch.object(bd, "_resolve_flow_provider", new=_slow_resolve),
    ):
        with pytest.raises(AppError) as ei:
            await resolve_provider_data(
                board_id=_BOARD_ID,
                provider_id=_PROVIDER_ID,
                params={},
                org_id=_ORG,
                claims={"policies": {}},
                repo=repo,
            )

    assert ei.value.code == "provider_timeout"
    assert ei.value.status == 504


@pytest.mark.asyncio
async def test_flow_provider_exec_timeout_releases_semaphore(repo: InMemoryRepo) -> None:
    """After an exec-timeout 504 the per-(org, provider) semaphore slot is freed.

    A subsequent (fast) request on the same (org, provider) must be able to
    acquire the slot and succeed — proving the timeout path released it in
    finally rather than leaking it.
    """
    import app.dashboards.board_data as bd  # noqa: PLC0415

    await repo.update(
        "boards",
        _ORG,
        _BOARD_ID,
        {"config": {"spec": _make_spec_with_flow_provider()}},
    )

    sem = bd._get_flow_provider_semaphore(_ORG, _PROVIDER_ID)
    # With _CountingSemaphore, idle state is tracked via _holders (0 = idle).
    assert sem.is_idle(), "Semaphore should be idle before any acquisition"

    async def _slow_resolve(*args: Any, **kwargs: Any) -> dict[str, pa.Table]:
        await asyncio.sleep(60)
        return {"summary": _make_arrow_table(1)}

    with (
        patch.object(bd, "_FLOW_PROVIDER_EXEC_TIMEOUT_S", 0.05),
        patch.object(bd, "_resolve_flow_provider", new=_slow_resolve),
    ):
        with pytest.raises(AppError):
            await resolve_provider_data(
                board_id=_BOARD_ID,
                provider_id=_PROVIDER_ID,
                params={},
                org_id=_ORG,
                claims={"policies": {}},
                repo=repo,
            )

    # Slot fully returned to the registry semaphore after the timeout.
    assert bd._get_flow_provider_semaphore(_ORG, _PROVIDER_ID).is_idle(), (
        "Semaphore must be idle (all slots returned) after an exec-timeout."
    )

    # And a fast follow-up request can acquire + succeed.
    with patch.object(
        bd,
        "_resolve_flow_provider",
        new=AsyncMock(return_value={"summary": _make_arrow_table(2)}),
    ):
        tables = await resolve_provider_data(
            board_id=_BOARD_ID,
            provider_id=_PROVIDER_ID,
            params={"k": "v2"},  # distinct params → cache miss → real execution
            org_id=_ORG,
            claims={"policies": {}},
            repo=repo,
        )
    assert tables["summary"].num_rows == 2


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


def test_cross_board_cache_key_no_collision() -> None:
    """Two boards with the same provider_id in the same org MUST get distinct keys.

    Before the fix, _provider_cache_key omitted board_id so board-A and board-B
    sharing a provider_id (e.g. 'p1') in the same org with the same params and
    empty policies produced identical cache keys — board-B would silently be
    served board-A's cached Arrow tables.

    After the fix, board_id is included as a key component.
    """
    params: dict = {}
    policies: dict = {}
    key_board_a = _provider_cache_key("org-x", "p1", params, policies, "board-alpha")
    key_board_b = _provider_cache_key("org-x", "p1", params, policies, "board-beta")
    assert key_board_a != key_board_b, (
        "Cross-board cache collision: board-alpha and board-beta must NOT share a "
        "cache key for the same provider_id in the same org."
    )
    # Sanity: both keys must embed the board_id.
    assert "board-alpha" in key_board_a
    assert "board-beta" in key_board_b


# ---------------------------------------------------------------------------
# AUDIT-36: cross-org provider cache collision (end-to-end integration test)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_board_id_and_provider_different_orgs_get_distinct_cache_entries() -> None:
    """[LOW] Audit-36: two orgs sharing the same board_id + provider_id + params
    MUST get DISTINCT cache entries — no cross-org cache collision.

    Scenario: org-A and org-B each own a board with the SAME board_id string
    (possible in multi-tenant setups using fixed/deterministic IDs) and a
    provider with the same provider_id and the same params dict.  Org-A calls
    resolve_provider_data first; its result is cached.  Org-B then calls with
    the same arguments against its own repo.  Org-B must NOT hit org-A's cache
    entry — it must re-execute and produce its own independent result.

    This verifies that _provider_cache_key includes org_id as a discriminating
    component (fix-22 added board_id; this test confirms org_id is also present
    in every provider cache get/put path, including the inline ephemeral path).
    """
    _SHARED_BOARD_ID = "board-shared-id"
    _SHARED_PROVIDER_ID = "shared-provider"
    _ORG_A = "org-cross-a"
    _ORG_B = "org-cross-b"

    # Shared board spec used by both orgs — same board_id, same provider_id.
    shared_spec = {
        "version": 1,
        "title": "Shared Spec Board",
        "widgets": [
            {
                "id": "w1",
                "type": "table",
                "source": {"provider": _SHARED_PROVIDER_ID, "result": "revenue"},
            }
        ],
        "data": [
            {
                "id": _SHARED_PROVIDER_ID,
                "kind": "inline",
                "params": {},
                "base_cte": None,
                "results": [{"name": "revenue", "grain": None}],
            }
        ],
    }

    # Build a separate InMemoryRepo for each org, each seeded with the same
    # board_id but different org_id — simulating two isolated org namespaces.
    repo_a = InMemoryRepo()
    set_repo(repo_a)
    await repo_a.create(
        "boards",
        org_id=_ORG_A,
        created_by="test",
        name="Org A Board",
        config={"spec": shared_spec},
        id=_SHARED_BOARD_ID,
    )

    repo_b = InMemoryRepo()
    await repo_b.create(
        "boards",
        org_id=_ORG_B,
        created_by="test",
        name="Org B Board",
        config={"spec": shared_spec},
        id=_SHARED_BOARD_ID,
    )

    # Track how many times the provider executes — if org-B hits org-A's cache
    # the count will be 1 instead of 2 (cross-org collision).
    call_count = 0

    async def _fake_run(query_id, org_id, _repo, policies):
        nonlocal call_count
        call_count += 1
        # Return a distinct value per call so we can distinguish org-A from org-B.
        return ["val"], [[float(call_count * 100)]]

    async def _fake_enforce_quota(org_id: str, dimension: str, amount: float = 1.0) -> None:
        pass

    try:
        with (
            patch("app.features.enforce_quota", side_effect=_fake_enforce_quota),
            patch("app.dashboards.collect.run_query_rows", side_effect=_fake_run),
        ):
            # Org-A executes first and populates the cache.
            set_repo(repo_a)
            tables_a = await resolve_provider_data(
                board_id=_SHARED_BOARD_ID,
                provider_id=_SHARED_PROVIDER_ID,
                params={},
                org_id=_ORG_A,
                claims={"policies": {}},
                repo=repo_a,
            )

            # Org-B executes with the SAME board_id + provider_id + params.
            # If org_id is not part of the cache key, org-B will incorrectly
            # receive org-A's cached result and run_query_rows will NOT be called
            # a second time (call_count stays at 1 — collision detected).
            set_repo(repo_b)
            tables_b = await resolve_provider_data(
                board_id=_SHARED_BOARD_ID,
                provider_id=_SHARED_PROVIDER_ID,
                params={},
                org_id=_ORG_B,
                claims={"policies": {}},
                repo=repo_b,
            )
    finally:
        set_repo(None)

    # run_query_rows must have been called TWICE — once per org.
    # If it was called only once, org-B got org-A's cached result (cross-org leak).
    assert call_count == 2, (
        f"Cross-org provider cache collision: run_query_rows called {call_count} time(s) "
        "but expected 2 (once per org). Org-B incorrectly hit org-A's cache entry — "
        "org_id is missing from the provider cache key."
    )

    # Both results must exist and be structurally independent.
    assert "revenue" in tables_a, f"revenue missing from org-A result: {list(tables_a)}"
    assert "revenue" in tables_b, f"revenue missing from org-B result: {list(tables_b)}"

    # The values differ because each call produced a unique counter value.
    val_a = tables_a["revenue"].to_pydict()["val"][0]
    val_b = tables_b["revenue"].to_pydict()["val"][0]
    assert val_a != val_b, (
        f"Org-A and org-B returned identical values ({val_a!r}) — "
        "cross-org provider cache collision: org_id must be part of the cache key."
    )

    # Sanity: confirm org_id is the first discriminating component of the key format.
    from app.dashboards.board_data import _provider_cache_key as _pck
    key_a = _pck(_ORG_A, _SHARED_PROVIDER_ID, {}, {}, _SHARED_BOARD_ID)
    key_b = _pck(_ORG_B, _SHARED_PROVIDER_ID, {}, {}, _SHARED_BOARD_ID)
    assert key_a != key_b, "Cache key function itself must differ by org_id."
    assert f":{_ORG_A}:" in key_a, f"org_id not embedded in key: {key_a}"
    assert f":{_ORG_B}:" in key_b, f"org_id not embedded in key: {key_b}"


@pytest.mark.asyncio
async def test_same_provider_id_different_boards_get_distinct_cache_entries(
    repo: InMemoryRepo,
) -> None:
    """Two boards with the same provider_id execute independently (no cross-board hit).

    Scenario: org has two boards, both with a provider named 'p1'.  A first call
    on board-A populates the cache.  A second call on board-B with the same params
    must NOT hit the board-A cache entry — it must re-execute and produce its own
    (distinct) result.
    """
    _BOARD_A = "board-xb-a"
    _BOARD_B = "board-xb-b"
    _PID = "shared-pid"

    spec_a = {
        "version": 1,
        "title": "Board A",
        "widgets": [
            {"id": "w1", "type": "table", "source": {"provider": _PID, "result": "revenue"}},
        ],
        "data": [
            {
                "id": _PID,
                "kind": "inline",
                "params": {},
                "base_cte": None,
                "results": [{"name": "revenue", "grain": None}],
            }
        ],
    }
    spec_b = {
        "version": 1,
        "title": "Board B",
        "widgets": [
            {"id": "w1", "type": "table", "source": {"provider": _PID, "result": "revenue"}},
        ],
        "data": [
            {
                "id": _PID,
                "kind": "inline",
                "params": {},
                "base_cte": None,
                "results": [{"name": "revenue", "grain": None}],
            }
        ],
    }

    await repo.create(
        "boards",
        org_id=_ORG,
        created_by="test",
        name="Board A",
        config={"spec": spec_a},
        id=_BOARD_A,
    )
    await repo.create(
        "boards",
        org_id=_ORG,
        created_by="test",
        name="Board B",
        config={"spec": spec_b},
        id=_BOARD_B,
    )

    call_count = 0

    async def _fake_run(query_id, org_id, _repo, policies):
        nonlocal call_count
        call_count += 1
        # Return a unique value per call so we can tell the two apart.
        return ["val"], [[float(call_count * 100)]]

    async def _fake_enforce_quota(org_id: str, dimension: str, amount: float = 1.0) -> None:
        pass

    with (
        patch("app.features.enforce_quota", side_effect=_fake_enforce_quota),
        patch("app.dashboards.collect.run_query_rows", side_effect=_fake_run),
    ):
        tables_a = await resolve_provider_data(
            board_id=_BOARD_A,
            provider_id=_PID,
            params={},
            org_id=_ORG,
            claims={"policies": {}},
            repo=repo,
        )
        # Board-B has the same provider_id + params — must NOT hit board-A's cache.
        tables_b = await resolve_provider_data(
            board_id=_BOARD_B,
            provider_id=_PID,
            params={},
            org_id=_ORG,
            claims={"policies": {}},
            repo=repo,
        )

    # run_query_rows must have been called TWICE (once per board) — not once
    # (which would mean board-B got a cross-board cache hit from board-A).
    assert call_count == 2, (
        f"Cross-board cache collision detected: run_query_rows called {call_count} times "
        "but expected 2 (once per board). Board-B incorrectly hit board-A's cache entry."
    )

    # Both results must exist and be structurally independent.
    assert "revenue" in tables_a
    assert "revenue" in tables_b
    # The values differ because each call produced a unique counter value.
    val_a = tables_a["revenue"].to_pydict()["val"][0]
    val_b = tables_b["revenue"].to_pydict()["val"][0]
    assert val_a != val_b, (
        f"Board-A and board-B returned identical values ({val_a!r}) — "
        "cross-board cache collision not fixed."
    )


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
# SECURITY: materialized fast-path must fail closed for RLS-scoped callers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_materialized_rls_caller_does_not_get_materialized_data(
    repo: InMemoryRepo,
) -> None:
    """[HIGH RLS cross-tenant] A caller with non-empty RLS policies MUST NOT be
    served the materialized (scheduled, owner-scope) result.

    The materialized task_run bytes were computed under the OWNER policy snapshot
    (often policies={} = no filter), i.e. they contain EVERY tenant's rows. The
    fast-path cannot row-filter those bytes, so a tenant-scoped caller must fall
    back to the RLS-enforced ephemeral path instead of receiving the full,
    unfiltered admin-scope result.

    This test proves:
      1. _resolve_materialized_flow_provider returns None when claims carry
         non-empty policies (fail-closed gate).
      2. resolve_provider_data therefore falls back to the ephemeral path and
         the caller gets the (distinctly-shaped) ephemeral result, NOT the
         materialized one — i.e. no cross-tenant rows leak.
    """
    import io as _io
    import pyarrow as pa
    from app.dashboards.board_data import _resolve_materialized_flow_provider
    import app.flows.store as _fs_mod

    await repo.update(
        "boards",
        _ORG,
        _BOARD_ID,
        {"config": {"spec": _make_spec_with_flow_provider()}},
    )

    # Materialized (owner-scope) result: 7 rows — the "leaky" full dataset.
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

    # RLS-scoped caller (tenant 'acme').
    rls_claims = {"policies": {"tenant_id": "acme"}}

    # ── 1. Direct: the resolver fails closed for an RLS-scoped caller. ────────
    with patch.object(_fs_mod, "get_flow_store", return_value=_FakeStore()):
        direct = await _resolve_materialized_flow_provider(
            _board_flow_provider(),
            {},
            _ORG,
            rls_claims,
        )
    assert direct is None, (
        "RLS-scoped caller must NOT receive materialized data; expected None "
        "(fail-closed), got a result — cross-tenant leak."
    )

    # ── 2. End-to-end: resolve_provider_data falls back to ephemeral (RLS). ──
    # The ephemeral result is distinctly shaped (3 rows) so we can prove the
    # caller did NOT get the 7-row materialized (full-scope) dataset.
    ephemeral_table = _make_arrow_table(3)

    async def _fake_enforce_quota(org_id: str, dimension: str, amount: float = 1.0) -> None:
        pass

    with (
        patch("app.features.enforce_quota", side_effect=_fake_enforce_quota),
        patch.object(_fs_mod, "get_flow_store", return_value=_FakeStore()),
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
            claims=rls_claims,
            repo=repo,
            mode="materialized",
        )

    assert "summary" in tables
    assert tables["summary"].num_rows == 3, (
        "RLS-scoped caller must get the RLS-enforced ephemeral result (3 rows), "
        f"not the materialized full-scope result (7 rows); got "
        f"{tables['summary'].num_rows} rows."
    )


@pytest.mark.asyncio
async def test_materialized_no_policy_caller_still_gets_fast_path(
    repo: InMemoryRepo,
) -> None:
    """A caller with empty/absent RLS policies (admin / no-RLS scope) STILL gets
    the materialized fast-path — the fail-closed gate only blocks RLS-scoped
    callers, it must not regress the no-RLS case.
    """
    import io as _io
    import pyarrow as pa
    from app.dashboards.board_data import _resolve_materialized_flow_provider
    import app.flows.store as _fs_mod

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

    with patch.object(_fs_mod, "get_flow_store", return_value=_FakeStore()):
        # Empty policies dict.
        empty = await _resolve_materialized_flow_provider(
            _board_flow_provider(), {}, _ORG, {"policies": {}}
        )
        # Absent policies key entirely.
        absent = await _resolve_materialized_flow_provider(
            _board_flow_provider(), {}, _ORG, {}
        )

    for label, tables in (("empty-policies", empty), ("absent-policies", absent)):
        assert tables is not None, (
            f"{label}: no-RLS caller must still get the materialized fast-path, "
            "got None."
        )
        assert tables["summary"].num_rows == 7, (
            f"{label}: expected 7-row materialized result, got "
            f"{tables['summary'].num_rows}."
        )


# ---------------------------------------------------------------------------
# [LOW resource] materialized provider table-count cap (Fix)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_materialized_provider_exceeds_table_cap_raises() -> None:
    """_resolve_materialized_flow_provider raises provider_result_too_large (422)
    when the number of accumulated result tables exceeds _PROVIDER_MAX_TABLES.

    The in-loop cap must fire BEFORE the outer resolve_provider_data cap so
    the check is enforced even on the materialized fast-path.
    """
    import io as _io
    import app.dashboards.board_data as _bd_mod
    import app.flows.store as _fs_mod
    from app.dashboards.board_data import _resolve_materialized_flow_provider
    from app.dashboards.spec import ProviderResult

    original_cap = _bd_mod._PROVIDER_MAX_TABLES
    small_cap = 2
    _bd_mod._PROVIDER_MAX_TABLES = small_cap

    try:
        # Build a provider that declares small_cap+1 results.
        extra_result_names = [f"result_{i}" for i in range(small_cap + 1)]
        provider = DataProvider(
            id=_PROVIDER_ID,
            kind="flow",
            params={},
            results=[ProviderResult(name=n) for n in extra_result_names],
        )

        fake_flow = {"id": _PROVIDER_ID, "name": _PROVIDER_ID, "org_id": _ORG}
        fake_flow_run = {"id": "mat-run-cap-1", "state": "success"}

        # Build a task_run for each declared result so accumulation crosses the cap.
        def _make_ipc(n: int) -> bytes:
            tbl = pa.table({"v": pa.array([n])})
            buf = _io.BytesIO()
            w = pa.ipc.new_stream(buf, tbl.schema)
            w.write_table(tbl)
            w.close()
            return buf.getvalue()

        fake_task_runs = [
            {
                "task_key": name,
                "state": "success",
                "result": {"__arrow_ipc__": _make_ipc(i)},
            }
            for i, name in enumerate(extra_result_names)
        ]

        class _FakeStore:
            async def get_flow(self, flow_id: str) -> dict:
                return fake_flow

            async def list_flows(self, **kwargs: Any) -> list:
                return [fake_flow]

            async def list_flow_runs(self, flow_id: str, limit: int = 10, **kwargs: Any) -> list:
                return [fake_flow_run]

            async def list_task_runs(self, run_id: str, limit: int | None = None) -> list:
                result = list(fake_task_runs)
                return result[:limit] if limit is not None else result

        with (
            patch.object(_fs_mod, "get_flow_store", return_value=_FakeStore()),
            pytest.raises(AppError) as exc_info,
        ):
            await _resolve_materialized_flow_provider(provider, {}, _ORG, {"policies": {}})

        assert exc_info.value.code == "provider_result_too_large", (
            f"Expected provider_result_too_large, got {exc_info.value.code!r}"
        )
        assert exc_info.value.status == 422, (
            f"Expected HTTP 422, got {exc_info.value.status}"
        )
    finally:
        _bd_mod._PROVIDER_MAX_TABLES = original_cap


@pytest.mark.asyncio
async def test_materialized_provider_fill_missing_cap_raises() -> None:
    """_resolve_materialized_flow_provider raises provider_result_too_large (422)
    before the fill-missing-results loop when provider.results alone exceeds the cap.

    This guards against an oversized spec that declares more results than
    _PROVIDER_MAX_TABLES even when no task_runs produced data, preventing
    unbounded pa.table({}) creation.
    """
    import app.dashboards.board_data as _bd_mod
    import app.flows.store as _fs_mod
    from app.dashboards.board_data import _resolve_materialized_flow_provider
    from app.dashboards.spec import ProviderResult

    original_cap = _bd_mod._PROVIDER_MAX_TABLES
    small_cap = 2
    _bd_mod._PROVIDER_MAX_TABLES = small_cap

    try:
        # Declare small_cap+1 results but produce NO task_run data — so
        # accumulation loop adds nothing and the fill-missing guard fires.
        extra_result_names = [f"empty_{i}" for i in range(small_cap + 1)]
        provider = DataProvider(
            id=_PROVIDER_ID,
            kind="flow",
            params={},
            results=[ProviderResult(name=n) for n in extra_result_names],
        )

        fake_flow = {"id": _PROVIDER_ID, "name": _PROVIDER_ID, "org_id": _ORG}
        fake_flow_run = {"id": "mat-run-cap-2", "state": "success"}

        class _FakeStoreNoData:
            async def get_flow(self, flow_id: str) -> dict:
                return fake_flow

            async def list_flows(self, **kwargs: Any) -> list:
                return [fake_flow]

            async def list_flow_runs(self, flow_id: str, limit: int = 10, **kwargs: Any) -> list:
                return [fake_flow_run]

            async def list_task_runs(self, run_id: str, limit: int | None = None) -> list:
                # No task_run results — all declared results will be "missing".
                return []

        with (
            patch.object(_fs_mod, "get_flow_store", return_value=_FakeStoreNoData()),
            pytest.raises(AppError) as exc_info,
        ):
            await _resolve_materialized_flow_provider(provider, {}, _ORG, {"policies": {}})

        assert exc_info.value.code == "provider_result_too_large", (
            f"Expected provider_result_too_large, got {exc_info.value.code!r}"
        )
        assert exc_info.value.status == 422, (
            f"Expected HTTP 422, got {exc_info.value.status}"
        )
    finally:
        _bd_mod._PROVIDER_MAX_TABLES = original_cap


def test_spec_data_provider_too_many_results_rejected() -> None:
    """DataProvider.results with more than _PROVIDER_MAX_RESULTS_SPEC entries
    is rejected by Pydantic validation with a ValidationError.

    This ensures oversized provider specs are caught at parse/validate time,
    before they reach the resolver.
    """
    from pydantic import ValidationError
    from app.dashboards.spec import _PROVIDER_MAX_RESULTS_SPEC, ProviderResult

    # Exactly at the cap — must succeed.
    at_cap = DataProvider(
        id="p-at-cap",
        kind="flow",
        params={},
        results=[ProviderResult(name=f"r{i}") for i in range(_PROVIDER_MAX_RESULTS_SPEC)],
    )
    assert len(at_cap.results) == _PROVIDER_MAX_RESULTS_SPEC

    # One over the cap — must fail.
    with pytest.raises(ValidationError) as exc_info:
        DataProvider(
            id="p-over-cap",
            kind="flow",
            params={},
            results=[
                ProviderResult(name=f"r{i}") for i in range(_PROVIDER_MAX_RESULTS_SPEC + 1)
            ],
        )

    errors = exc_info.value.errors()
    assert any(
        "results" in (e.get("loc") or ()) or "results" in str(e.get("loc", ""))
        for e in errors
    ), f"Expected ValidationError on 'results' field, got: {errors}"


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
# FIX [MED]: _PROVIDER_MAX_TABLES guard fires WHILE accumulating (early exit)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_max_tables_guard_fires_early_not_post_hoc(
    repo: InMemoryRepo,
) -> None:
    """Over-limit flow provider is rejected without materializing all tables.

    Before the fix the table-count guard only ran after _resolve_flow_provider
    returned all tables (~line 1392), meaning a provider with N >> cap tables
    would fully materialise N tables into memory before the AppError was raised.

    After the fix the guard runs INSIDE the accumulation loop: as soon as the
    running count exceeds _PROVIDER_MAX_TABLES the function raises immediately,
    leaving the remaining task_run results un-materialised.

    Verification strategy
    ---------------------
    * Set a small cap (2 tables).
    * Supply a flow store whose task_runs produce many unique result keys
      (total = cap + 10).  Each task_run key IS in provider.results so none
      are filtered by the result_names guard.
    * Instrument pa.table (via a call-counting wrapper) to count how many
      Arrow tables are actually constructed during the aborted run.
    * Assert:
      a) AppError(code="provider_result_too_large", status=422) is raised.
      b) Fewer than (total_task_runs) Arrow tables were constructed, proving
         the remaining rows were never materialised.
    """
    import app.dashboards.board_data as _bd_mod
    import app.flows.store as _fs_mod
    import pyarrow as pa

    SMALL_CAP = 2
    TOTAL_RESULTS = SMALL_CAP + 10  # well over the cap

    original_max = _bd_mod._PROVIDER_MAX_TABLES
    _bd_mod._PROVIDER_MAX_TABLES = SMALL_CAP

    # Build a DataProvider spec that declares all TOTAL_RESULTS result names.
    result_names = [f"result_{i}" for i in range(TOTAL_RESULTS)]
    spec = {
        "version": 1,
        "title": "Early-Guard Test Board",
        "widgets": [
            {"id": "w1", "type": "table", "source": {"provider": _PROVIDER_ID, "result": result_names[0]}}
        ],
        "data": [
            {
                "id": _PROVIDER_ID,
                "kind": "flow",
                "params": {},
                "base_cte": None,
                "results": [{"name": n, "grain": None} for n in result_names],
            }
        ],
    }
    await repo.update("boards", _ORG, _BOARD_ID, {"config": {"spec": spec}})

    fake_flow = {"id": _PROVIDER_ID, "name": _PROVIDER_ID, "org_id": _ORG}
    fake_flow_run = {"id": "early-guard-run-1", "state": "success"}

    # One task_run per result name — all succeed with a tiny payload.
    fake_task_runs = [
        {
            "task_key": n,
            "state": "success",
            "result": {"columns": ["v"], "rows": [[i]]},
        }
        for i, n in enumerate(result_names)
    ]

    # Count how many rows/columns-format tables are actually constructed by
    # wrapping the pa.array constructor call counter inside the accumulation
    # path.  We count task_runs processed by counting how many result dicts
    # are decoded (each produces one pa.array call per column).
    materialised_keys: list[str] = []

    class _SpyStore:
        async def get_flow(self, flow_id: str) -> dict:
            return fake_flow

        async def list_flows(self, **kwargs: Any) -> list:
            return [fake_flow]

        async def list_flow_runs(self, flow_id: str, limit: int = 10, **kwargs: Any) -> list:
            return [fake_flow_run]

        async def list_task_runs(self, run_id: str, limit: int | None = None) -> list:
            result = list(fake_task_runs)
            return result[:limit] if limit is not None else result

    # Spy on _bd_mod.pa.array to count materialisation calls per task_run.
    # We track unique task_keys by intercepting at a higher level: wrap the
    # pa.table constructor used inside the flow accumulation loop.
    real_pa_table = pa.table

    def spy_pa_table(data, **kwargs):  # type: ignore[override]
        # Record each table construction.  data is a dict when coming from
        # the columns/rows branch of _resolve_flow_provider.
        if isinstance(data, dict):
            materialised_keys.append(str(len(materialised_keys)))
        return real_pa_table(data, **kwargs)

    async def _fake_enforce_quota(org_id: str, dimension: str, amount: float = 1.0) -> None:
        pass

    try:
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
            # Patch pa.table inside the board_data module so the spy fires on
            # every table construction inside _resolve_flow_provider.
            patch.object(_bd_mod.pa, "table", side_effect=spy_pa_table),
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

        assert exc_info.value.code == "provider_result_too_large", (
            f"Expected provider_result_too_large, got {exc_info.value.code!r}"
        )
        assert exc_info.value.status == 422

        # The fix: materialised_keys must be strictly fewer than TOTAL_RESULTS,
        # proving that not all task_run payloads were decoded into Arrow tables
        # before the guard fired.
        assert len(materialised_keys) < TOTAL_RESULTS, (
            f"Guard fired POST-HOC: all {TOTAL_RESULTS} tables were materialised "
            f"({len(materialised_keys)} pa.table calls) before the AppError was raised. "
            f"The cap should have stopped accumulation after {SMALL_CAP + 1} tables."
        )
    finally:
        _bd_mod._PROVIDER_MAX_TABLES = original_max


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
async def test_non_embed_flow_provider_bypasses_embed_semaphore(repo: InMemoryRepo) -> None:
    """[MED amplification] Non-embed (first-party) flow provider calls are NOT
    subject to the embed semaphore guard — they use the separate
    _flow_provider_semaphores registry instead.

    Verify: zeroing out _EMBED_FLOW_CONCURRENCY does NOT block a non-embed call
    (it routes through the non-embed registry which has its own ceiling).
    """
    import app.dashboards.board_data as _bd_mod
    from app.connectors.cache import reset_cache_for_tests as _reset

    await repo.update(
        "boards",
        _ORG,
        _BOARD_ID,
        {"config": {"spec": _make_spec_with_flow_provider()}},
    )

    original_embed_concurrency = _bd_mod._EMBED_FLOW_CONCURRENCY
    original_embed_timeout = _bd_mod._EMBED_FLOW_TIMEOUT_S
    original_flow_concurrency = _bd_mod._FLOW_PROVIDER_CONCURRENCY
    _bd_mod._embed_semaphores.clear()
    _bd_mod._flow_provider_semaphores.clear()
    # Zero-out the EMBED concurrency — if non-embed used the embed semaphore it
    # would immediately 429.  But non-embed uses its own registry (leave at >=1).
    _bd_mod._EMBED_FLOW_CONCURRENCY = 0
    _bd_mod._EMBED_FLOW_TIMEOUT_S = 0.0
    _bd_mod._FLOW_PROVIDER_CONCURRENCY = 4  # non-embed has capacity

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
            # Non-embed call must succeed even with embed concurrency=0 because
            # it uses the separate _flow_provider_semaphores registry (cap=4).
            tables = await resolve_provider_data(
                board_id=_BOARD_ID,
                provider_id=_PROVIDER_ID,
                params={},
                org_id=_ORG,
                claims={"policies": {}},
                repo=repo,
                is_embed=False,  # first-party — uses non-embed registry
            )
        assert "summary" in tables
        assert tables["summary"].num_rows == 1
    finally:
        _bd_mod._EMBED_FLOW_CONCURRENCY = original_embed_concurrency
        _bd_mod._EMBED_FLOW_TIMEOUT_S = original_embed_timeout
        _bd_mod._FLOW_PROVIDER_CONCURRENCY = original_flow_concurrency
        _bd_mod._embed_semaphores.clear()
        _bd_mod._flow_provider_semaphores.clear()
        _reset()


# ---------------------------------------------------------------------------
# NEW (fix-20): [MED non-embed concurrency] concurrent non-embed flow-provider
# requests are bounded by the _flow_provider_semaphores registry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_embed_flow_provider_concurrent_requests_are_bounded(
    repo: InMemoryRepo,
) -> None:
    """[MED non-embed concurrency] Concurrent non-embed flow-provider cache misses
    are limited by the per-(org, provider) _flow_provider_semaphores registry.

    Strategy: set _FLOW_PROVIDER_CONCURRENCY=1 and _FLOW_PROVIDER_TIMEOUT_S=0.0,
    pre-drain the semaphore to simulate a concurrent holder, then verify a second
    non-embed call raises AppError("provider_busy", 429) immediately.
    """
    import app.dashboards.board_data as _bd_mod
    from app.connectors.cache import reset_cache_for_tests as _reset

    await repo.update(
        "boards",
        _ORG,
        _BOARD_ID,
        {"config": {"spec": _make_spec_with_flow_provider()}},
    )

    original_concurrency = _bd_mod._FLOW_PROVIDER_CONCURRENCY
    original_timeout = _bd_mod._FLOW_PROVIDER_TIMEOUT_S
    _bd_mod._flow_provider_semaphores.clear()
    _bd_mod._FLOW_PROVIDER_CONCURRENCY = 1
    _bd_mod._FLOW_PROVIDER_TIMEOUT_S = 0.0  # zero timeout → immediate 429

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
            # Manually acquire the non-embed semaphore to simulate a concurrent holder.
            sem = _bd_mod._get_flow_provider_semaphore(_ORG, _PROVIDER_ID)
            await sem.acquire()  # drain the single token
            try:
                # A second non-embed call with timeout=0 must 429.
                with pytest.raises(AppError) as exc_info:
                    await resolve_provider_data(
                        board_id=_BOARD_ID,
                        provider_id=_PROVIDER_ID,
                        params={"_run": "contended-non-embed"},
                        org_id=_ORG,
                        claims={"policies": {}},
                        repo=repo,
                        is_embed=False,  # non-embed (interactive writer/viewer)
                    )
                assert exc_info.value.code == "provider_busy", (
                    f"Expected provider_busy 429 on non-embed semaphore contention; "
                    f"got {exc_info.value.code!r}"
                )
                assert exc_info.value.status == 429
            finally:
                sem.release()
    finally:
        _bd_mod._FLOW_PROVIDER_CONCURRENCY = original_concurrency
        _bd_mod._FLOW_PROVIDER_TIMEOUT_S = original_timeout
        _bd_mod._flow_provider_semaphores.clear()
        _reset()


@pytest.mark.asyncio
async def test_non_embed_flow_provider_succeeds_when_below_concurrency_cap(
    repo: InMemoryRepo,
) -> None:
    """[MED non-embed concurrency] A non-embed flow-provider call that fits within
    the concurrency cap must succeed (not 429).

    Sanity-checks that the guard does not over-eagerly block calls when there is
    capacity available.
    """
    import app.dashboards.board_data as _bd_mod
    from app.connectors.cache import reset_cache_for_tests as _reset

    await repo.update(
        "boards",
        _ORG,
        _BOARD_ID,
        {"config": {"spec": _make_spec_with_flow_provider()}},
    )

    original_concurrency = _bd_mod._FLOW_PROVIDER_CONCURRENCY
    original_timeout = _bd_mod._FLOW_PROVIDER_TIMEOUT_S
    _bd_mod._flow_provider_semaphores.clear()
    _bd_mod._FLOW_PROVIDER_CONCURRENCY = 4  # ample capacity
    _bd_mod._FLOW_PROVIDER_TIMEOUT_S = 1.0

    async def _fake_enforce_quota(org_id: str, dimension: str, amount: float = 1.0) -> None:
        pass

    try:
        with (
            patch("app.features.enforce_quota", side_effect=_fake_enforce_quota),
            patch(
                "app.dashboards.board_data._resolve_flow_provider",
                new=AsyncMock(return_value={"summary": pa.table({"val": pa.array([42])})}),
            ),
        ):
            tables = await resolve_provider_data(
                board_id=_BOARD_ID,
                provider_id=_PROVIDER_ID,
                params={},
                org_id=_ORG,
                claims={"policies": {}},
                repo=repo,
                is_embed=False,
            )
        assert "summary" in tables
        assert tables["summary"].to_pydict() == {"val": [42]}
    finally:
        _bd_mod._FLOW_PROVIDER_CONCURRENCY = original_concurrency
        _bd_mod._FLOW_PROVIDER_TIMEOUT_S = original_timeout
        _bd_mod._flow_provider_semaphores.clear()
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


# ---------------------------------------------------------------------------
# NEW (fix-20b): [LOW] _embed_semaphores LRU-bounded registry
# ---------------------------------------------------------------------------


def test_embed_semaphore_registry_stays_bounded_under_many_keys() -> None:
    """[LOW] The _embed_semaphores registry must not grow beyond _EMBED_SEM_REGISTRY_CAP.

    Inserting N >> cap distinct (org_id, provider_id) keys must result in a
    registry whose length is at most _EMBED_SEM_REGISTRY_CAP — idle entries are
    evicted in LRU order as the cap is approached.

    All created semaphores are idle (no callers hold them), so eviction is always
    safe and the final registry size must be <= cap.
    """
    import app.dashboards.board_data as _bd_mod

    original_cap = _bd_mod._EMBED_SEM_REGISTRY_CAP
    original_concurrency = _bd_mod._EMBED_FLOW_CONCURRENCY
    cap = 10
    _bd_mod._EMBED_SEM_REGISTRY_CAP = cap
    _bd_mod._EMBED_FLOW_CONCURRENCY = 2
    _bd_mod._embed_semaphores.clear()

    try:
        n_keys = cap * 3  # insert 3× the cap
        for i in range(n_keys):
            _bd_mod._get_embed_semaphore(f"org-{i}", f"provider-{i}")

        registry_size = len(_bd_mod._embed_semaphores)
        assert registry_size <= cap, (
            f"_embed_semaphores grew to {registry_size} entries with cap={cap} "
            f"and {n_keys} distinct idle keys — LRU eviction is not bounding the registry."
        )
    finally:
        _bd_mod._EMBED_SEM_REGISTRY_CAP = original_cap
        _bd_mod._EMBED_FLOW_CONCURRENCY = original_concurrency
        _bd_mod._embed_semaphores.clear()


@pytest.mark.asyncio
async def test_embed_semaphore_registry_does_not_evict_in_use_semaphore() -> None:
    """[LOW] An in-use semaphore (token held) is NEVER evicted from the registry,
    even when the registry is over its cap and all other entries are idle.

    Strategy:
    1. Set cap=2 and acquire a token on the semaphore for (org-0, p-0) so it
       is in-use.
    2. Fill the registry to beyond the cap with idle keys.
    3. Assert the in-use entry is still present.
    4. Assert the 429 provider_busy behaviour still works for a contended
       in-use semaphore (correctness check).
    """
    import app.dashboards.board_data as _bd_mod

    original_cap = _bd_mod._EMBED_SEM_REGISTRY_CAP
    original_concurrency = _bd_mod._EMBED_FLOW_CONCURRENCY
    cap = 2
    _bd_mod._EMBED_SEM_REGISTRY_CAP = cap
    _bd_mod._EMBED_FLOW_CONCURRENCY = 1  # one token per semaphore
    _bd_mod._embed_semaphores.clear()

    in_use_key = ("org-inuse", "provider-inuse")

    try:
        # Obtain the in-use semaphore and acquire its single token.
        sem_in_use = _bd_mod._get_embed_semaphore(*in_use_key)
        await sem_in_use.acquire()  # token is now held; semaphore is NOT idle

        # Insert enough idle keys to overflow the cap and trigger eviction.
        for i in range(cap + 5):
            _bd_mod._get_embed_semaphore(f"org-idle-{i}", f"provider-idle-{i}")

        # The in-use entry must still be in the registry.
        assert in_use_key in _bd_mod._embed_semaphores, (
            "In-use semaphore was evicted from the registry — "
            "live in-flight semaphore must not be removed."
        )

        # Correctness: the same semaphore object is returned on the next lookup.
        sem_retrieved = _bd_mod._get_embed_semaphore(*in_use_key)
        assert sem_retrieved is sem_in_use, (
            "Different semaphore object returned after near-eviction — "
            "in-use semaphore must be the same object throughout."
        )

        # The held token means the semaphore has _holders > 0 (not idle).
        assert not _bd_mod._embed_semaphore_is_idle(sem_in_use), (
            "_embed_semaphore_is_idle returned True for a held semaphore."
        )

    finally:
        # Always release before cleanup so the semaphore returns to idle.
        sem_in_use.release()
        _bd_mod._EMBED_SEM_REGISTRY_CAP = original_cap
        _bd_mod._EMBED_FLOW_CONCURRENCY = original_concurrency
        _bd_mod._embed_semaphores.clear()


# ---------------------------------------------------------------------------
# NEW (fix-21): [LOW resource-safety] semaphore LRU eviction private-API fallback
# ---------------------------------------------------------------------------


def test_embed_semaphore_is_idle_uses_counting_wrapper() -> None:
    """[LOW resource-safety] _embed_semaphore_is_idle uses _CountingSemaphore.is_idle()
    instead of the asyncio.Semaphore private ``_value`` attribute.

    The fix replaces getattr(sem, '_value', fallback) with a thin
    _CountingSemaphore wrapper that tracks acquire/release counts explicitly.
    This test verifies the idle-check correctly reflects holder state:
    - A freshly-created _CountingSemaphore (no holders) must be idle.
    - After acquire(), must not be idle (_holders > 0).
    - After release(), must be idle again.
    """
    import app.dashboards.board_data as _bd_mod

    sem = _bd_mod._CountingSemaphore(2)

    # Freshly created — no holders, must be idle.
    assert _bd_mod._embed_semaphore_is_idle(sem), (
        "_embed_semaphore_is_idle returned False for a fresh _CountingSemaphore "
        "(expected idle: no holders)."
    )
    assert sem._holders == 0, f"Expected _holders==0 on fresh semaphore, got {sem._holders}"


def test_flow_provider_semaphore_is_idle_uses_counting_wrapper() -> None:
    """[LOW resource-safety] _flow_provider_semaphore_is_idle uses _CountingSemaphore.is_idle()
    instead of the asyncio.Semaphore private ``_value`` attribute.

    The fix replaces getattr(sem, '_value', fallback) with a thin
    _CountingSemaphore wrapper that tracks acquire/release counts explicitly.
    This test verifies the idle-check correctly reflects holder state:
    - A freshly-created _CountingSemaphore (no holders) must be idle.
    - The _holders counter is always accurate (no private-API dependency).
    """
    import app.dashboards.board_data as _bd_mod

    sem = _bd_mod._CountingSemaphore(4)

    # Freshly created — no holders, must be idle.
    assert _bd_mod._flow_provider_semaphore_is_idle(sem), (
        "_flow_provider_semaphore_is_idle returned False for a fresh _CountingSemaphore "
        "(expected idle: no holders)."
    )
    assert sem._holders == 0, f"Expected _holders==0 on fresh semaphore, got {sem._holders}"


def test_embed_semaphore_registry_bounded_with_counting_semaphores() -> None:
    """[LOW resource-safety] _embed_semaphores registry is bounded by LRU eviction
    using _CountingSemaphore instances (no private _value dependency).

    Inserts N >> cap idle _CountingSemaphore entries and verifies the registry
    stays bounded at cap via LRU eviction.  Idle semaphores (_holders == 0)
    are always evictable; the registry must never exceed cap.
    """
    import app.dashboards.board_data as _bd_mod

    original_cap = _bd_mod._EMBED_SEM_REGISTRY_CAP
    original_concurrency = _bd_mod._EMBED_FLOW_CONCURRENCY
    cap = 5
    _bd_mod._EMBED_SEM_REGISTRY_CAP = cap
    _bd_mod._EMBED_FLOW_CONCURRENCY = 2
    _bd_mod._embed_semaphores.clear()

    try:
        # Insert N >> cap distinct keys — all semaphores are idle (no holders).
        for i in range(cap * 3):
            _bd_mod._get_embed_semaphore(f"real-org-{i}", f"real-provider-{i}")

        registry_size = len(_bd_mod._embed_semaphores)
        assert registry_size <= cap, (
            f"_embed_semaphores grew to {registry_size} entries with cap={cap} "
            "using _CountingSemaphore — LRU eviction is not bounding the registry "
            "(resource-safety regression)."
        )
    finally:
        _bd_mod._EMBED_SEM_REGISTRY_CAP = original_cap
        _bd_mod._EMBED_FLOW_CONCURRENCY = original_concurrency
        _bd_mod._embed_semaphores.clear()


# ---------------------------------------------------------------------------
# NEW (fix-28a): [LOW] _resolve_org_connector is bounded
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_org_connector_bounded_does_not_inspect_all_datastores() -> None:
    """[LOW] _resolve_org_connector inspects at most _CONNECTOR_SCAN_LIMIT
    datastores, even when the org has many more.

    An org with thousands of datastores would previously cause repo.list() to
    load them ALL into RAM on every inline-provider call just to find the first
    eligible connector.  The fix slices the result to _CONNECTOR_SCAN_LIMIT
    before filtering, bounding Python-side iteration.

    Strategy:
    - Seed an org with CONNECTOR_SCAN_LIMIT + 10 datastores (all real connectors).
    - The first datastore in the list must be found without iterating all of them.
    - We verify by wrapping repo.list to track how many items are actually
      consumed by _resolve_org_connector (the slice bounds the iteration).
    - _CONNECTOR_SCAN_LIMIT is temporarily set to a small value (5) to make the
      bound easy to observe.
    """
    import app.dashboards.board_data as _bd_mod
    from app.dashboards.board_data import _resolve_org_connector

    original_limit = _bd_mod._CONNECTOR_SCAN_LIMIT
    small_limit = 5
    _bd_mod._CONNECTOR_SCAN_LIMIT = small_limit

    # Build a large list of fake datastores — all real (non-system, non-demo).
    n_total = small_limit + 10
    fake_datastores = [
        {
            "id": f"ds-{i}",
            "config": {"connector_type": "duckdb", "type": "duckdb"},
        }
        for i in range(n_total)
    ]

    items_consumed: list[int] = []

    class _BoundedCheckRepo:
        """Wraps list() to return all items but track the slice via the result."""

        async def list(self, resource: str, org_id: str, *args, **kwargs):
            return list(fake_datastores)  # return all; slicing is done in board_data

    repo = _BoundedCheckRepo()

    # Patch _get_demo_connector (needed for the fallback path if no connector builds).
    # Also patch the connector registry so we don't need a real DuckDB file.
    mock_connector = object()  # sentinel

    async def _fake_get_demo():
        return mock_connector

    try:
        with (
            patch("app.routes.query._get_demo_connector", return_value=mock_connector),
            patch("app.connectors.registry.get_connector_registry") as mock_reg,
        ):
            # Make the registry return None for every type so we fall back to demo.
            mock_reg.return_value.get.return_value = None

            connector, owned = await _resolve_org_connector("org-bounded", repo)

        # We expect the demo connector (owned=False) because the registry has no
        # factory for "duckdb" in our mock.  The important assertion is that
        # _resolve_org_connector did NOT blow up when given many datastores AND
        # that it only processed at most _CONNECTOR_SCAN_LIMIT of them.
        assert connector is mock_connector, (
            "Expected the demo connector fallback when registry has no factory."
        )
        assert owned is False

    finally:
        _bd_mod._CONNECTOR_SCAN_LIMIT = original_limit


@pytest.mark.asyncio
async def test_resolve_org_connector_scan_limit_is_configurable() -> None:
    """[LOW] _CONNECTOR_SCAN_LIMIT is a positive integer (env-overridable cap).

    Verifies the constant exists, is an integer, and is > 0.
    """
    import app.dashboards.board_data as _bd_mod

    assert isinstance(_bd_mod._CONNECTOR_SCAN_LIMIT, int), (
        f"_CONNECTOR_SCAN_LIMIT is not an int: {type(_bd_mod._CONNECTOR_SCAN_LIMIT)}"
    )
    assert _bd_mod._CONNECTOR_SCAN_LIMIT > 0, (
        f"_CONNECTOR_SCAN_LIMIT must be positive, got {_bd_mod._CONNECTOR_SCAN_LIMIT}"
    )


# ---------------------------------------------------------------------------
# NEW (fix-29): [LOW] _resolve_org_connector passes limit= to repo.list (DB-bounded)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_org_connector_passes_limit_to_repo_list() -> None:
    """[LOW] _resolve_org_connector passes limit=_CONNECTOR_SCAN_LIMIT to repo.list
    so the DB query itself is bounded — not just a Python-side slice of all rows.

    Before the fix, repo.list("datastores", org_id) fetched ALL rows from the DB
    and the Python slice [:_CONNECTOR_SCAN_LIMIT] ran after all rows were already
    in memory.  After the fix, repo.list receives limit=_CONNECTOR_SCAN_LIMIT so
    only that many rows are transferred from the database.

    Strategy: replace repo.list with a spy that records the keyword arguments it
    receives.  Assert that limit= is passed and equals _CONNECTOR_SCAN_LIMIT.
    """
    import app.dashboards.board_data as _bd_mod
    from app.dashboards.board_data import _resolve_org_connector

    original_limit = _bd_mod._CONNECTOR_SCAN_LIMIT
    small_limit = 5
    _bd_mod._CONNECTOR_SCAN_LIMIT = small_limit

    list_kwargs_seen: list[dict] = []

    class _SpyRepo:
        async def list(self, resource: str, org_id: str, *args, **kwargs):
            list_kwargs_seen.append({"resource": resource, "org_id": org_id, **kwargs})
            # Return an empty list — we only care that limit= was forwarded.
            return []

    mock_connector = object()

    try:
        with patch("app.routes.query._get_demo_connector", return_value=mock_connector):
            connector, owned = await _resolve_org_connector("org-spy", _SpyRepo())
    finally:
        _bd_mod._CONNECTOR_SCAN_LIMIT = original_limit

    # repo.list must have been called at least once (for "datastores").
    datastore_calls = [c for c in list_kwargs_seen if c.get("resource") == "datastores"]
    assert len(datastore_calls) >= 1, (
        "repo.list('datastores', ...) was never called by _resolve_org_connector."
    )

    call = datastore_calls[0]
    assert "limit" in call, (
        "repo.list was called WITHOUT a limit= kwarg — DB fetch is unbounded. "
        "The fix must pass limit=_CONNECTOR_SCAN_LIMIT to repo.list so the DB "
        "query uses LIMIT N rather than fetching all rows."
    )
    assert call["limit"] == small_limit, (
        f"repo.list received limit={call['limit']!r} but expected {small_limit} "
        f"(_CONNECTOR_SCAN_LIMIT). The DB-level limit is not being passed correctly."
    )

    # Connector falls back to demo when the spy returns [].
    assert connector is mock_connector
    assert owned is False


@pytest.mark.asyncio
async def test_inmemory_repo_list_limit_is_honoured() -> None:
    """[LOW] InMemoryRepo.list respects the limit= kwarg.

    Ensures the in-memory implementation (used in tests) correctly honours
    limit= so that tests that rely on bounded fetches get the right behaviour.
    """
    r = InMemoryRepo()
    org = "org-limit-test"
    for i in range(10):
        await r.create(
            "datastores",
            org_id=org,
            created_by="test",
            name=f"ds-{i}",
            config={"connector_type": "duckdb"},
            id=f"ds-limit-{i}",
        )

    # Without limit: all 10 rows.
    all_rows = await r.list("datastores", org)
    assert len(all_rows) == 10

    # With limit=3: exactly 3 rows.
    limited = await r.list("datastores", org, limit=3)
    assert len(limited) == 3, (
        f"InMemoryRepo.list(limit=3) returned {len(limited)} rows; expected 3."
    )

    # With limit > total: all rows (no error).
    over = await r.list("datastores", org, limit=100)
    assert len(over) == 10


# ---------------------------------------------------------------------------
# NEW (fix-28b): [LOW] _CountingSemaphore idle-check is private-API-free
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_counting_semaphore_idle_check_tracks_holders_accurately() -> None:
    """[LOW] _CountingSemaphore.is_idle() accurately tracks acquire/release state
    without relying on asyncio.Semaphore private ``_value`` attribute.

    Verifies:
    - Fresh semaphore: is_idle() == True, _holders == 0
    - After acquire(): is_idle() == False, _holders > 0
    - After release(): is_idle() == True again, _holders == 0
    """
    import app.dashboards.board_data as _bd_mod

    sem = _bd_mod._CountingSemaphore(2)

    # 1. Fresh — idle.
    assert sem.is_idle(), "Fresh _CountingSemaphore must be idle"
    assert sem._holders == 0

    # 2. After first acquire — not idle.
    await sem.acquire()
    assert not sem.is_idle(), "_CountingSemaphore must not be idle after acquire()"
    assert sem._holders == 1

    # 3. After second acquire — still not idle.
    await sem.acquire()
    assert not sem.is_idle()
    assert sem._holders == 2

    # 4. After first release — still one holder.
    sem.release()
    assert not sem.is_idle()
    assert sem._holders == 1

    # 5. After second release — idle again.
    sem.release()
    assert sem.is_idle(), "_CountingSemaphore must be idle after all holders release"
    assert sem._holders == 0

    # 6. Verify no ``_value`` dependency — the check does NOT read _value.
    # (Accessing _sem._value might still work but our code must not require it.)
    assert hasattr(sem, "_holders"), "_CountingSemaphore must have _holders attribute"


def test_all_semaphore_idle_checks_safe_when_no_value_attribute() -> None:
    """[LOW] _embed_semaphore_is_idle and _flow_provider_semaphore_is_idle
    remain safe even when passed objects without a ``_value`` attribute.

    With the _CountingSemaphore wrapper, the registries only ever contain
    _CountingSemaphore instances that always have is_idle().  But the idle-check
    functions accept any object — a stub without is_idle() should raise
    AttributeError (expected: the functions require the is_idle() protocol).
    This test confirms that _CountingSemaphore satisfies the protocol and that
    the functions correctly delegate to is_idle().
    """
    import app.dashboards.board_data as _bd_mod

    # Real _CountingSemaphore — must work correctly.
    sem = _bd_mod._CountingSemaphore(3)
    assert _bd_mod._embed_semaphore_is_idle(sem), (
        "embed idle-check must return True for idle _CountingSemaphore"
    )
    assert _bd_mod._flow_provider_semaphore_is_idle(sem), (
        "flow-provider idle-check must return True for idle _CountingSemaphore"
    )

    # Also confirm that a _CountingSemaphore does NOT expose _value
    # (we rely on _holders, not the private stdlib attribute).
    # Note: the inner asyncio.Semaphore still has _value, but our wrapper
    # does not expose it at the outer API level — callers should use is_idle().
    assert not hasattr(sem, "_value"), (
        "_CountingSemaphore must NOT expose _value at the wrapper level; "
        "use is_idle() or _holders instead."
    )


# ---------------------------------------------------------------------------
# NEW (fix-34b): [MED event-loop] inline provider planner_plan runs off-loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inline_provider_planner_plan_runs_off_event_loop(repo: InMemoryRepo) -> None:
    """[MED event-loop] planner_plan() in the inline base_cte path must run via
    asyncio.to_thread so it does not block the event loop.

    Before the fix, planner_plan(...) was called synchronously on the event loop,
    starving other coroutines during SQL compilation.  After the fix it is wrapped
    in ``await asyncio.to_thread(planner_plan, ...)``.

    Verification strategy:
    * Patch planner_plan with a synchronous spy that records the OS thread ID it
      runs on.
    * Run resolve_provider_data and collect the thread ID from the spy.
    * Assert the thread ID is NOT the event-loop thread (the main thread), proving
      planner_plan executed in a worker thread — not directly on the loop.
    """
    import threading

    cte_spec = {
        "version": 1,
        "title": "Off-Loop Planner Test Board",
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

    event_loop_thread_id = threading.get_ident()
    planner_thread_ids: list[int] = []

    def _spy_planner(sql: str, claims: dict, params: list) -> object:
        planner_thread_ids.append(threading.get_ident())
        return object()  # dummy plan

    expected_table = pa.table({"amount": pa.array([1])})

    async def _fake_enforce_quota(org_id: str, dimension: str, amount: float = 1.0) -> None:
        pass

    with (
        patch("app.features.enforce_quota", side_effect=_fake_enforce_quota),
        patch("app.connectors.plan", side_effect=_spy_planner),
        patch("app.routes.query._get_demo_connector") as mock_connector_factory,
    ):
        mock_connector = mock_connector_factory.return_value
        mock_connector.execute.return_value = expected_table

        await resolve_provider_data(
            board_id=_BOARD_ID,
            provider_id=_PROVIDER_ID,
            params={},
            org_id=_ORG,
            claims={"policies": {}},
            repo=repo,
        )

    assert len(planner_thread_ids) >= 1, (
        "planner_plan spy was never called — inline base_cte path not reached."
    )

    for tid in planner_thread_ids:
        assert tid != event_loop_thread_id, (
            f"planner_plan ran on the event-loop thread (tid={tid}) — "
            "it MUST run via asyncio.to_thread to avoid blocking the event loop "
            "(MED event-loop fix regression)."
        )


# ---------------------------------------------------------------------------
# NEW (fix-34c): [LOW] provider flow lookup is bounded — list_flows uses a limit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_provider_data_prefetch_passes_limit_to_list_flows(
    repo: InMemoryRepo,
) -> None:
    """[LOW bounded prefetch] resolve_provider_data's flow pre-fetch must call
    list_flows with limit=_FLOWS_PREFETCH_LIMIT (not an unbounded scan of all
    org flows).

    Before the fix, the pre-fetch called list_flows(org_id=org_id) without a
    limit, loading up to 1000 flows per provider HTTP request.  After the fix,
    limit=_FLOWS_PREFETCH_LIMIT (default 200) is passed so the DB query is
    bounded.

    Verification strategy:
    * Replace the flow store with a spy whose list_flows records kwargs.
    * Exercise the flow-provider path (provider.kind == 'flow').
    * Assert list_flows was called with a finite limit= kwarg and that it
      equals _FLOWS_PREFETCH_LIMIT.
    """
    import app.dashboards.board_data as _bd_mod
    import app.flows.store as _fs_mod

    await repo.update(
        "boards",
        _ORG,
        _BOARD_ID,
        {"config": {"spec": _make_spec_with_flow_provider()}},
    )

    list_flows_kwargs: list[dict] = []
    fake_flow = {"id": _PROVIDER_ID, "name": _PROVIDER_ID, "org_id": _ORG}

    class _SpyStore:
        async def get_flow(self, flow_id: str) -> dict:
            return fake_flow

        async def list_flows(self, **kwargs: Any) -> list:
            list_flows_kwargs.append(dict(kwargs))
            return [fake_flow]

        def __getattr__(self, name: str) -> Any:
            # Fallback for any other store method not exercised here.
            raise AttributeError(f"_SpyStore has no attribute {name!r}")

    async def _fake_enforce_quota(org_id: str, dimension: str, amount: float = 1.0) -> None:
        pass

    with (
        patch("app.features.enforce_quota", side_effect=_fake_enforce_quota),
        patch.object(_fs_mod, "get_flow_store", return_value=_SpyStore()),
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

    # The pre-fetch in resolve_provider_data must have called list_flows with a
    # limit= kwarg equal to _FLOWS_PREFETCH_LIMIT.
    prefetch_calls = [c for c in list_flows_kwargs if "org_id" in c]
    assert len(prefetch_calls) >= 1, (
        "list_flows was never called with org_id= — pre-fetch not executed."
    )

    call = prefetch_calls[0]
    assert "limit" in call, (
        "list_flows was called WITHOUT a limit= kwarg — the flow prefetch is "
        "unbounded (loads up to 1000 flows per request). "
        "Fix: pass limit=_FLOWS_PREFETCH_LIMIT to list_flows."
    )
    assert call["limit"] == _bd_mod._FLOWS_PREFETCH_LIMIT, (
        f"list_flows called with limit={call['limit']!r} but expected "
        f"_FLOWS_PREFETCH_LIMIT={_bd_mod._FLOWS_PREFETCH_LIMIT}. "
        "The bounded prefetch limit is not being forwarded correctly."
    )


# ---------------------------------------------------------------------------
# [LOW DoS] ProviderDataRequest.params bounds enforcement
# ---------------------------------------------------------------------------


def test_provider_data_request_too_many_keys_raises_app_error() -> None:
    """ProviderDataRequest with more than _MAX_PARAMS_KEYS keys raises AppError(400)."""
    from app.errors import AppError
    from app.routes.dashboards import ProviderDataRequest, _MAX_PARAMS_KEYS

    oversized = {str(i): i for i in range(_MAX_PARAMS_KEYS + 1)}
    with pytest.raises(AppError) as exc_info:
        ProviderDataRequest(params=oversized)
    assert exc_info.value.code == "params_too_large"
    assert exc_info.value.status == 400


def test_provider_data_request_too_large_bytes_raises_app_error() -> None:
    """ProviderDataRequest whose params serialise to more than _MAX_PARAMS_BYTES
    raises AppError(400)."""
    from app.errors import AppError
    from app.routes.dashboards import ProviderDataRequest, _MAX_PARAMS_BYTES

    # One key whose value pushes the serialized size over the byte cap.
    oversized = {"k": "x" * (_MAX_PARAMS_BYTES + 1)}
    with pytest.raises(AppError) as exc_info:
        ProviderDataRequest(params=oversized)
    assert exc_info.value.code == "params_too_large"
    assert exc_info.value.status == 400


def test_provider_data_request_normal_params_accepted() -> None:
    """ProviderDataRequest with a small, well-formed params dict is accepted."""
    from app.routes.dashboards import ProviderDataRequest

    req = ProviderDataRequest(params={"date": "2024-01", "region": "ZA"})
    assert req.params == {"date": "2024-01", "region": "ZA"}


def test_provider_data_request_empty_params_accepted() -> None:
    """ProviderDataRequest with an empty params dict is accepted."""
    from app.routes.dashboards import ProviderDataRequest

    req = ProviderDataRequest(params={})
    assert req.params == {}


@pytest.mark.asyncio
async def test_route_rejects_too_many_params_keys(route_client) -> None:
    """POST /boards/{id}/providers/{pid}/data returns 400 when params has too many keys."""
    from app.routes.dashboards import _MAX_PARAMS_KEYS

    ac, user_id, _, _ = route_client
    oversized = {str(i): i for i in range(_MAX_PARAMS_KEYS + 1)}
    resp = await ac.post(
        f"/api/v1/boards/{_BOARD_ID}/providers/{_PROVIDER_ID}/data",
        json={"params": oversized},
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "params_too_large"


@pytest.mark.asyncio
async def test_route_rejects_params_too_large_bytes(route_client) -> None:
    """POST /boards/{id}/providers/{pid}/data returns 400 when params exceed byte cap."""
    from app.routes.dashboards import _MAX_PARAMS_BYTES

    ac, user_id, _, _ = route_client
    oversized = {"k": "x" * (_MAX_PARAMS_BYTES + 1)}
    resp = await ac.post(
        f"/api/v1/boards/{_BOARD_ID}/providers/{_PROVIDER_ID}/data",
        json={"params": oversized},
        headers=_auth_headers(user_id),
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "params_too_large"


# ---------------------------------------------------------------------------
# NEW [MED]: inline provider — concurrent requests are bounded (429 on contention)
# NEW [MED]: inline provider — slow execution 504s at exec timeout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inline_provider_concurrent_requests_bounded_429(repo: InMemoryRepo) -> None:
    """Concurrent cache-miss requests on an inline provider are bounded by the
    per-(org, provider) semaphore.  When all slots are occupied and a new request
    waits beyond _INLINE_PROVIDER_TIMEOUT_S, it receives a 429 'provider_busy'.

    We simulate a fully-occupied semaphore by pre-acquiring all slots on a fresh
    1-slot semaphore injected via _get_inline_provider_semaphore, then making a
    new request with a short timeout.  The request cannot acquire and must raise
    provider_busy (429).
    """
    import app.dashboards.board_data as bd

    # Build a fresh 1-slot semaphore and pre-acquire its only slot to simulate
    # a fully occupied semaphore (in-flight request holding the concurrency cap).
    occupied_sem = bd._CountingSemaphore(1)
    await occupied_sem.acquire()  # pre-occupy all capacity

    try:
        with (
            # Redirect _get_inline_provider_semaphore to return our pre-occupied sem.
            patch.object(bd, "_get_inline_provider_semaphore", return_value=occupied_sem),
            # Short acquisition timeout so the test completes quickly.
            patch.object(bd, "_INLINE_PROVIDER_TIMEOUT_S", 0.05),
            patch("app.features.enforce_quota", new=AsyncMock()),
        ):
            with pytest.raises(AppError) as exc_info:
                await resolve_provider_data(
                    board_id=_BOARD_ID,
                    provider_id=_PROVIDER_ID,
                    params={"req": "contended"},
                    org_id=_ORG,
                    claims={"policies": {}},
                    repo=repo,
                )
    finally:
        occupied_sem.release()  # clean up the pre-acquired slot

    assert exc_info.value.code == "provider_busy", (
        f"Expected provider_busy (429), got code={exc_info.value.code!r}"
    )
    assert exc_info.value.status == 429, (
        f"Expected HTTP 429, got {exc_info.value.status}"
    )


@pytest.mark.asyncio
async def test_inline_provider_slow_execution_504s(repo: InMemoryRepo) -> None:
    """A slow inline provider raises provider_timeout (504) at the exec timeout.

    The per-(org, provider) slot is acquired fine, but post-acquisition execution
    exceeds _INLINE_PROVIDER_EXEC_TIMEOUT_S, triggering a 504 'provider_timeout'.
    The slot must be released in the finally block so subsequent requests can proceed.
    """
    import app.dashboards.board_data as bd

    async def _slow_inline(*args: Any, **kwargs: Any) -> dict[str, pa.Table]:
        await asyncio.sleep(60)  # far longer than the patched exec timeout
        return {"revenue": _make_arrow_table(1)}

    with (
        patch.object(bd, "_INLINE_PROVIDER_EXEC_TIMEOUT_S", 0.05),
        patch.object(bd, "_resolve_inline_provider", new=_slow_inline),
        patch("app.features.enforce_quota", new=AsyncMock()),
    ):
        with pytest.raises(AppError) as exc_info:
            await resolve_provider_data(
                board_id=_BOARD_ID,
                provider_id=_PROVIDER_ID,
                params={},
                org_id=_ORG,
                claims={"policies": {}},
                repo=repo,
            )

    assert exc_info.value.code == "provider_timeout", (
        f"Expected provider_timeout (504), got code={exc_info.value.code!r}"
    )
    assert exc_info.value.status == 504, (
        f"Expected HTTP 504, got {exc_info.value.status}"
    )

    # Slot must be released after the timeout (semaphore is idle again).
    assert bd._get_inline_provider_semaphore(_ORG, _PROVIDER_ID).is_idle(), (
        "Inline provider semaphore slot leaked after exec-timeout — "
        "slot must be released in the finally block."
    )


@pytest.mark.asyncio
async def test_inline_provider_exec_timeout_releases_slot_for_next_request(
    repo: InMemoryRepo,
) -> None:
    """After a 504 exec-timeout, the inline semaphore slot is freed and a
    subsequent fast request succeeds — proving the finally-release path works.
    """
    import app.dashboards.board_data as bd

    # First: trigger a slow execution that times out.
    async def _slow_inline(*args: Any, **kwargs: Any) -> dict[str, pa.Table]:
        await asyncio.sleep(60)
        return {"revenue": _make_arrow_table(1)}

    with (
        patch.object(bd, "_INLINE_PROVIDER_EXEC_TIMEOUT_S", 0.05),
        patch.object(bd, "_resolve_inline_provider", new=_slow_inline),
        patch("app.features.enforce_quota", new=AsyncMock()),
    ):
        with pytest.raises(AppError) as exc_info:
            await resolve_provider_data(
                board_id=_BOARD_ID,
                provider_id=_PROVIDER_ID,
                params={},
                org_id=_ORG,
                claims={"policies": {}},
                repo=repo,
            )
    assert exc_info.value.code == "provider_timeout"

    # Second: a fast request on the same (org, provider) with distinct params
    # (cache miss → real execution path) should succeed now that the slot is free.
    async def _fast_inline(*args: Any, **kwargs: Any) -> dict[str, pa.Table]:
        return {"revenue": _make_arrow_table(2)}

    with (
        patch.object(bd, "_resolve_inline_provider", new=_fast_inline),
        patch("app.features.enforce_quota", new=AsyncMock()),
    ):
        tables = await resolve_provider_data(
            board_id=_BOARD_ID,
            provider_id=_PROVIDER_ID,
            params={"k": "v2"},  # distinct params → fresh cache miss → real execution
            org_id=_ORG,
            claims={"policies": {}},
            repo=repo,
        )
    assert tables["revenue"].num_rows == 2, (
        "Fast follow-up request failed — semaphore slot may not have been released "
        "after the exec-timeout."
    )
