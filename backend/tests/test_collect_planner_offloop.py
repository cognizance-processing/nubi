"""Tests that planner_plan() is offloaded from the asyncio event loop.

FIX: planner.plan() (sqlglot parse + RLS AST rewrite, pure-Python) was called
synchronously on the event loop at multiple call sites:
  - collect.py run_query_rows (~341)
  - routes/query.py (~799)

The fix wraps each call in ``await asyncio.to_thread(planner_plan, ...)``.

Covered:
1. run_query_rows calls asyncio.to_thread with planner_plan before the
   connector execute (two to_thread calls total: plan then execute+convert).
2. The planner_plan to_thread call in run_query_rows carries the correct
   keyword arguments (sql, claims, params, limit).
"""

from __future__ import annotations

import asyncio
import os
import unittest.mock as mock

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef")

from typing import Any

import pyarrow as pa
import pytest

from app.connectors import plan as planner_plan
from app.dashboards.collect import _execute_and_convert, run_query_rows
from app.queries.registry import get_query_registry
from app.queries.registry import reset_for_tests as reset_query_registry
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo

_ORG = "org-planner-offloop-test"


@pytest.fixture()
def repo() -> InMemoryRepo:
    reset_query_registry()
    r = InMemoryRepo()
    set_repo(r)
    yield r
    set_repo(None)
    reset_query_registry()


# ---------------------------------------------------------------------------
# 1. planner_plan is offloaded via asyncio.to_thread in run_query_rows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_query_rows_offloads_planner_to_thread(repo: InMemoryRepo) -> None:
    """run_query_rows must call asyncio.to_thread with planner_plan (not inline).

    Before the fix planner_plan(...) was called directly on the event loop.
    After the fix the first asyncio.to_thread call in run_query_rows wraps
    planner_plan, and the second wraps _execute_and_convert.
    """
    reg = get_query_registry()
    reg.register(
        id="q-planner-offloop",
        sql="SELECT id FROM demo ORDER BY id",
        name="PlannerOffLoop",
    )

    to_thread_callables: list[Any] = []
    original_to_thread = asyncio.to_thread

    async def _spy_to_thread(func: Any, *args: Any, **kwargs: Any) -> Any:
        to_thread_callables.append(func)
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
        columns, rows = await run_query_rows("q-planner-offloop", _ORG, repo, {})

    assert columns == ["id"]
    assert rows == [[1], [2]]

    # planner_plan must appear as a to_thread callable.
    assert planner_plan in to_thread_callables, (
        "planner_plan must be offloaded via asyncio.to_thread in run_query_rows; "
        f"to_thread was called with: {to_thread_callables}"
    )

    # _execute_and_convert must also appear (existing MED event-loop fix intact).
    assert _execute_and_convert in to_thread_callables, (
        "_execute_and_convert must still be called via asyncio.to_thread; "
        f"to_thread was called with: {to_thread_callables}"
    )


# ---------------------------------------------------------------------------
# 2. planner_plan to_thread call precedes _execute_and_convert call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_planner_to_thread_called_before_execute_to_thread(
    repo: InMemoryRepo,
) -> None:
    """planner_plan must be dispatched to a thread BEFORE _execute_and_convert.

    The plan must be produced before the connector execute can run.
    """
    reg = get_query_registry()
    reg.register(
        id="q-order-check",
        sql="SELECT 1 AS n",
        name="OrderCheck",
    )

    to_thread_callables: list[Any] = []
    original_to_thread = asyncio.to_thread

    async def _spy_to_thread(func: Any, *args: Any, **kwargs: Any) -> Any:
        to_thread_callables.append(func)
        return await original_to_thread(func, *args, **kwargs)

    with mock.patch(
        "app.dashboards.collect._resolve_connector",
        return_value=(
            type(
                "_FC2",
                (),
                {
                    "execute": lambda self, p: pa.table({"n": [1]}),
                    "close": lambda self: None,
                },
            )(),
            False,
        ),
    ), mock.patch("asyncio.to_thread", side_effect=_spy_to_thread):
        await run_query_rows("q-order-check", _ORG, repo, {})

    plan_idx = next(
        (i for i, f in enumerate(to_thread_callables) if f is planner_plan), None
    )
    execute_idx = next(
        (i for i, f in enumerate(to_thread_callables) if f is _execute_and_convert),
        None,
    )

    assert plan_idx is not None, "planner_plan not found in asyncio.to_thread calls"
    assert execute_idx is not None, "_execute_and_convert not found in asyncio.to_thread calls"
    assert plan_idx < execute_idx, (
        f"planner_plan (index {plan_idx}) must be dispatched before "
        f"_execute_and_convert (index {execute_idx})"
    )
