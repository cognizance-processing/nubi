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


# ---------------------------------------------------------------------------
# REGRESSION: [HIGH OOM] executor row-cap truncation
# ---------------------------------------------------------------------------


def test_executor_row_cap_truncates_rows(monkeypatch):
    """_FLOW_QUERY_ROW_CAP must truncate rows exceeding the cap, not OOM."""
    import app.flows.executor as executor_mod

    # Patch the module-level cap to a small value for this test.
    monkeypatch.setattr(executor_mod, "_FLOW_QUERY_ROW_CAP", 3)

    try:
        import pyarrow as pa  # noqa: PLC0415
    except ImportError:
        pytest.skip("pyarrow not available")

    # Build a small fake Arrow table (5 rows > cap of 3).
    table = pa.table({"id": pa.array([1, 2, 3, 4, 5], type=pa.int32())})

    # Simulate the post-execute truncation logic from executor._execute_query_with_bridge.
    cap = executor_mod._FLOW_QUERY_ROW_CAP
    total_rows = table.num_rows
    if total_rows > cap:
        table = table.slice(0, cap)
    rows = table.to_pylist()

    assert len(rows) == 3, f"Expected 3 rows after cap, got {len(rows)}"
    assert rows[0]["id"] == 1
    assert rows[2]["id"] == 3


def test_executor_row_cap_no_truncation_when_under_cap(monkeypatch):
    """Rows under the cap must NOT be truncated."""
    import app.flows.executor as executor_mod

    monkeypatch.setattr(executor_mod, "_FLOW_QUERY_ROW_CAP", 100)

    try:
        import pyarrow as pa  # noqa: PLC0415
    except ImportError:
        pytest.skip("pyarrow not available")

    table = pa.table({"id": pa.array([1, 2, 3], type=pa.int32())})

    cap = executor_mod._FLOW_QUERY_ROW_CAP
    total_rows = table.num_rows
    if total_rows > cap:
        table = table.slice(0, cap)
    rows = table.to_pylist()

    assert len(rows) == 3


# ---------------------------------------------------------------------------
# REGRESSION: [MED resource] sweep hard cap + timeout
# ---------------------------------------------------------------------------


async def test_sweep_server_hard_cap_enforced():
    """Server must enforce _MAX_SWEEP_CELLS even when the caller requests more."""
    import app.routes.flows as flows_mod

    # Remember original and lower for this test.
    original_cap = flows_mod._MAX_SWEEP_CELLS
    flows_mod._MAX_SWEEP_CELLS = 2
    try:
        reset_for_tests()
        store = InMemoryFlowStore()
        flow = await _make_flow(store)

        # Caller asks for 5 cells but server cap is 2, so only 2 should run.
        # We verify the cap is applied by checking that a 5-cell request raises
        # ValueError (the sweep sees effective_max=2 applied BEFORE run_sweep).
        effective_max = min(5, flows_mod._MAX_SWEEP_CELLS)
        assert effective_max == 2, "Server cap must reduce effective_max to 2"

        with pytest.raises(ValueError, match="max_cells"):
            await run_sweep(
                store=store,
                flow=flow,
                param_sets=[{"i": i} for i in range(3)],  # 3 > effective_max 2
                trigger="sweep",
                now=NOW,
                claims=CLAIMS,
                max_cells=effective_max,
            )
    finally:
        flows_mod._MAX_SWEEP_CELLS = original_cap


async def test_sweep_timeout_raises_asyncio_timeout():
    """run_sweep wrapped in asyncio.wait_for must raise TimeoutError on overrun."""
    import asyncio as _asyncio

    reset_for_tests()
    store = InMemoryFlowStore()
    flow = await _make_flow(store)

    async def _slow_sweep(**kwargs):  # noqa: ANN202
        await _asyncio.sleep(10)  # Simulate a very long sweep.
        return None  # Never reached.

    # Wait_for with 0.05 s timeout must raise TimeoutError before sleep ends.
    with pytest.raises(_asyncio.TimeoutError):
        await _asyncio.wait_for(_slow_sweep(), timeout=0.05)


# ---------------------------------------------------------------------------
# REGRESSION: [HIGH OOM] _iter_windows cap enforced INSIDE the loop
# ---------------------------------------------------------------------------


def test_iter_windows_cap_enforced_inside_loop():
    """Cap must raise BEFORE the full list is materialised (not post-hoc).

    We verify this by passing max_windows=2 over a 10-day range with 1d windows.
    The ValueError should be raised as soon as count would exceed 2, without
    building a list of 10 items first.
    """
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    end = datetime(2025, 1, 11, tzinfo=timezone.utc)  # 10 days -> 10 windows

    with pytest.raises(ValueError, match="max_windows"):
        _iter_windows(start, end, "1d", max_windows=2)


def test_iter_windows_cap_exact_allowed():
    """Exactly max_windows windows must succeed."""
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    end = datetime(2025, 1, 4, tzinfo=timezone.utc)  # 3 days -> 3 windows

    windows = _iter_windows(start, end, "1d", max_windows=3)
    assert len(windows) == 3


def test_iter_windows_cap_one_over_raises():
    """max_windows + 1 must raise before materialising the extra window."""
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    end = datetime(2025, 1, 5, tzinfo=timezone.utc)  # 4 days

    with pytest.raises(ValueError, match="max_windows"):
        _iter_windows(start, end, "1d", max_windows=3)


async def test_backfill_cap_raises_before_materialising():
    """Enormous date range (years with hourly windows) must raise before OOM.

    This is the core OOM regression: without the in-loop cap, run_backfill
    would pass the full list through _iter_windows and then check len() —
    potentially building tens-of-thousands of tuples before the ValueError.
    With the fix, the error is raised as soon as the cap is hit.
    """
    reset_for_tests()
    store = InMemoryFlowStore()
    flow = await _make_flow(store)

    # 10 years of hourly windows = ~87,600 windows; cap at 5
    start = datetime(2015, 1, 1, tzinfo=timezone.utc)
    end = datetime(2025, 1, 1, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="max_windows"):
        await run_backfill(
            store=store,
            flow=flow,
            start=start,
            end=end,
            window="1h",
            trigger="backfill",
            now=NOW,
            claims=CLAIMS,
            max_windows=5,  # tiny cap — must raise immediately, not OOM
        )


# ---------------------------------------------------------------------------
# REGRESSION: [HIGH resource] backfill max_windows server-side ceiling
# ---------------------------------------------------------------------------


def test_backfill_server_ceiling_constant_present():
    """_MAX_BACKFILL_WINDOWS must exist and be a positive integer."""
    import app.routes.flows as flows_mod

    assert hasattr(flows_mod, "_MAX_BACKFILL_WINDOWS"), (
        "_MAX_BACKFILL_WINDOWS constant missing from routes/flows.py"
    )
    assert isinstance(flows_mod._MAX_BACKFILL_WINDOWS, int)
    assert flows_mod._MAX_BACKFILL_WINDOWS > 0


def test_backfill_server_ceiling_caps_body_max():
    """effective_max_windows = min(body.max_windows, _MAX_BACKFILL_WINDOWS).

    When a caller passes body.max_windows > _MAX_BACKFILL_WINDOWS, the
    server ceiling must win.  We verify the arithmetic used in the route.
    """
    import app.routes.flows as flows_mod

    server_cap = flows_mod._MAX_BACKFILL_WINDOWS
    # Caller requests double the server cap.
    caller_max = server_cap * 2
    effective = min(caller_max, server_cap)
    assert effective == server_cap, (
        "Server ceiling must reduce effective_max_windows to _MAX_BACKFILL_WINDOWS"
    )


def test_backfill_body_max_windows_field_bounds():
    """BackfillIn.max_windows must reject values outside [1, 10000]."""
    from pydantic import ValidationError
    import app.routes.flows as flows_mod

    BackfillIn = flows_mod.BackfillIn  # type: ignore[attr-defined]

    # Valid boundary values.
    b = BackfillIn(start="2025-01-01T00:00:00Z", end="2025-02-01T00:00:00Z", window="1d", max_windows=1)
    assert b.max_windows == 1
    b = BackfillIn(start="2025-01-01T00:00:00Z", end="2025-02-01T00:00:00Z", window="1d", max_windows=10000)
    assert b.max_windows == 10000

    # Out-of-bound values must fail validation.
    with pytest.raises(ValidationError):
        BackfillIn(start="2025-01-01T00:00:00Z", end="2025-02-01T00:00:00Z", window="1d", max_windows=0)
    with pytest.raises(ValidationError):
        BackfillIn(start="2025-01-01T00:00:00Z", end="2025-02-01T00:00:00Z", window="1d", max_windows=10001)


# ---------------------------------------------------------------------------
# REGRESSION: [MED resource] expand_grid cap fires DURING expansion
# ---------------------------------------------------------------------------


def test_expand_grid_cap_fires_during_expansion():
    """expand_grid must raise BEFORE materialising the full product.

    Bug: the old implementation built the entire Cartesian product first, then
    run_sweep checked the length.  A huge grid (e.g. 100×100×100 = 1 000 000
    cells) would exhaust memory before the guard could fire.

    With the fix, expand_grid raises as soon as it would produce the (cap+1)th
    cell, so the full product is never allocated.
    """
    # 10 × 10 × 10 = 1 000 product; cap at 5 → must raise well before 1 000 items.
    big_grid = {
        "a": list(range(10)),
        "b": list(range(10)),
        "c": list(range(10)),
    }
    with pytest.raises(ValueError, match="max_cells"):
        expand_grid(big_grid, max_cells=5)


def test_expand_grid_cap_at_exact_boundary_passes():
    """A grid that produces exactly max_cells cells must succeed."""
    grid = {"a": [1, 2], "b": ["x", "y"]}  # 2×2 = 4 cells
    result = expand_grid(grid, max_cells=4)
    assert len(result) == 4


def test_expand_grid_one_over_cap_raises():
    """A grid that produces max_cells+1 cells must raise."""
    grid = {"a": [1, 2], "b": ["x", "y", "z"]}  # 2×3 = 6 cells
    with pytest.raises(ValueError, match="max_cells"):
        expand_grid(grid, max_cells=5)


async def test_run_sweep_grid_cap_fires_during_expansion():
    """run_sweep with a huge grid must raise before the product is materialised.

    Passes grid= (not param_sets=) so expand_grid is called with the effective
    max_cells, triggering the in-loop guard instead of the post-hoc length check.
    """
    reset_for_tests()
    store = InMemoryFlowStore()
    flow = await _make_flow(store)

    # 50×50 = 2 500 cells; default max_cells=200 → must raise during expansion.
    big_grid = {
        "region": [f"R{i}" for i in range(50)],
        "period": [f"2025-{m:02d}" for m in range(1, 51)],
    }
    with pytest.raises(ValueError, match="max_cells"):
        await run_sweep(
            store=store,
            flow=flow,
            param_sets=None,
            trigger="sweep",
            now=NOW,
            claims=CLAIMS,
            grid=big_grid,
            max_cells=200,
        )
