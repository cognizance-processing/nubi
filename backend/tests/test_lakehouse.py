"""Tests for the managed-lakehouse layer (multi-instance, normal-connector model).

A managed lakehouse is now just a normal connector: existence == provisioned,
``DELETE /connectors/{id}`` == deprovisioned, multiple coexist per org, each with
its OWN prefix ``orgs/<org>/lake/<datastore_id>/``.

Covers:
  - provision creates a NEW connector each call (multiple coexist, distinct prefixes)
  - managed rows appear in the NORMAL connectors list with usage_bytes/usage_gb
  - GET /lakehouse returns the list of managed lakehouses (with usage)
  - demo seeding into a specific managed lake + usage accounting
  - DELETE /connectors/{id} deprovisions (objects + row + secret gone), tenant-scoped
  - PUT still pins the storage path but ALLOWS renaming
  - cross-org isolation (org B cannot see / delete org A's lake)
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


def _lake_files(lake_dir: str, org: str) -> list[str]:
    root = os.path.join(lake_dir, "orgs", org, "lake")
    if not os.path.isdir(root):
        return []
    return [f for _dp, _dn, fns in os.walk(root) for f in fns]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _small_demo(monkeypatch):
    """Shrink the demo dataset to a single small dataset for fast tests."""
    import seed_data.generators as gens

    full = gens.DATASET_TABLES
    smallest = min(full, key=lambda k: len(full[k]))
    monkeypatch.setattr(gens, "DATASET_TABLES", {smallest: full[smallest]})
    yield


@pytest_asyncio.fixture
async def lake_dir(tmp_path, monkeypatch):
    """Point central storage at a local tmp dir (file backend)."""
    d = tmp_path / "managed-lake"
    d.mkdir()
    monkeypatch.setenv("NUBI_MANAGED_LAKE_DIR", str(d))
    for var in ("S3_ACCESS_KEY", "AWS_ACCESS_KEY_ID"):
        monkeypatch.delenv(var, raising=False)
    yield str(d)


@pytest_asyncio.fixture
async def lake_app(app, lake_dir, monkeypatch):
    """App with InMemoryRepo + InMemorySecretStore + local central storage."""
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
                assert body["lakehouses"] == []
                assert "detail" in body

                # Provision must 409 when unconfigured.
                resp2 = await c.post("/api/v1/lakehouse/provision", headers=_auth(uid))
                assert resp2.status_code == 409, resp2.text
        finally:
            set_repo(None)
            set_secret_store_for_tests(None)


# ---------------------------------------------------------------------------
# Provision — multi-instance
# ---------------------------------------------------------------------------


class TestProvision:
    @pytest.mark.asyncio
    async def test_list_before_provision_is_empty(self, client_org_a):
        c, uid, org, repo, store, _ = client_org_a
        resp = await c.get("/api/v1/lakehouse", headers=_auth(uid))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["configured"] is True
        assert body["lakehouses"] == []

    @pytest.mark.asyncio
    async def test_provision_creates_pinned_managed_connector(self, client_org_a):
        c, uid, org, repo, store, _ = client_org_a
        resp = await c.post(
            "/api/v1/lakehouse/provision", json={"name": "Analytics lake"},
            headers=_auth(uid),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        ds_id = body["id"]
        assert ds_id
        assert body["name"] == "Analytics lake"
        assert body["config"]["managed_lake"] is True
        # Prefix pinned to THIS datastore's own id.
        assert body["config"]["database"].endswith(f"orgs/{org}/lake/{ds_id}/")
        assert "usage_bytes" in body and "usage_gb" in body

        # The row is server-pinned + a secret is stored.
        row = await repo.get("datastores", org, ds_id)
        assert row["config"]["managed_lake"] is True
        assert row["config"]["managed_prefix"] == f"orgs/{org}/lake/{ds_id}/"

    @pytest.mark.asyncio
    async def test_provision_creates_a_new_connector_each_call(self, client_org_a):
        c, uid, org, repo, store, _ = client_org_a
        r1 = await c.post("/api/v1/lakehouse/provision", headers=_auth(uid))
        r2 = await c.post("/api/v1/lakehouse/provision", headers=_auth(uid))
        id1, id2 = r1.json()["id"], r2.json()["id"]
        assert id1 != id2  # NOT a singleton — each call is a fresh connector.

        # Two managed rows coexist with DISTINCT prefixes.
        rows = await repo.list("datastores", org)
        managed = [r for r in rows if r["config"].get("managed_lake")]
        assert len(managed) == 2
        prefixes = {r["config"]["managed_prefix"] for r in managed}
        assert prefixes == {
            f"orgs/{org}/lake/{id1}/", f"orgs/{org}/lake/{id2}/"
        }

        # GET /lakehouse lists both.
        listed = (await c.get("/api/v1/lakehouse", headers=_auth(uid))).json()
        assert {lh["id"] for lh in listed["lakehouses"]} == {id1, id2}

    @pytest.mark.asyncio
    async def test_managed_rows_appear_in_connectors_list_with_usage(self, client_org_a):
        c, uid, org, repo, store, _ = client_org_a
        prov = (await c.post("/api/v1/lakehouse/provision?seed_demo=true",
                             headers=_auth(uid))).json()
        ds_id = prov["id"]

        resp = await c.get("/api/v1/connectors", headers=_auth(uid))
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        managed = [r for r in rows if r["config"].get("managed_lake") is True]
        assert len(managed) == 1
        row = managed[0]
        assert row["id"] == ds_id
        # Surfaces usage on the card.
        assert "usage_bytes" in row and "usage_gb" in row
        assert row["usage_bytes"] > 0
        # No secret material leaks into the list response.
        assert "aws_secret_access_key" not in row["config"]

    @pytest.mark.asyncio
    async def test_get_connector_includes_usage_for_managed_row(self, client_org_a):
        c, uid, org, repo, store, _ = client_org_a
        prov = (await c.post("/api/v1/lakehouse/provision?seed_demo=true",
                             headers=_auth(uid))).json()
        ds_id = prov["id"]
        row = (await c.get(f"/api/v1/connectors/{ds_id}", headers=_auth(uid))).json()
        assert row["config"]["managed_lake"] is True
        assert row["usage_bytes"] > 0


# ---------------------------------------------------------------------------
# Demo seeding + usage accounting
# ---------------------------------------------------------------------------


class TestDemoAndUsage:
    @pytest.mark.asyncio
    async def test_provision_with_seed_demo_writes_parquet_and_usage(self, client_org_a):
        c, uid, org, repo, store, lake_dir = client_org_a
        prov = (await c.post("/api/v1/lakehouse/provision?seed_demo=true",
                             headers=_auth(uid))).json()
        ds_id = prov["id"]
        assert prov["usage_bytes"] > 0

        # Files exist on disk under THIS datastore's prefix.
        demo_root = os.path.join(lake_dir, "orgs", org, "lake", ds_id, "demo")
        assert os.path.isdir(demo_root)
        parquet = [
            f for _dp, _dn, fns in os.walk(demo_root) for f in fns if f.endswith(".parquet")
        ]
        assert parquet

    @pytest.mark.asyncio
    async def test_storage_usage_feeds_usage_endpoint(self, client_org_a):
        c, uid, org, repo, store, lake_dir = client_org_a
        from app.compute import metering

        metering.set_sink(metering.InMemorySink())
        await c.post("/api/v1/lakehouse/provision?seed_demo=true", headers=_auth(uid))
        # Hitting GET /lakehouse emits a storage snapshot.
        await c.get("/api/v1/lakehouse", headers=_auth(uid))

        usage = (await c.get("/api/v1/usage", headers=_auth(uid))).json()
        storage = next(m for m in usage["metrics"] if m["id"] == "storage_gb")
        assert storage["used"] >= 0
        events = [e for e in metering.get_usage()
                  if str(e.get("org_id")) == org and e.get("kind") == "storage"]
        assert events, "expected a storage usage event for the managed lake"


# ---------------------------------------------------------------------------
# Delete == deprovision (via DELETE /connectors/{id})
# ---------------------------------------------------------------------------


class TestDeprovision:
    @pytest.mark.asyncio
    async def test_delete_connector_deprovisions_objects_row_and_secret(self, client_org_a):
        c, uid, org, repo, store, lake_dir = client_org_a
        prov = (await c.post("/api/v1/lakehouse/provision?seed_demo=true",
                             headers=_auth(uid))).json()
        ds_id = prov["id"]
        # Objects + secret present.
        assert _lake_files(lake_dir, org)
        assert await store.get(ds_id, org) is not None

        resp = await c.delete(f"/api/v1/connectors/{ds_id}", headers=_auth(uid))
        assert resp.status_code == 204, resp.text

        # Row gone.
        assert await repo.get("datastores", org, ds_id) is None
        # Objects gone.
        assert _lake_files(lake_dir, org) == []
        # Secret gone.
        assert await store.get(ds_id, org) is None
        # No longer listed.
        listed = (await c.get("/api/v1/lakehouse", headers=_auth(uid))).json()
        assert listed["lakehouses"] == []

    @pytest.mark.asyncio
    async def test_delete_one_lake_leaves_the_other(self, client_org_a):
        c, uid, org, repo, store, lake_dir = client_org_a
        a = (await c.post("/api/v1/lakehouse/provision?seed_demo=true",
                          headers=_auth(uid))).json()["id"]
        b = (await c.post("/api/v1/lakehouse/provision?seed_demo=true",
                          headers=_auth(uid))).json()["id"]

        resp = await c.delete(f"/api/v1/connectors/{a}", headers=_auth(uid))
        assert resp.status_code == 204

        def _files_under(ds_id: str) -> list[str]:
            root = os.path.join(lake_dir, "orgs", org, "lake", ds_id)
            if not os.path.isdir(root):
                return []
            return [f for _dp, _dn, fns in os.walk(root) for f in fns]

        # a's objects gone, b's survive (the empty dir may linger on the file
        # backend — assert on actual files, not the directory).
        assert _files_under(a) == []
        assert _files_under(b)
        assert await repo.get("datastores", org, a) is None
        assert await repo.get("datastores", org, b) is not None


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


class TestTenantIsolation:
    @pytest.mark.asyncio
    async def test_org_b_cannot_see_or_delete_org_a(self, client_org_a, fake_db):
        c, uid_a, org_a, repo, store, lake_dir = client_org_a
        prov_a = (await c.post("/api/v1/lakehouse/provision?seed_demo=true",
                               headers=_auth(uid_a))).json()
        a_ds = prov_a["id"]

        # Second user/org B.
        uid_b = str(uuid.uuid4())
        org_b = str(uuid.uuid4())
        fake_db.users[uid_b] = _make_user(uid_b, email="bob@example.com")
        repo.seed_org_member(org_id=org_b, user_id=uid_b, role="owner")

        # B's list is empty (does not see A's lake).
        b_listed = (await c.get("/api/v1/lakehouse", headers=_auth(uid_b))).json()
        assert b_listed["lakehouses"] == []
        # B cannot see A's managed connector.
        assert (await c.get(f"/api/v1/connectors/{a_ds}", headers=_auth(uid_b))).status_code == 404

        # B's delete of A's connector is a 404 — A's lake survives.
        resp = await c.delete(f"/api/v1/connectors/{a_ds}", headers=_auth(uid_b))
        assert resp.status_code == 404, resp.text
        assert await repo.get("datastores", org_a, a_ds) is not None
        assert _lake_files(lake_dir, org_a), "org A's data must survive org B's delete"


# ---------------------------------------------------------------------------
# Path-pinning + rename: managed datastore cannot be repointed via /connectors
# ---------------------------------------------------------------------------


class TestPathPinningAndRename:
    @pytest.mark.asyncio
    async def test_cannot_repoint_managed_datastore_via_connectors_put(self, client_org_a):
        c, uid, org, repo, store, _ = client_org_a
        prov = (await c.post("/api/v1/lakehouse/provision", headers=_auth(uid))).json()
        ds_id = prov["id"]

        evil = "s3://nubi/orgs/some-other-org/lake/"
        resp = await c.put(
            f"/api/v1/connectors/{ds_id}",
            json={"config": {"database": evil}},
            headers=_auth(uid),
        )
        assert resp.status_code == 409, resp.text

        # The row's database is unchanged (still pinned to its own prefix).
        row = await repo.get("datastores", org, ds_id)
        assert row["config"]["database"].endswith(f"orgs/{org}/lake/{ds_id}/")

    @pytest.mark.asyncio
    async def test_can_rename_managed_datastore_via_connectors_put(self, client_org_a):
        c, uid, org, repo, store, _ = client_org_a
        prov = (await c.post("/api/v1/lakehouse/provision", headers=_auth(uid))).json()
        ds_id = prov["id"]

        resp = await c.put(
            f"/api/v1/connectors/{ds_id}",
            json={"name": "Renamed lake"},
            headers=_auth(uid),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["name"] == "Renamed lake"
        # Storage path untouched.
        row = await repo.get("datastores", org, ds_id)
        assert row["config"]["database"].endswith(f"orgs/{org}/lake/{ds_id}/")
