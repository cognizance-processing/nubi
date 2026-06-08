"""AWS S3 (and S3-compatible) storage backend for Nubi (``s3://`` scheme).

Implements :class:`~app.storage.base.StorageClient` using the *boto3* SDK,
imported lazily so the app runs without boto3 installed — only the ``s3``
storage scheme requires it.

Credentials dict
----------------
Pass via ``creds`` argument to :func:`~app.storage.base.get_storage_client`
or directly to :class:`S3StorageClient`:

.. code-block:: python

    {
        "aws_access_key_id":     "AKIA...",          # required (or use env/IAM)
        "aws_secret_access_key": "...",              # required (or use env/IAM)
        "region_name":           "us-east-1",        # optional; defaults to env
        "endpoint_url":          "https://...",      # S3-compatible overrides only
        "aws_session_token":     "...",              # STS / assumed-role token
    }

Omitting all keys causes boto3 to use its default credential chain (env vars,
``~/.aws/credentials``, IAM role, etc.).
"""

from __future__ import annotations

import io
import os
from typing import BinaryIO

from app.storage.base import StorageClient

# Sentinel so we only print the missing-SDK error once.
_SDK_ERROR: str | None = None


def _get_s3_client(creds: dict):
    """Return a boto3 S3 client, raising a clear error if boto3 is missing."""
    try:
        import boto3  # noqa: PLC0415
    except ImportError:
        raise RuntimeError(
            "boto3 is required for S3 storage but is not installed. "
            "Install it with: pip install boto3"
        ) from None

    kwargs: dict = {}
    for key in (
        "aws_access_key_id",
        "aws_secret_access_key",
        "region_name",
        "endpoint_url",
        "aws_session_token",
    ):
        if key in creds:
            kwargs[key] = creds[key]

    return boto3.client("s3", **kwargs)


class S3StorageClient(StorageClient):
    """AWS S3 / S3-compatible storage backend.

    Parameters
    ----------
    bucket:
        S3 bucket name.
    creds:
        Credential dict (see module docstring).  Defaults to boto3 default
        credential chain when empty.
    """

    SCHEME = "s3"

    def __init__(self, bucket: str, creds: dict | None = None) -> None:
        self._bucket = bucket
        self._creds = creds or {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _client(self):
        return _get_s3_client(self._creds)

    def _full_uri(self, key: str) -> str:
        return f"{self.SCHEME}://{self._bucket}/{key.lstrip('/')}"

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def upload_bytes(self, data: bytes, key: str) -> str:
        """Upload *data* to ``s3://<bucket>/<key>``."""
        self._client().put_object(
            Bucket=self._bucket,
            Key=key.lstrip("/"),
            Body=data,
        )
        return self._full_uri(key)

    def upload_file(self, local_path: str, key: str) -> str:
        """Upload the local file at *local_path* to ``s3://<bucket>/<key>``."""
        self._client().upload_file(
            Filename=local_path,
            Bucket=self._bucket,
            Key=key.lstrip("/"),
        )
        return self._full_uri(key)

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def download_bytes(self, key: str) -> bytes:
        """Download and return the S3 object at *key* as bytes.

        Raises
        ------
        FileNotFoundError
            Wraps ``ClientError`` when the object does not exist (404 / NoSuchKey).
        """
        try:
            import botocore.exceptions  # noqa: PLC0415
        except ImportError:
            raise RuntimeError(
                "boto3/botocore is required for S3 storage but is not installed. "
                "Install it with: pip install boto3"
            ) from None

        try:
            resp = self._client().get_object(Bucket=self._bucket, Key=key.lstrip("/"))
            return resp["Body"].read()
        except botocore.exceptions.ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("NoSuchKey", "404"):
                raise FileNotFoundError(
                    f"Storage object not found: {self._full_uri(key)}"
                ) from exc
            raise

    def download_to_file(self, key: str, local_path: str) -> str:
        """Download *key* from S3 and write to *local_path*."""
        import os  # noqa: PLC0415

        os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
        try:
            import botocore.exceptions  # noqa: PLC0415
        except ImportError:
            raise RuntimeError(
                "boto3/botocore is required for S3 storage but is not installed. "
                "Install it with: pip install boto3"
            ) from None

        try:
            self._client().download_file(
                Bucket=self._bucket,
                Key=key.lstrip("/"),
                Filename=local_path,
            )
        except botocore.exceptions.ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("NoSuchKey", "404"):
                raise FileNotFoundError(
                    f"Storage object not found: {self._full_uri(key)}"
                ) from exc
            raise
        return local_path

    def open_read(self, key: str) -> BinaryIO:
        """Return a streaming binary reader for the S3 object at *key*.

        The response body is wrapped in a ``BytesIO`` buffer so it can be
        used as a regular file-like object after the HTTP connection closes.

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
        """Return all keys in *bucket* that start with *prefix*."""
        client = self._client()
        paginator = client.get_paginator("list_objects_v2")
        keys: list[str] = []
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
        return sorted(keys)

    def exists(self, key: str) -> bool:
        """Return ``True`` if ``s3://<bucket>/<key>`` exists."""
        try:
            import botocore.exceptions  # noqa: PLC0415
        except ImportError:
            raise RuntimeError(
                "boto3/botocore is required for S3 storage but is not installed. "
                "Install it with: pip install boto3"
            ) from None

        try:
            self._client().head_object(Bucket=self._bucket, Key=key.lstrip("/"))
            return True
        except botocore.exceptions.ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("NoSuchKey", "404", "403"):
                return False
            raise
