"""Tests for N+1 datastore lookup elimination in collect_board_data.

The fix pre-fetches all unique datastore ids referenced by widgets in a
single asyncio.gather BEFORE the per-widget gather, building a request-scoped
{ds_id: row} cache.  These tests assert that a board with N widgets all
sharing one datastore issues exactly 1 (not N) ``repo.get("datastores", …)``
call via a counting repo.

Covers:
1. collect_board_data: N widgets on the same datastore → 1 datastore lookup.
2. collect_board_data: N widgets on K distinct datastores → K datastore lookups.
3. Zero-datastore board (demo queries only) → 0 datastore lookups.
"""

from __future__ import annotations

import asyncio
import os
import types
import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef")

import pytest

from app.dashboards.collect import collect_board_data
from app.queries.registry import get_query_registry
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_board(org_id: str, widgets: list[dict]) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "org_id": org_id,
        "name": "Test Board",
        "config": {"spec": {"title": "Test Board", "widgets": widgets}},
    }


class _CountingRepo(InMemoryRepo):
    """InMemoryRepo that counts calls to repo.get by (resource, id)."""

    def __init__(self) -> None:
        super().__init__()
        self.get_calls: list[tuple[str, str, str]] = []

    async def get(self, resource: str, org_id: str, id: str) -> dict[str, Any] | None:
        self.get_calls.append((resource, org_id, str(id)))
        return await super().get(resource, org_id, id)

    def datastore_get_count(self) -> int:
        return sum(1 for r, _o, _i in self.get_calls if r == "datastores")

    def datastore_get_ids(self) -> list[str]:
        return [i for r, _o, i in self.get_calls if r == "datastores"]


def _fake_query(query_id: str, datastore_id: str | None = None) -> Any:
    return types.SimpleNamespace(
        id=query_id,
        sql="SELECT 1",
        params=[],
        datastore_id=datastore_id,
    )


def _mock_run_query_rows_factory():
    """Return an AsyncMock for run_query_rows that returns a trivial result."""
    mock = AsyncMock(return_value=(["v"], [[1]]))
    return mock


# ---------------------------------------------------------------------------
# 1. N widgets on same datastore → 1 datastore lookup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_collect_board_data_n_widgets_same_datastore_one_lookup():
    """Board with 5 widgets all using the same datastore → exactly 1 repo.get(datastores)."""
    repo = _CountingRepo()
    set_repo(repo)

    org_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    ds_id = str(uuid.uuid4())
    repo.seed_org_member(org_id=org_id, user_id=user_id)

    # Seed the datastore so it can be found.
    await repo.create(
        "datastores",
        org_id=org_id,
        created_by=user_id,
        name="Shared DS",
        id=ds_id,
        config={"type": "duckdb"},
    )

    # 5 widgets all with the same query_id (all reference ds_id via registry).
    query_id = f"q_{uuid.uuid4().hex}"
    widgets = [{"id": f"w{i}", "query_id": query_id} for i in range(5)]
    board = _make_board(org_id, widgets)

    registry = get_query_registry()
    fake_q = _fake_query(query_id, datastore_id=ds_id)

    try:
        with (
            patch.object(registry, "get", side_effect=lambda qid: fake_q if qid == query_id else None),
            patch(
                "app.dashboards.collect.run_query_rows",
                new=AsyncMock(return_value=(["v"], [[1]])),
            ),
        ):
            await collect_board_data(
                board_id=board["id"],
                org_id=org_id,
                claims={},
                repo=repo,
                board=board,
            )
    finally:
        set_repo(None)

    ds_ids_fetched = repo.datastore_get_ids()
    assert repo.datastore_get_count() == 1, (
        f"Expected exactly 1 datastore lookup for 5 widgets sharing one ds, "
        f"got {repo.datastore_get_count()} (ids fetched: {ds_ids_fetched})"
    )
    assert ds_ids_fetched == [ds_id], (
        f"Expected ds_id={ds_id!r} to be fetched once, got: {ds_ids_fetched}"
    )


# ---------------------------------------------------------------------------
# 2. N widgets on K distinct datastores → K datastore lookups
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_collect_board_data_k_distinct_datastores_k_lookups():
    """Board with widgets across 3 distinct datastores → exactly 3 repo.get(datastores)."""
    repo = _CountingRepo()
    set_repo(repo)

    org_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    repo.seed_org_member(org_id=org_id, user_id=user_id)

    ds_ids = [str(uuid.uuid4()) for _ in range(3)]
    for i, ds_id in enumerate(ds_ids):
        await repo.create(
            "datastores",
            org_id=org_id,
            created_by=user_id,
            name=f"DS-{i}",
            id=ds_id,
            config={"type": "duckdb"},
        )

    # 2 widgets per datastore (6 total), 3 distinct ds_ids.
    query_ids = [f"q_{i}_{uuid.uuid4().hex}" for i in range(3)]
    widgets = []
    for i, (qid, _) in enumerate(zip(query_ids, ds_ids)):
        widgets.append({"id": f"w{i}a", "query_id": qid})
        widgets.append({"id": f"w{i}b", "query_id": qid})
    board = _make_board(org_id, widgets)

    registry = get_query_registry()
    fake_qs = {qid: _fake_query(qid, ds_id) for qid, ds_id in zip(query_ids, ds_ids)}

    try:
        with (
            patch.object(registry, "get", side_effect=lambda qid: fake_qs.get(qid)),
            patch(
                "app.dashboards.collect.run_query_rows",
                new=AsyncMock(return_value=(["v"], [[1]])),
            ),
        ):
            await collect_board_data(
                board_id=board["id"],
                org_id=org_id,
                claims={},
                repo=repo,
                board=board,
            )
    finally:
        set_repo(None)

    assert repo.datastore_get_count() == 3, (
        f"Expected exactly 3 datastore lookups (one per unique ds), "
        f"got {repo.datastore_get_count()} (ids: {repo.datastore_get_ids()})"
    )
    assert set(repo.datastore_get_ids()) == set(ds_ids), (
        f"Expected each ds fetched once; got: {repo.datastore_get_ids()}"
    )


# ---------------------------------------------------------------------------
# 3. Zero-datastore board (demo queries) → 0 datastore lookups
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_collect_board_data_demo_queries_no_datastore_lookup():
    """Board with demo (no-datastore) queries issues 0 datastore lookups."""
    repo = _CountingRepo()
    set_repo(repo)

    org_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    repo.seed_org_member(org_id=org_id, user_id=user_id)

    query_id = f"demo_{uuid.uuid4().hex}"
    widgets = [{"id": f"w{i}", "query_id": query_id} for i in range(4)]
    board = _make_board(org_id, widgets)

    registry = get_query_registry()
    # datastore_id=None → demo connector
    fake_q = _fake_query(query_id, datastore_id=None)

    try:
        with (
            patch.object(registry, "get", side_effect=lambda qid: fake_q if qid == query_id else None),
            patch(
                "app.dashboards.collect.run_query_rows",
                new=AsyncMock(return_value=(["v"], [[1]])),
            ),
        ):
            await collect_board_data(
                board_id=board["id"],
                org_id=org_id,
                claims={},
                repo=repo,
                board=board,
            )
    finally:
        set_repo(None)

    assert repo.datastore_get_count() == 0, (
        f"Expected 0 datastore lookups for demo queries, "
        f"got {repo.datastore_get_count()} (ids: {repo.datastore_get_ids()})"
    )


# ---------------------------------------------------------------------------
# 4. _prefetch_datastores concurrency never exceeds _PREFETCH_CONCURRENCY cap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prefetch_datastores_concurrency_capped():
    """_prefetch_datastores never runs more than _PREFETCH_CONCURRENCY fetches simultaneously.

    Strategy: instrument repo.get("datastores", …) with an async mock that
    tracks the current in-flight count.  With NUBI_PREFETCH_CONCURRENCY forced
    to a small cap (3) and 20 distinct datastores, the observed peak must never
    exceed the cap.
    """
    import app.dashboards.collect as collect_mod

    original_cap = collect_mod._PREFETCH_CONCURRENCY
    test_cap = 3
    collect_mod._PREFETCH_CONCURRENCY = test_cap

    try:
        peak_concurrent: list[int] = [0]
        in_flight: list[int] = [0]

        # Build 20 distinct ds_ids, each backed by a real row so the cache
        # includes them (rows that return None are omitted, which would still
        # exercise the semaphore but would make assertions about cache size
        # fragile).
        n_ds = 20
        org_id = str(uuid.uuid4())
        ds_ids = [str(uuid.uuid4()) for _ in range(n_ds)]
        ds_rows = {ds_id: {"id": ds_id, "org_id": org_id} for ds_id in ds_ids}

        async def _tracked_get(resource, _org_id, ds_id):
            in_flight[0] += 1
            if in_flight[0] > peak_concurrent[0]:
                peak_concurrent[0] = in_flight[0]
            # yield to the event loop so other coroutines can start and the
            # semaphore contention is actually exercised.
            await asyncio.sleep(0)
            in_flight[0] -= 1
            return ds_rows.get(ds_id)

        mock_repo = AsyncMock()
        mock_repo.get = _tracked_get

        cache = await collect_mod._prefetch_datastores(set(ds_ids), org_id, mock_repo)

        assert len(cache) == n_ds, (
            f"Expected all {n_ds} ds_ids in cache, got {len(cache)}"
        )
        assert peak_concurrent[0] <= test_cap, (
            f"Concurrency exceeded the cap: peak={peak_concurrent[0]}, cap={test_cap}. "
            "The semaphore in _prefetch_datastores is not working correctly."
        )
    finally:
        collect_mod._PREFETCH_CONCURRENCY = original_cap
