import os
from google.cloud import storage as gcs_storage
from . import StorageProvider

GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME", "")


class GCSStorageProvider(StorageProvider):
    def __init__(self):
        self._client = gcs_storage.Client()
        self._bucket_name = GCS_BUCKET_NAME

    def _bucket(self, bucket: str):
        name = bucket if bucket else self._bucket_name
        return self._client.bucket(name)

    async def upload(self, bucket: str, path: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        blob = self._bucket(bucket).blob(path)
        blob.upload_from_string(data, content_type=content_type)
        return path

    async def download(self, bucket: str, path: str) -> bytes:
        blob = self._bucket(bucket).blob(path)
        return blob.download_as_bytes()

    async def delete(self, bucket: str, path: str) -> None:
        blob = self._bucket(bucket).blob(path)
        blob.delete()
