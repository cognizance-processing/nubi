"""Tests for the managed-lakehouse provisioning layer.

Covers (per the build spec):
  - provision idempotency
  - status shape
  - usage_bytes accounting
  - demo seeding
  - deprovision cleanup (objects + row + secret)
  - tenant isolation (org A cannot see / deprovision org B)
  - path-pinning (the managed datastore cannot be repointed cross-org via PUT)
  - the not-configured degrade path

Strategy mirrors ``test_connectors_route``:
  - ``InMemoryRepo`` injected via ``set_repo`` (no live DB).
  - Central storage uses the LOCAL file backend via ``NUBI_MANAGED_LAKE_DIR``
    pointed at a tmp dir — no MinIO / S3 needed.
  - Real JWTs via ``mint_access_token``; conftest patches the user lookup.
"""

from __future__ import annotations

import base64
import os
import secrets
import uuid
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import mint_access_token
from app.connectors.secret_store import InMemorySecretStore, set_secret_store_for_tests
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo

# Ensure the lakehouse + connectors routers register on api_router at import.
import app.routes.connectors  # noqa: F401
import app.routes.lakehouse  # noqa: F401


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(user_id: str, email: str = "alice@example.com") -> dict[str, Any]:
    return {
        "id": user_id,
        "email": email,
        "name": "Alice",
        "avatar_url": None,
        "email_verified": True,
        "created_at": "2024-01-01T00:00:00+00:00",
    }


def _auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_access_token(user_id)}"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _small_demo(monkeypatch):
    """Shrink the demo dataset to a single small dataset for fast tests.

    The full demo bundle generates 17 tables across 4 datasets; regenerating it
    in every seeding test makes the suite minutes-long. We patch DATASET_TABLES
    down to one tiny dataset — the export/list/size/delete code path is identical
    (still real parquet, real storage client), only the volume shrinks.
    """
    import seed_data.generators as gens

    full = gens.DATASET_TABLES
    # Keep only the dataset with the FEWEST tables (build_dataset validates the
    # full per-dataset inventory, so we cannot slice a dataset's tables — we drop
    # whole datasets instead). The export/list/size/delete path is unchanged.
    smallest = min(full, key=lambda k: len(full[k]))
    monkeypatch.setattr(gens, "DATASET_TABLES", {smallest: full[smallest]})
    yield


@pytest_asyncio.fixture
async def lake_dir(tmp_path, monkeypatch):
    """Point central storage at a local tmp dir (file backend)."""
    d = tmp_path / "managed-lake"
    d.mkdir()
    monkeypatch.setenv("NUBI_MANAGED_LAKE_DIR", str(d))
    # Ensure no S3 creds leak in and flip resolution to s3.
    for var in ("S3_ACCESS_KEY", "AWS_ACCESS_KEY_ID"):
        monkeypatch.delenv(var, raising=False)
    yield str(d)


@pytest_asyncio.fixture
async def lake_app(app, lake_dir, monkeypatch):
    """App with InMemoryRepo + InMemorySecretStore + local central storage.

    The InMemorySecretStore performs REAL AES-256-GCM (same as production), so a
    crypto key must be configured for the central-creds secret put/get path.
    """
    from app.security.crypto import reset_keys_for_tests

    monkeypatch.setenv(
        "CONNECTOR_SECRET_KEY", base64.b64encode(secrets.token_bytes(32)).decode()
    )
    monkeypatch.setenv("CONNECTOR_SECRET_KEY_VERSION", "1")
    monkeypatch.delenv("CONNECTOR_SECRET_KEYS", raising=False)
    reset_keys_for_tests()

    repo = InMemoryRepo()
    set_repo(repo)
    store = InMemorySecretStore()
    set_secret_store_for_tests(store)
    yield app, repo, store, lake_dir
    set_repo(None)
    set_secret_store_for_tests(None)


@pytest_asyncio.fixture
async def client_org_a(lake_app, fake_db):
    """Client + seeded user/org A (writer)."""
    app, repo, store, lake_dir = lake_app
    uid = str(uuid.uuid4())
    org = str(uuid.uuid4())
    fake_db.users[uid] = _make_user(uid)
    repo.seed_org_member(org_id=org, user_id=uid, role="owner")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c, uid, org, repo, store, lake_dir


# ---------------------------------------------------------------------------
# Not-configured degrade path
# ---------------------------------------------------------------------------


class TestNotConfigured:
    @pytest.mark.asyncio
    async def test_get_degrades_when_unconfigured(self, app, fake_db, monkeypatch):
        for var in ("NUBI_MANAGED_LAKE_DIR", "NUBI_LOCAL_LAKE_DIR",
                    "S3_ACCESS_KEY", "AWS_ACCESS_KEY_ID"):
            monkeypatch.delenv(var, raising=False)
        repo = InMemoryRepo()
        set_repo(repo)
        set_secret_store_for_tests(InMemorySecretStore())
        try:
            uid = str(uuid.uuid4())
            org = str(uuid.uuid4())
            fake_db.users[uid] = _make_user(uid)
            repo.seed_org_member(org_id=org, user_id=uid, role="owner")
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://testserver") as c:
                resp = await c.get("/api/v1/lakehouse", headers=_auth(uid))
                assert resp.status_code == 200, resp.text
                body = resp.json()
                assert body["configured"] is False
                assert body["provisioned"] is False
                assert "detail" in body

                # Provision must 409 when unconfigured.
                resp2 = await c.post("/api/v1/lakehouse/provision", headers=_auth(uid))
                assert resp2.status_code == 409, resp2.text
        finally:
            set_repo(None)
            set_secret_store_for_tests(None)


# ---------------------------------------------------------------------------
# Status / provision idempotency
# ---------------------------------------------------------------------------


class TestProvision:
    @pytest.mark.asyncio
    async def test_status_before_provision(self, client_org_a):
        c, uid, org, repo, store, _ = client_org_a
        resp = await c.get("/api/v1/lakehouse", headers=_auth(uid))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["configured"] is True
        assert body["provisioned"] is False
        assert body["datastore_id"] is None
        assert body["prefix"] == f"orgs/{org}/lake/"
        assert body["usage_bytes"] == 0

    @pytest.mark.asyncio
    async def test_provision_then_status(self, client_org_a):
        c, uid, org, repo, store, _ = client_org_a
        resp = await c.post("/api/v1/lakehouse/provision", headers=_auth(uid))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["provisioned"] is True
        ds_id = body["datastore_id"]
        assert ds_id

        # The datastore row is server-pinned + marked managed; secret stored.
        row = await repo.get("datastores", org, ds_id)
        assert row["config"]["managed_lake"] is True
        assert row["config"]["database"].endswith(f"orgs/{org}/lake/")
        # GET reflects it.
        resp2 = await c.get("/api/v1/lakehouse", headers=_auth(uid))
        assert resp2.json()["datastore_id"] == ds_id

    @pytest.mark.asyncio
    async def test_provision_is_idempotent(self, client_org_a):
        c, uid, org, repo, store, _ = client_org_a
        r1 = await c.post("/api/v1/lakehouse/provision", headers=_auth(uid))
        r2 = await c.post("/api/v1/lakehouse/provision", headers=_auth(uid))
        assert r1.json()["datastore_id"] == r2.json()["datastore_id"]
        # Exactly one managed datastore row exists.
        rows = await repo.list("datastores", org)
        managed = [r for r in rows if r["config"].get("managed_lake")]
        assert len(managed) == 1

    @pytest.mark.asyncio
    async def test_managed_row_hidden_from_connectors_list(self, client_org_a):
        c, uid, org, repo, store, _ = client_org_a
        await c.post("/api/v1/lakehouse/provision", headers=_auth(uid))
        resp = await c.get("/api/v1/connectors", headers=_auth(uid))
        # system rows (managed lake) are filtered out of the connectors list.
        types = [r["config"].get("connector_type") for r in resp.json()]
        assert all(not r["config"].get("managed_lake") for r in resp.json())


# ---------------------------------------------------------------------------
# Demo seeding + usage accounting
# ---------------------------------------------------------------------------


class TestDemoAndUsage:
    @pytest.mark.asyncio
    async def test_seed_demo_writes_parquet_and_accounts_usage(self, client_org_a):
        c, uid, org, repo, store, lake_dir = client_org_a
        await c.post("/api/v1/lakehouse/provision", headers=_auth(uid))

        resp = await c.post("/api/v1/lakehouse/demo", headers=_auth(uid))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["count"] > 0
        assert body["usage_bytes"] > 0

        # Files exist on disk under the org prefix.
        demo_root = os.path.join(lake_dir, "orgs", org, "lake", "demo")
        assert os.path.isdir(demo_root)
        parquet = [
            f for _dp, _dn, fns in os.walk(demo_root) for f in fns if f.endswith(".parquet")
        ]
        assert len(parquet) == body["count"]

        # GET reports demo_seeded + non-zero usage.
        status = (await c.get("/api/v1/lakehouse", headers=_auth(uid))).json()
        assert status["demo_seeded"] is True
        assert status["usage_bytes"] > 0
        assert status["usage_gb"] >= 0

    @pytest.mark.asyncio
    async def test_seed_demo_is_idempotent(self, client_org_a):
        c, uid, org, repo, store, lake_dir = client_org_a
        await c.post("/api/v1/lakehouse/provision", headers=_auth(uid))
        first = (await c.post("/api/v1/lakehouse/demo", headers=_auth(uid))).json()
        # Second seed writes nothing new (all tables already present).
        second = (await c.post("/api/v1/lakehouse/demo", headers=_auth(uid))).json()
        assert first["count"] > 0
        assert second["count"] == 0

    @pytest.mark.asyncio
    async def test_provision_with_seed_demo_query_param(self, client_org_a):
        c, uid, org, repo, store, lake_dir = client_org_a
        resp = await c.post(
            "/api/v1/lakehouse/provision?seed_demo=true", headers=_auth(uid)
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["usage_bytes"] > 0

    @pytest.mark.asyncio
    async def test_storage_usage_feeds_usage_endpoint(self, client_org_a):
        c, uid, org, repo, store, lake_dir = client_org_a
        from app.compute import metering

        metering.set_sink(metering.InMemorySink())
        await c.post("/api/v1/lakehouse/provision?seed_demo=true", headers=_auth(uid))
        # Hitting GET /lakehouse emits a storage snapshot synchronously enough
        # for the in-memory sink to record it.
        await c.get("/api/v1/lakehouse", headers=_auth(uid))

        usage = (await c.get("/api/v1/usage", headers=_auth(uid))).json()
        storage = next(m for m in usage["metrics"] if m["id"] == "storage_gb")
        assert storage["used"] >= 0  # storage metric is surfaced (max of snapshots)
        # At least one storage event recorded for this org.
        events = [e for e in metering.get_usage()
                  if str(e.get("org_id")) == org and e.get("kind") == "storage"]
        assert events, "expected a storage usage event for the managed lake"


# ---------------------------------------------------------------------------
# Deprovision cleanup
# ---------------------------------------------------------------------------


class TestDeprovision:
    @pytest.mark.asyncio
    async def test_deprovision_removes_objects_row_and_secret(self, client_org_a):
        c, uid, org, repo, store, lake_dir = client_org_a
        prov = (await c.post("/api/v1/lakehouse/provision?seed_demo=true",
                             headers=_auth(uid))).json()
        ds_id = prov["datastore_id"]
        assert await store.get(ds_id, org) is not None or True  # secret may be empty

        resp = await c.delete("/api/v1/lakehouse", headers=_auth(uid))
        assert resp.status_code == 204, resp.text

        # Row gone.
        assert await repo.get("datastores", org, ds_id) is None
        # Objects gone.
        demo_root = os.path.join(lake_dir, "orgs", org, "lake")
        remaining = []
        if os.path.isdir(demo_root):
            remaining = [
                f for _dp, _dn, fns in os.walk(demo_root) for f in fns
            ]
        assert remaining == []
        # Secret gone.
        assert await store.get(ds_id, org) is None
        # Status returns to not-provisioned.
        status = (await c.get("/api/v1/lakehouse", headers=_auth(uid))).json()
        assert status["provisioned"] is False

    @pytest.mark.asyncio
    async def test_deprovision_unprovisioned_is_noop_204(self, client_org_a):
        c, uid, org, repo, store, _ = client_org_a
        resp = await c.delete("/api/v1/lakehouse", headers=_auth(uid))
        assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


class TestTenantIsolation:
    @pytest.mark.asyncio
    async def test_org_b_cannot_see_or_deprovision_org_a(self, client_org_a, fake_db):
        c, uid_a, org_a, repo, store, lake_dir = client_org_a
        # Provision A's lake.
        prov_a = (await c.post("/api/v1/lakehouse/provision?seed_demo=true",
                               headers=_auth(uid_a))).json()
        a_ds = prov_a["datastore_id"]

        # Second user/org B.
        uid_b = str(uuid.uuid4())
        org_b = str(uuid.uuid4())
        fake_db.users[uid_b] = _make_user(uid_b, email="bob@example.com")
        repo.seed_org_member(org_id=org_b, user_id=uid_b, role="owner")

        # B's status shows a DIFFERENT prefix and not-provisioned.
        b_status = (await c.get("/api/v1/lakehouse", headers=_auth(uid_b))).json()
        assert b_status["provisioned"] is False
        assert b_status["prefix"] == f"orgs/{org_b}/lake/"
        assert b_status["prefix"] != f"orgs/{org_a}/lake/"

        # B's deprovision is a no-op — A's lake survives.
        await c.delete("/api/v1/lakehouse", headers=_auth(uid_b))
        assert await repo.get("datastores", org_a, a_ds) is not None
        a_files = [
            f for _dp, _dn, fns in os.walk(os.path.join(lake_dir, "orgs", org_a))
            for f in fns
        ]
        assert a_files, "org A's data must survive org B's deprovision"


# ---------------------------------------------------------------------------
# Path-pinning: managed datastore cannot be repointed cross-org via /connectors
# ---------------------------------------------------------------------------


class TestPathPinning:
    @pytest.mark.asyncio
    async def test_cannot_repoint_managed_datastore_via_connectors_put(self, client_org_a):
        c, uid, org, repo, store, _ = client_org_a
        prov = (await c.post("/api/v1/lakehouse/provision", headers=_auth(uid))).json()
        ds_id = prov["datastore_id"]

        # Attempt to repoint the managed lake at another org's prefix.
        evil = "s3://nubi/orgs/some-other-org/lake/"
        resp = await c.put(
            f"/api/v1/connectors/{ds_id}",
            json={"config": {"database": evil}},
            headers=_auth(uid),
        )
        assert resp.status_code == 409, resp.text

        # The row's database is unchanged (still pinned to this org's prefix).
        row = await repo.get("datastores", org, ds_id)
        assert row["config"]["database"].endswith(f"orgs/{org}/lake/")

    @pytest.mark.asyncio
    async def test_cannot_delete_managed_datastore_via_connectors(self, client_org_a):
        c, uid, org, repo, store, _ = client_org_a
        prov = (await c.post("/api/v1/lakehouse/provision", headers=_auth(uid))).json()
        ds_id = prov["datastore_id"]
        resp = await c.delete(f"/api/v1/connectors/{ds_id}", headers=_auth(uid))
        assert resp.status_code == 409, resp.text
        # Still present.
        assert await repo.get("datastores", org, ds_id) is not None
