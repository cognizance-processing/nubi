"""Engine + python-handler tests for the flows v2 redesign.

Covers:
- Inferred ordering: a 2nd query cell `SELECT * FROM first` with NO explicit
  `needs` runs after `first` (materialize_flow_run sets depends_on from
  effective_needs; drain honours it).
- preview_cell pulls SQL-referenced upstream cells via effective deps even with
  no explicit edge.
- Python handler: a cell returning a pandas.DataFrame serialises to
  {rows, columns, row_count}; `dataframes[...]` is populated from upstream
  {rows, columns}; a downstream SQL cell reads the DataFrame via the bridge.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from app.flows.registry import reset_for_tests
from app.flows.runtime import (
    drain_flow_run,
    materialize_flow_run,
    preview_cell,
)
from app.flows.store import InMemoryFlowStore

NOW = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
CLAIMS: dict[str, Any] = {}


async def _make_flow(store: InMemoryFlowStore, spec: dict[str, Any]) -> dict[str, Any]:
    return await store.create_flow(
        org_id="org-test", created_by="user-test", name="inferred_flow", spec=spec
    )


# ---------------------------------------------------------------------------
# Inferred ordering through the durable engine
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestInferredOrderingEngine:
    def setup_method(self):
        reset_for_tests()
        self.store = InMemoryFlowStore()

    async def test_depends_on_set_from_inferred_ref(self):
        spec = {
            "version": 1,
            "name": "inferred",
            "tasks": [
                {"key": "first", "kind": "query", "needs": [], "config": {"sql": "SELECT 1 AS n"}},
                # No explicit needs — must be inferred from `FROM first`.
                {"key": "second", "kind": "query", "needs": [], "config": {"sql": "SELECT * FROM first"}},
            ],
        }
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        trs = {tr["task_key"]: tr for tr in await self.store.list_task_runs(frun["id"])}

        # `second` must carry an inferred dependency on `first` and start pending.
        assert trs["second"]["depends_on"] == ["first"]
        assert trs["second"]["state"] == "pending"
        # `first` is a true root.
        assert trs["first"]["depends_on"] == []
        assert trs["first"]["state"] == "ready"

    async def test_inferred_dependent_runs_after_source(self):
        spec = {
            "version": 1,
            "name": "inferred",
            "tasks": [
                {"key": "first", "kind": "query", "needs": [], "config": {"sql": "SELECT 1 AS n"}},
                {"key": "second", "kind": "query", "needs": [], "config": {"sql": "SELECT * FROM first"}},
            ],
        }
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        final = await drain_flow_run(self.store, frun["id"], NOW, CLAIMS)
        assert final["state"] == "success"
        trs = {tr["task_key"]: tr for tr in await self.store.list_task_runs(frun["id"])}
        assert trs["first"]["state"] == "success"
        assert trs["second"]["state"] == "success"


# ---------------------------------------------------------------------------
# preview_cell pulls inferred upstream
# ---------------------------------------------------------------------------


def test_preview_cell_pulls_inferred_upstream():
    reset_for_tests()
    tasks = [
        {"key": "first", "kind": "query", "needs": [], "config": {"sql": "SELECT 1 AS n"}},
        # No explicit needs — preview must still execute `first` because the
        # SQL references it.
        {"key": "second", "kind": "query", "needs": [], "config": {"sql": "SELECT * FROM first"}},
    ]
    out = preview_cell(tasks, "second", now=NOW)
    assert out["state"] == "success", out
    assert "first" in out["cell_results"], "inferred upstream cell was not executed"
    assert "second" in out["cell_results"]


# ---------------------------------------------------------------------------
# Python DataFrame contract
# ---------------------------------------------------------------------------


def test_python_returns_dataframe_serialises():
    """A python cell returning a pandas.DataFrame → {rows, columns, row_count}."""
    reset_for_tests()
    pd = pytest.importorskip("pandas")
    assert pd is not None
    code = (
        "import pandas as pd\n"
        "result = pd.DataFrame({'a': [1, 2], 'b': ['x', 'y']})\n"
    )
    tasks = [{"key": "make_df", "kind": "python", "needs": [], "config": {"code": code}}]
    out = preview_cell(tasks, "make_df", now=NOW)
    assert out["state"] == "success", out
    res = out["result"]
    assert res["columns"] == ["a", "b"]
    assert res["row_count"] == 2
    assert res["rows"] == [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]


def test_python_dataframes_input_and_sql_bridge():
    """`dataframes[...]` is populated from upstream rows/columns; a returned
    DataFrame bridges into a downstream SQL cell."""
    reset_for_tests()
    pytest.importorskip("pandas")

    # first: python cell producing a DataFrame (→ {rows, columns, row_count}).
    make = (
        "import pandas as pd\n"
        "result = pd.DataFrame({'id': [1, 2, 3], 'value': [10, -5, 20]})\n"
    )
    # second: python cell reading the upstream as a DataFrame and filtering.
    transform = (
        "df = dataframes['first']\n"
        "df = df[df['value'] > 0].copy()\n"
        "df['doubled'] = df['value'] * 2\n"
        "result = df\n"
    )
    # third: SQL cell reading the python output via the bridge.
    agg = {"sql": "SELECT count(*) AS n FROM second"}

    tasks = [
        {"key": "first", "kind": "python", "needs": [], "config": {"code": make}},
        {"key": "second", "kind": "python", "needs": ["first"], "config": {"code": transform}},
        {"key": "third", "kind": "query", "needs": ["second"], "config": agg},
    ]

    out = preview_cell(tasks, "third", now=NOW)
    assert out["state"] == "success", out

    second_res = out["cell_results"]["second"]
    assert second_res["row_count"] == 2  # only positive values survive
    assert "doubled" in second_res["columns"]

    third_res = out["cell_results"]["third"]
    # count(*) over the 2 surviving rows.
    assert third_res["rows"][0]["n"] == 2
