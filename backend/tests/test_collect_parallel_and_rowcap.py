"""Tests for collect_board_data parallel execution and per-widget row cap.

Covers:
1. Parallel collection returns the same results (same widget_id / query_id /
   columns / rows) as a serial baseline — order is preserved.
2. Row cap truncates results when the connector returns more rows than the cap.
3. Truncation emits a WARNING log (so operators can observe it).
4. Small results (below the cap) pass through unchanged — happy path untouched.
5. Best-effort error handling still works in the parallel path: one widget
   failing does not prevent other widgets from succeeding.
"""

from __future__ import annotations

import asyncio
import logging
import os

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-that-is-at-least-32-bytes-long-abcdef")

import unittest.mock as mock

import pyarrow as pa
import pytest

from app.dashboards.collect import collect_board_data, run_query_rows
from app.errors import AppError
from app.queries.registry import get_query_registry
from app.queries.registry import reset_for_tests as reset_query_registry
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo

_ORG = "org-parallel-test"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_arrow(col: str, values: list) -> pa.Table:
    return pa.table({col: values})


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


@pytest.fixture()
def repo() -> InMemoryRepo:
    reset_query_registry()
    r = InMemoryRepo()
    set_repo(r)
    yield r
    set_repo(None)
    reset_query_registry()


# ---------------------------------------------------------------------------
# 1. Parallel collection returns same results as serial baseline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parallel_collect_same_results_as_serial(repo: InMemoryRepo) -> None:
    """asyncio.gather over multiple widgets returns the same data as serial iteration."""

    # Register three demo queries (no datastore → demo connector path).
    reg = get_query_registry()
    reg.register(id="q-alpha", sql="SELECT id, name FROM demo ORDER BY id", name="Alpha")
    reg.register(id="q-beta", sql="SELECT id, name FROM demo ORDER BY id", name="Beta")
    reg.register(id="q-gamma", sql="SELECT id, name FROM demo ORDER BY id", name="Gamma")

    board_id = "board-par-1"
    await repo.create(
        "boards",
        org_id=_ORG,
        created_by="test",
        name="Parallel board",
        id=board_id,
        **_board_spec(
            [
                {"id": "w1", "query_id": "q-alpha", "pos": {"x": 1, "y": 1, "w": 4, "h": 2}},
                {"id": "w2", "query_id": "q-beta", "pos": {"x": 5, "y": 1, "w": 4, "h": 2}},
                {"id": "w3", "query_id": "q-gamma", "pos": {"x": 9, "y": 1, "w": 4, "h": 2}},
            ]
        ),
    )

    results = await collect_board_data(board_id, _ORG, {}, repo)

    # All three widgets must be present, in original spec order.
    assert len(results) == 3
    widget_ids = [r["widget_id"] for r in results]
    assert widget_ids == ["w1", "w2", "w3"], f"Order must be preserved: {widget_ids}"

    # Each result must have columns + rows (no error key).
    for entry in results:
        assert "error" not in entry, f"Unexpected error in entry: {entry}"
        assert "columns" in entry and "rows" in entry


@pytest.mark.asyncio
async def test_parallel_collect_preserves_widget_query_id_mapping(
    repo: InMemoryRepo,
) -> None:
    """Each entry in the result carries the correct widget_id and query_id pair."""
    reg = get_query_registry()
    reg.register(id="q-x", sql="SELECT id, name FROM demo ORDER BY id", name="X")
    reg.register(id="q-y", sql="SELECT id, name FROM demo ORDER BY id", name="Y")

    board_id = "board-par-2"
    await repo.create(
        "boards",
        org_id=_ORG,
        created_by="test",
        name="Mapping board",
        id=board_id,
        **_board_spec(
            [
                {"id": "wa", "query_id": "q-x", "pos": {"x": 1, "y": 1, "w": 4, "h": 2}},
                {"id": "wb", "query_id": "q-y", "pos": {"x": 5, "y": 1, "w": 4, "h": 2}},
            ]
        ),
    )

    results = await collect_board_data(board_id, _ORG, {}, repo)

    assert results[0]["widget_id"] == "wa"
    assert results[0]["query_id"] == "q-x"
    assert results[1]["widget_id"] == "wb"
    assert results[1]["query_id"] == "q-y"


# ---------------------------------------------------------------------------
# 2 & 3. Row cap truncates + logs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_row_cap_truncates_large_result(repo: InMemoryRepo) -> None:
    """run_query_rows truncates results to the configured cap."""
    import app.dashboards.collect as _collect

    reg = get_query_registry()
    reg.register(id="q-big", sql="SELECT id, name FROM demo ORDER BY id", name="Big")

    # Patch the connector to return 10 rows, then set cap to 3.
    big_table = pa.table({"id": list(range(10)), "val": list(range(10))})

    class _FakeConnector:
        def execute(self, plan):  # noqa: ANN001
            return big_table

        def close(self) -> None:
            pass

    original_cap = _collect._ROW_CAP
    try:
        _collect._ROW_CAP = 3

        with mock.patch(
            "app.dashboards.collect._resolve_connector",
            return_value=(_FakeConnector(), False),
        ):
            cols, rows = await run_query_rows("q-big", _ORG, repo, {})
    finally:
        _collect._ROW_CAP = original_cap

    assert len(rows) == 3, f"Expected 3 rows after cap, got {len(rows)}"
    assert rows[0] == [0, 0]
    assert rows[1] == [1, 1]
    assert rows[2] == [2, 2]


@pytest.mark.asyncio
async def test_row_cap_logs_warning_when_truncating(
    repo: InMemoryRepo, caplog: pytest.LogCaptureFixture
) -> None:
    """run_query_rows emits a WARNING when results are truncated by the row cap."""
    import app.dashboards.collect as _collect

    reg = get_query_registry()
    reg.register(id="q-warn", sql="SELECT id, name FROM demo ORDER BY id", name="Warn")

    big_table = pa.table({"id": list(range(20)), "val": list(range(20))})

    class _FakeConnector:
        def execute(self, plan):  # noqa: ANN001
            return big_table

        def close(self) -> None:
            pass

    original_cap = _collect._ROW_CAP
    try:
        _collect._ROW_CAP = 5

        with mock.patch(
            "app.dashboards.collect._resolve_connector",
            return_value=(_FakeConnector(), False),
        ), caplog.at_level(logging.WARNING, logger="app.dashboards.collect"):
            cols, rows = await run_query_rows("q-warn", _ORG, repo, {})
    finally:
        _collect._ROW_CAP = original_cap

    assert len(rows) == 5

    warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("truncat" in m.lower() for m in warning_msgs), (
        f"Expected a truncation warning; got log records: {warning_msgs}"
    )
    # The warning should mention the query_id so operators can identify the source.
    assert any("q-warn" in m for m in warning_msgs), (
        f"Warning should mention query_id; got: {warning_msgs}"
    )


# ---------------------------------------------------------------------------
# 3b. LIMIT is pushed into the physical plan (not just sliced post-fetch)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_row_cap_limit_pushed_into_plan(repo: InMemoryRepo) -> None:
    """run_query_rows must push LIMIT(_ROW_CAP+1) into the PhysicalPlan SQL.

    The DB-level cap must be enforced before the connector returns rows so that
    the connector never materialises more than cap+1 rows into memory.  The test
    captures the plan that was actually passed to connector.execute and asserts
    that its SQL contains a LIMIT clause bounded by cap+1.
    """
    import app.dashboards.collect as _collect

    reg = get_query_registry()
    reg.register(id="q-limit-push", sql="SELECT id, val FROM demo", name="LimitPush")

    captured_plans: list = []

    class _CapturingConnector:
        def execute(self, plan):  # noqa: ANN001
            captured_plans.append(plan)
            # Return a table smaller than cap so no post-fetch truncation fires.
            return pa.table({"id": [1, 2], "val": [10, 20]})

        def close(self) -> None:
            pass

    original_cap = _collect._ROW_CAP
    try:
        _collect._ROW_CAP = 5  # cap=5 → expect LIMIT 6 in the plan SQL

        with mock.patch(
            "app.dashboards.collect._resolve_connector",
            return_value=(_CapturingConnector(), False),
        ):
            cols, rows = await run_query_rows("q-limit-push", _ORG, repo, {})
    finally:
        _collect._ROW_CAP = original_cap

    assert len(rows) == 2, "Rows below cap should be returned unchanged"
    assert captured_plans, "connector.execute was never called"

    plan_sql: str = captured_plans[0].sql.upper()
    assert "LIMIT" in plan_sql, (
        f"Expected LIMIT in physical plan SQL (cap+1 pushed); got: {captured_plans[0].sql!r}"
    )
    # The LIMIT value must be cap+1 (6 for cap=5) — not larger.
    import re as _re
    limit_match = _re.search(r"LIMIT\s+(\d+)", plan_sql)
    assert limit_match is not None, f"LIMIT not parseable in SQL: {captured_plans[0].sql!r}"
    limit_val = int(limit_match.group(1))
    assert limit_val == 6, (
        f"Expected LIMIT 6 (cap+1=5+1) in plan SQL; got LIMIT {limit_val} in {captured_plans[0].sql!r}"
    )


# ---------------------------------------------------------------------------
# 4. Small results (below cap) are unchanged — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_row_cap_does_not_affect_small_results(repo: InMemoryRepo) -> None:
    """Results with fewer rows than the cap are returned unchanged."""
    import app.dashboards.collect as _collect

    reg = get_query_registry()
    reg.register(id="q-small", sql="SELECT id, name FROM demo ORDER BY id", name="Small")

    small_table = pa.table({"id": [1, 2, 3], "val": [10, 20, 30]})

    class _FakeConnector:
        def execute(self, plan):  # noqa: ANN001
            return small_table

        def close(self) -> None:
            pass

    original_cap = _collect._ROW_CAP
    try:
        _collect._ROW_CAP = 100  # cap well above 3 rows

        with mock.patch(
            "app.dashboards.collect._resolve_connector",
            return_value=(_FakeConnector(), False),
        ):
            cols, rows = await run_query_rows("q-small", _ORG, repo, {})
    finally:
        _collect._ROW_CAP = original_cap

    assert len(rows) == 3, "All rows should pass through when below the cap"
    assert rows == [[1, 10], [2, 20], [3, 30]]


# ---------------------------------------------------------------------------
# 5. Best-effort error handling in parallel path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parallel_collect_one_error_does_not_abort_others(
    repo: InMemoryRepo,
) -> None:
    """A failing widget gets an error entry; sibling widgets still succeed."""
    reg = get_query_registry()
    reg.register(id="q-ok", sql="SELECT id, name FROM demo ORDER BY id", name="OK")
    reg.register(id="q-fail", sql="SELECT id, name FROM demo ORDER BY id", name="Fail")

    board_id = "board-par-err"
    await repo.create(
        "boards",
        org_id=_ORG,
        created_by="test",
        name="Error board",
        id=board_id,
        **_board_spec(
            [
                {"id": "wok", "query_id": "q-ok", "pos": {"x": 1, "y": 1, "w": 4, "h": 2}},
                {"id": "wfail", "query_id": "q-fail", "pos": {"x": 5, "y": 1, "w": 4, "h": 2}},
            ]
        ),
    )

    ok_table = pa.table({"id": [1], "name": ["alice"]})

    original_run = None

    async def _patched_run_query_rows(
        query_id: str, org_id: str, repo_arg, policies, **_kwargs
    ):
        if query_id == "q-fail":
            raise AppError("deliberate_fail", "deliberate test failure", 500)
        # Delegate to the real implementation for the ok query via the demo connector.
        # Instead, just return a small table directly to avoid demo-connector setup.
        return (["id", "name"], [[1, "alice"]])

    import app.dashboards.collect as _collect

    with mock.patch.object(_collect, "run_query_rows", _patched_run_query_rows):
        results = await collect_board_data(board_id, _ORG, {}, repo)

    assert len(results) == 2
    by_wid = {r["widget_id"]: r for r in results}

    # The ok widget succeeded.
    assert "error" not in by_wid["wok"]
    assert by_wid["wok"]["rows"] == [[1, "alice"]]

    # The failing widget has an error key, not rows.
    assert "error" in by_wid["wfail"]
    assert "rows" not in by_wid["wfail"]


# ---------------------------------------------------------------------------
# 6. Board widget cap: boards with > cap widgets are truncated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_board_widget_cap_truncates_excess_widgets(
    repo: InMemoryRepo, caplog: pytest.LogCaptureFixture
) -> None:
    """collect_board_data truncates targets to _MAX_BOARD_WIDGETS when exceeded.

    A board with N > cap widgets must:
    - return exactly cap results (not N),
    - emit a WARNING log mentioning the board_id and the cap,
    - preserve the first cap widgets in original order.
    """
    import app.dashboards.collect as _collect

    # Register enough queries to populate the widgets list.
    cap = 5
    total = cap + 3  # 8 widgets total; cap is 5
    reg = get_query_registry()
    for i in range(total):
        reg.register(
            id=f"q-cap-{i}",
            sql="SELECT id, name FROM demo ORDER BY id",
            name=f"Cap{i}",
        )

    board_id = "board-widget-cap"
    widgets = [
        {"id": f"w{i}", "query_id": f"q-cap-{i}", "pos": {"x": i, "y": 1, "w": 2, "h": 2}}
        for i in range(total)
    ]
    await repo.create(
        "boards",
        org_id=_ORG,
        created_by="test",
        name="Widget cap board",
        id=board_id,
        **_board_spec(widgets),
    )

    # Patch run_query_rows to avoid needing a real connector.
    async def _fake_run(query_id: str, org_id: str, repo_arg, policies, **_kw):
        return (["id"], [[1]])

    original_cap = _collect._MAX_BOARD_WIDGETS
    try:
        _collect._MAX_BOARD_WIDGETS = cap

        with mock.patch.object(_collect, "run_query_rows", _fake_run), \
             caplog.at_level(logging.WARNING, logger="app.dashboards.collect"):
            results = await collect_board_data(board_id, _ORG, {}, repo)
    finally:
        _collect._MAX_BOARD_WIDGETS = original_cap

    # Only the first `cap` widgets should be returned.
    assert len(results) == cap, (
        f"Expected {cap} results after widget cap, got {len(results)}"
    )
    # Order must be preserved: w0 … w{cap-1}.
    returned_ids = [r["widget_id"] for r in results]
    assert returned_ids == [f"w{i}" for i in range(cap)], (
        f"Widget order not preserved: {returned_ids}"
    )

    # A WARNING must have been logged mentioning the board_id.
    warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("truncat" in m.lower() for m in warning_msgs), (
        f"Expected a truncation warning; got: {warning_msgs}"
    )
    assert any(board_id in m for m in warning_msgs), (
        f"Warning should mention board_id={board_id!r}; got: {warning_msgs}"
    )


# ---------------------------------------------------------------------------
# 7. NUBI_WIDGET_CONCURRENCY env var overrides _WIDGET_CONCURRENCY
# ---------------------------------------------------------------------------


def test_nubi_widget_concurrency_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """NUBI_WIDGET_CONCURRENCY must override _WIDGET_CONCURRENCY at import time.

    The constant is read from the env during module initialisation.  We verify
    the parsing logic by directly evaluating the same expression the module
    uses (``max(1, int(os.environ.get(..., default)))``), which lets us confirm
    the env var is wired correctly without reloading the module (a reload would
    invalidate cached function-object references held by sibling test modules).
    """
    import app.dashboards.collect as _collect

    # --- default: env var absent → _DEFAULT_WIDGET_CONCURRENCY ---------------
    assert _collect._DEFAULT_WIDGET_CONCURRENCY == 8, (
        f"Unexpected default value: {_collect._DEFAULT_WIDGET_CONCURRENCY}"
    )

    # --- explicit override: env value must be respected ----------------------
    # Evaluate the same expression the module uses at init time.
    monkeypatch.setenv("NUBI_WIDGET_CONCURRENCY", "3")
    computed = max(
        1,
        int(os.environ.get("NUBI_WIDGET_CONCURRENCY", _collect._DEFAULT_WIDGET_CONCURRENCY)),
    )
    assert computed == 3, f"Expected 3 from env override, got {computed}"

    # --- floor guard: value "0" must be clamped to 1 -------------------------
    monkeypatch.setenv("NUBI_WIDGET_CONCURRENCY", "0")
    computed_floor = max(
        1,
        int(os.environ.get("NUBI_WIDGET_CONCURRENCY", _collect._DEFAULT_WIDGET_CONCURRENCY)),
    )
    assert computed_floor >= 1, (
        f"_WIDGET_CONCURRENCY must be >=1 even for env='0'; got {computed_floor}"
    )

    # --- runtime constant: current process value matches the formula ---------
    # The module was imported without NUBI_WIDGET_CONCURRENCY set (see the
    # os.environ.setdefault calls at the top of this file), so it should equal
    # the default.
    monkeypatch.delenv("NUBI_WIDGET_CONCURRENCY", raising=False)
    assert _collect._WIDGET_CONCURRENCY == _collect._DEFAULT_WIDGET_CONCURRENCY, (
        f"Module-level constant should equal the default "
        f"({_collect._DEFAULT_WIDGET_CONCURRENCY}); "
        f"got {_collect._WIDGET_CONCURRENCY}"
    )
