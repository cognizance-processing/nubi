"""Tests for the flows "cells, not kinds" refactor (v1).

Covers the three cell config blocks layered onto SQL (``query``) / Python
(``python``) cells without any new kind:

1. ``config.materialized`` on a SQL cell — the cell's OWN SELECT result is
   persisted (full/incremental) via the incremental.py object-storage path;
   the watermark advances and persists across runs.
2. ``config.for_each`` on a SQL or Python cell — runs the cell body once per
   item (rewritten into the legacy map fan-out at run time).
3. ``config.run_when`` on any cell — skips the cell (state 'skipped') when the
   safe boolean expression evaluates False; runs it when True.
4. The safe run_when evaluator rejects dangerous input (no eval/exec/builtins).
5. Legacy map / branch / materialize specs still execute (back-compat guard).
"""

from __future__ import annotations

import os
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest

from app.flows.executor import TaskContext
from app.flows.registry import reset_for_tests
from app.flows.run_when import evaluate_run_when
from app.flows.for_each import to_map_config
from app.flows.runtime import (
    advance_readiness,
    drain_flow_run,
    materialize_flow_run,
)
from app.flows.spec import flow_spec_is_valid, validate_flow_spec
from app.flows.store import InMemoryFlowStore

pytestmark = pytest.mark.asyncio

NOW = datetime(2025, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
CLAIMS: dict[str, Any] = {"org_id": "org-test", "sub": "user-test"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(inputs=None, params=None, secrets=None) -> TaskContext:
    return TaskContext(
        flow_params=params or {},
        inputs=inputs or {},
        secrets=secrets or {},
    )


async def _make_flow(store, spec, name="cells_flow") -> dict[str, Any]:
    return await store.create_flow(
        org_id="org-test", created_by="user-test", name=name, spec=spec
    )


def _trs_by_key(trs):
    return {tr["task_key"]: tr for tr in trs}


# ===========================================================================
# 1. Safe run_when evaluator (unit)
# ===========================================================================


class TestRunWhenEvaluator:
    def test_empty_or_none_always_runs(self):
        assert evaluate_run_when(None, _ctx()) is True
        assert evaluate_run_when("", _ctx()) is True
        assert evaluate_run_when("   ", _ctx()) is True

    def test_equality_over_inputs_true(self):
        ctx = _ctx(inputs={"classify": {"label": "high_value"}})
        assert evaluate_run_when("inputs.classify.label == 'high_value'", ctx) is True

    def test_equality_over_inputs_false(self):
        ctx = _ctx(inputs={"classify": {"label": "low"}})
        assert evaluate_run_when("inputs.classify.label == 'high_value'", ctx) is False

    def test_subscript_form(self):
        ctx = _ctx(inputs={"classify": {"label": "x"}})
        assert evaluate_run_when("inputs['classify']['label'] == 'x'", ctx) is True

    def test_missing_upstream_is_soft_none(self):
        # Not-yet-run upstream cell — unknown key yields None, never raises.
        ctx = _ctx(inputs={})
        assert evaluate_run_when("inputs.not_run.label == 'x'", ctx) is False
        assert evaluate_run_when("inputs.not_run.label == None", ctx) is True

    def test_params_and_secrets(self):
        ctx = _ctx(params={"threshold": 10}, secrets={"FLAG": "on"})
        assert evaluate_run_when("params.threshold > 5", ctx) is True
        assert evaluate_run_when("secrets.FLAG == 'on'", ctx) is True

    def test_bool_and_or_not(self):
        ctx = _ctx(inputs={"a": {"v": 1}}, params={"go": True})
        assert evaluate_run_when("inputs.a.v == 1 and params.go", ctx) is True
        assert evaluate_run_when("inputs.a.v == 2 or params.go", ctx) is True
        assert evaluate_run_when("not (inputs.a.v == 2)", ctx) is True

    def test_in_operator_and_safe_call(self):
        ctx = _ctx(inputs={"a": {"tags": ["x", "y"]}})
        assert evaluate_run_when("'x' in inputs.a.tags", ctx) is True
        assert evaluate_run_when("len(inputs.a.tags) == 2", ctx) is True

    def test_template_brace_form_stripped(self):
        ctx = _ctx(inputs={"a": {"ok": True}})
        assert evaluate_run_when("{{ inputs.a.ok }}", ctx) is True

    # ── Dangerous input must be rejected (ValueError, never executed) ────────

    @pytest.mark.parametrize(
        "expr",
        [
            "__import__('os').system('echo hi')",
            "{x: 1 for x in range(3)}",
            "open('/etc/passwd').read()",
            "[x for x in range(3)]",
            "(lambda: 1)()",
            "exec('x=1')",
            "eval('1')",
            "inputs.update({'x': 1})",  # arbitrary method call
        ],
    )
    def test_rejects_dangerous_input(self, expr):
        with pytest.raises(ValueError):
            evaluate_run_when(expr, _ctx(inputs={"a": {}}))

    def test_malformed_expr_raises(self):
        with pytest.raises(ValueError):
            evaluate_run_when("inputs.a ==", _ctx())

    def test_dunder_attr_on_nondict_is_soft_none(self):
        # Attribute access never reaches a real object's __class__ etc. — it is
        # a soft dict-get, so a class-escape attempt collapses to None (safe).
        ctx = _ctx(inputs={"a": {}})
        assert evaluate_run_when("inputs.a.__class__ == None", ctx) is True


# ===========================================================================
# 2. run_when skip / run on a real cell (engine)
# ===========================================================================


def _run_when_spec(expr: str) -> dict[str, Any]:
    """decision (python) → gated (python, run_when=expr) → tail (python)."""
    return {
        "version": 1,
        "name": "run_when_flow",
        "tasks": [
            {
                "key": "decision",
                "kind": "python",
                "needs": [],
                "config": {"code": "result = {'label': 'high_value'}"},
            },
            {
                "key": "gated",
                "kind": "python",
                "needs": ["decision"],
                "config": {"code": "result = {'ran': True}", "run_when": expr},
            },
        ],
    }


class TestRunWhenExecution:
    def setup_method(self):
        reset_for_tests()
        self.store = InMemoryFlowStore()

    async def test_gate_true_runs(self):
        spec = _run_when_spec("inputs.decision.label == 'high_value'")
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=50)

        by_key = _trs_by_key(await self.store.list_task_runs(frun["id"]))
        assert by_key["gated"]["state"] == "success"
        ff = await self.store.get_flow_run(frun["id"])
        assert ff["state"] == "success"

    async def test_gate_false_skips(self):
        spec = _run_when_spec("inputs.decision.label == 'low'")
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=50)

        by_key = _trs_by_key(await self.store.list_task_runs(frun["id"]))
        assert by_key["gated"]["state"] == "skipped"
        # Skipped is NOT a flow failure (branch-not-taken semantics).
        ff = await self.store.get_flow_run(frun["id"])
        assert ff["state"] == "success"

    async def test_malformed_gate_fails_loudly(self):
        spec = _run_when_spec("__import__('os')")
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=50)

        by_key = _trs_by_key(await self.store.list_task_runs(frun["id"]))
        assert by_key["gated"]["state"] == "failed"
        assert "run_when" in (by_key["gated"].get("error") or "")


# ===========================================================================
# 3. for_each on a SQL / Python cell (engine)
# ===========================================================================


def _for_each_python_spec() -> dict[str, Any]:
    """source (python → items) → fanned (python cell with for_each)."""
    return {
        "version": 1,
        "name": "for_each_flow",
        "tasks": [
            {
                "key": "source",
                "kind": "python",
                "needs": [],
                "config": {"code": "result = {'items': [{'n': 1}, {'n': 2}, {'n': 3}]}"},
            },
            {
                "key": "fanned",
                "kind": "python",
                "needs": ["source"],
                "config": {
                    "code": "result = {'doubled': item['n'] * 2}",
                    "for_each": {"items": "{{ inputs.source.items }}", "var": "item"},
                },
            },
        ],
    }


class TestForEachExecution:
    def setup_method(self):
        reset_for_tests()
        self.store = InMemoryFlowStore()

    def test_to_map_config_shape(self):
        task = {
            "key": "fanned",
            "kind": "query",
            "config": {
                "sql": "SELECT 1",
                "for_each": {"items": "{{ inputs.x.rows }}", "var": "row", "max_concurrency": 2},
            },
        }
        cfg = to_map_config(task)
        assert cfg["item_expr"] == "{{ inputs.x.rows }}"
        assert cfg["item_var"] == "row"
        assert cfg["max_concurrency"] == 2
        assert cfg["collect_key"] == "__self__"
        assert len(cfg["body"]) == 1
        body = cfg["body"][0]
        assert body["key"] == "__self__"
        assert body["kind"] == "query"
        assert body["config"]["sql"] == "SELECT 1"
        # for_each is stripped from the body to avoid recursive fan-out.
        assert "for_each" not in body["config"]

    async def test_python_for_each_fans_out(self):
        spec = _for_each_python_spec()
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=80)

        all_trs = await self.store.list_task_runs(frun["id"])
        by_key = _trs_by_key(all_trs)

        # 3 children, one per item, keyed "fanned[i].__self__".
        child_keys = [k for k in by_key if k.startswith("fanned[")]
        assert len(child_keys) == 3, f"got {child_keys}"

        # Parent aggregates the per-item results.
        fanned = by_key["fanned"]
        assert fanned["state"] == "success"
        items = fanned["result"]["items"]
        doubled = sorted(it["result"]["doubled"] for it in items)
        assert doubled == [2, 4, 6]

        ff = await self.store.get_flow_run(frun["id"])
        assert ff["state"] == "success"

    async def test_for_each_gated_false_skips_whole_fanout(self):
        # run_when gate evaluated False on a for_each cell ⇒ the cell is skipped
        # entirely (no fan-out, no children).  Precedence: run_when → for_each.
        spec = {
            "version": 1,
            "name": "gated_for_each",
            "tasks": [
                {
                    "key": "source",
                    "kind": "python",
                    "needs": [],
                    "config": {"code": "result = {'items': [{'n': 1}], 'go': False}"},
                },
                {
                    "key": "fanned",
                    "kind": "python",
                    "needs": ["source"],
                    "config": {
                        "code": "result = {'doubled': item['n'] * 2}",
                        "run_when": "inputs.source.go == True",
                        "for_each": {"items": "{{ inputs.source.items }}", "var": "item"},
                    },
                },
            ],
        }
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=80)
        by_key = _trs_by_key(await self.store.list_task_runs(frun["id"]))
        assert by_key["fanned"]["state"] == "skipped"
        # No children expanded.
        assert not [k for k in by_key if k.startswith("fanned[")]

    async def test_for_each_validates(self):
        spec = _for_each_python_spec()
        _, issues = validate_flow_spec(spec)
        assert flow_spec_is_valid(issues), issues

    async def test_for_each_requires_items(self):
        spec = {
            "version": 1,
            "name": "bad",
            "tasks": [
                {
                    "key": "c",
                    "kind": "python",
                    "needs": [],
                    "config": {"code": "result = 1", "for_each": {"var": "item"}},
                }
            ],
        }
        _, issues = validate_flow_spec(spec)
        assert not flow_spec_is_valid(issues)
        assert any("for_each.items" in i for i in issues)


# ===========================================================================
# 4. materialized on a SQL cell (engine + persistence)
# ===========================================================================


def _materialized_query_spec(base_uri: str, target: str, kind: str) -> dict[str, Any]:
    return {
        "version": 1,
        "name": "mat_cell_flow",
        "env": "prod",
        "tasks": [
            {
                "key": "rows",
                "kind": "python",
                "needs": [],
                "config": {
                    "code": (
                        "result = {'rows': "
                        "[{'id': 1, 'ts': '2025-06-01T00:00:00'},"
                        " {'id': 2, 'ts': '2025-06-02T00:00:00'}],"
                        " 'columns': ['id', 'ts']}"
                    )
                },
            },
            {
                "key": "store_cell",
                "kind": "query",
                "needs": ["rows"],
                "config": {
                    # SELECT over the upstream python rows via the Python->SQL bridge.
                    "sql": "SELECT * FROM rows",
                    "materialized": {
                        "kind": kind,
                        "target": target,
                        "base_uri": base_uri,
                        **({"time_column": "ts"} if kind == "incremental" else {}),
                    },
                },
            },
        ],
    }


def _read_parquet(path: str) -> list[dict[str, Any]]:
    import duckdb

    conn = duckdb.connect(database=":memory:")
    try:
        rel = conn.execute(f"SELECT * FROM read_parquet('{path}')")
        cols = [d[0] for d in rel.description]
        return [dict(zip(cols, row)) for row in rel.fetchall()]
    finally:
        conn.close()


class TestMaterializedQueryCell:
    def setup_method(self):
        reset_for_tests()
        self.store = InMemoryFlowStore()

    def test_query_cell_materialized_validates(self):
        with tempfile.TemporaryDirectory() as d:
            spec = _materialized_query_spec(d, "models/cell", "full")
            _, issues = validate_flow_spec(spec)
            assert flow_spec_is_valid(issues), issues

    def test_query_cell_materialized_incremental_requires_time_column(self):
        spec = {
            "version": 1,
            "name": "bad",
            "tasks": [
                {
                    "key": "q",
                    "kind": "query",
                    "needs": [],
                    "config": {
                        "sql": "SELECT 1",
                        "materialized": {"kind": "incremental", "target": "x"},
                    },
                }
            ],
        }
        _, issues = validate_flow_spec(spec)
        assert not flow_spec_is_valid(issues)
        assert any("time_column" in i for i in issues)

    async def test_full_materialize_persists_parquet(self):
        with tempfile.TemporaryDirectory() as d:
            spec = _materialized_query_spec(d, "models/cell", "full")
            flow = await _make_flow(self.store, spec)
            frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
            await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=50)

            by_key = _trs_by_key(await self.store.list_task_runs(frun["id"]))
            assert by_key["store_cell"]["state"] == "success", by_key["store_cell"].get("error")
            res = by_key["store_cell"]["result"]
            assert res["materialized_kind"] == "full"
            target = res["physical_target"]
            assert os.path.exists(target), target
            persisted = _read_parquet(target)
            assert len(persisted) == 2

    async def test_incremental_materialize_advances_watermark(self):
        with tempfile.TemporaryDirectory() as d:
            spec = _materialized_query_spec(d, "models/inc", "incremental")
            flow = await _make_flow(self.store, spec)
            frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
            await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=50)

            by_key = _trs_by_key(await self.store.list_task_runs(frun["id"]))
            res = by_key["store_cell"]["result"]
            assert res["materialized_kind"] == "incremental"
            assert res["new_watermark"] is not None

            # Watermark persisted to the store, env-scoped.
            wm = await self.store.get_watermark(flow["id"], "store_cell", "prod")
            assert wm is not None and wm == res["new_watermark"]

    async def test_view_kind_is_noop_persistence(self):
        spec = {
            "version": 1,
            "name": "view_flow",
            "tasks": [
                {
                    "key": "rows",
                    "kind": "python",
                    "needs": [],
                    "config": {"code": "result = {'rows': [{'id': 1}], 'columns': ['id']}"},
                },
                {
                    "key": "v",
                    "kind": "query",
                    "needs": ["rows"],
                    "config": {"sql": "SELECT * FROM rows", "materialized": {"kind": "view"}},
                },
            ],
        }
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=50)
        by_key = _trs_by_key(await self.store.list_task_runs(frun["id"]))
        assert by_key["v"]["state"] == "success"
        # No physical target written for a view cell.
        assert "physical_target" not in by_key["v"]["result"]


# ===========================================================================
# 5. Back-compat: legacy map / branch / materialize specs still run
# ===========================================================================


class TestLegacyKindsBackCompat:
    def setup_method(self):
        reset_for_tests()
        self.store = InMemoryFlowStore()

    async def test_legacy_map_kind_still_runs(self):
        spec = {
            "version": 1,
            "name": "legacy_map",
            "tasks": [
                {
                    "key": "src",
                    "kind": "python",
                    "needs": [],
                    "config": {"code": "result = {'items': [{'n': 1}, {'n': 2}]}"},
                },
                {
                    "key": "m",
                    "kind": "map",
                    "needs": ["src"],
                    "config": {
                        "item_expr": "{{ inputs.src.items }}",
                        "item_var": "item",
                        "collect_key": "body",
                        "body": [
                            {"key": "body", "kind": "python", "needs": [],
                             "config": {"code": "result = {'v': item['n']}"}},
                        ],
                    },
                },
            ],
        }
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=80)
        by_key = _trs_by_key(await self.store.list_task_runs(frun["id"]))
        assert by_key["m"]["state"] == "success"
        assert by_key["m"]["result"]["item_count"] == 2

    async def test_legacy_branch_kind_still_runs(self):
        spec = {
            "version": 1,
            "name": "legacy_branch",
            "tasks": [
                {
                    "key": "classify",
                    "kind": "python",
                    "needs": [],
                    "config": {"code": "result = {'label': 'high'}"},
                },
                {
                    "key": "route",
                    "kind": "branch",
                    "needs": ["classify"],
                    "config": {
                        "conditions": [
                            {"when": "'{{ inputs.classify.label }}' == 'high'", "next": ["hi"]},
                        ],
                        "default": ["lo"],
                    },
                },
                {"key": "hi", "kind": "python", "needs": ["route"],
                 "config": {"code": "result = {'arm': 'hi'}"}},
                {"key": "lo", "kind": "python", "needs": ["route"],
                 "config": {"code": "result = {'arm': 'lo'}"}},
            ],
        }
        flow = await _make_flow(self.store, spec)
        frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
        await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=80)
        by_key = _trs_by_key(await self.store.list_task_runs(frun["id"]))
        assert by_key["hi"]["state"] == "success"
        assert by_key["lo"]["state"] == "upstream_failed"
        ff = await self.store.get_flow_run(frun["id"])
        assert ff["state"] == "success"

    async def test_legacy_materialize_kind_still_runs(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = os.path.join(d, "blend.duckdb")
            spec = {
                "version": 1,
                "name": "legacy_mat",
                "tasks": [
                    {
                        "key": "rows",
                        "kind": "python",
                        "needs": [],
                        "config": {"code": "result = {'rows': [{'id': 1}], 'columns': ['id']}"},
                    },
                    {
                        "key": "blend",
                        "kind": "materialize",
                        "needs": ["rows"],
                        "config": {
                            "combine_sql": "SELECT * FROM rows",
                            "sources": ["rows"],
                            "table": "blend",
                            "database": db_path,
                            "datastore_id": str(uuid.uuid4()),
                            "query_id": str(uuid.uuid4()),
                        },
                    },
                ],
            }
            flow = await _make_flow(self.store, spec)
            frun = await materialize_flow_run(self.store, flow, {}, "manual", NOW)
            await drain_flow_run(self.store, frun["id"], NOW, CLAIMS, max_steps=50)
            by_key = _trs_by_key(await self.store.list_task_runs(frun["id"]))
            assert by_key["blend"]["state"] == "success", by_key["blend"].get("error")
            assert by_key["blend"]["result"]["materialized_kind"] == "view"
