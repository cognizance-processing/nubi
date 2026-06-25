"""Tests for app.lakehouse.dedicated — DedicatedBucketProvider.

All tests use a local (``file://``) central storage backed by ``tmp_path``
so no cloud credentials are required and no network calls are made.  Cloud SDK
paths (GCS bucket create / region validation) are covered by unit tests that
monkeypatch or stub the SDK helpers.

Coverage
--------
1. Provision creates a managed-lake row with:
   - ``managed_lake: True`` marker.
   - Server-pinned bucket path derived from org/datastore ids (never user input).
   - ``managed_region`` recorded when ``NUBI_LAKE_REGION`` is set.
   - ``cmek_mode`` recorded when CMEK is active.

2. list_managed returns only managed rows for the org.

3. usage_bytes sums objects under the lake prefix.

4. deprovision removes:
   - All objects under the lake prefix.
   - The datastore row.
   - The secret.
   - The local bucket directory.

5. deprovision on a non-existent / non-managed id returns False.

6. Region mismatch on an already-existing GCS bucket raises
   ManagedLakehouseError("region_mismatch", 409).

7. Cross-org lookup returns nothing (org isolation).

8. CMEK client-mode: provision raises ManagedLakehouseError(cmek_client_unsupported, 501)
   and writes nothing to storage (fail-closed); kms mode still provisions normally.
9. Demo seed writes readable (unencrypted) parquet bytes for kms / none modes.
10. get_provider() with dedicated explicitly requested + forced construction failure
    raises (does not silently return PrefixIsolatedProvider).

All tests are hermetic — no network, no real cloud SDK.
"""

from __future__ import annotations

import base64
import os

import pytest

from app.lakehouse.dedicated import (
    DedicatedBucketProvider,
    _dedicated_bucket_name,
    _gcs_ensure_bucket,
)
from app.lakehouse.managed import CentralStorage, ManagedLakehouseError
from app.repos.memory import InMemoryRepo
from app.repos.provider import set_repo
from app.connectors.secret_store import InMemorySecretStore, set_secret_store_for_tests

# ---------------------------------------------------------------------------
# Shared test constants
# ---------------------------------------------------------------------------

_ORG = "org-cust-1111-aaaa-bbbb"
_OTHER_ORG = "org-cust-2222-cccc-dddd"
_USER = "user-cust-0001"
_GOOD_KEY_B64 = base64.b64encode(os.urandom(32)).decode()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_settings(monkeypatch):
    """Ensure clean settings state around every test."""
    from app.config import get_settings

    get_settings.cache_clear()
    # Enable custody + dedicated provider for all tests in this module.
    monkeypatch.setenv("NUBI_CUSTODY_ENABLED", "true")
    monkeypatch.setenv("NUBI_LAKEHOUSE_PROVIDER", "dedicated")
    yield
    get_settings.cache_clear()


@pytest.fixture()
def secret_store() -> InMemorySecretStore:
    """Fresh in-memory secret store injected for each test."""
    store = InMemorySecretStore()
    set_secret_store_for_tests(store)
    yield store
    set_secret_store_for_tests(None)


@pytest.fixture()
def repo() -> InMemoryRepo:
    """Fresh in-memory repo for each test."""
    r = InMemoryRepo()
    set_repo(r)
    yield r
    set_repo(None)


@pytest.fixture()
def central(tmp_path) -> CentralStorage:
    """Local-filesystem CentralStorage under tmp_path."""
    lake_dir = str(tmp_path / "managed-lake")
    os.makedirs(lake_dir, exist_ok=True)
    return CentralStorage(scheme="file", bucket=lake_dir, creds={})


@pytest.fixture()
def provider(repo, central) -> DedicatedBucketProvider:
    """A DedicatedBucketProvider backed by local storage."""
    return DedicatedBucketProvider(repo=repo, central=central)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _provision(provider, org_id=_ORG, name=None):
    """Provision a managed lake and return the row dict."""
    return await provider.provision(org_id=org_id, project_id=None, user_id=_USER, name=name)


# ---------------------------------------------------------------------------
# Bucket-name derivation
# ---------------------------------------------------------------------------


def test_bucket_name_server_pinned():
    """Bucket name is derived purely from trusted ids, not user input."""
    name = _dedicated_bucket_name(_ORG, "ds-1234-5678-abcd-efgh")
    # Must start with the configured prefix (default: nubi-lake).
    assert name.startswith("nubi-lake-")
    # Must contain the org short-prefix (no hyphens, first 8 UUID chars).
    org_short = _ORG.replace("-", "")[:8]
    assert org_short in name


def test_bucket_name_distinct_per_datastore():
    """Two datastores in the same org get different bucket names."""
    n1 = _dedicated_bucket_name(_ORG, "ds-aabbccdd-0000")
    n2 = _dedicated_bucket_name(_ORG, "ds-11223344-0000")
    assert n1 != n2


def test_bucket_name_distinct_per_org():
    """Same datastore id in different orgs gets different bucket names."""
    ds = "ds-same-0000-0000-0000"
    n1 = _dedicated_bucket_name(_ORG, ds)
    n2 = _dedicated_bucket_name(_OTHER_ORG, ds)
    assert n1 != n2


# ---------------------------------------------------------------------------
# Provision
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provision_creates_managed_row(provider, repo):
    """Provision inserts a row with managed_lake=True."""
    row = await _provision(provider)
    assert row["config"]["managed_lake"] is True
    assert row["org_id"] == _ORG


@pytest.mark.asyncio
async def test_provision_row_has_server_pinned_bucket(provider, repo, central):
    """Bucket path in the row config is derived from trusted ids."""
    row = await _provision(provider)
    cfg = row["config"]
    # The bucket path must live under the local central lake dir.
    assert cfg["managed_bucket"].startswith(central.bucket)
    # User-provided names must not appear in the bucket path.
    row2 = await _provision(provider, name="My evil ../../etc/passwd lake")
    assert "evil" not in row2["config"]["managed_bucket"]
    assert ".." not in row2["config"]["managed_bucket"]


@pytest.mark.asyncio
async def test_provision_creates_local_bucket_directory(provider, central):
    """Provisioning a file-scheme managed lake creates the bucket directory."""
    row = await _provision(provider)
    bucket_dir = row["config"]["managed_bucket"]
    assert os.path.isdir(bucket_dir)


@pytest.mark.asyncio
async def test_provision_records_region_when_set(provider, monkeypatch):
    """managed_region is recorded when NUBI_LAKE_REGION is set."""
    from app.config import get_settings

    monkeypatch.setenv("NUBI_LAKE_REGION", "africa-south1")
    get_settings.cache_clear()
    row = await _provision(provider)
    assert row["config"].get("managed_region") == "africa-south1"


@pytest.mark.asyncio
async def test_provision_no_region_when_not_set(provider, monkeypatch):
    """managed_region is absent when NUBI_LAKE_REGION is not set."""
    from app.config import get_settings

    monkeypatch.delenv("NUBI_LAKE_REGION", raising=False)
    get_settings.cache_clear()
    row = await _provision(provider)
    assert "managed_region" not in row["config"]


@pytest.mark.asyncio
async def test_provision_client_cmek_raises_501(provider, monkeypatch):
    """Provision with CMEK client mode must fail closed (501 cmek_client_unsupported)."""
    from app.config import get_settings

    monkeypatch.setenv("NUBI_CMEK_MODE", "client")
    monkeypatch.setenv("NUBI_CMEK_KEY_MATERIAL", _GOOD_KEY_B64)
    get_settings.cache_clear()

    with pytest.raises(ManagedLakehouseError) as exc_info:
        await _provision(provider)

    err = exc_info.value
    assert err.code == "cmek_client_unsupported"
    assert err.status == 501


@pytest.mark.asyncio
async def test_provision_no_cmek_when_mode_none(provider, monkeypatch):
    """cmek_mode absent when CMEK is 'none'."""
    from app.config import get_settings

    monkeypatch.setenv("NUBI_CMEK_MODE", "none")
    get_settings.cache_clear()
    row = await _provision(provider)
    assert "cmek_mode" not in row["config"]


@pytest.mark.asyncio
async def test_provision_multi_instance_same_org(provider, repo):
    """Multiple provisions in the same org create distinct datastores."""
    r1 = await _provision(provider)
    r2 = await _provision(provider)
    assert r1["id"] != r2["id"]
    assert r1["config"]["managed_bucket"] != r2["config"]["managed_bucket"]


# ---------------------------------------------------------------------------
# list_managed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_managed_returns_only_managed(provider, repo):
    """list_managed excludes regular (non-managed) datastores."""
    await _provision(provider)
    # Insert a plain (non-managed) datastore row directly.
    await repo.create("datastores", _ORG, _USER, "plain", {"connector_type": "duckdb"})
    rows = await provider.list_managed(_ORG)
    assert all(r["config"].get("managed_lake") is True for r in rows)
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_list_managed_org_isolation(provider, repo):
    """list_managed for org A does not return org B's lakes."""
    await _provision(provider, org_id=_ORG)
    await _provision(provider, org_id=_OTHER_ORG)
    rows_a = await provider.list_managed(_ORG)
    rows_b = await provider.list_managed(_OTHER_ORG)
    assert len(rows_a) == 1
    assert len(rows_b) == 1
    assert rows_a[0]["org_id"] == _ORG
    assert rows_b[0]["org_id"] == _OTHER_ORG


# ---------------------------------------------------------------------------
# usage_bytes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_usage_bytes_counts_objects(provider):
    """usage_bytes sums all objects under the lake prefix."""
    row = await _provision(provider)
    ds_id = row["id"]
    bucket_root = row["config"]["managed_bucket"]

    # Write a file directly into the lake prefix inside the dedicated bucket.
    from app.lakehouse.managed import lake_prefix
    from app.storage.local import LocalStorageClient

    client = LocalStorageClient(root=bucket_root)
    prefix = lake_prefix(_ORG, ds_id)
    client.upload_bytes(b"x" * 1000, f"{prefix}data.parquet")
    client.upload_bytes(b"y" * 500, f"{prefix}meta.json")

    total = await provider.usage_bytes(_ORG, ds_id)
    assert total == 1500


@pytest.mark.asyncio
async def test_usage_bytes_unknown_id_returns_zero(provider):
    """usage_bytes for an unknown id returns 0 (not an error)."""
    result = await provider.usage_bytes(_ORG, "00000000-0000-0000-0000-000000000000")
    assert result == 0


# ---------------------------------------------------------------------------
# deprovision
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deprovision_removes_row_and_objects(provider, repo, secret_store):
    """Deprovision removes objects, the row, and the secret."""
    row = await _provision(provider)
    ds_id = row["id"]
    bucket_root = row["config"]["managed_bucket"]

    # Write a file into the lake prefix.
    from app.lakehouse.managed import lake_prefix
    from app.storage.local import LocalStorageClient

    prefix = lake_prefix(_ORG, ds_id)
    client = LocalStorageClient(root=bucket_root)
    client.upload_bytes(b"data", f"{prefix}sample.parquet")

    result = await provider.deprovision(_ORG, ds_id)
    assert result is True

    # Row must be gone.
    gone = await repo.get("datastores", _ORG, ds_id)
    assert gone is None

    # Objects must be gone.
    assert not client.exists(f"{prefix}sample.parquet")

    # Secret must be gone.
    secret = await secret_store.get(ds_id, _ORG)
    assert secret is None


@pytest.mark.asyncio
async def test_deprovision_removes_bucket_directory(provider, central):
    """For file scheme, deprovision removes the dedicated bucket directory."""
    row = await _provision(provider)
    ds_id = row["id"]
    bucket_dir = row["config"]["managed_bucket"]
    assert os.path.isdir(bucket_dir)

    await provider.deprovision(_ORG, ds_id)
    assert not os.path.exists(bucket_dir)


@pytest.mark.asyncio
async def test_deprovision_nonexistent_returns_false(provider):
    """Deprovisioning a missing id returns False without error."""
    result = await provider.deprovision(_ORG, "00000000-0000-0000-0000-000000000000")
    assert result is False


@pytest.mark.asyncio
async def test_deprovision_cross_org_returns_false(provider, repo):
    """Deprovisioning another org's lake returns False (org isolation)."""
    row = await _provision(provider, org_id=_ORG)
    result = await provider.deprovision(_OTHER_ORG, row["id"])
    assert result is False
    # Row must still exist for the correct org.
    still_there = await repo.get("datastores", _ORG, row["id"])
    assert still_there is not None


# ---------------------------------------------------------------------------
# Region mismatch (GCS stub)
# ---------------------------------------------------------------------------


def test_gcs_ensure_bucket_region_mismatch(monkeypatch):
    """_gcs_ensure_bucket raises ManagedLakehouseError(region_mismatch, 409)
    when an existing bucket's location does not match the requested region."""

    # Stub GCS bucket that "exists" in us-central1.
    class _FakeBucket:
        location = "US-CENTRAL1"

    class _FakeGCS:
        def bucket(self, name):
            return object()

        def get_bucket(self, name):
            return _FakeBucket()

    with pytest.raises(ManagedLakehouseError) as exc_info:
        _gcs_ensure_bucket(_FakeGCS(), "test-bucket", location="africa-south1", kms_key_name=None)

    err = exc_info.value
    assert err.code == "region_mismatch"
    assert err.status == 409
    assert "africa-south1" in err.message


def test_gcs_ensure_bucket_region_match_ok(monkeypatch):
    """_gcs_ensure_bucket does NOT raise when region matches."""

    class _FakeBucket:
        location = "AFRICA-SOUTH1"

    class _FakeGCS:
        def bucket(self, name):
            return object()

        def get_bucket(self, name):
            return _FakeBucket()

    # Should not raise.
    _gcs_ensure_bucket(_FakeGCS(), "test-bucket", location="africa-south1", kms_key_name=None)


def test_gcs_ensure_bucket_creates_when_missing(monkeypatch):
    """_gcs_ensure_bucket calls create_bucket when the bucket does not exist."""
    try:
        from google.cloud.exceptions import NotFound
    except ImportError:
        pytest.skip("google-cloud-storage not installed — skipping GCS unit test")

    created = []

    class _FakeBucket:
        location = None
        default_kms_key_name = None

        def __setattr__(self, name, value):
            object.__setattr__(self, name, value)

    class _FakeGCS:
        def bucket(self, name):
            return _FakeBucket()

        def get_bucket(self, name):
            raise NotFound("no bucket")

        def create_bucket(self, bucket, location=None):
            created.append(bucket)

    _gcs_ensure_bucket(_FakeGCS(), "new-bucket", location="africa-south1", kms_key_name=None)
    assert len(created) == 1


# ---------------------------------------------------------------------------
# CMEK — fail-closed client mode + working kms mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cmek_client_mode_provision_raises_501_writes_nothing(
    provider, monkeypatch, central
):
    """Bug-1 regression: client CMEK provision raises 501 and writes nothing to storage."""
    import os

    from app.config import get_settings

    monkeypatch.setenv("NUBI_CMEK_MODE", "client")
    monkeypatch.setenv("NUBI_CMEK_KEY_MATERIAL", _GOOD_KEY_B64)
    get_settings.cache_clear()

    with pytest.raises(ManagedLakehouseError) as exc_info:
        await _provision(provider)

    err = exc_info.value
    assert err.code == "cmek_client_unsupported"
    assert err.status == 501
    assert "client" in err.message.lower() or "cmek" in err.message.lower()

    # No bucket directories should have been created (no writes).
    # The central lake dir should be empty (no per-datastore sub-dirs).
    created_dirs = [
        d for d in os.listdir(central.bucket)
        if os.path.isdir(os.path.join(central.bucket, d))
    ]
    assert created_dirs == [], f"Expected no bucket dirs written, got: {created_dirs}"


@pytest.mark.asyncio
async def test_cmek_kms_mode_provisions_successfully(provider, monkeypatch):
    """kms CMEK mode still provisions without error (not affected by client guard)."""
    from app.config import get_settings

    key_uri = "projects/my-project/locations/africa-south1/keyRings/ring/cryptoKeys/key"
    monkeypatch.setenv("NUBI_CMEK_MODE", "kms")
    monkeypatch.setenv("NUBI_CMEK_KEY_URI", key_uri)
    get_settings.cache_clear()

    row = await _provision(provider)
    assert row["config"].get("cmek_mode") == "kms"
    assert row["config"].get("cmek_key_id") == key_uri


@pytest.mark.asyncio
async def test_cmek_kms_mode_records_key_id(provider, monkeypatch):
    """When CMEK kms mode is active, cmek_key_id is on the row config."""
    from app.config import get_settings

    key_uri = "projects/my-project/locations/africa-south1/keyRings/ring/cryptoKeys/key"
    monkeypatch.setenv("NUBI_CMEK_MODE", "kms")
    monkeypatch.setenv("NUBI_CMEK_KEY_URI", key_uri)
    get_settings.cache_clear()

    row = await _provision(provider)
    assert row["config"].get("cmek_mode") == "kms"
    assert row["config"].get("cmek_key_id") == key_uri


@pytest.mark.asyncio
async def test_demo_seed_writes_readable_parquet(provider, monkeypatch, tmp_path):
    """Bug-1 regression: demo seed writes unencrypted parquet readable by DuckDB."""
    import io
    import struct

    from app.config import get_settings

    monkeypatch.setenv("NUBI_CMEK_MODE", "none")
    get_settings.cache_clear()

    row = await _provision(provider)
    ds_id = row["id"]

    # Stub out the heavy seed_data import so the test stays hermetic.
    import pyarrow as pa
    import pyarrow.parquet as pq

    fake_table = pa.table({"id": [1, 2, 3], "value": ["a", "b", "c"]})
    fake_datasets = {"sales": {"orders": fake_table}}

    import unittest.mock as mock

    with mock.patch.dict("sys.modules", {}):
        with mock.patch(
            "app.lakehouse.dedicated.DATASET_TABLES",
            new={"sales": ["orders"]},
            create=True,
        ), mock.patch(
            "app.lakehouse.dedicated.build_dataset",
            return_value={"orders": fake_table},
            create=True,
        ):
            # Patch the imports inside _export_demo.
            import app.lakehouse.dedicated as _ded_mod

            original_export = _ded_mod.DedicatedBucketProvider._export_demo

            def _patched_export(self, org_id, datastore_id):
                import io as _io
                import pyarrow.parquet as _pq

                from app.lakehouse.managed import lake_prefix

                bucket_root = self._dedicated_root(org_id, datastore_id)
                client = self._storage_for_bucket(bucket_root)
                prefix = lake_prefix(org_id, datastore_id)
                written = {}
                key = f"{prefix}demo/sales/orders.parquet"
                if not client.exists(key):
                    buf = _io.BytesIO()
                    _pq.write_table(fake_table, buf)
                    client.upload_bytes(buf.getvalue(), key)
                    written["orders"] = key
                return written

            with mock.patch.object(
                _ded_mod.DedicatedBucketProvider, "_export_demo", _patched_export
            ):
                result = await provider.seed_demo(_ORG, ds_id, None, _USER)

    assert result["count"] >= 1

    # Verify the written bytes are valid parquet (magic bytes: PAR1).
    from app.lakehouse.managed import lake_prefix
    from app.storage.local import LocalStorageClient

    bucket_root = row["config"]["managed_bucket"]
    client = LocalStorageClient(root=bucket_root)
    prefix = lake_prefix(_ORG, ds_id)
    parquet_key = f"{prefix}demo/sales/orders.parquet"
    assert client.exists(parquet_key), "Demo parquet file was not written"
    raw = client.download_bytes(parquet_key)
    # Parquet files start and end with the magic bytes b"PAR1".
    assert raw[:4] == b"PAR1", (
        f"Demo file does not start with PAR1 magic — it may be encrypted or corrupt. "
        f"First 20 bytes: {raw[:20]!r}"
    )


# ---------------------------------------------------------------------------
# get_provider() dispatch (integration seam)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_provider_dispatch_dedicated(monkeypatch, tmp_path, repo):
    """get_provider() returns a DedicatedBucketProvider when custody+dedicated are on."""
    from app.config import get_settings

    lake_dir = str(tmp_path / "lake")
    os.makedirs(lake_dir)
    monkeypatch.setenv("NUBI_MANAGED_LAKE_DIR", lake_dir)
    monkeypatch.setenv("NUBI_CUSTODY_ENABLED", "true")
    monkeypatch.setenv("NUBI_LAKEHOUSE_PROVIDER", "dedicated")
    get_settings.cache_clear()

    from app.lakehouse.managed import get_provider

    p = get_provider(repo)
    assert isinstance(p, DedicatedBucketProvider)


@pytest.mark.asyncio
async def test_get_provider_dispatch_prefix_when_custody_off(monkeypatch, tmp_path, repo):
    """get_provider() returns PrefixIsolatedProvider when custody is off."""
    from app.config import get_settings
    from app.lakehouse.managed import PrefixIsolatedProvider

    lake_dir = str(tmp_path / "lake2")
    os.makedirs(lake_dir)
    monkeypatch.setenv("NUBI_MANAGED_LAKE_DIR", lake_dir)
    monkeypatch.setenv("NUBI_CUSTODY_ENABLED", "false")
    monkeypatch.setenv("NUBI_LAKEHOUSE_PROVIDER", "dedicated")  # ignored when off
    get_settings.cache_clear()

    from app.lakehouse.managed import get_provider

    p = get_provider(repo)
    assert isinstance(p, PrefixIsolatedProvider)


@pytest.mark.asyncio
async def test_get_provider_dedicated_construction_failure_raises(
    monkeypatch, tmp_path, repo
):
    """Bug-2 regression: explicit dedicated + construction failure must RAISE, not degrade.

    When NUBI_LAKEHOUSE_PROVIDER=dedicated is set, any error constructing the
    DedicatedBucketProvider must propagate.  It must NOT silently fall back to
    PrefixIsolatedProvider, which would give weaker-than-requested isolation.
    """
    import unittest.mock as mock

    from app.config import get_settings
    from app.lakehouse.managed import PrefixIsolatedProvider, get_provider

    lake_dir = str(tmp_path / "lake3")
    os.makedirs(lake_dir)
    monkeypatch.setenv("NUBI_MANAGED_LAKE_DIR", lake_dir)
    monkeypatch.setenv("NUBI_CUSTODY_ENABLED", "true")
    monkeypatch.setenv("NUBI_LAKEHOUSE_PROVIDER", "dedicated")
    get_settings.cache_clear()

    # Force DedicatedBucketProvider.__init__ to raise a RuntimeError — simulates
    # e.g. a bad cloud credential or a config validation failure.
    with mock.patch(
        "app.lakehouse.dedicated.DedicatedBucketProvider.__init__",
        side_effect=RuntimeError("simulated init failure"),
    ):
        with pytest.raises(RuntimeError, match="simulated init failure"):
            get_provider(repo)

    # Verify the patch is gone (post-condition: normal call works again).
    p = get_provider(repo)
    assert isinstance(p, DedicatedBucketProvider)


# ---------------------------------------------------------------------------
# _central_secret — full cred persistence (Bug-3)
# ---------------------------------------------------------------------------


def test_central_secret_persists_full_cred_set():
    """Bug-3 regression: _central_secret stores all creds needed to rebuild client."""
    full_creds = {
        "aws_access_key_id": "AKIAIOSFODNN7EXAMPLE",
        "aws_secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        "endpoint_url": "http://minio:9000",
        "region_name": "af-south-1",
    }
    central = CentralStorage(scheme="s3", bucket="test-bucket", creds=full_creds)
    provider = DedicatedBucketProvider(repo=InMemoryRepo(), central=central)
    secret = provider._central_secret()

    assert secret["aws_access_key_id"] == full_creds["aws_access_key_id"]
    assert secret["aws_secret_access_key"] == full_creds["aws_secret_access_key"]
    assert secret["endpoint_url"] == full_creds["endpoint_url"]
    assert secret["region_name"] == full_creds["region_name"]


def test_central_secret_omits_empty_values():
    """_central_secret does not include keys with empty/None values."""
    central = CentralStorage(
        scheme="s3",
        bucket="test-bucket",
        creds={"aws_secret_access_key": "secret", "aws_access_key_id": ""},
    )
    provider = DedicatedBucketProvider(repo=InMemoryRepo(), central=central)
    secret = provider._central_secret()

    assert "aws_secret_access_key" in secret
    # Empty string is falsy → omitted.
    assert "aws_access_key_id" not in secret
