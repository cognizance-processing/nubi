"""Nubi storage abstraction layer.

Public API
----------
``StorageClient``
    Abstract base class defining the storage interface.

``parse_uri(uri)``
    Decompose a ``<scheme>://<bucket>/<key>`` URI into a 3-tuple.

``get_storage_client(uri, creds)``
    Factory that returns the appropriate ``StorageClient`` for the given URI
    scheme.  Supported schemes:

    - ``file://`` — local filesystem (:mod:`app.storage.local`)
    - ``s3://``   — AWS S3 / S3-compatible (:mod:`app.storage.s3`)
    - ``gs://``   — Google Cloud Storage (:mod:`app.storage.gcs`)
    - ``az://``   — Azure Blob Storage (:mod:`app.storage.azure`)

Import example::

    from app.storage import get_storage_client

    client = get_storage_client("s3://my-bucket/", creds={...})
    uri = client.upload_bytes(b"hello", "exports/hello.txt")
"""

from __future__ import annotations

from app.storage.base import StorageClient, get_storage_client, parse_uri

__all__ = [
    "StorageClient",
    "get_storage_client",
    "parse_uri",
]
