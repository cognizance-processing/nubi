"""Test that collect_board_data skips the repo lookup when board= is supplied.

Covers:
1. collect_board_data(board=<row>) does NOT call repo.get for the board.
2. collect_board_data(board=None) still calls repo.get (existing behaviour).
3. collect_board_data(board=<missing-row>→None) still raises board_not_found.
4. render_board_svg_from_data passes its pre-fetched board to collect_board_data,
   so repo.get("boards", ...) is called exactly once per render invocation.
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef")

import asyncio
import unittest.mock as mock
from typing import Any

import pytest

from app.dashboards.collect import collect_board_data
from app.errors import AppError
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo

_ORG = "org-prefetch-test"
_BOARD_ID = "board-1"

_BOARD_ROW: dict[str, Any] = {
    "id": _BOARD_ID,
    "org_id": _ORG,
    "name": "Test board",
    "config": {"spec": {"title": "Test board", "widgets": []}},
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def empty_repo() -> InMemoryRepo:
    """An InMemoryRepo with the test board pre-seeded."""
    r = InMemoryRepo()
    set_repo(r)

    async def _seed() -> None:
        await r.create(
            "boards",
            org_id=_ORG,
            created_by="test",
            name=_BOARD_ROW["name"],
            config=_BOARD_ROW["config"],
            id=_BOARD_ID,
        )

    asyncio.run(_seed())
    yield r
    set_repo(None)


# ---------------------------------------------------------------------------
# 1. board= supplied → repo.get("boards") is NOT called
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_collect_board_data_skips_repo_when_board_provided(
    empty_repo: InMemoryRepo,
) -> None:
    """When board= is passed, collect_board_data must not call repo.get for boards."""
    get_calls: list[tuple] = []
    original_get = empty_repo.get

    async def _spy_get(table: str, *args: Any, **kwargs: Any) -> Any:
        get_calls.append((table,) + args)
        return await original_get(table, *args, **kwargs)

    with mock.patch.object(empty_repo, "get", side_effect=_spy_get):
        result = await collect_board_data(
            _BOARD_ID,
            _ORG,
            claims={},
            repo=empty_repo,
            board=_BOARD_ROW,
        )

    board_fetches = [c for c in get_calls if c[0] == "boards"]
    assert board_fetches == [], (
        "repo.get('boards') must NOT be called when board= is supplied; "
        f"got: {board_fetches}"
    )
    # No widgets → empty result list.
    assert result == []


# ---------------------------------------------------------------------------
# 2. board=None (default) → repo.get("boards") IS called
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_collect_board_data_fetches_board_when_not_provided(
    empty_repo: InMemoryRepo,
) -> None:
    """When board= is omitted, collect_board_data must call repo.get for the board."""
    get_calls: list[tuple] = []
    original_get = empty_repo.get

    async def _spy_get(table: str, *args: Any, **kwargs: Any) -> Any:
        get_calls.append((table,) + args)
        return await original_get(table, *args, **kwargs)

    with mock.patch.object(empty_repo, "get", side_effect=_spy_get):
        result = await collect_board_data(
            _BOARD_ID,
            _ORG,
            claims={},
            repo=empty_repo,
            # board not passed — should trigger the fetch
        )

    board_fetches = [c for c in get_calls if c[0] == "boards"]
    assert len(board_fetches) == 1, (
        "repo.get('boards') must be called exactly once when board= is not supplied; "
        f"got: {board_fetches}"
    )
    assert result == []


# ---------------------------------------------------------------------------
# 3. board= is None (explicit) and board not found → board_not_found raised
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_collect_board_data_raises_when_board_none_and_not_found() -> None:
    """When board=None and repo returns None, AppError('board_not_found') is raised."""
    r = InMemoryRepo()
    set_repo(r)
    try:
        with pytest.raises(AppError) as exc_info:
            await collect_board_data(
                "nonexistent-board",
                _ORG,
                claims={},
                repo=r,
            )
        assert exc_info.value.code == "board_not_found"
    finally:
        set_repo(None)


# ---------------------------------------------------------------------------
# 4. render_board_svg_from_data calls repo.get("boards") exactly once
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_board_svg_from_data_single_board_fetch(
    empty_repo: InMemoryRepo,
) -> None:
    """render_board_svg_from_data must fetch the board row only once (not twice)."""
    from app.dashboards.svg_render import render_board_svg_from_data

    get_calls: list[tuple] = []
    original_get = empty_repo.get

    async def _spy_get(table: str, *args: Any, **kwargs: Any) -> Any:
        get_calls.append((table,) + args)
        return await original_get(table, *args, **kwargs)

    # Patch render_board_svg to avoid needing Node/ECharts in unit tests.
    with mock.patch.object(empty_repo, "get", side_effect=_spy_get), mock.patch(
        "app.dashboards.svg_render.render_board_svg",
        return_value="<svg/>",
    ):
        result = await render_board_svg_from_data(
            board_id=_BOARD_ID,
            org_id=_ORG,
            claims={},
            repo=empty_repo,
        )

    assert result == "<svg/>"
    board_fetches = [c for c in get_calls if c[0] == "boards"]
    assert len(board_fetches) == 1, (
        "render_board_svg_from_data must call repo.get('boards') exactly once; "
        f"got {len(board_fetches)} call(s): {board_fetches}"
    )
