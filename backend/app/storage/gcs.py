import os
from google.cloud import storage as gcs_storage
from . import StorageProvider

GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME", "")


class GCSStorageProvider(StorageProvider):
    def __init__(self):
        self._client = gcs_storage.Client()
        self._bucket = self._client.bucket(GCS_BUCKET_NAME)

    def _blob_path(self, bucket: str, path: str) -> str:
        return f"{bucket}/{path}" if bucket else path

    async def upload(self, bucket: str, path: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        blob = self._bucket.blob(self._blob_path(bucket, path))
        blob.upload_from_string(data, content_type=content_type)
        return path

    async def download(self, bucket: str, path: str) -> bytes:
        blob = self._bucket.blob(self._blob_path(bucket, path))
        return blob.download_as_bytes()

    async def delete(self, bucket: str, path: str) -> None:
        blob = self._bucket.blob(self._blob_path(bucket, path))
        blob.delete()
