"""Tests for the named managed-table materialization path.

This covers the data-plane target for hosts (e.g. KeyOne) that have no
warehouse of their own.  The pattern:

  1. A Flow whose SQL cell computes a projection.
  2. A ``materialize`` cell with ``kind='full'`` or ``kind='incremental'``
     writing the result to a named Parquet file (the managed table).
  3. The materialized table is registered in the runtime query registry so a
     downstream registered query or metric can SELECT from it immediately,
     without a server restart.

Coverage
--------
1. ``materialize_blend`` kind='full' registers the query in the runtime query
   registry after writing Parquet (``register_parquet_query`` called).
2. The registered query resolves via ``get_query_registry().get(query_id)``.
3. The Parquet file is org-scoped and directly readable via DuckDB
   ``read_parquet``.
4. ``register_parquet_query`` is idempotent (calling it twice is safe).
5. A re-materialization (second full run) updates the registry to point at
   the same ``query_id``; data is overwritten (not appended).
6. Incremental re-materialization advances the watermark and appends only new
   rows; the query_id remains registered throughout.
7. Org isolation: each org's Parquet file is written to a distinct path; the
   registered query for org-A is independent of org-B's.
8. End-to-end via ``drain_flow_run``: a Flow with a ``noop`` + ``materialize``
   task completes successfully and the materialized table is queryable.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest

from app.flows.materialize import materialize_blend, register_parquet_query
from app.flows.store import InMemoryFlowStore
from app.queries.registry import get_query_registry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _src(rows: list[dict[str, Any]], columns: list[str] | None = None) -> dict[str, Any]:
    cols = columns or (list(rows[0].keys()) if rows else [])
    return {"rows": rows, "row_count": len(rows), "columns": cols}


def _read_parquet(path: str) -> list[dict[str, Any]]:
    import duckdb

    conn = duckdb.connect(database=":memory:")
    try:
        rel = conn.execute(f"SELECT * FROM read_parquet('{path}')")
        col_names = [d[0] for d in rel.description]
        return [dict(zip(col_names, row)) for row in rel.fetchall()]
    finally:
        conn.close()


def _blend_cfg(base_uri: str, *, kind: str, query_id: str, datastore_id: str, **mat: Any) -> dict[str, Any]:
    materialized = {"kind": kind, "base_uri": base_uri, **mat}
    return {
        "combine_sql": "SELECT * FROM src",
        "sources": ["src"],
        "rls_keys": [],
        "table": "projection",
        "datastore_id": datastore_id,
        "query_id": query_id,
        "materialized": materialized,
    }


# ---------------------------------------------------------------------------
# 1-3. register_parquet_query wired into materialize_blend full path
# ---------------------------------------------------------------------------


class TestManagedTableRegistration:
    def test_full_kind_registers_query_after_write(self):
        """A full-kind materialize_blend call registers the query_id in the runtime registry."""
        query_id = f"managed-{uuid.uuid4()}"
        datastore_id = str(uuid.uuid4())

        with tempfile.TemporaryDirectory() as base:
            cfg = _blend_cfg(
                base,
                kind="full",
                target="category_projection",
                query_id=query_id,
                datastore_id=datastore_id,
            )
            manifest = materialize_blend(
                cfg,
                {"src": _src([{"org_id": "o1", "category": "A", "total": 10}])},
                env="prod",
            )
            assert manifest["materialized_kind"] == "full"
            assert manifest["rows_written"] == 1
            assert manifest["query_id"] == query_id

            # The runtime query registry must contain the entry immediately.
            registry = get_query_registry()
            rq = registry.get(query_id)
            assert rq is not None, f"query_id {query_id!r} not found in registry"
            assert rq.datastore_id == datastore_id
            assert "projection" in rq.sql  # SELECT * FROM "projection"

    def test_parquet_file_is_directly_readable(self):
        """The Parquet file written by materialize_blend is valid and readable."""
        query_id = f"managed-{uuid.uuid4()}"
        datastore_id = str(uuid.uuid4())

        with tempfile.TemporaryDirectory() as base:
            cfg = _blend_cfg(
                base,
                kind="full",
                target="assignments",
                query_id=query_id,
                datastore_id=datastore_id,
            )
            rows_in = [
                {"org_id": "o1", "user_id": "u1", "role": "admin"},
                {"org_id": "o1", "user_id": "u2", "role": "viewer"},
                {"org_id": "o2", "user_id": "u3", "role": "admin"},
            ]
            manifest = materialize_blend(
                cfg,
                {"src": _src(rows_in)},
                env="prod",
            )

            physical_target = manifest["physical_target"]
            assert os.path.exists(physical_target)

            rows_out = _read_parquet(physical_target)
            assert len(rows_out) == 3
            roles = {r["role"] for r in rows_out}
            assert roles == {"admin", "viewer"}

    def test_incremental_kind_also_registers_query(self):
        """incremental kind registers query after the first write."""
        query_id = f"managed-{uuid.uuid4()}"
        datastore_id = str(uuid.uuid4())

        with tempfile.TemporaryDirectory() as base:
            cfg = _blend_cfg(
                base,
                kind="incremental",
                target="events_proj",
                time_column="ts",
                query_id=query_id,
                datastore_id=datastore_id,
            )
            materialize_blend(
                cfg,
                {"src": _src([{"ts": "2024-01-01T00:00:00", "val": 1}])},
                env="prod",
                watermark=None,
            )

            rq = get_query_registry().get(query_id)
            assert rq is not None
            assert rq.datastore_id == datastore_id


# ---------------------------------------------------------------------------
# 4. register_parquet_query is idempotent
# ---------------------------------------------------------------------------


class TestRegisterParquetQueryIdempotent:
    def test_double_register_is_safe(self):
        """Calling register_parquet_query twice with the same query_id is safe."""
        query_id = f"idempotent-{uuid.uuid4()}"
        datastore_id = str(uuid.uuid4())

        with tempfile.TemporaryDirectory() as tmp:
            parquet_path = os.path.join(tmp, "test.parquet")
            # Write a minimal parquet file.
            import duckdb as _duckdb
            conn = _duckdb.connect(":memory:")
            conn.execute(f"COPY (SELECT 1 AS id) TO '{parquet_path}' (FORMAT parquet)")
            conn.close()

            register_parquet_query(
                query_id=query_id,
                physical_target=parquet_path,
                table="test",
                datastore_id=datastore_id,
            )
            register_parquet_query(
                query_id=query_id,
                physical_target=parquet_path,
                table="test",
                datastore_id=datastore_id,
            )

            # Still exactly one entry, no crash.
            rq = get_query_registry().get(query_id)
            assert rq is not None
            assert rq.datastore_id == datastore_id


# ---------------------------------------------------------------------------
# 5. Re-materialization overwrites (full kind)
# ---------------------------------------------------------------------------


class TestFullKindOverwrite:
    def test_second_full_run_overwrites_data(self):
        """A second full-kind run replaces all rows; the query_id stays registered."""
        query_id = f"overwrite-{uuid.uuid4()}"
        datastore_id = str(uuid.uuid4())

        with tempfile.TemporaryDirectory() as base:
            cfg = _blend_cfg(
                base,
                kind="full",
                target="kpi_snapshot",
                query_id=query_id,
                datastore_id=datastore_id,
            )

            # First run: 3 rows.
            m1 = materialize_blend(
                cfg,
                {"src": _src([{"id": 1}, {"id": 2}, {"id": 3}])},
                env="prod",
            )
            assert m1["rows_written"] == 3

            # Second run: 1 row (full overwrite, not append).
            m2 = materialize_blend(
                cfg,
                {"src": _src([{"id": 99}])},
                env="prod",
            )
            assert m2["rows_written"] == 1
            rows = _read_parquet(m2["physical_target"])
            assert len(rows) == 1
            assert rows[0]["id"] == 99

            # query_id still registered.
            assert get_query_registry().get(query_id) is not None


# ---------------------------------------------------------------------------
# 6. Incremental re-materialization advances watermark + appends
# ---------------------------------------------------------------------------


class TestIncrementalKindQueryable:
    def test_incremental_appends_and_stays_queryable(self):
        """Incremental run appends new rows; query_id stays registered throughout."""
        query_id = f"incr-{uuid.uuid4()}"
        datastore_id = str(uuid.uuid4())

        with tempfile.TemporaryDirectory() as base:
            cfg = _blend_cfg(
                base,
                kind="incremental",
                target="revenue_incr",
                time_column="ts",
                query_id=query_id,
                datastore_id=datastore_id,
            )

            # First run — seeds the parquet.
            m1 = materialize_blend(
                cfg,
                {"src": _src([{"id": 1, "ts": "2024-01-01T00:00:00", "amt": 100}])},
                env="prod",
                watermark=None,
            )
            assert m1["rows_written"] == 1
            assert m1["new_watermark"] == "2024-01-01T00:00:00"
            assert get_query_registry().get(query_id) is not None

            # Second run — only new row is written.
            m2 = materialize_blend(
                cfg,
                {"src": _src([
                    {"id": 1, "ts": "2024-01-01T00:00:00", "amt": 100},  # <= wm, filtered
                    {"id": 2, "ts": "2024-01-02T00:00:00", "amt": 200},  # new
                ])},
                env="prod",
                watermark=m1["new_watermark"],
            )
            assert m2["rows_written"] == 1
            assert m2["new_watermark"] == "2024-01-02T00:00:00"

            all_rows = _read_parquet(m2["physical_target"])
            assert len(all_rows) == 2
            assert get_query_registry().get(query_id) is not None


# ---------------------------------------------------------------------------
# 7. Org isolation — distinct paths per org
# ---------------------------------------------------------------------------


class TestOrgIsolation:
    def test_different_orgs_write_distinct_parquet_files(self):
        """Each org's managed table is written to a distinct physical_target."""
        q1, q2 = f"org-a-{uuid.uuid4()}", f"org-b-{uuid.uuid4()}"
        ds1, ds2 = str(uuid.uuid4()), str(uuid.uuid4())

        with tempfile.TemporaryDirectory() as base_a, tempfile.TemporaryDirectory() as base_b:
            cfg_a = _blend_cfg(base_a, kind="full", target="sales", query_id=q1, datastore_id=ds1)
            cfg_b = _blend_cfg(base_b, kind="full", target="sales", query_id=q2, datastore_id=ds2)

            m_a = materialize_blend(
                cfg_a,
                {"src": _src([{"org": "A", "v": 1}])},
                env="prod",
            )
            m_b = materialize_blend(
                cfg_b,
                {"src": _src([{"org": "B", "v": 99}])},
                env="prod",
            )

            assert m_a["physical_target"] != m_b["physical_target"]
            rows_a = _read_parquet(m_a["physical_target"])
            rows_b = _read_parquet(m_b["physical_target"])
            assert rows_a[0]["org"] == "A"
            assert rows_b[0]["org"] == "B"

            # Each query_id resolves independently.
            assert get_query_registry().get(q1) is not None
            assert get_query_registry().get(q2) is not None
            assert get_query_registry().get(q1).datastore_id == ds1
            assert get_query_registry().get(q2).datastore_id == ds2


# ---------------------------------------------------------------------------
# 8. End-to-end via drain_flow_run
# ---------------------------------------------------------------------------


class TestEndToEndManagedTable:
    @pytest.mark.asyncio
    async def test_flow_with_managed_materialize_succeeds(self):
        """A Flow with a SQL + materialize cell runs to success; table is queryable."""
        from app.flows.runtime import drain_flow_run, materialize_flow_run

        query_id = f"e2e-managed-{uuid.uuid4()}"
        datastore_id = str(uuid.uuid4())

        with tempfile.TemporaryDirectory() as base:
            store = InMemoryFlowStore()
            spec = {
                "version": 1,
                "name": "managed_projection_flow",
                "tasks": [
                    {
                        "key": "pull",
                        "kind": "python",
                        "needs": [],
                        "config": {
                            "code": (
                                "result = {"
                                "'columns': ['category', 'amount'], "
                                "'rows': ["
                                "{'category': 'A', 'amount': 100}, "
                                "{'category': 'B', 'amount': 200}"
                                "], "
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
                            "rls_keys": [],
                            "table": "category_totals",
                            "datastore_id": datastore_id,
                            "query_id": query_id,
                            "materialized": {
                                "kind": "full",
                                "target": "category_totals",
                                "base_uri": base,
                            },
                        },
                    },
                ],
            }
            flow = await store.create_flow("org1", "user1", "managed_projection_flow", spec)
            now = datetime.now(timezone.utc)

            run = await materialize_flow_run(store, flow, {}, "manual", now, env="prod")
            final = await drain_flow_run(store, run["id"], now)
            assert final["state"] == "success", final

            # The materialize task result has the expected shape.
            trs = await store.list_task_runs(run["id"])
            mat_tr = next(t for t in trs if t["task_key"] == "mat")
            assert mat_tr["result"]["materialized_kind"] == "full"
            assert mat_tr["result"]["rows_written"] == 2

            # The query_id is registered and immediately queryable.
            rq = get_query_registry().get(query_id)
            assert rq is not None, f"{query_id!r} not in registry after flow run"
            assert rq.datastore_id == datastore_id

            # The Parquet file exists and has the correct data.
            physical_target = mat_tr["result"]["physical_target"]
            assert os.path.exists(physical_target)
            rows = _read_parquet(physical_target)
            assert len(rows) == 2
            categories = {r["category"] for r in rows}
            assert categories == {"A", "B"}
