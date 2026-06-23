"""Tests that execute + to_pylist + row-comprehension happen off the event loop.

The fix in collect.py moves the Arrow → Python conversion (to_pylist + row
comprehension) inside the asyncio.to_thread call via _execute_and_convert /
_execute_and_convert_api helpers, so the event loop is NEVER blocked by CPU-
intensive conversion of large result sets.

Covered:
1. _execute_and_convert returns (columns, rows) and runs synchronously (the
   entire execute + slice + to_pylist + comprehension is inside one callable
   that asyncio.to_thread can call off-loop).
2. run_query_rows delegates to a single asyncio.to_thread call and returns
   (columns, rows) correctly — no post-to_thread conversion on the event loop.
3. _execute_and_convert respects the row cap: truncation happens inside the
   helper (off-loop), not after it returns.
4. _execute_and_convert_api works the same way for Canvas API bindings.
5. run_query_rows with _execute_and_convert: mock verifies exactly one
   to_thread call containing the conversion helper (not two separate calls).
"""

from __future__ import annotations

import asyncio
import os
import threading
import unittest.mock as mock

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef")

from typing import Any

import pyarrow as pa
import pytest

import app.dashboards.collect as _collect
from app.dashboards.collect import (
    _execute_and_convert,
    _execute_and_convert_api,
    run_query_rows,
)
from app.queries.registry import get_query_registry
from app.queries.registry import reset_for_tests as reset_query_registry
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo

_ORG = "org-offloop-test"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def repo() -> InMemoryRepo:
    reset_query_registry()
    r = InMemoryRepo()
    set_repo(r)
    yield r
    set_repo(None)
    reset_query_registry()


# ---------------------------------------------------------------------------
# 1. _execute_and_convert is a pure synchronous callable returning (cols, rows)
# ---------------------------------------------------------------------------


def test_execute_and_convert_returns_columns_and_rows() -> None:
    """_execute_and_convert must return (list[str], list[list]) from an Arrow table."""
    table = pa.table({"id": [1, 2, 3], "val": [10.0, 20.0, 30.0]})

    class _FakeConnector:
        def execute(self, plan: object) -> pa.Table:  # noqa: ANN001
            return table

    connector = _FakeConnector()
    columns, rows = _execute_and_convert(connector, object(), cap=0, query_id="q-test")

    assert columns == ["id", "val"]
    assert rows == [[1, 10.0], [2, 20.0], [3, 30.0]]


def test_execute_and_convert_is_synchronous() -> None:
    """_execute_and_convert must be a plain (non-async) callable.

    asyncio.to_thread requires a synchronous function — if it were a coroutine
    the conversion would silently not run on the worker thread.
    """
    import inspect

    assert not inspect.iscoroutinefunction(_execute_and_convert), (
        "_execute_and_convert must be a plain synchronous function so that "
        "asyncio.to_thread runs it on a worker thread."
    )


# ---------------------------------------------------------------------------
# 2. _execute_and_convert respects the row cap (truncation off-loop)
# ---------------------------------------------------------------------------


def test_execute_and_convert_truncates_to_cap() -> None:
    """_execute_and_convert must slice to cap before to_pylist() (off-loop)."""
    big_table = pa.table({"x": list(range(20))})

    class _FakeConnector:
        def execute(self, plan: object) -> pa.Table:
            return big_table

    connector = _FakeConnector()
    columns, rows = _execute_and_convert(connector, object(), cap=5, query_id="q-big")

    assert len(rows) == 5, f"Expected 5 rows after cap=5, got {len(rows)}"
    assert rows[0] == [0]
    assert rows[4] == [4]


def test_execute_and_convert_cap_zero_means_unlimited() -> None:
    """cap=0 must return all rows without truncation."""
    table = pa.table({"n": list(range(50))})

    class _FakeConnector:
        def execute(self, plan: object) -> pa.Table:
            return table

    connector = _FakeConnector()
    columns, rows = _execute_and_convert(connector, object(), cap=0, query_id="q-unlimited")

    assert len(rows) == 50


# ---------------------------------------------------------------------------
# 3. _execute_and_convert_api mirrors the same contract
# ---------------------------------------------------------------------------


def test_execute_and_convert_api_returns_columns_and_rows() -> None:
    """_execute_and_convert_api must return (cols, rows) for Canvas API bindings."""
    table = pa.table({"name": ["alice", "bob"], "score": [99, 88]})

    class _FakeConnector:
        def execute(self, plan: object) -> pa.Table:
            return table

    connector = _FakeConnector()
    columns, rows = _execute_and_convert_api(connector, object(), cap=0, el_id="el-1")

    assert columns == ["name", "score"]
    assert rows == [["alice", 99], ["bob", 88]]


def test_execute_and_convert_api_is_synchronous() -> None:
    """_execute_and_convert_api must be a plain synchronous function."""
    import inspect

    assert not inspect.iscoroutinefunction(_execute_and_convert_api), (
        "_execute_and_convert_api must be a plain synchronous function."
    )


def test_execute_and_convert_api_truncates_to_cap() -> None:
    """_execute_and_convert_api must truncate to cap inside the worker thread."""
    table = pa.table({"v": list(range(15))})

    class _FakeConnector:
        def execute(self, plan: object) -> pa.Table:
            return table

    connector = _FakeConnector()
    columns, rows = _execute_and_convert_api(connector, object(), cap=7, el_id="el-api")

    assert len(rows) == 7


# ---------------------------------------------------------------------------
# 4. run_query_rows delegates to a single to_thread call (off-loop conversion)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_query_rows_uses_single_to_thread_for_conversion(
    repo: InMemoryRepo,
) -> None:
    """run_query_rows must perform execute + conversion in one asyncio.to_thread call.

    Before the fix, execute() was in to_thread but to_pylist() + the row
    comprehension ran inline on the event loop.  After the fix, both are
    inside _execute_and_convert which is the sole argument passed to to_thread.

    This test patches asyncio.to_thread to capture the callable passed to it
    and verifies that it is _execute_and_convert (not _execute_with_lock),
    proving the conversion is also offloaded.
    """
    reg = get_query_registry()
    reg.register(id="q-offloop", sql="SELECT id FROM demo ORDER BY id", name="OffLoop")

    # Track what callable was passed to asyncio.to_thread.
    to_thread_callables: list[Any] = []
    original_to_thread = asyncio.to_thread

    async def _spy_to_thread(func: Any, *args: Any, **kwargs: Any) -> Any:
        to_thread_callables.append(func)
        # Run the actual call so the result is correct.
        return await original_to_thread(func, *args, **kwargs)

    with mock.patch(
        "app.dashboards.collect._resolve_connector",
        return_value=(
            type(
                "_FC",
                (),
                {
                    "execute": lambda self, p: pa.table({"id": [1, 2]}),
                    "close": lambda self: None,
                },
            )(),
            False,
        ),
    ), mock.patch("asyncio.to_thread", side_effect=_spy_to_thread):
        columns, rows = await run_query_rows("q-offloop", _ORG, repo, {})

    assert columns == ["id"]
    assert rows == [[1], [2]]

    # Must have called asyncio.to_thread exactly once with _execute_and_convert.
    assert len(to_thread_callables) == 1, (
        f"Expected exactly 1 asyncio.to_thread call, got {len(to_thread_callables)}: "
        f"{to_thread_callables}"
    )
    assert to_thread_callables[0] is _collect._execute_and_convert, (
        "The single asyncio.to_thread call must use _execute_and_convert "
        "(which includes both execute + to_pylist + row comprehension), "
        f"but got: {to_thread_callables[0]!r}"
    )


# ---------------------------------------------------------------------------
# 5. Per-connector lock is still held during execution (thread-safety preserved)
# ---------------------------------------------------------------------------


def test_execute_and_convert_holds_connector_lock_during_execute() -> None:
    """The per-connector threading.Lock must be held while execute() runs.

    DuckDB connections are not thread-safe; the lock ensures at most one thread
    uses a given connection at a time.  This test verifies the lock is HELD
    (not released) during execute(), not just acquired and immediately released.
    """
    lock_held_during_execute = []

    class _LockSpyConnector:
        def execute(self, plan: object) -> pa.Table:
            # Check if the connector's lock is currently locked.
            lock = _collect._get_connector_lock(self)
            lock_held_during_execute.append(lock.locked())
            return pa.table({"x": [1]})

    connector = _LockSpyConnector()
    _execute_and_convert(connector, object(), cap=0)

    assert lock_held_during_execute == [True], (
        "The per-connector threading.Lock must be HELD while connector.execute() "
        f"runs; lock.locked() during execute: {lock_held_during_execute}"
    )
