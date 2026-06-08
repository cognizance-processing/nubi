"""Azure Blob Storage backend for Nubi (``az://`` scheme).

Implements :class:`~app.storage.base.StorageClient` using the
*azure-storage-blob* SDK, imported lazily so the app runs without it
installed — only the ``az`` storage scheme requires it.

Credentials dict
----------------
Pass via ``creds`` argument to :func:`~app.storage.base.get_storage_client`
or directly to :class:`AzureStorageClient`.  Two credential shapes are
supported:

**Connection string** (simplest):

.. code-block:: python

    {
        "connection_string": "DefaultEndpointsProtocol=https;AccountName=...;AccountKey=...;EndpointSuffix=core.windows.net"
    }

**Account URL + credential**:

.. code-block:: python

    {
        "account_url": "https://<account>.blob.core.windows.net",
        "credential":  "<account-key-or-sas-token>",  # optional; omit for managed identity
    }

Omitting both uses the ``AZURE_STORAGE_CONNECTION_STRING`` environment variable
as a fallback (standard Azure SDK convention).
"""

from __future__ import annotations

import io
import os
from typing import BinaryIO

from app.storage.base import StorageClient


def _get_blob_service_client(creds: dict):
    """Return an ``azure.storage.blob.BlobServiceClient``, raising a clear error if missing."""
    try:
        from azure.storage.blob import BlobServiceClient  # noqa: PLC0415
    except ImportError:
        raise RuntimeError(
            "azure-storage-blob is required for Azure storage but is not installed. "
            "Install it with: pip install azure-storage-blob"
        ) from None

    if "connection_string" in creds:
        return BlobServiceClient.from_connection_string(creds["connection_string"])

    if "account_url" in creds:
        return BlobServiceClient(
            account_url=creds["account_url"],
            credential=creds.get("credential"),
        )

    # Fallback: environment variable
    conn_str = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if conn_str:
        return BlobServiceClient.from_connection_string(conn_str)

    raise RuntimeError(
        "No Azure credentials found.  Provide 'connection_string' or 'account_url' "
        "in the creds dict, or set the AZURE_STORAGE_CONNECTION_STRING environment "
        "variable."
    )


class AzureStorageClient(StorageClient):
    """Azure Blob Storage backend.

    Parameters
    ----------
    container:
        Azure Blob Storage container name.
    creds:
        Credentials dict (see module docstring).
    """

    SCHEME = "az"

    def __init__(self, container: str, creds: dict | None = None) -> None:
        self._container = container
        self._creds = creds or {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _service_client(self):
        return _get_blob_service_client(self._creds)

    def _blob_client(self, key: str):
        return self._service_client().get_blob_client(
            container=self._container,
            blob=key.lstrip("/"),
        )

    def _container_client(self):
        return self._service_client().get_container_client(self._container)

    def _full_uri(self, key: str) -> str:
        return f"{self.SCHEME}://{self._container}/{key.lstrip('/')}"

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def upload_bytes(self, data: bytes, key: str) -> str:
        """Upload *data* to ``az://<container>/<key>``."""
        self._blob_client(key).upload_blob(data, overwrite=True)
        return self._full_uri(key)

    def upload_file(self, local_path: str, key: str) -> str:
        """Upload the local file at *local_path* to ``az://<container>/<key>``."""
        with open(local_path, "rb") as fh:
            self._blob_client(key).upload_blob(fh, overwrite=True)
        return self._full_uri(key)

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def download_bytes(self, key: str) -> bytes:
        """Download and return the Azure blob at *key* as bytes.

        Raises
        ------
        FileNotFoundError
            If the blob does not exist.
        """
        try:
            from azure.core.exceptions import ResourceNotFoundError  # noqa: PLC0415
        except ImportError:
            raise RuntimeError(
                "azure-storage-blob is required for Azure storage but is not installed. "
                "Install it with: pip install azure-storage-blob"
            ) from None

        try:
            stream = self._blob_client(key).download_blob()
            return stream.readall()
        except ResourceNotFoundError:
            raise FileNotFoundError(
                f"Storage object not found: {self._full_uri(key)}"
            ) from None

    def download_to_file(self, key: str, local_path: str) -> str:
        """Download *key* from Azure and write to *local_path*."""
        os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
        try:
            from azure.core.exceptions import ResourceNotFoundError  # noqa: PLC0415
        except ImportError:
            raise RuntimeError(
                "azure-storage-blob is required for Azure storage but is not installed. "
                "Install it with: pip install azure-storage-blob"
            ) from None

        try:
            with open(local_path, "wb") as fh:
                stream = self._blob_client(key).download_blob()
                stream.readinto(fh)
        except ResourceNotFoundError:
            raise FileNotFoundError(
                f"Storage object not found: {self._full_uri(key)}"
            ) from None
        return local_path

    def open_read(self, key: str) -> BinaryIO:
        """Return a streaming binary reader for the Azure blob at *key*.

        Raises
        ------
        FileNotFoundError
            If the blob does not exist.
        """
        data = self.download_bytes(key)
        return io.BytesIO(data)

    # ------------------------------------------------------------------
    # Listing / inspection
    # ------------------------------------------------------------------

    def list(self, prefix: str = "") -> list[str]:
        """Return all blob names in *container* filtered by *prefix*."""
        container_client = self._container_client()
        blobs = container_client.list_blobs(name_starts_with=prefix or None)
        return sorted(blob.name for blob in blobs)

    def exists(self, key: str) -> bool:
        """Return ``True`` if ``az://<container>/<key>`` exists."""
        try:
            from azure.core.exceptions import ResourceNotFoundError  # noqa: PLC0415
        except ImportError:
            raise RuntimeError(
                "azure-storage-blob is required for Azure storage but is not installed. "
                "Install it with: pip install azure-storage-blob"
            ) from None

        try:
            self._blob_client(key).get_blob_properties()
            return True
        except ResourceNotFoundError:
            return False
