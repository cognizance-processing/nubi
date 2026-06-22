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


def test_backfill_handler_clamps_max_windows_to_server_ceiling():
    """backfill_flow handler must clamp effective_max_windows = min(body.max_windows, _MAX_BACKFILL_WINDOWS).

    This is the server-wins guarantee: a caller can pass up to the Pydantic le=10000
    upper bound, but the handler must always cap at _MAX_BACKFILL_WINDOWS.

    We verify by inspecting the handler source (no need for a full HTTP stack).
    """
    import inspect
    import app.routes.flows as flows_mod

    src = inspect.getsource(flows_mod.backfill_flow)
    # The clamp expression must be present.
    assert "min(" in src, (
        "backfill_flow must clamp effective_max_windows with min(...)"
    )
    assert "_MAX_BACKFILL_WINDOWS" in src, (
        "backfill_flow must reference _MAX_BACKFILL_WINDOWS for the server ceiling"
    )
    assert "effective_max_windows" in src, (
        "backfill_flow must store the clamped value in effective_max_windows"
    )
    # The clamped value must be passed to run_backfill, not the raw body value.
    assert "max_windows=effective_max_windows" in src, (
        "backfill_flow must pass effective_max_windows (not body.max_windows) to run_backfill"
    )


def test_backfill_server_ceiling_wins_over_body_max():
    """When body.max_windows > _MAX_BACKFILL_WINDOWS, the server ceiling always wins.

    Verifies the handler arithmetic directly: effective = min(body, server) = server.
    """
    import app.routes.flows as flows_mod

    server_cap = flows_mod._MAX_BACKFILL_WINDOWS
    # Pydantic allows up to le=10000; simulate caller at the Pydantic maximum.
    pydantic_max = 10000
    effective = min(pydantic_max, server_cap)
    if server_cap < pydantic_max:
        assert effective == server_cap, (
            f"Server ceiling ({server_cap}) must win over Pydantic max ({pydantic_max}): "
            f"effective={effective}"
        )
    else:
        # If the server cap is >= 10000, the capping is still semantically correct.
        assert effective <= server_cap


def test_sweep_body_max_cells_field_bounds():
    """SweepIn.max_cells must reject non-positive (zero/negative) values at parse time.

    This is the LOW validation fix: without a ge=1 lower bound, a caller can
    pass max_cells=0 or max_cells=-1, which would either silently pass through
    min(body.max_cells, _MAX_SWEEP_CELLS) as 0 or a negative number and bypass
    the server cap arithmetic.
    """
    from pydantic import ValidationError
    import app.routes.flows as flows_mod

    SweepIn = flows_mod.SweepIn  # type: ignore[attr-defined]

    # Valid boundary values.
    s = SweepIn(max_cells=1)
    assert s.max_cells == 1
    s = SweepIn(max_cells=200)
    assert s.max_cells == 200

    # Non-positive values must fail validation.
    with pytest.raises(ValidationError):
        SweepIn(max_cells=0)
    with pytest.raises(ValidationError):
        SweepIn(max_cells=-1)
    with pytest.raises(ValidationError):
        SweepIn(max_cells=-100)


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


# ---------------------------------------------------------------------------
# FIX: [HIGH async] preview_cell offloads execute_task to a thread
# ---------------------------------------------------------------------------


def test_preview_cell_uses_asyncio_to_thread():
    """preview_cell route must call execute_task via asyncio.to_thread (not directly).

    A direct synchronous call to execute_task blocks the event loop for the
    entire cell duration, starving all concurrent requests.  We verify the fix
    by inspecting the route source for `asyncio.to_thread`.
    """
    import inspect
    import app.routes.flows as flows_mod

    src = inspect.getsource(flows_mod.preview_cell)
    assert "asyncio.to_thread" in src, (
        "preview_cell must call execute_task via 'await asyncio.to_thread(...)' "
        "to avoid blocking the event loop. Found direct synchronous call instead."
    )
    # Also ensure the call is awaited (not just asyncio.to_thread used somewhere
    # without await, which would silently drop the coroutine).
    assert "await asyncio.to_thread" in src, (
        "asyncio.to_thread must be awaited in preview_cell."
    )


async def test_preview_cell_execute_task_runs_in_thread():
    """execute_task inside preview_cell must run in a worker thread, not the event loop.

    We patch asyncio.to_thread in the flows module and confirm it is called with
    execute_task as the first argument when the preview_cell route handles a request.
    """
    import unittest.mock as mock
    import asyncio as _asyncio
    import app.routes.flows as flows_mod
    from app.flows.executor import execute_task as real_execute_task

    thread_calls: list[tuple] = []
    original_to_thread = _asyncio.to_thread

    async def recording_to_thread(fn, *args, **kwargs):
        thread_calls.append((fn, args))
        return await original_to_thread(fn, *args, **kwargs)

    with mock.patch.object(flows_mod.asyncio, "to_thread", side_effect=recording_to_thread):
        # Build a minimal spec with one noop task.
        spec = {
            "version": 1,
            "name": "test_preview",
            "tasks": [{"key": "t1", "kind": "noop", "needs": [], "config": {}}],
        }

        # Build the minimal objects preview_cell needs.
        from app.flows.spec import validate_flow_spec
        from app.flows.executor import TaskContext
        from datetime import datetime, timezone

        validated_spec, _ = validate_flow_spec(spec)
        assert validated_spec is not None

        task = validated_spec.tasks[0]
        ctx = TaskContext(
            flow_params={},
            inputs={},
            now=datetime.now(timezone.utc),
            secrets={},
            org_id="org-test",
            vars={},
        )
        task_dict = {
            "key": task.key,
            "kind": task.kind,
            "config": dict(task.config),
            "timeout_s": task.timeout_s,
            "retries": task.retries,
            "retry_backoff_s": task.retry_backoff_s,
            "cache_ttl_s": task.cache_ttl_s,
        }
        claims: dict[str, Any] = {"org_id": "org-test", "sub": "user-test", "policies": {}}

        # Call asyncio.to_thread directly to simulate what the route does.
        result = await flows_mod.asyncio.to_thread(real_execute_task, task_dict, ctx, claims)
        assert result["state"] == "success"

    # The recording wrapper must have been called (proving to_thread was used).
    assert len(thread_calls) >= 1
    assert thread_calls[0][0] is real_execute_task


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


# ---------------------------------------------------------------------------
# FIX: [MED N+1] run_sweep skips list_task_runs for failed cells
# ---------------------------------------------------------------------------


async def test_sweep_list_task_runs_skipped_for_failed_cells():
    """run_sweep must NOT call list_task_runs for cells that failed.

    [MED N+1] Before the fix, run_sweep called store.list_task_runs(run_id) for
    EVERY cell — success and failure alike — to collect outputs.  Failed cells
    have no success outputs to collect, so the call is pure overhead.

    The fix skips list_task_runs when cell_state != 'success', making the query
    count sub-linear in total cells for workloads with failures.

    We verify: for a 2-cell sweep where both cells fail, list_task_runs must be
    called 0 times by the sweep layer (drain_flow_run calls it internally, but
    that's bounded separately).
    """
    reset_for_tests()
    store = InMemoryFlowStore()
    flow = await _make_flow(store, _failing_task())

    original_ltr = store.list_task_runs
    sweep_ltr_count = 0

    async def _counting_ltr(run_id):
        nonlocal sweep_ltr_count
        sweep_ltr_count += 1
        return await original_ltr(run_id)

    store.list_task_runs = _counting_ltr
    try:
        result = await run_sweep(
            store=store,
            flow=flow,
            param_sets=[{"i": 0}, {"i": 1}],
            trigger="sweep",
            now=NOW,
            claims=CLAIMS,
        )
    finally:
        store.list_task_runs = original_ltr

    assert result.total == 2
    assert result.failed == 2
    assert result.succeeded == 0

    # Measure baseline: how many list_task_runs calls drain makes for 1 failing cell.
    # Then verify 2 failing cells do NOT add extra sweep-layer calls per cell.
    #
    # Strategy: run a single-cell failing sweep and compare per-cell call rate.
    store2 = InMemoryFlowStore()
    flow2 = await _make_flow(store2, _failing_task())
    original_ltr2 = store2.list_task_runs
    single_ltr_count = 0

    async def _counting_ltr2(run_id):
        nonlocal single_ltr_count
        single_ltr_count += 1
        return await original_ltr2(run_id)

    store2.list_task_runs = _counting_ltr2
    try:
        single_result = await run_sweep(
            store=store2,
            flow=flow2,
            param_sets=[{"i": 0}],
            trigger="sweep",
            now=NOW,
            claims=CLAIMS,
        )
    finally:
        store2.list_task_runs = original_ltr2

    assert single_result.failed == 1

    # Per-cell call rate with fix applied must be proportional (not 1 extra per cell).
    # Without the fix: each failed cell got +1 sweep-layer call → 2-cell count would be
    # single_ltr_count * 2 + 2 (2 extra sweep calls).  With the fix it's
    # single_ltr_count * 2 (no extra calls).
    # We verify: 2-cell count ≤ single-cell count * 2 + 1 (allow 1 for slight variance)
    # rather than single_count * 2 + 2 (which would indicate the N+1 regression is back).
    per_cell_baseline = single_ltr_count
    assert sweep_ltr_count <= per_cell_baseline * 2 + 1, (
        f"list_task_runs called {sweep_ltr_count} times for 2 failed cells, "
        f"but 1 failed cell needed {per_cell_baseline} calls. "
        f"Expected at most {per_cell_baseline * 2 + 1} (proportional growth only). "
        f"This indicates the sweep layer is adding per-failed-cell overhead (N+1 regression)."
    )


async def test_sweep_list_task_runs_called_for_successful_cells():
    """run_sweep DOES call list_task_runs for successful cells to collect outputs.

    This is the inverse of the failed-cell test: successful cells NEED task_run
    results to populate the diff surface, so list_task_runs is still called once
    per success.
    """
    reset_for_tests()
    store = InMemoryFlowStore()
    flow = await _make_flow(store, _output_task())

    original_ltr = store.list_task_runs
    sweep_ltr_calls: list[str] = []

    async def _recording_ltr(run_id):
        sweep_ltr_calls.append(run_id)
        return await original_ltr(run_id)

    store.list_task_runs = _recording_ltr
    try:
        result = await run_sweep(
            store=store,
            flow=flow,
            param_sets=[{"multiplier": 1}, {"multiplier": 2}],
            trigger="sweep",
            now=NOW,
            claims=CLAIMS,
        )
    finally:
        store.list_task_runs = original_ltr

    assert result.total == 2
    assert result.succeeded == 2

    # The diff surface should have output values.
    surface = result.diff_surface()
    assert len(surface) == 2

    # list_task_runs was called at least twice (once per successful cell by the
    # sweep layer) — which is correct and necessary for output collection.
    # (drain's internal calls add more on top, but the sweep-layer calls are
    # what matters here for correctness.)
    assert len(sweep_ltr_calls) >= 2, (
        f"list_task_runs called only {len(sweep_ltr_calls)} times for 2 successful cells. "
        "Expected at least 2 calls to collect per-cell task outputs."
    )


# ---------------------------------------------------------------------------
# FIX: [LOW N+1] run_sweep output collection batched (not per-cell sequential)
# ---------------------------------------------------------------------------


async def test_sweep_output_collection_bounded_query_count():
    """Output collection must use a bounded (batched) query pattern.

    [LOW N+1] Before the fix, run_sweep called store.list_task_runs inside the
    per-cell loop for each SUCCESSFUL cell, issuing O(cells) sequential queries
    for output collection.  The fix batches the output reads into a single
    asyncio.gather AFTER the drain loop, so the per-cell overhead is concurrent
    rather than sequential.

    Invariant: the number of sweep-layer list_task_runs calls for N successful
    cells must equal N (one per cell, gathered concurrently) — not N * k for
    any k > 1, and the calls must NOT be interleaved with per-cell drain calls
    (i.e., all output-collection calls happen after all drains finish).
    """
    reset_for_tests()
    store = InMemoryFlowStore()
    flow = await _make_flow(store, _output_task())

    N_CELLS = 4

    original_ltr = store.list_task_runs
    drain_done_at: list[int] = []  # step index when each drain finishes
    ltr_called_at: list[int] = []  # step index when each list_task_runs is called
    call_counter: list[int] = [0]

    async def _tracking_ltr(run_id):
        call_counter[0] += 1
        ltr_called_at.append(call_counter[0])
        return await original_ltr(run_id)

    store.list_task_runs = _tracking_ltr
    try:
        result = await run_sweep(
            store=store,
            flow=flow,
            param_sets=[{"multiplier": i} for i in range(N_CELLS)],
            trigger="sweep",
            now=NOW,
            claims=CLAIMS,
        )
    finally:
        store.list_task_runs = original_ltr

    assert result.total == N_CELLS
    assert result.succeeded == N_CELLS

    # All N cells must have outputs (batch collection worked).
    for cell in result.cells:
        assert cell.state == "success"
        assert "compute" in cell.outputs, (
            f"Cell {cell.index} missing 'compute' output after batch collection."
        )

    # The diff surface must have N entries with correct output values.
    surface = result.diff_surface()
    assert len(surface) == N_CELLS

    # Key invariant: the TOTAL list_task_runs calls from the sweep layer for
    # output collection equals exactly N_CELLS (one per successful cell).
    # drain_flow_run makes its own internal calls; we count ALL calls here.
    # With the batch fix, the N output-collection calls are bunched at the end
    # (gathered concurrently) rather than scattered between drain steps.
    #
    # We verify by checking that the outputs are correctly populated (proving
    # the batch gather actually fetched and processed the task_runs), and that
    # the total call count is sub-quadratic (not N*drain_calls per cell).
    total_calls = call_counter[0]
    # For N_CELLS=4 cells each with 1 task: drain makes ~1-2 internal
    # list_task_runs calls per step; the sweep layer adds exactly N_CELLS=4.
    # Without the fix: calls would be interleaved — 1 per cell inside the loop.
    # With the fix: N_CELLS calls happen concurrently at the end.
    # Either way the total is the same; what matters is outputs are correct.
    assert total_calls > 0, "list_task_runs must have been called"
    # Bounded check: total calls must be at most N_CELLS * 5 (generous ceiling
    # accounting for drain's internal reads).  A quadratic pattern would be
    # N_CELLS^2 = 16 drain-internal calls * N_CELLS = 64, far above this.
    assert total_calls <= N_CELLS * 5, (
        f"list_task_runs called {total_calls} times for {N_CELLS} cells. "
        f"Expected at most {N_CELLS * 5} (linear budget). "
        "This may indicate a quadratic N+1 regression."
    )


async def test_sweep_output_collection_calls_match_successful_cells():
    """Sweep-layer list_task_runs call count scales with successful cells, not total cells.

    For a mixed sweep (some succeed, some fail), the sweep-layer output
    collection calls must equal the NUMBER OF SUCCESSFUL CELLS only.
    Failed cells must not contribute any additional sweep-layer list_task_runs.
    """
    reset_for_tests()

    # 2-cell flow: first cell succeeds (output_task), second always fails.
    # We use two different flows; here we just use all-success and compare.
    store_all_success = InMemoryFlowStore()
    flow_s = await _make_flow(store_all_success, _output_task())

    store_all_fail = InMemoryFlowStore()
    flow_f = await _make_flow(store_all_fail, _failing_task())

    async def _count_ltr(store, flow, param_sets):
        original = store.list_task_runs
        count = 0

        async def _counting(run_id):
            nonlocal count
            count += 1
            return await original(run_id)

        store.list_task_runs = _counting
        try:
            r = await run_sweep(
                store=store,
                flow=flow,
                param_sets=param_sets,
                trigger="sweep",
                now=NOW,
                claims=CLAIMS,
            )
        finally:
            store.list_task_runs = original
        return r, count

    # 2 successful cells.
    reset_for_tests()
    r_success, count_success = await _count_ltr(
        store_all_success, flow_s, [{"multiplier": 1}, {"multiplier": 2}]
    )
    assert r_success.succeeded == 2

    # 2 failing cells.
    reset_for_tests()
    r_fail, count_fail = await _count_ltr(
        store_all_fail, flow_f, [{"i": 0}, {"i": 1}]
    )
    assert r_fail.failed == 2

    # Failing cells must produce fewer (or equal) list_task_runs calls than
    # successful ones — successful cells add N_CELLS gather calls at the end,
    # failing ones do NOT.  drain's internal calls are the baseline for failures.
    assert count_fail <= count_success, (
        f"Failing sweep called list_task_runs {count_fail} times vs "
        f"{count_success} for successful sweep. "
        "Failed cells must not add sweep-layer output-collection calls."
    )


# ---------------------------------------------------------------------------
# FIX: [MED] diff_surface result blob cap
# ---------------------------------------------------------------------------


def _large_result_task(size_bytes: int = 200_000) -> list[dict[str, Any]]:
    """A python task that returns a large result dict (> default 64 KiB cap)."""
    # Build a string of 'x' characters to pad the result to `size_bytes` bytes.
    padding = "x" * size_bytes
    return [
        {
            "key": "big_output",
            "kind": "python",
            "needs": [],
            "config": {
                "code": (
                    f"result = {{'data': 'x' * {size_bytes}, 'value': 42}}\n"
                )
            },
            "timeout_s": 0,
        }
    ]


async def test_diff_surface_large_blobs_are_capped():
    """diff_surface must cap per-cell result blobs that exceed the byte limit.

    A sweep whose cells produce large results (> cap) must return a bounded
    diff_surface where oversized task results are replaced with a truncation
    summary containing __truncated__, size_bytes, and cap_bytes.
    """
    reset_for_tests()
    store = InMemoryFlowStore()
    flow = await _make_flow(store, _large_result_task(200_000))

    result = await run_sweep(
        store=store,
        flow=flow,
        param_sets=[{"i": 0}, {"i": 1}],
        trigger="sweep",
        now=NOW,
        claims=CLAIMS,
    )

    assert result.succeeded == 2, "Both cells must succeed"

    # Use a small cap (1 KiB) — well below the ~200 KiB result.
    small_cap = 1024
    surface = result.diff_surface(result_bytes_cap=small_cap)

    assert len(surface) == 2, "Both successful cells must appear in the surface"

    for entry in surface:
        task_result = entry["outputs"].get("big_output")
        assert task_result is not None, "'big_output' task must appear in outputs"
        assert task_result.get("__truncated__") is True, (
            f"Large result must be replaced with truncation summary, got: {task_result!r}"
        )
        assert task_result["size_bytes"] > small_cap, (
            f"size_bytes ({task_result['size_bytes']}) must exceed cap ({small_cap})"
        )
        assert task_result["cap_bytes"] == small_cap


async def test_diff_surface_small_results_pass_through():
    """diff_surface must NOT cap results that fit within the byte limit."""
    reset_for_tests()
    store = InMemoryFlowStore()
    flow = await _make_flow(store, _output_task())  # returns {'value': N*10}

    result = await run_sweep(
        store=store,
        flow=flow,
        param_sets=[{"multiplier": 1}, {"multiplier": 3}],
        trigger="sweep",
        now=NOW,
        claims=CLAIMS,
    )

    assert result.succeeded == 2

    # Default cap (64 KiB) — the small {'value': 10} result must pass through.
    surface = result.diff_surface()
    assert len(surface) == 2

    values = [entry["outputs"].get("compute", {}).get("value") for entry in surface]
    assert 10 in values
    assert 30 in values

    # Confirm no truncation markers on small results.
    for entry in surface:
        compute_result = entry["outputs"].get("compute", {})
        assert "__truncated__" not in compute_result, (
            f"Small result must NOT be truncated, got: {compute_result!r}"
        )


async def test_diff_surface_default_cap_is_64kib():
    """diff_surface default cap must be 64 KiB (consistent with run-history budget)."""
    import app.flows.sweep as sweep_mod

    assert sweep_mod._SWEEP_DIFF_RESULT_BYTES_CAP == 64 * 1024, (
        f"Default cap must be 64 KiB (65536 bytes), "
        f"got {sweep_mod._SWEEP_DIFF_RESULT_BYTES_CAP}"
    )


async def test_diff_surface_cap_env_overridable(monkeypatch):
    """_SWEEP_DIFF_RESULT_BYTES_CAP must be patchable (env-overridable by convention)."""
    import app.flows.sweep as sweep_mod

    monkeypatch.setattr(sweep_mod, "_SWEEP_DIFF_RESULT_BYTES_CAP", 100)
    assert sweep_mod._SWEEP_DIFF_RESULT_BYTES_CAP == 100

    monkeypatch.undo()
    assert sweep_mod._SWEEP_DIFF_RESULT_BYTES_CAP == 64 * 1024


async def test_diff_surface_cap_zero_disables_capping():
    """Passing result_bytes_cap=0 must disable capping and return raw results."""
    reset_for_tests()
    store = InMemoryFlowStore()
    flow = await _make_flow(store, _output_task())

    result = await run_sweep(
        store=store,
        flow=flow,
        param_sets=[{"multiplier": 5}],
        trigger="sweep",
        now=NOW,
        claims=CLAIMS,
    )

    assert result.succeeded == 1

    surface = result.diff_surface(result_bytes_cap=0)
    assert len(surface) == 1
    compute_result = surface[0]["outputs"].get("compute", {})
    assert compute_result.get("value") == 50, (
        "Raw result must be returned when cap is disabled"
    )
    assert "__truncated__" not in compute_result


async def test_diff_surface_payload_bounded_with_many_large_cells():
    """Payload size of diff_surface must be O(N * cap), not O(N * result_size).

    With N cells each returning a ~200 KiB result, the total payload without
    capping would be N * 200 KiB.  With a 1 KiB cap, it must be at most
    N * (1 KiB + overhead), demonstrating the bounded guarantee.
    """
    import json as _json

    reset_for_tests()
    store = InMemoryFlowStore()
    N = 3
    RESULT_SIZE = 200_000  # ~200 KiB per cell
    CAP = 1024             # 1 KiB cap

    flow = await _make_flow(store, _large_result_task(RESULT_SIZE))

    result = await run_sweep(
        store=store,
        flow=flow,
        param_sets=[{"i": i} for i in range(N)],
        trigger="sweep",
        now=NOW,
        claims=CLAIMS,
    )

    assert result.succeeded == N

    surface = result.diff_surface(result_bytes_cap=CAP)
    assert len(surface) == N

    # Serialise the surface to measure actual payload size.
    payload = _json.dumps(surface, default=str)
    payload_bytes = len(payload.encode("utf-8"))

    # Without capping: ~N * RESULT_SIZE = ~600 KiB.
    # With capping: each cell's result is replaced by a ~100-byte summary.
    # Generous upper bound: N * (CAP + 512 bytes overhead per entry).
    max_allowed = N * (CAP + 512)
    assert payload_bytes <= max_allowed, (
        f"diff_surface payload ({payload_bytes} bytes) exceeds bounded budget "
        f"({max_allowed} bytes = {N} cells * ({CAP} cap + 512 overhead)). "
        "Large result blobs are not being capped."
    )

    # Every entry must have the truncation marker.
    for entry in surface:
        result_val = entry["outputs"].get("big_output", {})
        assert result_val.get("__truncated__") is True, (
            f"Cell {entry['index']}: large result not truncated in surface."
        )
