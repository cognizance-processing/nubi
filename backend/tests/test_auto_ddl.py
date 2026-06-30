"""Tests for auto-DDL (auto_create flag) on the lake-ingest and file-ingest paths.

Test plan
---------
1. First ingest of a new table (auto_create=True, default) → auto-creates schema
   contract; table is queryable (sidecar exists).
2. Re-ingest is idempotent — schema is the same, no error, sidecar stable.
3. Inferred schema becomes the contract: incompatible 2nd ingest → 409
   schema_incompatible.
4. Additive column on 2nd ingest → contract extended (new column in sidecar).
5. auto_create=False → no sidecar written on first ingest (original behaviour).
6. Org-scoped: org B cannot read or clobber org A's contract.
7. file_ingest path: auto_create=True writes sidecar after first successful load.
8. file_ingest path: auto_create=False skips sidecar write.
"""

from __future__ import annotations

# IMPORTANT: import route module BEFORE app is built so it self-registers.
import app.routes.ingest  # noqa: F401

import hashlib
import io
import json
import os
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.lakehouse.managed import MANAGED_MARKER, lake_prefix
from app.lakehouse.ingest_session import (
    InMemoryIngestSessionStore,
    set_ingest_session_store,
)
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(user_id: str | None = None, email: str = "tester@nubi.test") -> dict[str, Any]:
    return {
        "id": user_id or str(uuid.uuid4()),
        "email": email,
        "name": "Tester",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


def _auth_headers(user_id: str) -> dict[str, str]:
    token = mint_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


def _build_parquet(rows: list[dict[str, Any]]) -> bytes:
    """Build a minimal Parquet file from a list of row dicts."""
    if not rows:
        table = pa.table({"id": pa.array([], type=pa.int64())})
    else:
        keys = list(rows[0].keys())
        arrays = {k: pa.array([r[k] for r in rows]) for k in keys}
        table = pa.table(arrays)
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _managed_lake_config(org_id: str, datastore_id: str, lake_dir: str) -> dict[str, Any]:
    """Return the managed-lake connector config matching PrefixIsolatedProvider output."""
    prefix = lake_prefix(org_id, datastore_id)
    return {
        "connector_type": "duckdb",
        "database": f"file://{lake_dir}/{prefix}",
        MANAGED_MARKER: True,
        "managed_prefix": prefix,
        "managed_scheme": "file",
        "description": "Test managed lakehouse.",
    }


def _load_sidecar(lake_dir: str, org_id: str, datastore_id: str) -> dict:
    """Load the _nubi/schema.json sidecar from the local lake dir."""
    prefix = lake_prefix(org_id, datastore_id)
    sidecar_path = os.path.join(lake_dir, prefix, "_nubi", "schema.json")
    if not os.path.isfile(sidecar_path):
        return {}
    with open(sidecar_path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def ingest_env(tmp_path, monkeypatch, fake_db):
    """Full ingest test environment (local file:// lake, in-memory repo + session store)."""
    lake_dir = str(tmp_path / "managed-lake")
    os.makedirs(lake_dir, exist_ok=True)
    monkeypatch.setenv("NUBI_MANAGED_LAKE_DIR", lake_dir)
    monkeypatch.setenv("NUBI_CUSTODY_ENABLED", "true")

    from app.config import get_settings
    get_settings.cache_clear()

    repo = InMemoryRepo()
    set_repo(repo)
    session_store = InMemoryIngestSessionStore()
    set_ingest_session_store(session_store)

    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    user = _make_user(user_id=user_id)
    fake_db.users[user_id] = user
    repo.seed_org_member(org_id=org_id, user_id=user_id)

    datastore_id = str(uuid.uuid4())
    await repo.create(
        resource="datastores",
        org_id=org_id,
        created_by=user_id,
        name="Test managed lake",
        config=_managed_lake_config(org_id, datastore_id, lake_dir),
        id=datastore_id,
    )

    patches = [
        patch("app.db.fetchrow", side_effect=fake_db.fake_fetchrow),
        patch("app.db.fetch", side_effect=fake_db.fake_fetch),
        patch("app.db.execute", side_effect=fake_db.fake_execute),
        patch("app.db.get_connection", new=fake_db.fake_get_connection),
        patch("app.routes.auth.fetchrow", side_effect=fake_db.fake_fetchrow),
        patch("app.routes.auth.execute", side_effect=fake_db.fake_execute),
        patch("app.auth.sessions.fetchrow", side_effect=fake_db.fake_fetchrow),
        patch("app.auth.sessions.execute", side_effect=fake_db.fake_execute),
        patch("app.auth.sessions.get_connection", new=fake_db.fake_get_connection),
        patch("app.auth.deps.fetchrow", side_effect=fake_db.fake_fetchrow),
        patch("app.db.init_db", new=AsyncMock()),
        patch("app.db.close_db", new=AsyncMock()),
    ]
    for p in patches:
        p.start()

    import main as main_module
    test_app = main_module.create_app()
    transport = ASGITransport(app=test_app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as ac:
        yield {
            "lake_dir": lake_dir,
            "user_id": user_id,
            "org_id": org_id,
            "datastore_id": datastore_id,
            "client": ac,
            "repo": repo,
            "session_store": session_store,
            "headers": _auth_headers(user_id),
        }

    for p in patches:
        p.stop()
    set_repo(None)
    set_ingest_session_store(None)
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# HTTP session helpers
# ---------------------------------------------------------------------------


async def _open_session(
    client, headers, datastore_id, *,
    mode="full_replace", schema=None, partition=None,
    table_name="orders", idempotency_key=None, auto_create=None,
) -> dict:
    body: dict[str, Any] = {
        "mode": mode,
        "schema": schema or [{"name": "id", "type": "int64"}, {"name": "val", "type": "string"}],
        "idempotency_key": idempotency_key or str(uuid.uuid4()),
        "table_name": table_name,
    }
    if partition:
        body["partition"] = partition
    if auto_create is not None:
        body["auto_create"] = auto_create
    r = await client.post(
        f"/api/v1/lake/{datastore_id}/ingest/sessions",
        json=body, headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _upload_and_commit(
    client, headers, datastore_id, pq_data, *,
    mode="full_replace", schema=None, partition=None,
    table_name="orders", auto_create=None,
) -> dict:
    session = await _open_session(
        client, headers, datastore_id,
        mode=mode, schema=schema, partition=partition,
        table_name=table_name, auto_create=auto_create,
    )
    sid = session["session_id"]
    r = await client.put(
        f"/api/v1/lake/{datastore_id}/ingest/sessions/{sid}/parts/part0.parquet",
        content=pq_data,
        headers={**headers, "Content-Type": "application/octet-stream"},
    )
    assert r.status_code == 200, r.text
    entry = r.json()
    manifest = {"files": [entry], "row_counts": {"part0.parquet": 1}}
    r2 = await client.post(
        f"/api/v1/lake/{datastore_id}/ingest/sessions/{sid}/commit",
        json=manifest, headers=headers,
    )
    assert r2.status_code == 200, r2.text
    return r2.json()


# ---------------------------------------------------------------------------
# Test 1: First ingest auto-creates schema contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_create_first_ingest_writes_sidecar(ingest_env):
    """auto_create=True (default): first ingest writes the schema sidecar."""
    env = ingest_env
    pq_data = _build_parquet([{"id": 1, "val": "hello"}])

    result = await _upload_and_commit(
        env["client"], env["headers"], env["datastore_id"], pq_data,
        schema=[{"name": "id", "type": "BIGINT"}, {"name": "val", "type": "VARCHAR"}],
        table_name="orders",
    )
    assert result["published"] is True

    # The schema sidecar must now exist with the "orders" table entry.
    sidecar = _load_sidecar(env["lake_dir"], env["org_id"], env["datastore_id"])
    assert "orders" in sidecar, f"Expected 'orders' in sidecar: {sidecar}"
    cols = {c["name"] for c in sidecar["orders"]}
    assert "id" in cols
    assert "val" in cols


# ---------------------------------------------------------------------------
# Test 2: Re-ingest is idempotent (same schema → no error, sidecar stable)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_create_reingest_idempotent(ingest_env):
    """Re-ingesting with the same schema is idempotent (no 409, sidecar unchanged)."""
    env = ingest_env
    pq_data = _build_parquet([{"id": 1, "val": "hello"}])
    schema = [{"name": "id", "type": "BIGINT"}, {"name": "val", "type": "VARCHAR"}]

    # First ingest → creates sidecar
    await _upload_and_commit(
        env["client"], env["headers"], env["datastore_id"], pq_data,
        schema=schema, table_name="orders",
    )

    # Second ingest with identical schema → must succeed
    result2 = await _upload_and_commit(
        env["client"], env["headers"], env["datastore_id"], pq_data,
        schema=schema, table_name="orders",
    )
    assert result2["published"] is True

    sidecar = _load_sidecar(env["lake_dir"], env["org_id"], env["datastore_id"])
    cols = {c["name"] for c in sidecar.get("orders", [])}
    assert cols == {"id", "val"}, f"Unexpected sidecar columns: {cols}"


# ---------------------------------------------------------------------------
# Test 3: Inferred schema becomes the contract → incompatible 2nd ingest → 409
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_create_contract_gate_incompatible(ingest_env):
    """After auto-DDL registers the schema, an incompatible commit returns 409."""
    env = ingest_env
    pq_data = _build_parquet([{"id": 1, "val": "hello"}])
    schema_v1 = [{"name": "id", "type": "BIGINT"}, {"name": "val", "type": "VARCHAR"}]
    schema_bad = [{"name": "id", "type": "BIGINT"}]  # 'val' removed → incompatible

    # First ingest → sidecar created
    await _upload_and_commit(
        env["client"], env["headers"], env["datastore_id"], pq_data,
        schema=schema_v1, table_name="orders",
    )

    # Second ingest with 'val' column removed → must fail 409
    session = await _open_session(
        env["client"], env["headers"], env["datastore_id"],
        schema=schema_bad, table_name="orders",
    )
    sid = session["session_id"]
    r = await env["client"].put(
        f"/api/v1/lake/{env['datastore_id']}/ingest/sessions/{sid}/parts/part0.parquet",
        content=pq_data,
        headers={**env["headers"], "Content-Type": "application/octet-stream"},
    )
    assert r.status_code == 200
    entry = r.json()
    manifest = {"files": [entry], "row_counts": {"part0.parquet": 1}}
    r2 = await env["client"].post(
        f"/api/v1/lake/{env['datastore_id']}/ingest/sessions/{sid}/commit",
        json=manifest, headers=env["headers"],
    )
    assert r2.status_code == 409, f"Expected 409 for incompatible schema, got {r2.status_code}: {r2.text}"
    body = r2.json()
    error_code = body.get("code") or (body.get("error") or {}).get("code", "")
    assert "schema_incompatible" in error_code, f"Expected schema_incompatible, got: {body}"


# ---------------------------------------------------------------------------
# Test 4: Additive column on 2nd ingest → contract extended
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_create_additive_column_extends_contract(ingest_env):
    """Additive new column on 2nd ingest extends the stored contract."""
    env = ingest_env
    pq_v1 = _build_parquet([{"id": 1, "val": "hello"}])
    pq_v2 = _build_parquet([{"id": 2, "val": "world", "extra": "new_col"}])
    schema_v1 = [{"name": "id", "type": "BIGINT"}, {"name": "val", "type": "VARCHAR"}]
    schema_v2 = schema_v1 + [{"name": "extra", "type": "VARCHAR"}]

    # First ingest
    await _upload_and_commit(
        env["client"], env["headers"], env["datastore_id"], pq_v1,
        schema=schema_v1, table_name="events",
    )
    sidecar_after_v1 = _load_sidecar(env["lake_dir"], env["org_id"], env["datastore_id"])
    assert {c["name"] for c in sidecar_after_v1.get("events", [])} == {"id", "val"}

    # Second ingest with additive column
    result2 = await _upload_and_commit(
        env["client"], env["headers"], env["datastore_id"], pq_v2,
        schema=schema_v2, table_name="events",
    )
    assert result2["published"] is True

    sidecar_after_v2 = _load_sidecar(env["lake_dir"], env["org_id"], env["datastore_id"])
    cols = {c["name"] for c in sidecar_after_v2.get("events", [])}
    assert "extra" in cols, f"Additive column 'extra' not in sidecar: {cols}"
    assert "id" in cols and "val" in cols


# ---------------------------------------------------------------------------
# Test 5: auto_create=False → no sidecar written on first ingest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_create_false_no_sidecar(ingest_env):
    """auto_create=False: first ingest does NOT write the schema sidecar."""
    env = ingest_env
    pq_data = _build_parquet([{"id": 1, "val": "hello"}])

    await _upload_and_commit(
        env["client"], env["headers"], env["datastore_id"], pq_data,
        schema=[{"name": "id", "type": "BIGINT"}, {"name": "val", "type": "VARCHAR"}],
        table_name="no_ddl_table",
        auto_create=False,
    )

    sidecar = _load_sidecar(env["lake_dir"], env["org_id"], env["datastore_id"])
    assert "no_ddl_table" not in sidecar, (
        f"auto_create=False must not write sidecar, but found: {sidecar}"
    )


# ---------------------------------------------------------------------------
# Test 6: Org-scoped — org B cannot read or clobber org A's contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_create_org_scoped(tmp_path, monkeypatch, fake_db):
    """Org A's contract is invisible to org B (different lake prefix)."""
    import app.routes.ingest  # noqa: F401

    lake_dir = str(tmp_path / "managed-lake")
    os.makedirs(lake_dir, exist_ok=True)
    monkeypatch.setenv("NUBI_MANAGED_LAKE_DIR", lake_dir)
    monkeypatch.setenv("NUBI_CUSTODY_ENABLED", "true")

    from app.config import get_settings
    get_settings.cache_clear()

    repo = InMemoryRepo()
    set_repo(repo)
    session_store = InMemoryIngestSessionStore()
    set_ingest_session_store(session_store)

    # Set up two orgs
    user_a_id = str(uuid.uuid4())
    org_a_id = str(uuid.uuid4())
    user_b_id = str(uuid.uuid4())
    org_b_id = str(uuid.uuid4())
    for uid, oid in ((user_a_id, org_a_id), (user_b_id, org_b_id)):
        fake_db.users[uid] = _make_user(user_id=uid)
        repo.seed_org_member(org_id=oid, user_id=uid)

    ds_a_id = str(uuid.uuid4())
    ds_b_id = str(uuid.uuid4())
    for uid, oid, dsid in (
        (user_a_id, org_a_id, ds_a_id),
        (user_b_id, org_b_id, ds_b_id),
    ):
        await repo.create(
            resource="datastores",
            org_id=oid,
            created_by=uid,
            name="Test lake",
            config=_managed_lake_config(oid, dsid, lake_dir),
            id=dsid,
        )

    patches = [
        patch("app.db.fetchrow", side_effect=fake_db.fake_fetchrow),
        patch("app.db.fetch", side_effect=fake_db.fake_fetch),
        patch("app.db.execute", side_effect=fake_db.fake_execute),
        patch("app.db.get_connection", new=fake_db.fake_get_connection),
        patch("app.routes.auth.fetchrow", side_effect=fake_db.fake_fetchrow),
        patch("app.routes.auth.execute", side_effect=fake_db.fake_execute),
        patch("app.auth.sessions.fetchrow", side_effect=fake_db.fake_fetchrow),
        patch("app.auth.sessions.execute", side_effect=fake_db.fake_execute),
        patch("app.auth.sessions.get_connection", new=fake_db.fake_get_connection),
        patch("app.auth.deps.fetchrow", side_effect=fake_db.fake_fetchrow),
        patch("app.db.init_db", new=AsyncMock()),
        patch("app.db.close_db", new=AsyncMock()),
    ]
    for p in patches:
        p.start()

    try:
        import main as main_module
        test_app = main_module.create_app()
        transport = ASGITransport(app=test_app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver", follow_redirects=False
        ) as ac:
            headers_a = _auth_headers(user_a_id)
            headers_b = _auth_headers(user_b_id)
            pq_data = _build_parquet([{"id": 1}])
            schema = [{"name": "id", "type": "BIGINT"}]

            # Org A ingests into ds_a → sidecar for org A
            await _upload_and_commit(ac, headers_a, ds_a_id, pq_data, schema=schema, table_name="shared")

            sidecar_a = _load_sidecar(lake_dir, org_a_id, ds_a_id)
            sidecar_b = _load_sidecar(lake_dir, org_b_id, ds_b_id)

            # Org A has a sidecar; org B does NOT
            assert "shared" in sidecar_a, "Org A should have 'shared' in its sidecar"
            assert "shared" not in sidecar_b, (
                f"Org B must NOT see org A's sidecar, but got: {sidecar_b}"
            )

            # Org B can ingest the same table name independently (different prefix)
            result_b = await _upload_and_commit(
                ac, headers_b, ds_b_id, pq_data, schema=schema, table_name="shared"
            )
            assert result_b["published"] is True

    finally:
        for p in patches:
            p.stop()
        set_repo(None)
        set_ingest_session_store(None)
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Test 7: file_ingest path — auto_create=True writes sidecar after load
# ---------------------------------------------------------------------------


def _make_staging_area(tmp_path):
    """Return a real StagingArea backed by a local temp dir."""
    from app.lakehouse.managed import CentralStorage
    from app.lakehouse.staging import StagingArea
    central = CentralStorage(scheme="file", bucket=str(tmp_path / "staging"), creds={})
    os.makedirs(central.bucket, exist_ok=True)
    return StagingArea(central=central, org_id="org1", run_id="run1")


def _make_pq_bytes(rows: list[dict]) -> bytes:
    return _build_parquet(rows)


def test_file_ingest_auto_create_writes_sidecar(tmp_path, monkeypatch):
    """file_ingest handle: auto_create=True writes schema sidecar after first load."""
    import app.flows.handlers.file_ingest as fi
    from app.connectors.base import FileStat, file_capabilities, FileConnectorMixin
    from app.flows.executor import TaskContext
    from app.lakehouse.staging import StagingArea
    from app.lakehouse.managed import CentralStorage

    pq_rows = [{"id": 1, "name": "alice"}]
    pq_bytes = _make_pq_bytes(pq_rows)

    # Fake file connector with one CSV file (auto-detected from .csv extension)
    import csv

    csv_buf = io.StringIO()
    writer = csv.DictWriter(csv_buf, fieldnames=["id", "name"])
    writer.writeheader()
    writer.writerows(pq_rows)
    csv_bytes = csv_buf.getvalue().encode()

    class FakeSrc(FileConnectorMixin):
        def capabilities(self):
            return file_capabilities(file_interface=True)
        def list_files(self, pattern, since=None):
            return [FileStat(path="data.csv", size=len(csv_bytes), mtime=None)]
        def open(self, path):
            return io.BytesIO(csv_bytes)
        def move(self, src, dst): pass
        def delete(self, path): pass

    # Fake load target (no-op promote)
    from app.flows.loaders import LoadTarget
    from app.connectors.base import file_capabilities as fcap

    lake_dir = str(tmp_path / "lake")
    os.makedirs(lake_dir, exist_ok=True)
    staging_dir = str(tmp_path / "staging")
    os.makedirs(staging_dir, exist_ok=True)

    tgt_connector_id = "conn-" + str(uuid.uuid4())
    tgt_object = "raw.customers"

    # Fake _resolve_source_connector + _resolve_target + _resolve_staging
    staging_central = CentralStorage(scheme="file", bucket=staging_dir, creds={})
    staging_area = StagingArea(central=staging_central, org_id="org1", run_id="run1")

    def fake_final_key(staged_rel):
        return staged_rel

    from app.storage.local import LocalStorageClient
    fake_client = LocalStorageClient(root=lake_dir)

    load_target = LoadTarget(
        object_name=tgt_object,
        capabilities=fcap(file_interface=True),
    )
    load_target._promote_client = fake_client
    load_target._final_key = fake_final_key

    ctx = TaskContext(
        org_id="org1", run_id="r1", watermark=None,
    )

    config = {
        "source": {"connector_id": "src-1", "path": "*.csv"},
        "target": {"connector_id": tgt_connector_id, "object": tgt_object},
        "format": "csv",
        "mode": "append",
        "auto_create": True,
    }

    # We need a sidecar target dir
    sidecar_lake_dir = str(tmp_path / "sidecar-lake")
    os.makedirs(sidecar_lake_dir, exist_ok=True)
    monkeypatch.setenv("NUBI_MANAGED_LAKE_DIR", sidecar_lake_dir)

    from app.lakehouse.ingest_session import InMemoryIngestSessionStore, set_ingest_session_store
    set_ingest_session_store(InMemoryIngestSessionStore())

    with (
        patch.object(fi, "_resolve_source_connector", return_value=FakeSrc()),
        patch.object(fi, "_resolve_target", return_value=load_target),
        patch.object(fi, "_resolve_staging", return_value=staging_area),
    ):
        result = fi.handle(config, ctx, {})

    assert result["files_ingested"] == 1

    # Check sidecar was written
    from app.routes.ingest import _load_table_schema
    schema = _load_table_schema("org1", tgt_connector_id, tgt_object)
    assert schema is not None, "auto_create=True should have written a schema sidecar"
    col_names = {c["name"] for c in schema}
    assert "id" in col_names
    assert "name" in col_names

    set_ingest_session_store(None)


def test_file_ingest_auto_create_false_no_sidecar(tmp_path, monkeypatch):
    """file_ingest handle: auto_create=False skips sidecar write."""
    import app.flows.handlers.file_ingest as fi
    from app.connectors.base import FileStat, file_capabilities, FileConnectorMixin
    from app.flows.executor import TaskContext
    from app.lakehouse.staging import StagingArea
    from app.lakehouse.managed import CentralStorage
    from app.flows.loaders import LoadTarget
    from app.connectors.base import file_capabilities as fcap

    import csv

    pq_rows = [{"id": 1, "name": "alice"}]
    csv_buf = io.StringIO()
    writer = csv.DictWriter(csv_buf, fieldnames=["id", "name"])
    writer.writeheader()
    writer.writerows(pq_rows)
    csv_bytes = csv_buf.getvalue().encode()

    class FakeSrc(FileConnectorMixin):
        def capabilities(self):
            return file_capabilities(file_interface=True)
        def list_files(self, pattern, since=None):
            return [FileStat(path="data.csv", size=len(csv_bytes), mtime=None)]
        def open(self, path):
            return io.BytesIO(csv_bytes)
        def move(self, src, dst): pass
        def delete(self, path): pass

    lake_dir = str(tmp_path / "lake")
    os.makedirs(lake_dir, exist_ok=True)
    staging_dir = str(tmp_path / "staging")
    os.makedirs(staging_dir, exist_ok=True)

    tgt_connector_id = "conn-" + str(uuid.uuid4())
    tgt_object = "raw.no_ddl"

    staging_central = CentralStorage(scheme="file", bucket=staging_dir, creds={})
    staging_area = StagingArea(central=staging_central, org_id="org2", run_id="run2")

    from app.storage.local import LocalStorageClient
    fake_client = LocalStorageClient(root=lake_dir)

    load_target = LoadTarget(
        object_name=tgt_object,
        capabilities=fcap(file_interface=True),
    )
    load_target._promote_client = fake_client
    load_target._final_key = lambda staged_rel: staged_rel

    ctx = TaskContext(
        org_id="org2", run_id="r1", watermark=None,
    )

    config = {
        "source": {"connector_id": "src-1", "path": "*.csv"},
        "target": {"connector_id": tgt_connector_id, "object": tgt_object},
        "format": "csv",
        "mode": "append",
        "auto_create": False,
    }

    sidecar_lake_dir = str(tmp_path / "sidecar-lake2")
    os.makedirs(sidecar_lake_dir, exist_ok=True)
    monkeypatch.setenv("NUBI_MANAGED_LAKE_DIR", sidecar_lake_dir)

    from app.lakehouse.ingest_session import InMemoryIngestSessionStore, set_ingest_session_store
    set_ingest_session_store(InMemoryIngestSessionStore())

    with (
        patch.object(fi, "_resolve_source_connector", return_value=FakeSrc()),
        patch.object(fi, "_resolve_target", return_value=load_target),
        patch.object(fi, "_resolve_staging", return_value=staging_area),
    ):
        result = fi.handle(config, ctx, {})

    assert result["files_ingested"] == 1

    from app.routes.ingest import _load_table_schema
    schema = _load_table_schema("org2", tgt_connector_id, tgt_object)
    assert schema is None, (
        f"auto_create=False must NOT write sidecar, but got: {schema}"
    )

    set_ingest_session_store(None)
