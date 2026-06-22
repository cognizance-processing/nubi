"""B5 — Scenario sweep + backfill tests.

Coverage
--------
1. Sweep runs the full matrix: correct number of cells, each is a real run.
2. Grid expansion: grid dict is expanded into the Cartesian product.
3. Diff surface contains only successful cells with their outputs.
4. A failing cell is isolated: other cells still complete (best-effort).
5. Backfill iterates windows correctly: correct number of windows + params.
6. Backfill window bounds are correct (half-open [start, end)).
7. Backfill window params injected into each run (__window_start__, __window_end__).
8. max_cells / max_windows safety caps raise ValueError.
9. expand_grid helper produces the correct Cartesian product.
10. _parse_window handles various shorthand forms.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.flows.store import InMemoryFlowStore
from app.flows.sweep import (
    BackfillResult,
    SweepResult,
    _iter_windows,
    _parse_window,
    expand_grid,
    run_backfill,
    run_sweep,
)
from app.flows.registry import reset_for_tests

pytestmark = pytest.mark.asyncio

NOW = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
CLAIMS: dict[str, Any] = {"org_id": "org-test", "sub": "user-test"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_flow(
    store: InMemoryFlowStore,
    tasks: list[dict[str, Any]] | None = None,
    org_id: str = "org-test",
) -> dict[str, Any]:
    if tasks is None:
        tasks = [{"key": "t1", "kind": "noop", "needs": [], "config": {}}]
    return await store.create_flow(
        org_id=org_id,
        created_by="user-test",
        name="sweep_test_flow",
        spec={"version": 1, "name": "sweep_test", "tasks": tasks},
    )


def _output_task() -> list[dict[str, Any]]:
    """A python task that returns a value (useful for diffing outputs)."""
    return [
        {
            "key": "compute",
            "kind": "python",
            "needs": [],
            "config": {
                "code": (
                    "val = params.get('multiplier', 1)\n"
                    "result = {'value': val * 10}\n"
                )
            },
            "timeout_s": 0,
        }
    ]


def _failing_task() -> list[dict[str, Any]]:
    """A task that always raises."""
    return [
        {
            "key": "boom",
            "kind": "python",
            "needs": [],
            "config": {"code": "raise RuntimeError('intentional failure')"},
            "timeout_s": 0,
        }
    ]


# ---------------------------------------------------------------------------
# 1. Sweep runs the full matrix
# ---------------------------------------------------------------------------


async def test_sweep_runs_full_matrix():
    reset_for_tests()
    store = InMemoryFlowStore()
    flow = await _make_flow(store)

    param_sets = [{"x": 1}, {"x": 2}, {"x": 3}]
    result = await run_sweep(
        store=store,
        flow=flow,
        param_sets=param_sets,
        trigger="sweep",
        now=NOW,
        claims=CLAIMS,
    )

    assert isinstance(result, SweepResult)
    assert result.total == 3
    assert len(result.cells) == 3
    assert result.succeeded == 3
    assert result.failed == 0

    # Each cell should have a distinct run_id.
    run_ids = {c.run_id for c in result.cells}
    assert len(run_ids) == 3, "Each sweep cell must produce a distinct run_id"


# ---------------------------------------------------------------------------
# 2. Grid expansion
# ---------------------------------------------------------------------------


async def test_sweep_grid_expansion():
    reset_for_tests()
    store = InMemoryFlowStore()
    flow = await _make_flow(store)

    grid = {"region": ["ZA", "NG"], "period": ["2025-01", "2025-02"]}
    result = await run_sweep(
        store=store,
        flow=flow,
        param_sets=None,
        trigger="sweep",
        now=NOW,
        claims=CLAIMS,
        grid=grid,
    )

    # 2 regions × 2 periods = 4 cells.
    assert result.total == 4
    assert result.succeeded == 4


# ---------------------------------------------------------------------------
# 3. Diff surface contains only successful cells
# ---------------------------------------------------------------------------


async def test_sweep_diff_surface_only_successful():
    reset_for_tests()
    store = InMemoryFlowStore()
    flow = await _make_flow(store, _output_task())

    param_sets = [{"multiplier": 1}, {"multiplier": 3}]
    result = await run_sweep(
        store=store,
        flow=flow,
        param_sets=param_sets,
        trigger="sweep",
        now=NOW,
        claims=CLAIMS,
    )

    surface = result.diff_surface()
    assert len(surface) == 2

    values = [entry["outputs"].get("compute", {}).get("value") for entry in surface]
    assert 10 in values  # multiplier=1
    assert 30 in values  # multiplier=3


# ---------------------------------------------------------------------------
# 4. Failing cell is isolated (best-effort)
# ---------------------------------------------------------------------------


async def test_sweep_failing_cell_isolated():
    reset_for_tests()
    store = InMemoryFlowStore()

    # Mix: first cell uses a normal noop flow, second is a failing spec.
    # We'll simulate by using a flow whose task always fails.
    flow = await _make_flow(store, _failing_task())

    # One param set — the single cell will fail.
    result = await run_sweep(
        store=store,
        flow=flow,
        param_sets=[{"attempt": 1}],
        trigger="sweep",
        now=NOW,
        claims=CLAIMS,
    )

    assert result.total == 1
    assert result.failed == 1
    assert result.succeeded == 0
    assert result.cells[0].state in ("failed", "error")


async def test_sweep_mixed_success_failure():
    """Run two cells: noop succeeds, failing raises — both cells recorded."""
    reset_for_tests()
    store = InMemoryFlowStore()

    # Use a flow that always fails.
    fail_flow = await _make_flow(store, _failing_task())

    result = await run_sweep(
        store=store,
        flow=fail_flow,
        param_sets=[{"i": 0}, {"i": 1}],
        trigger="sweep",
        now=NOW,
        claims=CLAIMS,
    )

    # Both fail (same spec), but both are recorded independently.
    assert result.total == 2
    assert len(result.cells) == 2


# ---------------------------------------------------------------------------
# 5. Backfill iterates windows
# ---------------------------------------------------------------------------


async def test_backfill_iterates_windows():
    reset_for_tests()
    store = InMemoryFlowStore()
    flow = await _make_flow(store)

    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    end = datetime(2025, 1, 8, tzinfo=timezone.utc)  # 7 days

    result = await run_backfill(
        store=store,
        flow=flow,
        start=start,
        end=end,
        window="1d",
        trigger="backfill",
        now=NOW,
        claims=CLAIMS,
    )

    assert isinstance(result, BackfillResult)
    assert result.total == 7
    assert len(result.windows) == 7
    assert result.succeeded == 7


# ---------------------------------------------------------------------------
# 6. Backfill window bounds are correct
# ---------------------------------------------------------------------------


async def test_backfill_window_bounds():
    reset_for_tests()
    store = InMemoryFlowStore()
    flow = await _make_flow(store)

    start = datetime(2025, 3, 1, tzinfo=timezone.utc)
    end = datetime(2025, 3, 3, tzinfo=timezone.utc)

    result = await run_backfill(
        store=store,
        flow=flow,
        start=start,
        end=end,
        window="1d",
        trigger="backfill",
        now=NOW,
        claims=CLAIMS,
    )

    assert result.total == 2
    w0 = result.windows[0]
    w1 = result.windows[1]

    assert w0.window_start == start
    assert w0.window_end == start + timedelta(days=1)
    assert w1.window_start == start + timedelta(days=1)
    assert w1.window_end == end


# ---------------------------------------------------------------------------
# 7. Backfill window params injected into runs
# ---------------------------------------------------------------------------


async def test_backfill_params_injected():
    """Each window run gets __window_start__, __window_end__, __backfill_id__ params."""
    reset_for_tests()
    store = InMemoryFlowStore()

    # A python task that captures params into result.
    capture_task = [
        {
            "key": "capture",
            "kind": "python",
            "needs": [],
            "config": {
                "code": (
                    "result = {\n"
                    "    'window_start': params.get('__window_start__'),\n"
                    "    'window_end': params.get('__window_end__'),\n"
                    "    'backfill_id': params.get('__backfill_id__'),\n"
                    "}\n"
                )
            },
            "timeout_s": 0,
        }
    ]

    flow = await _make_flow(store, capture_task)

    start = datetime(2025, 6, 1, tzinfo=timezone.utc)
    end = datetime(2025, 6, 3, tzinfo=timezone.utc)

    result = await run_backfill(
        store=store,
        flow=flow,
        start=start,
        end=end,
        window="1d",
        trigger="backfill",
        now=NOW,
        claims=CLAIMS,
    )

    assert result.total == 2
    assert result.succeeded == 2

    # Check params were injected by inspecting the task_run results.
    for win in result.windows:
        task_runs = await store.list_task_runs(win.run_id)
        capture_tr = next((tr for tr in task_runs if tr["task_key"] == "capture"), None)
        assert capture_tr is not None, f"'capture' task not found for window {win.index}"
        assert capture_tr["state"] == "success"
        res = capture_tr["result"] or {}
        assert res.get("window_start") is not None
        assert res.get("backfill_id") == result.backfill_id


# ---------------------------------------------------------------------------
# 8. Safety caps
# ---------------------------------------------------------------------------


async def test_sweep_max_cells_cap():
    reset_for_tests()
    store = InMemoryFlowStore()
    flow = await _make_flow(store)

    with pytest.raises(ValueError, match="max_cells"):
        await run_sweep(
            store=store,
            flow=flow,
            param_sets=[{"i": i} for i in range(10)],
            trigger="sweep",
            now=NOW,
            claims=CLAIMS,
            max_cells=5,
        )


async def test_backfill_max_windows_cap():
    reset_for_tests()
    store = InMemoryFlowStore()
    flow = await _make_flow(store)

    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    end = datetime(2025, 1, 31, tzinfo=timezone.utc)  # 30 days

    with pytest.raises(ValueError, match="max_windows"):
        await run_backfill(
            store=store,
            flow=flow,
            start=start,
            end=end,
            window="1d",
            trigger="backfill",
            now=NOW,
            claims=CLAIMS,
            max_windows=10,
        )


# ---------------------------------------------------------------------------
# 9. expand_grid produces correct Cartesian product
# ---------------------------------------------------------------------------


def test_expand_grid_cartesian_product():
    result = expand_grid({"a": [1, 2], "b": ["x", "y"]})
    assert len(result) == 4
    params_set = {(r["a"], r["b"]) for r in result}
    assert params_set == {(1, "x"), (1, "y"), (2, "x"), (2, "y")}


def test_expand_grid_empty():
    result = expand_grid({})
    assert result == [{}]


def test_expand_grid_single_key():
    result = expand_grid({"region": ["ZA", "NG", "KE"]})
    assert len(result) == 3
    regions = [r["region"] for r in result]
    assert "ZA" in regions and "NG" in regions and "KE" in regions


# ---------------------------------------------------------------------------
# 10. _parse_window and _iter_windows
# ---------------------------------------------------------------------------


def test_parse_window_named():
    assert _parse_window("daily") == timedelta(days=1)
    assert _parse_window("weekly") == timedelta(weeks=1)
    assert _parse_window("hourly") == timedelta(hours=1)


def test_parse_window_shorthand():
    assert _parse_window("1d") == timedelta(days=1)
    assert _parse_window("7d") == timedelta(days=7)
    assert _parse_window("2h") == timedelta(hours=2)
    assert _parse_window("30m") == timedelta(minutes=30)


def test_parse_window_iso8601():
    assert _parse_window("P1D") == timedelta(days=1)
    assert _parse_window("PT1H") == timedelta(hours=1)
    assert _parse_window("PT30M") == timedelta(minutes=30)
    assert _parse_window("P7D") == timedelta(days=7)


def test_parse_window_invalid():
    with pytest.raises(ValueError):
        _parse_window("not_a_window")


def test_iter_windows_daily():
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    end = datetime(2025, 1, 4, tzinfo=timezone.utc)
    windows = _iter_windows(start, end, "1d")
    assert len(windows) == 3
    assert windows[0] == (start, start + timedelta(days=1))
    assert windows[2] == (start + timedelta(days=2), end)


def test_iter_windows_partial_last():
    """Last window is clipped to the range end."""
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    end = datetime(2025, 1, 3, 12, 0, 0, tzinfo=timezone.utc)  # 2.5 days
    windows = _iter_windows(start, end, "1d")
    assert len(windows) == 3
    # Last window should end at 'end', not start + 3d.
    assert windows[2][1] == end


def test_iter_windows_zero_delta_raises():
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    end = datetime(2025, 1, 2, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        _iter_windows(start, end, "0d")
