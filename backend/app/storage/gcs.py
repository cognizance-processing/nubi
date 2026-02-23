import os
from . import StorageProvider


class GCSStorageProvider(StorageProvider):
    def __init__(self):
        self._bucket_obj = None

    def _get_bucket(self):
        if self._bucket_obj is None:
            from google.cloud import storage as gcs_storage
            bucket_name = os.getenv("GCS_BUCKET_NAME", "").strip()
            if not bucket_name:
                raise RuntimeError("GCS_BUCKET_NAME environment variable is not set")
            print(f"GCS bucket: {bucket_name!r}")
            client = gcs_storage.Client()
            self._bucket_obj = client.bucket(bucket_name)
        return self._bucket_obj

    def _blob_path(self, bucket: str, path: str) -> str:
        return f"{bucket}/{path}" if bucket else path

    async def upload(self, bucket: str, path: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        blob = self._get_bucket().blob(self._blob_path(bucket, path))
        blob.upload_from_string(data, content_type=content_type)
        return path

    async def download(self, bucket: str, path: str) -> bytes:
        blob = self._get_bucket().blob(self._blob_path(bucket, path))
        return blob.download_as_bytes()

    async def delete(self, bucket: str, path: str) -> None:
        blob = self._get_bucket().blob(self._blob_path(bucket, path))
        blob.delete()
