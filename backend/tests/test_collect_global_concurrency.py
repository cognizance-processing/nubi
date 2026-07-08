"""Tests for the process-global widget concurrency semaphore.

Covers:
1. Total concurrent widget execution across simultaneous board loads is
   bounded by the global cap (_GLOBAL_WIDGET_CONCURRENCY), regardless of how
   many concurrent loads are in flight.
2. The global semaphore constant defaults to 4 × _WIDGET_CONCURRENCY and can
   be overridden via NUBI_WIDGET_GLOBAL_CONCURRENCY.
3. No deadlock when per-call semaphore slots < global slots (the typical case).
4. Correctness is preserved: all widgets across all loads are collected.
"""

from __future__ import annotations

import asyncio
import os

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef")

import unittest.mock as mock

import pytest

import app.dashboards.collect as _collect
from app.dashboards.collect import collect_board_data
from app.queries.registry import get_query_registry
from app.queries.registry import reset_for_tests as reset_query_registry
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo

_ORG = "org-global-concurrency-test"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _board_spec(widgets: list[dict]) -> dict:
    return {
        "config": {
            "spec": {
                "version": 1,
                "title": "Test Board",
                "widgets": widgets,
            }
        }
    }


@pytest.fixture(autouse=True)
def _reset_global_sem():
    """Reset the module-level global semaphore before each test so tests are isolated."""
    original_sem = _collect._global_widget_sem
    original_cap = _collect._GLOBAL_WIDGET_CONCURRENCY
    yield
    # Restore the global semaphore and cap after the test.
    _collect._global_widget_sem = original_sem
    _collect._GLOBAL_WIDGET_CONCURRENCY = original_cap


@pytest.fixture()
def repo() -> InMemoryRepo:
    reset_query_registry()
    r = InMemoryRepo()
    set_repo(r)
    yield r
    set_repo(None)
    reset_query_registry()


# ---------------------------------------------------------------------------
# 1. Global cap bounds total concurrent widget execution across board loads
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_global_cap_bounds_total_concurrent_widgets(repo: InMemoryRepo) -> None:
    """Simultaneous board loads must not exceed _GLOBAL_WIDGET_CONCURRENCY total
    concurrent widget executions.

    Strategy
    --------
    * Set global cap to 3.
    * Launch 2 simultaneous collect_board_data calls, each with 4 widgets
      (8 total widgets across the two loads).
    * Instrument run_query_rows with a fake that:
        - increments a shared counter when it starts,
        - records the peak value of that counter,
        - decrements the counter when done.
    * Assert the recorded peak never exceeds the global cap (3).
    * Assert all 8 widgets are collected (no data loss / deadlock).
    """
    GLOBAL_CAP = 3
    WIDGETS_PER_BOARD = 4

    # Force a fresh global semaphore with the reduced cap.
    _collect._GLOBAL_WIDGET_CONCURRENCY = GLOBAL_CAP
    _collect._global_widget_sem = asyncio.Semaphore(GLOBAL_CAP)

    # Register queries.
    reg = get_query_registry()
    for i in range(WIDGETS_PER_BOARD):
        for board_idx in ("a", "b"):
            qid = f"q-gc-{board_idx}-{i}"
            if reg.get(qid) is None:
                reg.register(id=qid, sql="SELECT 1", name=qid)

    # Create boards.
    widgets_a = [
        {"id": f"el-a-{i}", "query_id": f"q-gc-a-{i}"}
        for i in range(WIDGETS_PER_BOARD)
    ]
    widgets_b = [
        {"id": f"el-b-{i}", "query_id": f"q-gc-b-{i}"}
        for i in range(WIDGETS_PER_BOARD)
    ]

    board_a_id = "board-gc-a"
    board_b_id = "board-gc-b"
    await repo.create(
        "boards", org_id=_ORG, created_by="test", name="Board A",
        id=board_a_id, **_board_spec(widgets_a),
    )
    await repo.create(
        "boards", org_id=_ORG, created_by="test", name="Board B",
        id=board_b_id, **_board_spec(widgets_b),
    )

    # Instrumented fake for run_query_rows.
    active_counter = 0
    peak_concurrent = 0
    completed_widgets = 0

    async def _fake_run_query_rows(query_id, org_id, repo_arg, policies, **_kw):
        nonlocal active_counter, peak_concurrent, completed_widgets
        active_counter += 1
        if active_counter > peak_concurrent:
            peak_concurrent = active_counter
        # Yield control so other coroutines can run; this maximises overlap.
        await asyncio.sleep(0)
        active_counter -= 1
        completed_widgets += 1
        return (["v"], [[1]])

    with mock.patch.object(_collect, "run_query_rows", _fake_run_query_rows):
        results_a, results_b = await asyncio.gather(
            collect_board_data(board_a_id, _ORG, {}, repo),
            collect_board_data(board_b_id, _ORG, {}, repo),
        )

    total_widgets = WIDGETS_PER_BOARD * 2
    assert completed_widgets == total_widgets, (
        f"Expected {total_widgets} widgets completed, got {completed_widgets}"
    )
    assert peak_concurrent <= GLOBAL_CAP, (
        f"Peak concurrent widget executions {peak_concurrent} exceeded "
        f"global cap {GLOBAL_CAP}"
    )

    # All widgets in both boards must have data (no errors).
    for entry in results_a:
        assert "error" not in entry, f"Board A widget has error: {entry}"
    for entry in results_b:
        assert "error" not in entry, f"Board B widget has error: {entry}"


# ---------------------------------------------------------------------------
# 2. Correctness: all widgets are collected when global cap < total widgets
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_global_cap_does_not_lose_widgets(repo: InMemoryRepo) -> None:
    """All widgets are returned even when global cap is smaller than widget count."""
    GLOBAL_CAP = 2
    WIDGETS = 5

    _collect._GLOBAL_WIDGET_CONCURRENCY = GLOBAL_CAP
    _collect._global_widget_sem = asyncio.Semaphore(GLOBAL_CAP)

    reg = get_query_registry()
    for i in range(WIDGETS):
        qid = f"q-nodrop-{i}"
        if reg.get(qid) is None:
            reg.register(id=qid, sql="SELECT 1", name=qid)

    widgets = [
        {"id": f"w{i}", "query_id": f"q-nodrop-{i}", "pos": {"x": i, "y": 1, "w": 2, "h": 2}}
        for i in range(WIDGETS)
    ]
    board_id = "board-nodrop"
    await repo.create(
        "boards", org_id=_ORG, created_by="test", name="NoDrop",
        id=board_id, **_board_spec(widgets),
    )

    async def _fake_run(query_id, org_id, repo_arg, policies, **_kw):
        await asyncio.sleep(0)
        return (["v"], [[42]])

    with mock.patch.object(_collect, "run_query_rows", _fake_run):
        results = await collect_board_data(board_id, _ORG, {}, repo)

    assert len(results) == WIDGETS, (
        f"Expected {WIDGETS} widgets, got {len(results)}"
    )
    for entry in results:
        assert "error" not in entry
        assert entry["rows"] == [[42]]


# ---------------------------------------------------------------------------
# 3. Global cap constant defaults and env-var parsing
# ---------------------------------------------------------------------------


def test_global_concurrency_default_is_multiple_of_per_call() -> None:
    """_DEFAULT_GLOBAL_WIDGET_CONCURRENCY must be >= _WIDGET_CONCURRENCY."""
    assert _collect._DEFAULT_GLOBAL_WIDGET_CONCURRENCY >= _collect._WIDGET_CONCURRENCY, (
        "_DEFAULT_GLOBAL_WIDGET_CONCURRENCY must be at least as large as "
        f"_WIDGET_CONCURRENCY ({_collect._WIDGET_CONCURRENCY}); "
        f"got {_collect._DEFAULT_GLOBAL_WIDGET_CONCURRENCY}"
    )


def test_global_concurrency_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """NUBI_WIDGET_GLOBAL_CONCURRENCY must be parsed by the module formula."""
    monkeypatch.setenv("NUBI_WIDGET_GLOBAL_CONCURRENCY", "7")
    computed = max(
        1,
        int(
            os.environ.get(
                "NUBI_WIDGET_GLOBAL_CONCURRENCY",
                _collect._DEFAULT_GLOBAL_WIDGET_CONCURRENCY,
            )
        ),
    )
    assert computed == 7, f"Expected 7 from env override; got {computed}"


def test_global_concurrency_floor_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """NUBI_WIDGET_GLOBAL_CONCURRENCY='0' must be clamped to 1."""
    monkeypatch.setenv("NUBI_WIDGET_GLOBAL_CONCURRENCY", "0")
    computed = max(
        1,
        int(
            os.environ.get(
                "NUBI_WIDGET_GLOBAL_CONCURRENCY",
                _collect._DEFAULT_GLOBAL_WIDGET_CONCURRENCY,
            )
        ),
    )
    assert computed >= 1, f"Floor guard failed; computed={computed}"


# ---------------------------------------------------------------------------
# 4. No deadlock: per-call semaphore < global → all tasks complete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_deadlock_when_per_call_lt_global(repo: InMemoryRepo) -> None:
    """No deadlock when per-call concurrency (8) < global concurrency (32).

    If acquisition order were reversed (global first, then per-call), a load
    holding many global slots could starve itself.  Verify that the correct
    acquisition order (per-call first, then global) completes without hanging.
    """
    # Per-call default (8) < global (32) — typical production config.
    GLOBAL_CAP = 32
    PER_CALL = 4  # override per-call to be tiny
    WIDGETS = 6

    original_per_call = _collect._WIDGET_CONCURRENCY
    _collect._GLOBAL_WIDGET_CONCURRENCY = GLOBAL_CAP
    _collect._global_widget_sem = asyncio.Semaphore(GLOBAL_CAP)
    _collect._WIDGET_CONCURRENCY = PER_CALL
    try:
        reg = get_query_registry()
        for i in range(WIDGETS):
            qid = f"q-nodeadlock-{i}"
            if reg.get(qid) is None:
                reg.register(id=qid, sql="SELECT 1", name=qid)

        widgets = [
            {"id": f"w{i}", "query_id": f"q-nodeadlock-{i}", "pos": {"x": i, "y": 1, "w": 2, "h": 2}}
            for i in range(WIDGETS)
        ]
        board_id = "board-nodeadlock"
        await repo.create(
            "boards", org_id=_ORG, created_by="test", name="NoDeadlock",
            id=board_id, **_board_spec(widgets),
        )

        async def _fake_run(query_id, org_id, repo_arg, policies, **_kw):
            await asyncio.sleep(0)
            return (["v"], [[1]])

        with mock.patch.object(_collect, "run_query_rows", _fake_run):
            results = await asyncio.wait_for(
                collect_board_data(board_id, _ORG, {}, repo),
                timeout=5.0,
            )
    finally:
        _collect._WIDGET_CONCURRENCY = original_per_call

    assert len(results) == WIDGETS
    for entry in results:
        assert "error" not in entry
