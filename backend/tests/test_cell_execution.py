"""Tests for notebook cell execution: PREVIEW vs DURABLE modes + Python→SQL bridge.

Coverage
--------
1. preview_cell — basic SQL cell
   a. Returns capped rows (preview_limit applied via planner LIMIT).
   b. State is 'success', result has rows/row_count/columns.

2. preview_cell — python cell
   a. Python cell executes code, returns result dict.
   b. preview_mode flag is passed through TaskContext.

3. preview_cell — python→sql bridge chain
   a. Python cell produces rows.
   b. Downstream SQL cell reads from the python cell's table via bridge.
   c. preview_limit caps the final SQL output.

4. preview_cell — sequential notebook (no explicit needs)
   a. Cells with needs=[] run in listed order when target has explicit needs.
   b. Only cells in the transitive dependency chain of target_key execute.

5. preview_cell — failure propagation
   a. A failing python cell returns state='failed' with error set.
   b. cell_results only contains results up to the failing cell.

6. TaskContext — new fields
   a. org_id, preview_mode, preview_limit have correct defaults.
   b. Fields are preserved through dataclasses.replace().

7. execute_task — preview_mode injects row cap
   a. A standalone SQL task with preview_mode=True returns at most
      preview_limit rows.

8. execute_task — bridge without preview_mode (durable path)
   a. SQL task consuming Python cell output resolves the bridge table.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from app.flows.executor import TaskContext, execute_task
from app.flows.registry import reset_for_tests
from app.flows.runtime import preview_cell

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

NOW = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
CLAIMS: dict[str, Any] = {"org_id": "org-test", "sub": "user-test"}


def _sql_task(key: str, sql: str, needs: list[str] | None = None) -> dict[str, Any]:
    return {
        "key": key,
        "kind": "query",
        "needs": needs or [],
        "config": {"sql": sql},
        "retries": 0,
        "timeout_s": 0,
        "cache_ttl_s": 0,
    }


def _python_task(key: str, code: str, needs: list[str] | None = None) -> dict[str, Any]:
    return {
        "key": key,
        "kind": "python",
        "needs": needs or [],
        "config": {"code": code},
        "retries": 0,
        "timeout_s": 30,
        "cache_ttl_s": 0,
    }


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    reset_for_tests()


# ---------------------------------------------------------------------------
# 1. preview_cell — basic SQL cell
# ---------------------------------------------------------------------------


class TestPreviewCellSql:
    def test_returns_success_with_rows(self) -> None:
        """Single SQL cell returns success with rows/row_count/columns."""
        tasks = [_sql_task("cell_a", "SELECT 1 AS n, 2 AS m")]
        out = preview_cell(tasks, "cell_a", claims=CLAIMS, now=NOW)
        assert out["state"] == "success"
        result = out["result"]
        assert result is not None
        assert "rows" in result
        assert result["row_count"] >= 1
        assert "n" in result["columns"]

    def test_preview_limit_caps_rows(self) -> None:
        """SQL returning many rows is capped to preview_limit."""
        # Generate 20 rows using DuckDB's unnest/range; planner accepts SELECT.
        tasks = [
            _sql_task(
                "cell_many",
                "SELECT n FROM (SELECT unnest(range(20)) AS n)",
            )
        ]
        out = preview_cell(tasks, "cell_many", claims=CLAIMS, now=NOW, preview_limit=5)
        assert out["state"] == "success", f"Failed: {out.get('error')}"
        assert out["result"]["row_count"] <= 5
        assert len(out["result"]["rows"]) <= 5

    def test_cell_results_contains_target(self) -> None:
        """cell_results dict contains the target cell's result."""
        tasks = [_sql_task("cell_x", "SELECT 42 AS val")]
        out = preview_cell(tasks, "cell_x", claims=CLAIMS, now=NOW)
        assert "cell_x" in out["cell_results"]
        assert out["cell_results"]["cell_x"]["rows"][0]["val"] == 42


# ---------------------------------------------------------------------------
# 2. preview_cell — python cell
# ---------------------------------------------------------------------------


class TestPreviewCellPython:
    def test_python_cell_runs_code(self) -> None:
        """Python cell executes and returns result dict."""
        code = "result = {'answer': 6 * 7}"
        tasks = [_python_task("cell_py", code)]
        out = preview_cell(tasks, "cell_py", claims=CLAIMS, now=NOW)
        assert out["state"] == "success"
        assert out["result"]["answer"] == 42

    def test_cell_results_populated(self) -> None:
        code = "result = {'x': 1}"
        tasks = [_python_task("cell_py2", code)]
        out = preview_cell(tasks, "cell_py2", claims=CLAIMS, now=NOW)
        assert out["cell_results"]["cell_py2"]["x"] == 1


# ---------------------------------------------------------------------------
# 3. preview_cell — Python→SQL bridge chain
# ---------------------------------------------------------------------------


class TestPythonSqlBridge:
    def test_sql_reads_python_output_as_table(self) -> None:
        """SQL cell can SELECT FROM a table registered from a Python cell's rows."""
        # Python cell produces rows with column 'score'.
        python_code = (
            "result = {'rows': [{'score': 10}, {'score': 20}, {'score': 30}],"
            " 'row_count': 3, 'columns': ['score']}"
        )
        tasks = [
            _python_task("cell_py", python_code),
            _sql_task("cell_sql", "SELECT score FROM cell_py ORDER BY score", needs=["cell_py"]),
        ]
        out = preview_cell(tasks, "cell_sql", claims=CLAIMS, now=NOW)
        assert out["state"] == "success", f"Failed: {out.get('error')}"
        rows = out["result"]["rows"]
        assert len(rows) == 3
        scores = [r["score"] for r in rows]
        assert scores == [10, 20, 30]

    def test_bridge_with_preview_limit(self) -> None:
        """Python→SQL bridge respects preview_limit on the downstream SQL."""
        python_code = (
            "result = {'rows': [{'v': i} for i in range(10)],"
            " 'row_count': 10, 'columns': ['v']}"
        )
        tasks = [
            _python_task("cell_src", python_code),
            _sql_task("cell_consumer", "SELECT v FROM cell_src", needs=["cell_src"]),
        ]
        out = preview_cell(
            tasks, "cell_consumer", claims=CLAIMS, now=NOW, preview_limit=3
        )
        assert out["state"] == "success", f"Failed: {out.get('error')}"
        assert out["result"]["row_count"] <= 3

    def test_only_upstream_cells_run(self) -> None:
        """Cells not in the dependency chain of target_key are not executed."""
        tasks = [
            _sql_task("cell_unused", "SELECT 1/0"),  # Would fail if run.
            _sql_task("cell_target", "SELECT 99 AS n"),
        ]
        out = preview_cell(tasks, "cell_target", claims=CLAIMS, now=NOW)
        assert out["state"] == "success"
        # cell_unused must not appear in cell_results.
        assert "cell_unused" not in out["cell_results"]


# ---------------------------------------------------------------------------
# 4. preview_cell — dependency ordering
# ---------------------------------------------------------------------------


class TestPreviewCellOrdering:
    def test_transitive_chain_executes_in_order(self) -> None:
        """A → B → C: executing C runs A then B then C."""
        tasks = [
            _sql_task("cell_a", "SELECT 1 AS step"),
            _sql_task("cell_b", "SELECT 2 AS step", needs=["cell_a"]),
            _sql_task("cell_c", "SELECT 3 AS step", needs=["cell_b"]),
        ]
        out = preview_cell(tasks, "cell_c", claims=CLAIMS, now=NOW)
        assert out["state"] == "success"
        # All three cells should have run.
        assert set(out["cell_results"].keys()) == {"cell_a", "cell_b", "cell_c"}

    def test_diamond_dag_executes_all_paths(self) -> None:
        """A→B, A→C, B&C→D: all four cells execute."""
        tasks = [
            _sql_task("cell_a", "SELECT 1 AS n"),
            _sql_task("cell_b", "SELECT 2 AS n", needs=["cell_a"]),
            _sql_task("cell_c", "SELECT 3 AS n", needs=["cell_a"]),
            _sql_task("cell_d", "SELECT 4 AS n", needs=["cell_b", "cell_c"]),
        ]
        out = preview_cell(tasks, "cell_d", claims=CLAIMS, now=NOW)
        assert out["state"] == "success"
        assert set(out["cell_results"].keys()) == {"cell_a", "cell_b", "cell_c", "cell_d"}


# ---------------------------------------------------------------------------
# 5. preview_cell — failure propagation
# ---------------------------------------------------------------------------


class TestPreviewCellFailure:
    def test_failing_cell_returns_failed_state(self) -> None:
        """A Python cell that raises causes preview_cell to return state='failed'."""
        code = "raise ValueError('intentional test error')"
        tasks = [_python_task("cell_fail", code)]
        out = preview_cell(tasks, "cell_fail", claims=CLAIMS, now=NOW)
        assert out["state"] == "failed"
        assert out["error"] is not None
        assert "intentional test error" in out["error"]

    def test_downstream_not_executed_after_failure(self) -> None:
        """If an upstream cell fails, downstream cells are not executed."""
        bad_code = "raise RuntimeError('upstream fail')"
        tasks = [
            _python_task("cell_bad", bad_code),
            _sql_task("cell_good", "SELECT 1 AS n", needs=["cell_bad"]),
        ]
        out = preview_cell(tasks, "cell_good", claims=CLAIMS, now=NOW)
        assert out["state"] == "failed"
        # cell_good must not appear in cell_results.
        assert "cell_good" not in out["cell_results"]
        assert out.get("failed_cell") == "cell_bad"


# ---------------------------------------------------------------------------
# 6. TaskContext — new fields
# ---------------------------------------------------------------------------


class TestTaskContextFields:
    def test_default_values(self) -> None:
        ctx = TaskContext()
        assert ctx.org_id is None
        assert ctx.preview_mode is False
        assert ctx.preview_limit == 500

    def test_explicit_values(self) -> None:
        ctx = TaskContext(org_id="org-xyz", preview_mode=True, preview_limit=100)
        assert ctx.org_id == "org-xyz"
        assert ctx.preview_mode is True
        assert ctx.preview_limit == 100

    def test_replace_preserves_new_fields(self) -> None:
        from dataclasses import replace

        ctx = TaskContext(org_id="org-a", preview_mode=True, preview_limit=50)
        ctx2 = replace(ctx, preview_limit=10)
        assert ctx2.org_id == "org-a"
        assert ctx2.preview_mode is True
        assert ctx2.preview_limit == 10


# ---------------------------------------------------------------------------
# 7. execute_task — preview_mode injects row cap
# ---------------------------------------------------------------------------


class TestExecuteTaskPreviewMode:
    def test_sql_task_in_preview_mode_capped(self) -> None:
        """execute_task with preview_mode=True caps query results."""
        task = _sql_task(
            "cell_big",
            "SELECT n FROM (SELECT unnest(range(30)) AS n)",
        )
        ctx = TaskContext(preview_mode=True, preview_limit=4)
        out = execute_task(task, ctx, CLAIMS)
        assert out["state"] == "success", f"error: {out.get('error')}"
        assert out["result"]["row_count"] <= 4

    def test_sql_task_without_preview_returns_all(self) -> None:
        """execute_task without preview_mode returns all rows."""
        task = _sql_task(
            "cell_all",
            "SELECT n FROM (SELECT unnest(range(10)) AS n)",
        )
        ctx = TaskContext(preview_mode=False)
        out = execute_task(task, ctx, CLAIMS)
        assert out["state"] == "success", f"error: {out.get('error')}"
        assert out["result"]["row_count"] == 10


# ---------------------------------------------------------------------------
# 8. execute_task — bridge without preview_mode (durable path)
# ---------------------------------------------------------------------------


class TestExecuteTaskBridgeDurable:
    def test_sql_consumes_python_rows_in_durable_mode(self) -> None:
        """Durable SQL task reads upstream Python cell output via bridge."""
        # Simulate a Python cell result already in ctx.inputs.
        upstream_rows = [{"amount": 100}, {"amount": 200}]
        inputs = {
            "cell_transform": {
                "rows": upstream_rows,
                "row_count": 2,
                "columns": ["amount"],
            }
        }
        ctx = TaskContext(inputs=inputs, preview_mode=False)
        task = _sql_task(
            "cell_sql2",
            "SELECT amount FROM cell_transform ORDER BY amount",
        )
        out = execute_task(task, ctx, CLAIMS)
        assert out["state"] == "success", f"error: {out.get('error')}"
        rows = out["result"]["rows"]
        assert [r["amount"] for r in rows] == [100, 200]
