"""Tests for incremental materialization + dev/prod environment scoping.

Covers (all use a local Parquet base_uri so no S3 / httpfs is required):

1. parse_lookback — duration parsing.
2. resolve_target_uri — env namespacing + .parquet suffix + base_uri precedence.
3. materialize_blend kind='view' — no-op (no persisted target).
4. materialize_blend kind='full' — overwrite the target Parquet each run.
5. materialize_blend kind='incremental' — watermark filter + append; the
   watermark advances to max(time_column).
6. materialize_blend kind='incremental' + unique_key — upsert (delete-then-insert).
7. Env scoping — dev and prod write to distinct paths and never clobber.
8. spec validation — full/incremental require target; incremental requires
   time_column.
9. Store watermark round-trip (InMemoryFlowStore) + env on create_flow_run.
10. End-to-end via materialize_flow_run + drain: watermark persists and advances
    across two runs (env-scoped, InMemoryFlowStore).
"""

from __future__ import annotations

import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.flows.incremental import parse_lookback, resolve_target_uri
from app.flows.materialize import materialize_blend
from app.flows.spec import flow_spec_is_valid, validate_flow_spec
from app.flows.store import InMemoryFlowStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _src(rows: list[dict[str, Any]]) -> dict[str, Any]:
    columns = list(rows[0].keys()) if rows else []
    return {"rows": rows, "row_count": len(rows), "columns": columns}


def _read_parquet(path: str) -> list[dict[str, Any]]:
    import duckdb

    conn = duckdb.connect(database=":memory:")
    try:
        rel = conn.execute(f"SELECT * FROM read_parquet('{path}')")
        cols = [d[0] for d in rel.description]
        return [dict(zip(cols, row)) for row in rel.fetchall()]
    finally:
        conn.close()


def _blend_config(base_uri: str, *, kind: str, **mat: Any) -> dict[str, Any]:
    materialized = {"kind": kind, "base_uri": base_uri, **mat}
    return {
        "combine_sql": "SELECT * FROM src",
        "sources": ["src"],
        "rls_keys": [],
        "table": "blend",
        "materialized": materialized,
    }


# ---------------------------------------------------------------------------
# 1. parse_lookback
# ---------------------------------------------------------------------------


class TestParseLookback:
    def test_days(self):
        assert parse_lookback("3 days") == timedelta(days=3)

    def test_short_hours(self):
        assert parse_lookback("12h") == timedelta(hours=12)

    def test_combined(self):
        assert parse_lookback("1 week 2 days") == timedelta(weeks=1, days=2)

    def test_empty_is_zero(self):
        assert parse_lookback("") == timedelta(0)
        assert parse_lookback(None) == timedelta(0)

    def test_garbage_is_zero(self):
        assert parse_lookback("not a duration") == timedelta(0)


# ---------------------------------------------------------------------------
# 2. resolve_target_uri
# ---------------------------------------------------------------------------


class TestResolveTargetUri:
    def test_local_env_namespacing(self):
        uri = resolve_target_uri(
            "dev", {"target": "revenue", "base_uri": "/tmp/mat"}
        )
        assert uri == "/tmp/mat/dev/revenue.parquet"

    def test_prod_distinct_from_dev(self):
        dev = resolve_target_uri("dev", {"target": "rev", "base_uri": "/tmp/m"})
        prod = resolve_target_uri("prod", {"target": "rev", "base_uri": "/tmp/m"})
        assert dev != prod

    def test_preserves_s3_scheme(self):
        uri = resolve_target_uri(
            "prod", {"target": "rev", "base_uri": "s3://bucket/mat"}
        )
        assert uri == "s3://bucket/mat/prod/rev.parquet"

    def test_keeps_existing_parquet_ext(self):
        uri = resolve_target_uri(
            "prod", {"target": "rev.parquet", "base_uri": "/tmp/m"}
        )
        assert uri == "/tmp/m/prod/rev.parquet"

    def test_base_uri_precedence_flow_runtime_config(self):
        flow = {"runtime_config": {"materialize_base_uri": "/tmp/from-flow"}}
        uri = resolve_target_uri("prod", {"target": "rev"}, flow=flow)
        assert uri.startswith("/tmp/from-flow/prod/")


# ---------------------------------------------------------------------------
# 3-7. materialize_blend persisted kinds
# ---------------------------------------------------------------------------


class TestMaterializeBlendKinds:
    def test_view_is_noop_no_target_written(self):
        with tempfile.TemporaryDirectory() as base:
            # view kind with a database path keeps the existing local DuckDB path.
            db = os.path.join(base, f"{uuid.uuid4()}.duckdb")
            config = {
                "combine_sql": "SELECT * FROM src",
                "sources": ["src"],
                "rls_keys": [],
                "table": "blend",
                "database": db,
                "materialized": {"kind": "view"},
            }
            manifest = materialize_blend(config, {"src": _src([{"id": 1}])})
            assert manifest["materialized_kind"] == "view"
            assert "physical_target" not in manifest
            assert os.path.exists(db)

    def test_full_overwrites_target(self):
        with tempfile.TemporaryDirectory() as base:
            cfg = _blend_config(base, kind="full", target="rev")
            m1 = materialize_blend(
                cfg, {"src": _src([{"id": 1, "v": 10}, {"id": 2, "v": 20}])}, env="prod"
            )
            assert m1["materialized_kind"] == "full"
            assert m1["rows_written"] == 2
            rows = _read_parquet(m1["physical_target"])
            assert len(rows) == 2

            # Second run with fewer rows fully overwrites (not appends).
            m2 = materialize_blend(cfg, {"src": _src([{"id": 9, "v": 90}])}, env="prod")
            rows2 = _read_parquet(m2["physical_target"])
            assert len(rows2) == 1
            assert rows2[0]["id"] == 9

    def test_incremental_appends_above_watermark(self):
        with tempfile.TemporaryDirectory() as base:
            cfg = _blend_config(
                base, kind="incremental", target="events", time_column="ts"
            )
            # First run, no watermark — everything qualifies.
            m1 = materialize_blend(
                cfg,
                {"src": _src([
                    {"id": 1, "ts": "2024-01-01T00:00:00"},
                    {"id": 2, "ts": "2024-01-02T00:00:00"},
                ])},
                env="prod",
                watermark=None,
            )
            assert m1["rows_written"] == 2
            assert m1["new_watermark"] == "2024-01-02T00:00:00"

            # Second run, watermark set — only newer rows are written/appended.
            m2 = materialize_blend(
                cfg,
                {"src": _src([
                    {"id": 2, "ts": "2024-01-02T00:00:00"},   # not > watermark
                    {"id": 3, "ts": "2024-01-03T00:00:00"},   # newer
                ])},
                env="prod",
                watermark=m1["new_watermark"],
            )
            assert m2["rows_written"] == 1
            assert m2["new_watermark"] == "2024-01-03T00:00:00"

            rows = _read_parquet(m2["physical_target"])
            # Append (no unique_key): 2 from run 1 + 1 from run 2 = 3.
            assert len(rows) == 3
            assert sorted(r["id"] for r in rows) == [1, 2, 3]

    def test_incremental_upsert_with_unique_key(self):
        with tempfile.TemporaryDirectory() as base:
            cfg = _blend_config(
                base,
                kind="incremental",
                target="events",
                time_column="ts",
                unique_key=["id"],
            )
            m1 = materialize_blend(
                cfg,
                {"src": _src([{"id": 1, "ts": "2024-01-01T00:00:00", "v": 1}])},
                env="prod",
                watermark=None,
            )
            assert m1["rows_written"] == 1

            # Re-process id=1 with lookback so it re-enters, plus a new id=2.
            cfg_lb = _blend_config(
                base,
                kind="incremental",
                target="events",
                time_column="ts",
                unique_key=["id"],
                lookback="7 days",
            )
            m2 = materialize_blend(
                cfg_lb,
                {"src": _src([
                    {"id": 1, "ts": "2024-01-01T00:00:00", "v": 999},  # updated
                    {"id": 2, "ts": "2024-01-04T00:00:00", "v": 2},
                ])},
                env="prod",
                watermark=m1["new_watermark"],
            )
            rows = _read_parquet(m2["physical_target"])
            by_id = {r["id"]: r["v"] for r in rows}
            # Upsert: id=1 replaced (no duplicate), id=2 added.
            assert by_id == {1: 999, 2: 2}

    def test_env_scoping_dev_prod_isolated(self):
        with tempfile.TemporaryDirectory() as base:
            cfg = _blend_config(base, kind="full", target="rev")
            dev = materialize_blend(cfg, {"src": _src([{"id": 1}])}, env="dev")
            prod = materialize_blend(
                cfg, {"src": _src([{"id": 2}, {"id": 3}])}, env="prod"
            )
            assert dev["physical_target"] != prod["physical_target"]
            assert len(_read_parquet(dev["physical_target"])) == 1
            assert len(_read_parquet(prod["physical_target"])) == 2


# ---------------------------------------------------------------------------
# 8. spec validation
# ---------------------------------------------------------------------------


class TestMaterializedSpecValidation:
    def _spec(self, materialized: dict[str, Any]) -> dict[str, Any]:
        return {
            "version": 1,
            "name": "f",
            "tasks": [
                {
                    "key": "m",
                    "kind": "materialize",
                    "needs": [],
                    "config": {"combine_sql": "SELECT 1", "materialized": materialized},
                }
            ],
        }

    def test_view_needs_no_target(self):
        spec, issues = validate_flow_spec(self._spec({"kind": "view"}))
        assert flow_spec_is_valid(issues), issues

    def test_full_requires_target(self):
        _, issues = validate_flow_spec(self._spec({"kind": "full"}))
        assert not flow_spec_is_valid(issues)
        assert any("target" in i for i in issues)

    def test_incremental_requires_time_column(self):
        _, issues = validate_flow_spec(
            self._spec({"kind": "incremental", "target": "t"})
        )
        assert not flow_spec_is_valid(issues)
        assert any("time_column" in i for i in issues)

    def test_incremental_valid(self):
        spec, issues = validate_flow_spec(
            self._spec({"kind": "incremental", "target": "t", "time_column": "ts"})
        )
        assert flow_spec_is_valid(issues), issues

    def test_absent_materialized_still_valid(self):
        spec, issues = validate_flow_spec({
            "version": 1,
            "name": "f",
            "tasks": [
                {"key": "m", "kind": "materialize", "config": {"combine_sql": "SELECT 1"}}
            ],
        })
        assert flow_spec_is_valid(issues), issues


# ---------------------------------------------------------------------------
# 9. Store watermark round-trip + env on create_flow_run
# ---------------------------------------------------------------------------


class TestStoreWatermarks:
    @pytest.mark.asyncio
    async def test_watermark_round_trip(self):
        store = InMemoryFlowStore()
        assert await store.get_watermark("f1", "m", "dev") is None
        await store.set_watermark("f1", "m", "dev", "2024-01-01T00:00:00")
        assert await store.get_watermark("f1", "m", "dev") == "2024-01-01T00:00:00"
        # Different env → independent.
        assert await store.get_watermark("f1", "m", "prod") is None

    @pytest.mark.asyncio
    async def test_set_none_does_not_clobber(self):
        store = InMemoryFlowStore()
        await store.set_watermark("f1", "m", "prod", "X")
        await store.set_watermark("f1", "m", "prod", None)
        assert await store.get_watermark("f1", "m", "prod") == "X"

    @pytest.mark.asyncio
    async def test_copy_watermark_promote(self):
        store = InMemoryFlowStore()
        await store.set_watermark("f1", "m", "dev", "WM")
        copied = await store.copy_watermark("f1", "m", "dev", "prod")
        assert copied == "WM"
        assert await store.get_watermark("f1", "m", "prod") == "WM"

    @pytest.mark.asyncio
    async def test_create_flow_run_stores_env(self):
        store = InMemoryFlowStore()
        run = await store.create_flow_run("f1", "o1", {}, "manual", env="dev")
        assert run["env"] == "dev"

    @pytest.mark.asyncio
    async def test_create_flow_run_defaults_prod(self):
        store = InMemoryFlowStore()
        run = await store.create_flow_run("f1", "o1", {}, "manual")
        assert run["env"] == "prod"


# ---------------------------------------------------------------------------
# 10. End-to-end via materialize_flow_run + drain
# ---------------------------------------------------------------------------


class TestEndToEndIncremental:
    @pytest.mark.asyncio
    async def test_watermark_persists_and_advances_across_runs(self):
        from app.flows.runtime import drain_flow_run, materialize_flow_run

        with tempfile.TemporaryDirectory() as base:
            store = InMemoryFlowStore()
            spec = {
                "version": 1,
                "name": "incr_flow",
                "env": "dev",
                "tasks": [
                    {
                        "key": "pull",
                        "kind": "python",
                        "needs": [],
                        "config": {
                            "code": (
                                "result = {"
                                "'columns': ['id', 'ts'], "
                                "'rows': [{'id': 1, 'ts': '2024-01-01T00:00:00'}, "
                                "{'id': 2, 'ts': '2024-01-02T00:00:00'}], "
                                "'row_count': 2}"
                            )
                        },
                    },
                    {
                        "key": "mat",
                        "kind": "materialize",
                        "needs": ["pull"],
                        "config": {
                            "combine_sql": "SELECT * FROM pull",
                            "sources": ["pull"],
                            "materialized": {
                                "kind": "incremental",
                                "target": "events",
                                "time_column": "ts",
                                "base_uri": base,
                            },
                        },
                    },
                ],
            }
            flow = await store.create_flow("o1", "u1", "incr_flow", spec)
            now = datetime.now(timezone.utc)

            run1 = await materialize_flow_run(store, flow, {}, "manual", now)
            assert run1["env"] == "dev"
            final1 = await drain_flow_run(store, run1["id"], now)
            assert final1["state"] == "success", final1

            wm = await store.get_watermark(flow["id"], "mat", "dev")
            assert wm == "2024-01-02T00:00:00"

            # Second run reads the stored watermark; same rows → nothing new.
            run2 = await materialize_flow_run(store, flow, {}, "manual", now)
            final2 = await drain_flow_run(store, run2["id"], now)
            assert final2["state"] == "success", final2

            # Inspect the mat task result of run2 — 0 rows written (all <= wm).
            trs = await store.list_task_runs(run2["id"])
            mat_tr = next(t for t in trs if t["task_key"] == "mat")
            assert mat_tr["result"]["rows_written"] == 0
            # Watermark unchanged.
            assert await store.get_watermark(flow["id"], "mat", "dev") == "2024-01-02T00:00:00"

    @pytest.mark.asyncio
    async def test_env_override_at_trigger_time(self):
        from app.flows.runtime import materialize_flow_run

        store = InMemoryFlowStore()
        spec = {"version": 1, "name": "f", "env": "dev", "tasks": [
            {"key": "n", "kind": "noop", "needs": [], "config": {}}
        ]}
        flow = await store.create_flow("o1", "u1", "f", spec)
        now = datetime.now(timezone.utc)
        run = await materialize_flow_run(store, flow, {}, "manual", now, env="prod")
        assert run["env"] == "prod"
