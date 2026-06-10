"""Dataset datastore registration stamps the org-scoped S3 secret scope.

When a dataset's Parquet lives in object storage, the registered datastore
config must carry ``s3_scope`` = the org's prefix
(``s3://<bucket>/datasets/<org_id>/``) so the DuckDB S3 secret is bound to
that prefix (SCOPE clause) — engine-layer tenant isolation independent of RLS.
Local ``file://`` datasets get no scope (nothing to bind).
"""

from __future__ import annotations

import uuid

import pytest

from app.repos.memory import InMemoryRepo
from app.routes.datasets import _register_datastore


@pytest.mark.asyncio
async def test_s3_dataset_config_carries_org_scope():
    repo = InMemoryRepo()
    org_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    dataset_id = str(uuid.uuid4())
    storage_uri = f"s3://nubi-data/datasets/{org_id}/{dataset_id}/data.parquet"

    datastore_id = await _register_datastore(
        org_id=org_id,
        user_id=user_id,
        name="sales",
        parquet_path="/tmp/ignored.parquet",
        repo=repo,
        storage_uri=storage_uri,
    )

    row = await repo.get("datastores", org_id, datastore_id)
    cfg = row["config"]
    assert cfg["s3_scope"] == f"s3://nubi-data/datasets/{org_id}/"
    assert cfg["parquet_path"] == storage_uri
    assert storage_uri in cfg["view_sql"]


@pytest.mark.asyncio
async def test_local_dataset_config_has_no_scope():
    repo = InMemoryRepo()
    org_id = str(uuid.uuid4())

    datastore_id = await _register_datastore(
        org_id=org_id,
        user_id=str(uuid.uuid4()),
        name="local",
        parquet_path="/tmp/nubi-datasets/datasets/x/data.parquet",
        repo=repo,
        storage_uri=None,
    )

    row = await repo.get("datastores", org_id, datastore_id)
    assert "s3_scope" not in row["config"]
