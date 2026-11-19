"""Per-request lookup memo (app/request_memo.py).

A board tile's query used to ask the database "is this user in this org?" four
times and read the datastore row twice. These tests pin down that the repeats
are gone inside one request and that nothing is shared between requests or
outside them.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app import request_memo
from app.request_memo import RequestMemoMiddleware, memoized


async def _in_request(fn):
    """Run *fn* the way a route runs it: with a fresh request memo."""
    token = request_memo._memo.set({})
    try:
        return await fn()
    finally:
        request_memo._memo.reset(token)


@pytest.mark.asyncio
async def test_outside_a_request_every_call_loads():
    load = AsyncMock(return_value=1)
    assert await memoized("k", load) == 1
    assert await memoized("k", load) == 1
    assert load.await_count == 2


@pytest.mark.asyncio
async def test_inside_a_request_a_key_loads_once():
    load = AsyncMock(return_value={"a": 1})

    async def body():
        first = await memoized("k", load)
        second = await memoized("k", load)
        other = await memoized("other", load)
        return first, second, other

    first, second, other = await _in_request(body)
    assert first == second == other == {"a": 1}
    assert load.await_count == 2  # "k" once, "other" once


@pytest.mark.asyncio
async def test_a_failed_load_is_not_remembered():
    load = AsyncMock(side_effect=[RuntimeError("db down"), "ok"])

    async def body():
        with pytest.raises(RuntimeError):
            await memoized("k", load)
        return await memoized("k", load)

    assert await _in_request(body) == "ok"
    assert load.await_count == 2


@pytest.mark.asyncio
async def test_callers_get_copies_not_the_remembered_row():
    load = AsyncMock(return_value={"config": {"type": "bigquery"}})

    async def body():
        row = await memoized("k", load)
        row["config"]["type"] = "changed by caller"
        return await memoized("k", load)

    assert (await _in_request(body))["config"]["type"] == "bigquery"


@pytest.mark.asyncio
async def test_middleware_gives_each_request_its_own_memo():
    load = AsyncMock(return_value="member")

    async def endpoint(request):
        await memoized("k", load)
        await memoized("k", load)
        return JSONResponse({"ok": True})

    app = RequestMemoMiddleware(Starlette(routes=[Route("/", endpoint)]))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/")).status_code == 200
        assert (await client.get("/")).status_code == 200
    # Once per request, never shared across the two requests.
    assert load.await_count == 2
    # And nothing leaks out of the requests into the surrounding context.
    assert request_memo._memo.get() is None


@pytest.mark.asyncio
async def test_membership_check_queries_once_per_request():
    from app.routes import _org

    repo = object()  # a PgRepo stand-in: no in-memory member map
    fetch = AsyncMock(return_value={"?column?": 1})
    with patch.object(_org, "fetchrow", fetch):

        async def body():
            return [await _org._user_is_member("u1", "o1", repo) for _ in range(4)]

        assert await _in_request(body) == [True, True, True, True]
        assert fetch.await_count == 1

        # A "no" is asked again (the same request may add the membership).
        fetch.reset_mock()
        fetch.return_value = None

        async def no_then_yes():
            first = await _org._user_is_member("u1", "o2", repo)
            fetch.return_value = {"?column?": 1}
            return first, await _org._user_is_member("u1", "o2", repo)

        assert await _in_request(no_then_yes) == (False, True)
        assert fetch.await_count == 2

        # Outside a request nothing is remembered.
        fetch.reset_mock()
        fetch.return_value = {"?column?": 1}
        await _org._user_is_member("u1", "o1", repo)
        await _org._user_is_member("u1", "o1", repo)
        assert fetch.await_count == 2


@pytest.mark.asyncio
async def test_datastore_row_read_once_per_request():
    from app.routes.query import _datastore_row

    repo = MagicMock()
    repo.get = AsyncMock(return_value={"id": "d1", "config": {"type": "bigquery"}})

    async def body():
        a = await _datastore_row(repo, "o1", "d1")
        b = await _datastore_row(repo, "o1", "d1")
        return a, b

    a, b = await _in_request(body)
    assert a == b == {"id": "d1", "config": {"type": "bigquery"}}
    repo.get.assert_awaited_once_with("datastores", "o1", "d1")
