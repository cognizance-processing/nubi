"""Where user-uploaded assets live — avatars and dashboard images.

Local disk by default (``<LOCAL_STORAGE_ROOT>``, i.e. ``backend/storage_data``),
which is right for a laptop and wrong for any host whose disk is ephemeral: on
Fly every deploy or machine restart starts from a clean filesystem, so uploads
written there vanish. Set ``NUBI_ASSET_STORAGE_URI`` (e.g. ``s3://my-bucket/``)
to keep them in object storage instead.

Credentials come from the SDK's default chain — for ``s3://`` that is the
``AWS_ACCESS_KEY_ID`` / ``AWS_SECRET_ACCESS_KEY`` / ``AWS_ENDPOINT_URL_S3`` /
``AWS_REGION`` set that ``fly storage create`` (Tigris) attaches to an app.
"""

from __future__ import annotations

import os

from app.storage.base import StorageClient, get_storage_client

ASSET_STORAGE_URI_ENV = "NUBI_ASSET_STORAGE_URI"


def local_storage_root() -> str:
    """The local asset root: ``LOCAL_STORAGE_ROOT`` or ``backend/storage_data``."""
    default_root = os.path.join(os.path.dirname(__file__), "..", "..", "storage_data")
    return os.path.abspath(os.getenv("LOCAL_STORAGE_ROOT", default_root))


def remote_asset_storage() -> StorageClient | None:
    """The configured object store for assets, or None to use local disk."""
    uri = os.environ.get(ASSET_STORAGE_URI_ENV, "").strip()
    return get_storage_client(uri) if uri else None


def asset_storage() -> StorageClient:
    """Object storage when configured, else the local asset root."""
    remote = remote_asset_storage()
    if remote is not None:
        return remote
    from app.storage.local import LocalStorageClient  # noqa: PLC0415

    return LocalStorageClient(root=local_storage_root())
