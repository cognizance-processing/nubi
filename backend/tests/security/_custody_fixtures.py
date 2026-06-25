"""Shared fixtures + helpers for the custody adversarial security tests.

These tests stand up a local (``file://``) central lake with the custody tier
enabled, an ``InMemoryRepo``, and an in-memory ingest-session / export-job
store, then drive the ``/lake/*`` routes through an ASGI client.

Kept in a private module (leading underscore) so pytest does not collect it as
a test file.
"""

from __future__ import annotations

# Import the route modules BEFORE the app is built so they self-register on
# api_router.
import app.routes.ingest  # noqa: F401
import app.routes.lake_export  # noqa: F401

import hashlib
import io
import os
import uuid
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.lakehouse.ingest_session import (
    InMemoryIngestSessionStore,
    set_ingest_session_store,
)
from app.lakehouse.managed import MANAGED_MARKER, lake_prefix
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo
from app.routes.lake_export import InMemoryExportJobStore, set_export_job_store


def make_user(user_id: str | None = None, email: str = "tester@nubi.test") -> dict[str, Any]:
    return {
        "id": user_id or str(uuid.uuid4()),
        "email": email,
        "name": "Tester",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


def auth_headers(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


def build_parquet(rows: list[dict[str, Any]]) -> bytes:
    if not rows:
        table = pa.table({"id": pa.array([], type=pa.int64())})
    else:
        keys = list(rows[0].keys())
        table = pa.table({k: pa.array([r[k] for r in rows]) for k in keys})
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def managed_lake_config(org_id: str, datastore_id: str, lake_dir: str) -> dict[str, Any]:
    prefix = lake_prefix(org_id, datastore_id)
    return {
        "connector_type": "duckdb",
        "database": f"file://{lake_dir}/{prefix}",
        MANAGED_MARKER: True,
        "managed_prefix": prefix,
        "managed_scheme": "file",
        "description": "Test managed lakehouse.",
    }


@pytest_asyncio.fixture
async def custody_env(tmp_path, monkeypatch, fake_db):
    """Custody-enabled env with two orgs (alice, bob), each owning a managed lake.

    Yields a dict with: lake_dir, alice_id/alice_org/alice_ds,
    bob_id/bob_org/bob_ds, client, repo, session_store, job_store.
    """
    lake_dir = str(tmp_path / "managed-lake")
    os.makedirs(lake_dir, exist_ok=True)
    monkeypatch.setenv("NUBI_MANAGED_LAKE_DIR", lake_dir)
    monkeypatch.setenv("NUBI_CUSTODY_ENABLED", "true")
    # Avoid client-mode CMEK leaking from the host env.
    monkeypatch.setenv("NUBI_CMEK_MODE", "none")

    from app.config import get_settings
    get_settings.cache_clear()

    repo = InMemoryRepo()
    set_repo(repo)
    session_store = InMemoryIngestSessionStore()
    set_ingest_session_store(session_store)
    job_store = InMemoryExportJobStore()
    set_export_job_store(job_store)

    alice_id, alice_org, alice_ds = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    bob_id, bob_org, bob_ds = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())

    for uid, email in ((alice_id, "alice@nubi.test"), (bob_id, "bob@nubi.test")):
        fake_db.users[uid] = make_user(user_id=uid, email=email)

    repo.seed_org_member(org_id=alice_org, user_id=alice_id)
    repo.seed_org_member(org_id=bob_org, user_id=bob_id)

    for org, ds, uid in ((alice_org, alice_ds, alice_id), (bob_org, bob_ds, bob_id)):
        await repo.create(
            resource="datastores",
            org_id=org,
            created_by=uid,
            name="Managed lake",
            config=managed_lake_config(org, ds, lake_dir),
            id=ds,
        )

    from unittest.mock import AsyncMock, patch

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
            "alice_id": alice_id,
            "alice_org": alice_org,
            "alice_ds": alice_ds,
            "bob_id": bob_id,
            "bob_org": bob_org,
            "bob_ds": bob_ds,
            "client": ac,
            "repo": repo,
            "session_store": session_store,
            "job_store": job_store,
        }

    for p in patches:
        p.stop()
    set_repo(None)
    set_ingest_session_store(None)
    set_export_job_store(None)
    get_settings.cache_clear()


async def open_session(
    client: AsyncClient,
    headers: dict,
    datastore_id: str,
    *,
    mode: str = "full_replace",
    partition: str | None = None,
    schema: list | None = None,
    idempotency_key: str | None = None,
    table_name: str | None = None,
) -> Any:
    body: dict[str, Any] = {
        "mode": mode,
        "schema": schema or [{"name": "id", "type": "int64"}, {"name": "val", "type": "string"}],
        "idempotency_key": idempotency_key or str(uuid.uuid4()),
    }
    if partition:
        body["partition"] = partition
    if table_name:
        body["table_name"] = table_name
    return await client.post(
        f"/api/v1/lake/{datastore_id}/ingest/sessions", json=body, headers=headers
    )
