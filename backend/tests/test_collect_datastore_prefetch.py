"""Tests for N+1 datastore lookup elimination in collect_board_data / collect_canvas_data.

The fix pre-fetches all unique datastore ids referenced by widgets/bindings in a
single asyncio.gather BEFORE the per-widget gather, building a request-scoped
{ds_id: row} cache.  These tests assert that a board/canvas with N widgets all
sharing one datastore issues exactly 1 (not N) ``repo.get("datastores", …)``
call via a counting repo.

Covers:
1. collect_board_data: N widgets on the same datastore → 1 datastore lookup.
2. collect_board_data: N widgets on K distinct datastores → K datastore lookups.
3. collect_canvas_data (query bindings): N bindings on the same datastore → 1 lookup.
4. collect_canvas_data (api bindings):  N bindings on the same datastore → 1 lookup.
5. collect_canvas_data: mixed api+query bindings sharing datastores → bounded lookups.
6. Zero-datastore board (demo queries only) → 0 datastore lookups.
"""

from __future__ import annotations

import os
import types
import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef")

import pytest

from app.dashboards.collect import collect_board_data, collect_canvas_data
from app.errors import AppError
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


def _make_canvas(org_id: str, bindings: dict) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "org_id": org_id,
        "name": "Test Canvas",
        "config": {
            "doc": {
                "version": 1,
                "html": "<div></div>",
                "bindings": bindings,
            }
        },
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
# 3. collect_canvas_data (query bindings) N bindings → 1 lookup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_collect_canvas_data_query_bindings_shared_datastore_one_lookup():
    """Canvas with 4 query bindings on the same datastore → exactly 1 datastore lookup."""
    repo = _CountingRepo()
    set_repo(repo)

    org_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    ds_id = str(uuid.uuid4())
    repo.seed_org_member(org_id=org_id, user_id=user_id)

    await repo.create(
        "datastores",
        org_id=org_id,
        created_by=user_id,
        name="Shared DS",
        id=ds_id,
        config={"type": "duckdb"},
    )

    query_id = f"q_{uuid.uuid4().hex}"
    bindings = {
        f"el_{i}": {"kind": "query", "query_id": query_id}
        for i in range(4)
    }
    canvas = _make_canvas(org_id, bindings)

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
            await collect_canvas_data(
                canvas_id=canvas["id"],
                org_id=org_id,
                claims={},
                repo=repo,
                canvas=canvas,
            )
    finally:
        set_repo(None)

    assert repo.datastore_get_count() == 1, (
        f"Expected 1 datastore lookup for 4 query bindings sharing one ds, "
        f"got {repo.datastore_get_count()} (ids: {repo.datastore_get_ids()})"
    )


# ---------------------------------------------------------------------------
# 4. collect_canvas_data (api bindings) N bindings → 1 lookup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_collect_canvas_data_api_bindings_shared_datastore_one_lookup():
    """Canvas with 3 api bindings on the same connector → exactly 1 datastore lookup."""
    import pyarrow as pa

    repo = _CountingRepo()
    set_repo(repo)

    org_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    conn_id = str(uuid.uuid4())
    repo.seed_org_member(org_id=org_id, user_id=user_id)

    await repo.create(
        "datastores",
        org_id=org_id,
        created_by=user_id,
        name="Shared API DS",
        id=conn_id,
        config={"type": "http_json", "url": "http://api.example.com"},
    )

    bindings = {
        f"el_{i}": {"kind": "api", "connector_id": conn_id, "path": f"/v{i}"}
        for i in range(3)
    }
    canvas = _make_canvas(org_id, bindings)

    class _MockHttpConn:
        def execute(self, plan):
            return pa.table({"v": [1]})

        def capabilities(self):
            return {"predicate_rls": True}

        def close(self):
            pass

    try:
        with patch(
            "app.connectors.http_json.HttpJsonConnector",
            return_value=_MockHttpConn(),
        ):
            await collect_canvas_data(
                canvas_id=canvas["id"],
                org_id=org_id,
                claims={},
                repo=repo,
                canvas=canvas,
            )
    finally:
        set_repo(None)

    assert repo.datastore_get_count() == 1, (
        f"Expected 1 datastore lookup for 3 api bindings sharing one connector, "
        f"got {repo.datastore_get_count()} (ids: {repo.datastore_get_ids()})"
    )


# ---------------------------------------------------------------------------
# 5. Mixed api+query bindings, shared datastores → bounded (unique count)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_collect_canvas_data_mixed_bindings_bounded_lookups():
    """Canvas with mixed api+query bindings across 2 shared datastores → 2 lookups."""
    import pyarrow as pa

    repo = _CountingRepo()
    set_repo(repo)

    org_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    api_ds_id = str(uuid.uuid4())
    query_ds_id = str(uuid.uuid4())
    repo.seed_org_member(org_id=org_id, user_id=user_id)

    await repo.create(
        "datastores",
        org_id=org_id,
        created_by=user_id,
        name="API DS",
        id=api_ds_id,
        config={"type": "http_json", "url": "http://api.example.com"},
    )
    await repo.create(
        "datastores",
        org_id=org_id,
        created_by=user_id,
        name="Query DS",
        id=query_ds_id,
        config={"type": "duckdb"},
    )

    query_id = f"q_{uuid.uuid4().hex}"
    # 2 api bindings + 3 query bindings, all sharing 2 datastores.
    bindings = {
        "api_a": {"kind": "api", "connector_id": api_ds_id, "path": "/a"},
        "api_b": {"kind": "api", "connector_id": api_ds_id, "path": "/b"},
        "q_1": {"kind": "query", "query_id": query_id},
        "q_2": {"kind": "query", "query_id": query_id},
        "q_3": {"kind": "query", "query_id": query_id},
    }
    canvas = _make_canvas(org_id, bindings)

    registry = get_query_registry()
    fake_q = _fake_query(query_id, datastore_id=query_ds_id)

    class _MockHttpConn:
        def execute(self, plan):
            return pa.table({"v": [1]})

        def capabilities(self):
            return {"predicate_rls": True}

        def close(self):
            pass

    try:
        with (
            patch.object(registry, "get", side_effect=lambda qid: fake_q if qid == query_id else None),
            patch(
                "app.dashboards.collect.run_query_rows",
                new=AsyncMock(return_value=(["v"], [[1]])),
            ),
            patch(
                "app.connectors.http_json.HttpJsonConnector",
                return_value=_MockHttpConn(),
            ),
        ):
            await collect_canvas_data(
                canvas_id=canvas["id"],
                org_id=org_id,
                claims={},
                repo=repo,
                canvas=canvas,
            )
    finally:
        set_repo(None)

    fetched_ids = set(repo.datastore_get_ids())
    assert repo.datastore_get_count() == 2, (
        f"Expected exactly 2 datastore lookups (one per unique ds), "
        f"got {repo.datastore_get_count()} (ids: {repo.datastore_get_ids()})"
    )
    assert fetched_ids == {api_ds_id, query_ds_id}, (
        f"Expected both datastores fetched; got: {fetched_ids}"
    )


# ---------------------------------------------------------------------------
# 6. Zero-datastore board (demo queries) → 0 datastore lookups
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
