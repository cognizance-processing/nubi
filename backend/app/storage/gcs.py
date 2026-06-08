"""Google Cloud Storage backend for Nubi (``gs://`` scheme).

Implements :class:`~app.storage.base.StorageClient` using the
*google-cloud-storage* SDK, imported lazily so the app runs without it
installed — only the ``gs`` storage scheme requires it.

Credentials dict
----------------
Pass via ``creds`` argument to :func:`~app.storage.base.get_storage_client`
or directly to :class:`GCSStorageClient`.  Supply the parsed JSON of a GCP
service-account key file:

.. code-block:: python

    {
        "type":          "service_account",
        "project_id":    "my-project",
        "private_key_id": "...",
        "private_key":   "-----BEGIN RSA PRIVATE KEY-----\\n...",
        "client_email":  "...",
        "client_id":     "...",
        "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
        "token_uri":     "https://oauth2.googleapis.com/token",
        ...
    }

Omitting ``creds`` (or passing an empty dict) uses Application Default
Credentials (ADC) — suitable when running on GCP infrastructure.
"""

from __future__ import annotations

import io
import os
from typing import BinaryIO

from app.storage.base import StorageClient


def _get_gcs_client(creds: dict):
    """Return a ``google.cloud.storage.Client``, raising a clear error if missing."""
    try:
        from google.cloud import storage as gcs  # noqa: PLC0415
    except ImportError:
        raise RuntimeError(
            "google-cloud-storage is required for GCS storage but is not installed. "
            "Install it with: pip install google-cloud-storage"
        ) from None

    if creds:
        try:
            from google.oauth2 import service_account  # noqa: PLC0415
        except ImportError:
            raise RuntimeError(
                "google-auth is required for GCS service-account credentials but is "
                "not installed.  Install it with: pip install google-auth"
            ) from None
        credentials = service_account.Credentials.from_service_account_info(
            creds,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        project = creds.get("project_id")
        return gcs.Client(project=project, credentials=credentials)

    # ADC path
    return gcs.Client()


class GCSStorageClient(StorageClient):
    """Google Cloud Storage backend.

    Parameters
    ----------
    bucket:
        GCS bucket name.
    creds:
        Service-account credentials dict (see module docstring).  Defaults
        to Application Default Credentials when empty.
    """

    SCHEME = "gs"

    def __init__(self, bucket: str, creds: dict | None = None) -> None:
        self._bucket_name = bucket
        self._creds = creds or {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _bucket(self):
        client = _get_gcs_client(self._creds)
        return client.bucket(self._bucket_name)

    def _blob(self, key: str):
        return self._bucket().blob(key.lstrip("/"))

    def _full_uri(self, key: str) -> str:
        return f"{self.SCHEME}://{self._bucket_name}/{key.lstrip('/')}"

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def upload_bytes(self, data: bytes, key: str) -> str:
        """Upload *data* to ``gs://<bucket>/<key>``."""
        blob = self._blob(key)
        blob.upload_from_string(data)
        return self._full_uri(key)

    def upload_file(self, local_path: str, key: str) -> str:
        """Upload the local file at *local_path* to ``gs://<bucket>/<key>``."""
        blob = self._blob(key)
        blob.upload_from_filename(local_path)
        return self._full_uri(key)

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def download_bytes(self, key: str) -> bytes:
        """Download and return the GCS blob at *key* as bytes.

        Raises
        ------
        FileNotFoundError
            If the object does not exist.
        """
        try:
            from google.cloud.exceptions import NotFound  # noqa: PLC0415
        except ImportError:
            raise RuntimeError(
                "google-cloud-storage is required for GCS storage but is not installed. "
                "Install it with: pip install google-cloud-storage"
            ) from None

        try:
            return self._blob(key).download_as_bytes()
        except NotFound:
            raise FileNotFoundError(
                f"Storage object not found: {self._full_uri(key)}"
            ) from None

    def download_to_file(self, key: str, local_path: str) -> str:
        """Download *key* from GCS and write to *local_path*."""
        os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
        try:
            from google.cloud.exceptions import NotFound  # noqa: PLC0415
        except ImportError:
            raise RuntimeError(
                "google-cloud-storage is required for GCS storage but is not installed. "
                "Install it with: pip install google-cloud-storage"
            ) from None

        try:
            self._blob(key).download_to_filename(local_path)
        except NotFound:
            raise FileNotFoundError(
                f"Storage object not found: {self._full_uri(key)}"
            ) from None
        return local_path

    def open_read(self, key: str) -> BinaryIO:
        """Return a streaming binary reader for the GCS blob at *key*.

        The blob is downloaded into a ``BytesIO`` buffer.

        Raises
        ------
        FileNotFoundError
            If the object does not exist.
        """
        data = self.download_bytes(key)
        return io.BytesIO(data)

    # ------------------------------------------------------------------
    # Listing / inspection
    # ------------------------------------------------------------------

    def list(self, prefix: str = "") -> list[str]:
        """Return all blob names in *bucket* filtered by *prefix*."""
        client = _get_gcs_client(self._creds)
        blobs = client.list_blobs(self._bucket_name, prefix=prefix)
        return sorted(blob.name for blob in blobs)

    def exists(self, key: str) -> bool:
        """Return ``True`` if ``gs://<bucket>/<key>`` exists."""
        return self._blob(key).exists()
