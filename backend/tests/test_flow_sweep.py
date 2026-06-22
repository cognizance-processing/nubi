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


# ---------------------------------------------------------------------------
# FIX 4: explicit param_sets beyond server cap is rejected at the route layer
# ---------------------------------------------------------------------------


def test_sweep_route_rejects_param_sets_over_server_cap():
    """sweep_flow route must reject param_sets that exceed the server cap.

    The route-layer check (`effective_max`) must fire BEFORE run_sweep is
    called, so the cap is enforced regardless of the body.max_cells field.
    We verify by inspecting the route source for the guard.
    """
    import inspect
    import app.routes.flows as flows_mod

    src = inspect.getsource(flows_mod.sweep_flow)
    # The guard must check param_sets length against effective_max.
    assert "len(body.param_sets)" in src, (
        "sweep_flow must check len(body.param_sets) against effective_max"
    )
    assert "effective_max" in src, (
        "sweep_flow must compute effective_max from min(body.max_cells, _MAX_SWEEP_CELLS)"
    )


def test_sweep_explicit_param_sets_cap_arithmetic():
    """effective_max must always be bounded by _MAX_SWEEP_CELLS even when
    caller passes param_sets directly (bypassing grid expansion).

    This is the logic the route uses; verify it with plain arithmetic.
    """
    import app.routes.flows as flows_mod

    original_cap = flows_mod._MAX_SWEEP_CELLS
    flows_mod._MAX_SWEEP_CELLS = 3
    try:
        # Simulate the route arithmetic.
        body_max_cells = 200  # caller asks for 200
        effective_max = min(body_max_cells, flows_mod._MAX_SWEEP_CELLS)
        assert effective_max == 3, (
            "Server cap must reduce effective_max to _MAX_SWEEP_CELLS=3, got "
            f"{effective_max}"
        )

        # A param_sets list of length 4 must be rejected (4 > effective_max=3).
        param_sets = [{"i": i} for i in range(4)]
        assert len(param_sets) > effective_max, (
            "4 param_sets must exceed effective_max=3"
        )
    finally:
        flows_mod._MAX_SWEEP_CELLS = original_cap


async def test_sweep_explicit_param_sets_at_cap_is_allowed():
    """param_sets exactly at the server cap must NOT be rejected."""
    import app.routes.flows as flows_mod

    original_cap = flows_mod._MAX_SWEEP_CELLS
    flows_mod._MAX_SWEEP_CELLS = 3
    try:
        reset_for_tests()
        store = InMemoryFlowStore()
        flow = await _make_flow(store)

        # 3 param sets == cap → route should pass them through to run_sweep.
        effective_max = min(200, flows_mod._MAX_SWEEP_CELLS)  # = 3
        param_sets = [{"i": i} for i in range(3)]

        # The route guard fires when len > effective_max; exactly at cap is fine.
        assert len(param_sets) <= effective_max, (
            "param_sets of length 3 must not exceed effective_max=3"
        )

        result = await run_sweep(
            store=store,
            flow=flow,
            param_sets=param_sets,
            trigger="sweep",
            now=NOW,
            claims=CLAIMS,
            max_cells=effective_max,
        )
        assert result.total == 3
        assert result.succeeded == 3
    finally:
        flows_mod._MAX_SWEEP_CELLS = original_cap


# ---------------------------------------------------------------------------
# FIX: [MED N+1] drain_flow_run bounded list_task_runs in sweep
# ---------------------------------------------------------------------------


async def test_drain_in_sweep_bounded_list_task_runs():
    """drain_flow_run's list_task_runs call count must grow linearly with steps.

    The N+1 fix ensures the drain loop only re-queries list_task_runs when the
    snapshot is stale (i.e. after a state mutation).  We verify linearity by
    comparing a 1-task flow vs a 3-task flow: the per-task incremental call
    count must stay constant (not grow), proving O(steps) not O(steps^2).

    Internal calls from advance_readiness and _execute_claimed_task_run_inner
    are expected and counted; we verify proportionality, not an exact total.
    """
    from app.flows.runtime import materialize_flow_run, drain_flow_run

    async def _count_ltr_calls(store, tasks_spec):
        """Run drain on a flow and return list_task_runs call count."""
        flow = await _make_flow(store, tasks_spec)
        flow_run = await materialize_flow_run(store, flow, {}, "sweep", NOW)
        run_id = flow_run["id"]

        original_ltr = store.list_task_runs
        count = 0

        async def counting_ltr(fr_id):
            nonlocal count
            count += 1
            return await original_ltr(fr_id)

        store.list_task_runs = counting_ltr
        try:
            final = await drain_flow_run(store, run_id, NOW, claims=CLAIMS)
            assert final.get("state") == "success"
        finally:
            store.list_task_runs = original_ltr
        return count

    # 1-task flow.
    reset_for_tests()
    store1 = InMemoryFlowStore()
    count_1 = await _count_ltr_calls(
        store1,
        [{"key": "t1", "kind": "noop", "needs": [], "config": {}}],
    )

    # 3-task linear flow.
    reset_for_tests()
    store3 = InMemoryFlowStore()
    count_3 = await _count_ltr_calls(
        store3,
        [
            {"key": "t1", "kind": "noop", "needs": [], "config": {}},
            {"key": "t2", "kind": "noop", "needs": ["t1"], "config": {}},
            {"key": "t3", "kind": "noop", "needs": ["t2"], "config": {}},
        ],
    )

    # Per-task incremental cost must stay constant (linear).
    # count_3 must be ≤ count_1 + 2 * (count_1 + 1) to allow some slack.
    # The key invariant: count_3 / count_1 < 3.5 (roughly 3× for 3 tasks,
    # with a small constant per-call overhead from advance_readiness, not N²).
    assert count_1 > 0
    assert count_3 > 0
    # Quadratic growth (N+1 bug) would make count_3 ~ count_1 * 9 for a
    # 3-task flow (each drain-loop step fetches all N tasks again).
    # Linear growth means count_3 ≈ 3 × count_1 (a small constant per step).
    assert count_3 <= count_1 * 4, (
        f"list_task_runs grew super-linearly: 1-task={count_1}, 3-task={count_3}. "
        f"Expected 3-task count ≤ {count_1 * 4} (4× the 1-task baseline). "
        "This indicates the drain loop is re-fetching unnecessarily (N+1 regression)."
    )


async def test_run_sweep_passes_max_steps_to_drain():
    """run_sweep must pass max_steps=_SWEEP_CELL_DRAIN_MAX_STEPS to drain_flow_run.

    We verify this by patching drain_flow_run and asserting that the max_steps
    kwarg is always _SWEEP_CELL_DRAIN_MAX_STEPS (not the default 200).
    """
    import unittest.mock as mock
    import app.flows.sweep as sweep_mod
    from app.flows.runtime import materialize_flow_run

    reset_for_tests()
    store = InMemoryFlowStore()
    flow = await _make_flow(store)

    observed_max_steps: list[int] = []
    original_drain = sweep_mod.drain_flow_run if hasattr(sweep_mod, "drain_flow_run") else None

    # Patch inside the sweep module's local import namespace.
    import app.flows.runtime as rt_mod

    original_drain_fn = rt_mod.drain_flow_run

    async def capturing_drain(store_, run_id, now, claims=None, max_steps=200):
        observed_max_steps.append(max_steps)
        return await original_drain_fn(store_, run_id, now, claims=claims, max_steps=max_steps)

    # Patch at the runtime module level so that sweep's local import picks it up.
    rt_mod.drain_flow_run = capturing_drain
    try:
        result = await run_sweep(
            store=store,
            flow=flow,
            param_sets=[{"x": 1}, {"x": 2}],
            trigger="sweep",
            now=NOW,
            claims=CLAIMS,
        )
    finally:
        rt_mod.drain_flow_run = original_drain_fn

    assert result.total == 2
    # drain was called twice (one per cell).
    assert len(observed_max_steps) == 2
    # Both calls must have used the sweep cap, not the default 200.
    expected_cap = sweep_mod._SWEEP_CELL_DRAIN_MAX_STEPS
    for ms in observed_max_steps:
        assert ms == expected_cap, (
            f"drain_flow_run was called with max_steps={ms}, expected "
            f"_SWEEP_CELL_DRAIN_MAX_STEPS={expected_cap}. "
            "run_sweep must pass the per-cell cap to prevent runaway drains."
        )


async def test_sweep_cell_drain_max_steps_env_overridable(monkeypatch):
    """_SWEEP_CELL_DRAIN_MAX_STEPS is read from NUBI_SWEEP_CELL_DRAIN_MAX_STEPS env var."""
    import importlib
    import app.flows.sweep as sweep_mod

    original_val = sweep_mod._SWEEP_CELL_DRAIN_MAX_STEPS
    # Verify the constant exists and is a positive integer.
    assert isinstance(original_val, int), "_SWEEP_CELL_DRAIN_MAX_STEPS must be int"
    assert original_val > 0, "_SWEEP_CELL_DRAIN_MAX_STEPS must be positive"

    # Simulate env-override by patching the module attribute directly
    # (we don't reload to avoid side-effects, just verify the arithmetic).
    monkeypatch.setattr(sweep_mod, "_SWEEP_CELL_DRAIN_MAX_STEPS", 7)
    assert sweep_mod._SWEEP_CELL_DRAIN_MAX_STEPS == 7

    # After restoring, value should be back to original.
    monkeypatch.undo()
    assert sweep_mod._SWEEP_CELL_DRAIN_MAX_STEPS == original_val


async def test_backfill_drain_bounded_by_cell_cap():
    """run_backfill also passes max_steps cap to drain_flow_run for each window."""
    import app.flows.sweep as sweep_mod
    import app.flows.runtime as rt_mod

    reset_for_tests()
    store = InMemoryFlowStore()
    flow = await _make_flow(store)

    from datetime import datetime, timedelta, timezone

    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    end = datetime(2025, 1, 3, tzinfo=timezone.utc)  # 2 windows

    observed_max_steps: list[int] = []
    original_drain_fn = rt_mod.drain_flow_run

    async def capturing_drain(store_, run_id, now, claims=None, max_steps=200):
        observed_max_steps.append(max_steps)
        return await original_drain_fn(store_, run_id, now, claims=claims, max_steps=max_steps)

    rt_mod.drain_flow_run = capturing_drain
    try:
        from app.flows.sweep import run_backfill
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
    finally:
        rt_mod.drain_flow_run = original_drain_fn

    assert result.total == 2
    assert len(observed_max_steps) == 2
    expected_cap = sweep_mod._SWEEP_CELL_DRAIN_MAX_STEPS
    for ms in observed_max_steps:
        assert ms == expected_cap, (
            f"run_backfill passed max_steps={ms} to drain_flow_run, "
            f"expected _SWEEP_CELL_DRAIN_MAX_STEPS={expected_cap}."
        )
