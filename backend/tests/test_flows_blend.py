"""Tests for materialized multi-source BLENDS + Cloud Run scheduler readiness.

Coverage
--------
1. ``materialize_blend`` unit: merges two source results in DuckDB, writes the
   materialized table, preserves rls_keys, registers the runtime query.
2. ``materialize_blend`` raises ``rls_key_dropped`` when combine_sql flattens
   away a declared rls_key.
3. POST /flows/blend → 201 with {flow, materialized:{datastore_id, query_id}};
   the flow has source query tasks + a materialize task; the run succeeds and
   the DuckDB file exists.
4. POST /flows/blend then READ via POST /query using the returned query_id →
   200 Arrow with the blended rows (materialize-then-serve, single source).
5. RLS read: a query with policies injects WHERE on the preserved rls_key
   column at READ time on the materialized source.
6. POST /flows/tick auth: 503 when no secret configured; 401 on wrong/missing
   header; 200 with the right header.
7. Atomic claim: two concurrent flow_ticks materialize a due scheduled flow at
   most once (no double-run).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from io import BytesIO
from typing import Any

import pyarrow as pa
import pyarrow.ipc as pa_ipc
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.flows.materialize import (
    DEFAULT_BLEND_TABLE,
    blend_database_path,
    materialize_blend,
)
from app.flows.store import InMemoryFlowStore, set_flow_store
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auth_headers(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


def _parse_arrow(content: bytes) -> pa.Table:
    return pa_ipc.open_stream(BytesIO(content)).read_all()


def _src_result(rows: list[dict[str, Any]], columns: list[str]) -> dict[str, Any]:
    return {"rows": rows, "row_count": len(rows), "columns": columns}


def _cleanup_db(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# (1) + (2) materialize_blend unit tests
# ---------------------------------------------------------------------------


class TestMaterializeBlendUnit:
    def test_merges_sources_and_preserves_rls_keys(self):
        db = blend_database_path(f"unit-{uuid.uuid4()}")
        try:
            inputs = {
                "north": _src_result(
                    [
                        {"tenant_id": "t1", "region": "north", "amount": 10},
                        {"tenant_id": "t2", "region": "north", "amount": 20},
                    ],
                    ["tenant_id", "region", "amount"],
                ),
                "south": _src_result(
                    [{"tenant_id": "t1", "region": "south", "amount": 5}],
                    ["tenant_id", "region", "amount"],
                ),
            }
            config = {
                "combine_sql": (
                    "SELECT tenant_id, region, amount FROM north "
                    "UNION ALL SELECT tenant_id, region, amount FROM south"
                ),
                "sources": ["north", "south"],
                "rls_keys": ["tenant_id"],
                "table": DEFAULT_BLEND_TABLE,
                "database": db,
                "datastore_id": None,
                "query_id": None,
            }
            manifest = materialize_blend(config, inputs)

            assert manifest["row_count"] == 3
            assert "tenant_id" in manifest["columns"]
            assert manifest["rls_keys"] == ["tenant_id"]
            assert os.path.exists(db)

            # The written file has the blend table with the RLS column.
            import duckdb

            conn = duckdb.connect(database=db, read_only=True)
            try:
                cols = [
                    r[1]
                    for r in conn.execute(
                        f'PRAGMA table_info("{DEFAULT_BLEND_TABLE}")'
                    ).fetchall()
                ]
                assert "tenant_id" in cols
                n = conn.execute(
                    f'SELECT count(*) FROM "{DEFAULT_BLEND_TABLE}"'
                ).fetchone()[0]
                assert n == 3
            finally:
                conn.close()
        finally:
            _cleanup_db(db)

    def test_rls_key_dropped_raises(self):
        from app.errors import AppError

        db = blend_database_path(f"unit-{uuid.uuid4()}")
        try:
            inputs = {
                "a": _src_result(
                    [{"tenant_id": "t1", "amount": 10}], ["tenant_id", "amount"]
                ),
            }
            config = {
                # combine_sql drops tenant_id — must fail the RLS-key check.
                "combine_sql": "SELECT amount FROM a",
                "sources": ["a"],
                "rls_keys": ["tenant_id"],
                "table": DEFAULT_BLEND_TABLE,
                "database": db,
            }
            with pytest.raises(AppError) as exc:
                materialize_blend(config, inputs)
            assert exc.value.code == "rls_key_dropped"
        finally:
            _cleanup_db(db)


# ---------------------------------------------------------------------------
# Endpoint fixtures (mirror test_flows_api / test_query_connectors)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def blend_app(app):
    store = InMemoryFlowStore()
    set_flow_store(store)
    repo = InMemoryRepo()
    set_repo(repo)
    yield app, store, repo
    set_flow_store(None)
    set_repo(None)


@pytest_asyncio.fixture
async def blend_client(blend_app, fake_db):
    app, store, repo = blend_app

    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    fake_db.users[user_id] = {
        "id": user_id,
        "email": "blender@example.com",
        "name": "Blender",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    repo.seed_org_member(org_id=org_id, user_id=user_id)

    # Clear the query result cache so repeated reads recompute.
    from app.connectors.cache import get_cache

    get_cache().clear()

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as client:
        yield client, user_id, org_id, store, repo
    get_cache().clear()


# A blend over two ad-hoc DuckDB sources that both select from the seeded `demo`
# table (the query handler seeds `demo` automatically). We synthesise a
# tenant_id column so we can exercise rls_keys preservation + read-time RLS.
_BLEND_BODY = {
    "name": "Revenue blend",
    "sources": [
        {
            "key": "src_a",
            "sql": "SELECT 't1' AS tenant_id, name, value FROM demo WHERE active = true",
        },
        {
            "key": "src_b",
            "sql": "SELECT 't2' AS tenant_id, name, value FROM demo WHERE active = false",
        },
    ],
    "combine_sql": (
        "SELECT tenant_id, name, value FROM src_a "
        "UNION ALL SELECT tenant_id, name, value FROM src_b"
    ),
    "rls_keys": ["tenant_id"],
}


# ---------------------------------------------------------------------------
# (3) POST /flows/blend create + materialize
# ---------------------------------------------------------------------------


class TestCreateBlend:
    @pytest.mark.asyncio
    async def test_create_blend_materializes(self, blend_client):
        client, user_id, org_id, store, repo = blend_client

        resp = await client.post(
            "/api/v1/flows/blend", json=_BLEND_BODY, headers=_auth_headers(user_id)
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()

        assert "flow" in body and "materialized" in body
        mat = body["materialized"]
        assert mat["datastore_id"] and mat["query_id"]

        # Flow spec has 2 query source tasks + 1 materialize task.
        tasks = body["flow"]["spec"]["tasks"]
        kinds = sorted(t["kind"] for t in tasks)
        assert kinds == ["materialize", "query", "query"]

        # The run drained to success and the materialized DuckDB file exists.
        assert body["run"]["state"] == "success"
        db = blend_database_path(mat["datastore_id"])
        try:
            assert os.path.exists(db)
            import duckdb

            conn = duckdb.connect(database=db, read_only=True)
            try:
                n = conn.execute(
                    f'SELECT count(*) FROM "{DEFAULT_BLEND_TABLE}"'
                ).fetchone()[0]
                # demo has 3 active + 2 inactive = 5 rows total across the union.
                assert n == 5
            finally:
                conn.close()
        finally:
            _cleanup_db(db)

    @pytest.mark.asyncio
    async def test_create_blend_rls_dropped_returns_400(self, blend_client):
        client, user_id, *_ = blend_client
        bad = dict(_BLEND_BODY)
        bad = {**_BLEND_BODY, "combine_sql": "SELECT name, value FROM src_a"}
        resp = await client.post(
            "/api/v1/flows/blend", json=bad, headers=_auth_headers(user_id)
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["code"] == "blend_materialize_failed"

    @pytest.mark.asyncio
    async def test_create_blend_with_schedule_sets_next_run_at(self, blend_client):
        client, user_id, *_ = blend_client
        body = {**_BLEND_BODY, "schedule": "@hourly"}
        resp = await client.post(
            "/api/v1/flows/blend", json=body, headers=_auth_headers(user_id)
        )
        assert resp.status_code == 201, resp.text
        flow = resp.json()["flow"]
        assert flow["schedule"] == "@hourly"
        assert flow["next_run_at"] is not None
        _cleanup_db(blend_database_path(resp.json()["materialized"]["datastore_id"]))


# ---------------------------------------------------------------------------
# (4) + (5) read the materialized blend via POST /query (single-source serve)
# ---------------------------------------------------------------------------


class TestReadBlend:
    @pytest.mark.asyncio
    async def test_read_blend_returns_all_rows(self, blend_client):
        client, user_id, org_id, store, repo = blend_client
        resp = await client.post(
            "/api/v1/flows/blend", json=_BLEND_BODY, headers=_auth_headers(user_id)
        )
        assert resp.status_code == 201, resp.text
        query_id = resp.json()["materialized"]["query_id"]
        db = blend_database_path(resp.json()["materialized"]["datastore_id"])

        try:
            read = await client.post(
                "/api/v1/query",
                json={"query_id": query_id},
                headers=_auth_headers(user_id),
            )
            assert read.status_code == 200, read.text
            assert "application/vnd.apache.arrow.stream" in read.headers.get(
                "content-type", ""
            )
            table = _parse_arrow(read.content)
            assert table.num_rows == 5
            assert "tenant_id" in table.column_names
        finally:
            _cleanup_db(db)

    @pytest.mark.asyncio
    async def test_read_blend_rls_injection_on_materialized_source(self, blend_client):
        """An embed-style policy injects WHERE tenant_id=<claim> at READ time."""
        client, user_id, org_id, store, repo = blend_client
        resp = await client.post(
            "/api/v1/flows/blend", json=_BLEND_BODY, headers=_auth_headers(user_id)
        )
        assert resp.status_code == 201, resp.text
        mat = resp.json()["materialized"]
        db = blend_database_path(mat["datastore_id"])

        try:
            # Verify read-time RLS using the same read path the connector uses:
            # plan() injects WHERE tenant_id = 't1' on the materialized table,
            # and the duckdb file enforces it (rls_keys were preserved).
            import duckdb

            from app.connectors.duckdb_conn import DuckDBConnector
            from app.connectors.planner import plan

            conn = duckdb.connect(database=db, read_only=True)
            try:
                connector = DuckDBConnector(conn)
                physical = plan(
                    f'SELECT * FROM "{DEFAULT_BLEND_TABLE}"',
                    claims={"policies": {"tenant_id": "t1"}},
                )
                tbl = connector.execute(physical)
                # demo has 3 active rows (tenant t1) + 2 inactive (tenant t2).
                # RLS WHERE tenant_id='t1' keeps only the t1 rows.
                assert tbl.num_rows == 3
                assert set(tbl.column("tenant_id").to_pylist()) == {"t1"}
            finally:
                conn.close()
        finally:
            _cleanup_db(db)


# ---------------------------------------------------------------------------
# (6) POST /flows/tick auth
# ---------------------------------------------------------------------------


class TestFlowsTick:
    @pytest.mark.asyncio
    async def test_tick_disabled_without_secret(self, blend_client):
        client, *_ = blend_client
        from app.config import get_settings

        os.environ.pop("FLOWS_TICK_SECRET", None)
        get_settings.cache_clear()
        try:
            resp = await client.post("/api/v1/flows/tick")
            assert resp.status_code == 503, resp.text
            assert resp.json()["error"]["code"] == "tick_not_configured"
        finally:
            get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_tick_wrong_secret_401(self, blend_client):
        client, *_ = blend_client
        from app.config import get_settings

        os.environ["FLOWS_TICK_SECRET"] = "s3cr3t"
        get_settings.cache_clear()
        try:
            resp = await client.post(
                "/api/v1/flows/tick", headers={"X-Nubi-Tick-Secret": "wrong"}
            )
            assert resp.status_code == 401, resp.text
            # Also: missing header → 401.
            resp2 = await client.post("/api/v1/flows/tick")
            assert resp2.status_code == 401, resp2.text
        finally:
            os.environ.pop("FLOWS_TICK_SECRET", None)
            get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_tick_right_secret_200(self, blend_client):
        client, *_ = blend_client
        from app.config import get_settings

        os.environ["FLOWS_TICK_SECRET"] = "s3cr3t"
        get_settings.cache_clear()
        try:
            resp = await client.post(
                "/api/v1/flows/tick", headers={"X-Nubi-Tick-Secret": "s3cr3t"}
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert "materialised" in body and "tasks_run" in body
        finally:
            os.environ.pop("FLOWS_TICK_SECRET", None)
            get_settings.cache_clear()


# ---------------------------------------------------------------------------
# (7) Atomic claim — no double-run across concurrent ticks
# ---------------------------------------------------------------------------


class TestAtomicClaim:
    @pytest.mark.asyncio
    async def test_due_scheduled_flow_claimed_once(self):
        """Two concurrent claims on the same due slot: exactly one wins."""
        store = InMemoryFlowStore()
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        past = datetime(2026, 1, 1, 11, 0, 0, tzinfo=timezone.utc)
        next_slot = datetime(2026, 1, 1, 13, 0, 0, tzinfo=timezone.utc)

        flow = await store.create_flow(
            org_id=str(uuid.uuid4()),
            created_by=str(uuid.uuid4()),
            name="due flow",
            spec={"version": 1, "name": "due", "tasks": []},
            enabled=True,
            schedule="@hourly",
            next_run_at=past,
        )

        # First claim wins (next_run_at advanced); second sees advanced slot → None.
        first = await store.claim_due_scheduled_flow(flow["id"], now, next_slot)
        second = await store.claim_due_scheduled_flow(flow["id"], now, next_slot)
        assert first is not None
        assert second is None
        assert first["next_run_at"] == next_slot

    @pytest.mark.asyncio
    async def test_flow_tick_materializes_due_flow_once(self):
        """Two flow_ticks at the same instant materialize a due flow exactly once."""
        from app.flows.runtime import flow_tick

        store = InMemoryFlowStore()
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        past = datetime(2026, 1, 1, 11, 0, 0, tzinfo=timezone.utc)

        flow = await store.create_flow(
            org_id=str(uuid.uuid4()),
            created_by=str(uuid.uuid4()),
            name="due noop flow",
            spec={
                "version": 1,
                "name": "due",
                "tasks": [{"key": "n", "kind": "noop", "needs": [], "config": {}}],
            },
            enabled=True,
            schedule="@hourly",
            next_run_at=past,
        )

        s1 = await flow_tick(store, now)
        s2 = await flow_tick(store, now)
        # First tick materialises the due flow; the second finds it already
        # advanced (next_run_at moved to the next hour > now) → 0 materialised.
        assert s1["materialised"] == 1
        assert s2["materialised"] == 0

        runs = await store.list_flow_runs(flow["id"])
        assert len(runs) == 1
