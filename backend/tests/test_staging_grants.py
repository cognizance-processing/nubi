"""Ephemeral staging grants — write-only, prefix-pinned, short-TTL (§7).

What this suite verifies
------------------------
(1) A grant is PINNED to the run's staging prefix ``orgs/<org>/staging/<run>/``;
    a hostile relative path cannot escape it.
(2) A grant has a SHORT TTL clamped to [15, 60] min (presigned URLs aren't
    revocable mid-TTL, so TTL is the bound).
(3) A grant is WRITE-ONLY: for the S3 backend every upload is a PUT and there is
    no list/read/delete capability in the grant shape.
(4) A grant CANNOT be minted for a task not bound to the claiming bridge
    (``task_not_bound``); cross-org binding is rejected.
(5) Cross-org isolation: org B's bridge gets no grant for org A's run.
(6) The grant NEVER carries a connector secret (hard rule §9).

Backend
-------
Local/file staging store (``NUBI_STAGING_DIR``) drives the prefix/TTL/binding
tests with no cloud. A monkeypatched S3 ``CentralStorage`` drives the
presigned-PUT (write-only) shape with a fake presigner — no real boto3/network.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from app.lakehouse import grants as grants_mod
from app.lakehouse.grants import (
    DEFAULT_GRANT_TTL,
    MAX_GRANT_TTL,
    MIN_GRANT_TTL,
    StagingGrantError,
    mint_staging_grant,
    task_belongs_to_bridge,
)


# ---------------------------------------------------------------------------
# A minimal flows-store double that records a task_run's bridge binding.
# ---------------------------------------------------------------------------


class _FakeFlowStore:
    def __init__(self) -> None:
        self._runs: dict[str, dict] = {}

    def add(self, run_id: str, org_id: str, bridge_id: str | None) -> None:
        self._runs[run_id] = {
            "id": run_id,
            "org_id": org_id,
            "bridge_id": bridge_id,
            "state": "running",
        }

    async def get_task_run(self, run_id: str):
        return self._runs.get(run_id)


@pytest.fixture
def local_staging(tmp_path, monkeypatch):
    """Point the staging store at a local dir (self-host posture)."""
    monkeypatch.setenv("NUBI_STAGING_DIR", str(tmp_path / "staging"))
    # Ensure no S3 staging bucket shadows the local dir.
    monkeypatch.delenv("NUBI_STAGING_BUCKET_URI", raising=False)
    yield tmp_path


# ---------------------------------------------------------------------------
# (4)+(5) Binding gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_belongs_to_bridge_true_when_bound():
    org = str(uuid.uuid4())
    run = str(uuid.uuid4())
    bridge = str(uuid.uuid4())
    store = _FakeFlowStore()
    store.add(run, org, bridge)
    assert await task_belongs_to_bridge(org, run, bridge, store=store) is True


@pytest.mark.asyncio
async def test_task_belongs_to_bridge_false_for_other_bridge_or_org():
    org = str(uuid.uuid4())
    run = str(uuid.uuid4())
    bridge = str(uuid.uuid4())
    other_bridge = str(uuid.uuid4())
    other_org = str(uuid.uuid4())
    store = _FakeFlowStore()
    store.add(run, org, bridge)

    assert await task_belongs_to_bridge(org, run, other_bridge, store=store) is False
    assert await task_belongs_to_bridge(other_org, run, bridge, store=store) is False
    # Unclaimed run (no bridge) → not bound.
    store.add(run, org, None)
    assert await task_belongs_to_bridge(org, run, bridge, store=store) is False
    # Missing run → fail closed.
    assert await task_belongs_to_bridge(org, "missing", bridge, store=store) is False


@pytest.mark.asyncio
async def test_mint_refuses_task_not_bound(local_staging):
    org = str(uuid.uuid4())
    run = str(uuid.uuid4())
    bridge = str(uuid.uuid4())
    store = _FakeFlowStore()
    store.add(run, org, str(uuid.uuid4()))  # bound to a DIFFERENT bridge

    with pytest.raises(StagingGrantError) as exc:
        await mint_staging_grant(org, run, bridge, store=store, rel_paths=["x.parquet"])
    assert exc.value.code == "task_not_bound"


# ---------------------------------------------------------------------------
# (1)+(2)+(6) Local grant: prefix-pinned, short-TTL, no secrets
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_local_grant_is_prefix_pinned_and_ttl_bounded(local_staging):
    org = str(uuid.uuid4())
    run = str(uuid.uuid4())
    bridge = str(uuid.uuid4())
    store = _FakeFlowStore()
    store.add(run, org, bridge)

    grant = await mint_staging_grant(org, run, bridge, store=store)
    assert grant.kind == "local"
    # Prefix-pinned to this org+run, server-derived.
    assert grant.prefix == f"orgs/{org}/staging/{run}/"
    assert grant.base_uri.startswith("file://")
    # Default TTL is bounded.
    ttl = grant.expires_at - __import__("datetime").datetime.now(
        tz=__import__("datetime").timezone.utc
    )
    assert MIN_GRANT_TTL <= ttl <= MAX_GRANT_TTL + timedelta(seconds=5)

    # No connector secret anywhere in the wire shape (hard rule §9).
    blob = str(grant.to_dict()).lower()
    assert "secret" not in blob
    assert "password" not in blob


@pytest.mark.asyncio
async def test_ttl_is_clamped_into_window(local_staging):
    org = str(uuid.uuid4())
    run = str(uuid.uuid4())
    bridge = str(uuid.uuid4())
    store = _FakeFlowStore()
    store.add(run, org, bridge)
    now = __import__("datetime").datetime.now(tz=__import__("datetime").timezone.utc)

    # Too long → clamped to MAX.
    g = await mint_staging_grant(
        org, run, bridge, store=store, ttl=timedelta(hours=10), now=now
    )
    assert g.expires_at == now + MAX_GRANT_TTL

    # Too short → clamped to MIN.
    g = await mint_staging_grant(
        org, run, bridge, store=store, ttl=timedelta(seconds=1), now=now
    )
    assert g.expires_at == now + MIN_GRANT_TTL

    # None → default.
    g = await mint_staging_grant(org, run, bridge, store=store, now=now)
    assert g.expires_at == now + DEFAULT_GRANT_TTL


@pytest.mark.asyncio
async def test_unconfigured_staging_raises(monkeypatch):
    monkeypatch.delenv("NUBI_STAGING_DIR", raising=False)
    monkeypatch.delenv("NUBI_STAGING_BUCKET_URI", raising=False)
    monkeypatch.delenv("NUBI_MANAGED_LAKE_DIR", raising=False)
    monkeypatch.delenv("NUBI_LOCAL_LAKE_DIR", raising=False)
    monkeypatch.delenv("S3_ACCESS_KEY", raising=False)
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)

    org = str(uuid.uuid4())
    run = str(uuid.uuid4())
    bridge = str(uuid.uuid4())
    store = _FakeFlowStore()
    store.add(run, org, bridge)
    with pytest.raises(StagingGrantError) as exc:
        await mint_staging_grant(org, run, bridge, store=store)
    assert exc.value.code == "staging_unconfigured"


# ---------------------------------------------------------------------------
# (1)+(3) S3 grant: presigned PUT, write-only, prefix-pinned, hostile-path-safe
# ---------------------------------------------------------------------------


class _FakeS3Storage:
    """Stand-in CentralStorage describing an S3 staging bucket."""

    scheme = "s3"
    bucket = "nubi-staging"
    creds: dict = {}

    def base_uri(self) -> str:
        return "s3://nubi-staging"


class _FakeS3Client:
    """Captures the keys presigned so the test can assert prefix-pinning."""

    presigned: list[tuple[str, int]] = []

    def __init__(self, bucket, creds=None):
        self.bucket = bucket

    def presign_put(self, key: str, expires_in: int) -> str:
        _FakeS3Client.presigned.append((key, expires_in))
        return f"https://nubi-staging.s3.amazonaws.com/{key}?X-Amz-Signature=fake&PUT"


@pytest.mark.asyncio
async def test_s3_grant_is_write_only_and_prefix_pinned(monkeypatch):
    _FakeS3Client.presigned = []
    monkeypatch.setattr(grants_mod, "resolve_staging_storage", lambda: _FakeS3Storage())
    # Patch the S3 client class the grant module imports lazily.
    import app.storage.s3 as s3mod

    monkeypatch.setattr(s3mod, "S3StorageClient", _FakeS3Client)

    org = str(uuid.uuid4())
    run = str(uuid.uuid4())
    bridge = str(uuid.uuid4())
    store = _FakeFlowStore()
    store.add(run, org, bridge)

    grant = await mint_staging_grant(
        org,
        run,
        bridge,
        store=store,
        rel_paths=["part-0000.parquet", "../../escape.parquet"],
        ttl=timedelta(minutes=20),
    )
    assert grant.kind == "s3_presigned"
    prefix = f"orgs/{org}/staging/{run}/"

    # Every upload is a write-only PUT.
    for rel, cap in grant.uploads.items():
        assert cap["method"] == "PUT"
        assert "url" in cap
    # No read/list/delete capability leaked into the grant.
    blob = str(grant.to_dict()).lower()
    assert "getobject" not in blob and "get_object" not in blob
    assert "delete" not in blob and "listobjects" not in blob

    # Every presigned key is pinned under the run prefix — even the hostile
    # ``../../escape.parquet`` resolves INSIDE the prefix, never above it.
    for key, expires_in in _FakeS3Client.presigned:
        assert key.startswith(prefix), key
        assert ".." not in key
        assert expires_in == int(timedelta(minutes=20).total_seconds())


@pytest.mark.asyncio
async def test_s3_grant_requires_paths(monkeypatch):
    monkeypatch.setattr(grants_mod, "resolve_staging_storage", lambda: _FakeS3Storage())
    org = str(uuid.uuid4())
    run = str(uuid.uuid4())
    bridge = str(uuid.uuid4())
    store = _FakeFlowStore()
    store.add(run, org, bridge)
    with pytest.raises(StagingGrantError) as exc:
        await mint_staging_grant(org, run, bridge, store=store, rel_paths=[])
    assert exc.value.code == "no_paths"


# ---------------------------------------------------------------------------
# (5) Cross-org isolation end to end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_org_bridge_cannot_grant_for_other_orgs_run(local_staging):
    org_a = str(uuid.uuid4())
    org_b = str(uuid.uuid4())
    run = str(uuid.uuid4())
    bridge_b = str(uuid.uuid4())
    store = _FakeFlowStore()
    # The run belongs to org A and is claimed by org A's bridge.
    store.add(run, org_a, str(uuid.uuid4()))

    # Org B's bridge tries to grab a grant for org A's run → refused.
    with pytest.raises(StagingGrantError) as exc:
        await mint_staging_grant(org_b, run, bridge_b, store=store)
    assert exc.value.code == "task_not_bound"
