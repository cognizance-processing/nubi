"""Storage abstraction layer for Nubi.

This module defines the ``StorageClient`` abstract base class and the
``get_storage_client()`` dispatch factory.  Concrete backends live in
sibling modules:

- ``local.py``   — ``file://`` scheme; local filesystem (no creds needed).
- ``s3.py``      — ``s3://`` scheme; AWS S3 and S3-compatible stores (boto3).
- ``gcs.py``     — ``gs://`` scheme; Google Cloud Storage (google-cloud-storage).
- ``azure.py``   — ``az://`` scheme; Azure Blob Storage (azure-storage-blob).

URI format
----------
All methods that take a ``key`` parameter accept a *relative* object key
(e.g. ``"exports/2024/report.parquet"``).  Full URIs have the form::

    <scheme>://<bucket>/<key>

Use ``parse_uri(uri)`` to decompose a full URI into ``(scheme, bucket, key)``.

Credentials dict shapes
-----------------------
The ``creds`` dict passed to ``get_storage_client()`` is **backend-specific**:

AWS / S3-compatible
~~~~~~~~~~~~~~~~~~~
.. code-block:: python

    {
        "aws_access_key_id":     "AKIA...",
        "aws_secret_access_key": "...",
        "region_name":           "us-east-1",           # optional
        "endpoint_url":          "https://...",          # S3-compatible only
        "aws_session_token":     "...",                  # STS / role token, optional
    }

Google Cloud Storage
~~~~~~~~~~~~~~~~~~~~
Pass the parsed JSON of a GCP service-account key file:

.. code-block:: python

    {
        "type":                        "service_account",
        "project_id":                  "my-project",
        "private_key_id":              "...",
        "private_key":                 "-----BEGIN RSA PRIVATE KEY-----\\n...",
        "client_email":                "...",
        "client_id":                   "...",
        "auth_uri":                    "https://accounts.google.com/o/oauth2/auth",
        "token_uri":                   "https://oauth2.googleapis.com/token",
        # ... other standard service-account fields
    }

Azure Blob Storage
~~~~~~~~~~~~~~~~~~
.. code-block:: python

    {
        "connection_string": "DefaultEndpointsProtocol=https;AccountName=...;AccountKey=...;EndpointSuffix=core.windows.net"
    }

Or, using a SAS URL / account key directly:

.. code-block:: python

    {
        "account_url": "https://<account>.blob.core.windows.net",
        "credential":  "<account-key-or-sas-token>",          # optional
    }

Local filesystem (``file://``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
No credentials needed.  The ``bucket`` segment of the URI is used as the
root directory on the local filesystem.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import BinaryIO


@dataclass(frozen=True)
class ObjectStat:
    """Lightweight metadata for one stored object (``size``/``mtime``/``etag``).

    Returned by :meth:`StorageClient.stat`.  Feeds the file-connector
    ``FileStat`` (which the ingestion watermark consumes).  ``mtime`` is a
    timezone-aware UTC ``datetime`` when the backend exposes one, else ``None``.
    """

    size: int
    mtime: datetime | None = None
    etag: str | None = None


# ---------------------------------------------------------------------------
# URI helpers
# ---------------------------------------------------------------------------


def parse_uri(uri: str) -> tuple[str, str, str]:
    """Decompose a storage URI into ``(scheme, bucket, key)``.

    Parameters
    ----------
    uri:
        A full storage URI of the form ``<scheme>://<bucket>/<key>``.
        Examples:

        - ``s3://my-bucket/path/to/object.parquet``
        - ``gs://my-bucket/exports/report.csv``
        - ``az://my-container/data/file.json``
        - ``file:///tmp/data/uploads/file.parquet``  (triple slash: empty bucket)
        - ``file:///abs/root/key.txt``

    Returns
    -------
    tuple[str, str, str]
        ``(scheme, bucket, key)`` where *scheme* is the lowercase URI scheme
        (``"s3"``, ``"gs"``, ``"az"``, ``"file"``), *bucket* is the bucket /
        container / root-dir segment, and *key* is the object key (without a
        leading slash).

    Raises
    ------
    ValueError
        If *uri* does not contain ``://``.
    """
    if "://" not in uri:
        raise ValueError(f"Invalid storage URI (missing '://'): {uri!r}")

    scheme, rest = uri.split("://", 1)
    scheme = scheme.lower()

    # ``file://`` URIs conventionally use three slashes for absolute paths:
    # ``file:///abs/root/key`` → rest = ``/abs/root/key``.
    # We treat the *first directory component* as the bucket (root dir) and
    # the remainder as the key so that the round-trip is:
    #   parse_uri("file:///tmp/nubi-data/exports/f.parquet")
    #   → ("file", "/tmp/nubi-data", "exports/f.parquet")
    #
    # For non-file schemes the conventional form is ``s3://bucket/key``
    # (no leading slash in *rest*).
    if scheme == "file" and rest.startswith("/"):
        # ``file://`` URIs use three slashes for absolute paths so that
        # ``rest`` is the absolute path including the leading ``/``.
        # Convention (see module and local.py docstrings):
        #
        #   ``file:///tmp/nubi-data/exports/f.parquet``
        #   → bucket = ``/tmp/nubi-data``, key = ``exports/f.parquet``
        #
        # The bucket (root directory) is formed by the first two path
        # components; the key is everything after the third ``/``.
        # Specifically: ``rest`` = ``/a/b/c/d``; we find the slash at
        # index 0 (leading), the slash separating ``a`` from ``b`` at
        # the second position, and the slash separating ``b`` from ``c``
        # at the third position.  We split at that third position so
        # bucket = ``/a/b`` and key = ``c/d``.
        second_slash = rest.find("/", 1)   # slash after first component
        if second_slash == -1:
            # URI like ``file:///singlesegment`` — no key at all
            bucket = rest
            key = ""
        else:
            third_slash = rest.find("/", second_slash + 1)
            if third_slash == -1:
                # Two-component path, no key: ``file:///a/b``
                bucket = rest
                key = ""
            else:
                bucket = rest[:third_slash]       # e.g. ``/tmp/nubi-data``
                key = rest[third_slash + 1:]      # e.g. ``exports/f.parquet``
    elif "/" in rest:
        bucket, key = rest.split("/", 1)
    else:
        bucket, key = rest, ""

    return scheme, bucket, key


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class StorageClient(ABC):
    """Abstract interface for Nubi storage backends.

    All methods are synchronous (the heavy I/O is expected to run inside
    ``asyncio.to_thread`` or a ``ThreadPoolExecutor`` at the call site when
    needed).  This keeps the interface simple and testable without an event
    loop.

    Sub-classes must raise a clear, actionable ``RuntimeError`` if the
    required SDK is not installed, rather than surfacing an ``ImportError``
    directly.
    """

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    @abstractmethod
    def upload_bytes(self, data: bytes, key: str) -> str:
        """Upload *data* and store it under *key*.

        Parameters
        ----------
        data:
            Raw bytes to upload.
        key:
            Object key relative to this client's bucket / root.

        Returns
        -------
        str
            The full URI of the uploaded object (``<scheme>://<bucket>/<key>``).
        """

    @abstractmethod
    def upload_file(self, local_path: str, key: str) -> str:
        """Upload a local file at *local_path* and store it under *key*.

        Parameters
        ----------
        local_path:
            Absolute or relative path to the local file.
        key:
            Object key relative to this client's bucket / root.

        Returns
        -------
        str
            The full URI of the uploaded object.
        """

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    @abstractmethod
    def download_bytes(self, key: str) -> bytes:
        """Download and return the object at *key* as raw bytes.

        Raises
        ------
        FileNotFoundError
            If the object does not exist.
        """

    @abstractmethod
    def download_to_file(self, key: str, local_path: str) -> str:
        """Download the object at *key* to a local file at *local_path*.

        Parent directories are created automatically.

        Returns
        -------
        str
            *local_path* (echoed back for convenience).
        """

    @abstractmethod
    def open_read(self, key: str) -> BinaryIO:
        """Return a streaming binary reader for the object at *key*.

        The caller is responsible for closing the returned file-like object
        (use it as a context manager where possible).

        Raises
        ------
        FileNotFoundError
            If the object does not exist.
        """

    # ------------------------------------------------------------------
    # Listing / inspection
    # ------------------------------------------------------------------

    @abstractmethod
    def list(self, prefix: str = "") -> list[str]:
        """Return all object keys that start with *prefix*.

        Parameters
        ----------
        prefix:
            Optional key prefix to filter results.  An empty string lists
            **all** objects in the bucket / root.

        Returns
        -------
        list[str]
            Sorted list of matching object keys (relative, without a leading
            slash).
        """

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Return ``True`` if an object with *key* exists, ``False`` otherwise."""

    # ------------------------------------------------------------------
    # Optional metadata / mutation (used by the file-connector interface)
    # ------------------------------------------------------------------

    def stat(self, key: str) -> "ObjectStat | None":
        """Return size/mtime/etag for *key*, or ``None`` if unknown/absent.

        Default implementation returns ``None`` (metadata unavailable) so
        backends that cannot cheaply stat still work — the file connector then
        emits a ``FileStat`` with ``size=0`` / ``mtime=None`` for that object,
        which simply means the ``mtime`` watermark never skips it.  Backends
        override this to supply real metadata.
        """
        return None

    def delete(self, key: str) -> None:
        """Delete the object at *key* (no-op if absent).

        Default raises ``NotImplementedError``; backends that support deletion
        override it.  Used by the file-connector ``post_action: delete``.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support delete()."
        )


# ---------------------------------------------------------------------------
# Dispatch factory
# ---------------------------------------------------------------------------


def get_storage_client(
    uri: str,
    creds: dict | None = None,
) -> StorageClient:
    """Return a ``StorageClient`` appropriate for the scheme in *uri*.

    Dispatch table
    --------------
    ============  =====================================
    Scheme        Backend
    ============  =====================================
    ``s3``        :class:`~app.storage.s3.S3StorageClient`
    ``gs``        :class:`~app.storage.gcs.GCSStorageClient`
    ``az``        :class:`~app.storage.azure.AzureStorageClient`
    ``file``      :class:`~app.storage.local.LocalStorageClient`
    ============  =====================================

    Parameters
    ----------
    uri:
        A full storage URI — only the scheme and bucket are used for
        client construction; individual method calls supply the key.
    creds:
        Backend-specific credentials dict.  See module docstring for shapes.
        ``None`` uses environment-variable / ADC authentication where
        supported.

    Returns
    -------
    StorageClient
        A configured (but not yet connected) backend client.

    Raises
    ------
    ValueError
        If the URI scheme is not recognised.
    RuntimeError
        If the required SDK for the scheme is not installed.
    """
    scheme, bucket, _key = parse_uri(uri)

    if scheme == "file":
        from app.storage.local import LocalStorageClient  # noqa: PLC0415
        return LocalStorageClient(root=bucket)

    if scheme == "s3":
        from app.storage.s3 import S3StorageClient  # noqa: PLC0415
        return S3StorageClient(bucket=bucket, creds=creds or {})

    if scheme == "gs":
        from app.storage.gcs import GCSStorageClient  # noqa: PLC0415
        return GCSStorageClient(bucket=bucket, creds=creds or {})

    if scheme == "az":
        from app.storage.azure import AzureStorageClient  # noqa: PLC0415
        return AzureStorageClient(container=bucket, creds=creds or {})

    raise ValueError(
        f"Unknown storage scheme {scheme!r} in URI {uri!r}. "
        "Supported schemes: s3, gs, az, file."
    )
