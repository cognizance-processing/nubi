import os
from pathlib import Path
from . import StorageProvider

LOCAL_STORAGE_DIR = os.getenv("LOCAL_STORAGE_DIR", os.path.join(os.getcwd(), "storage"))


class LocalStorageProvider(StorageProvider):
    def __init__(self):
        self._root = Path(LOCAL_STORAGE_DIR)

    def _path(self, bucket: str, path: str) -> Path:
        full = self._root / bucket / path
        full.parent.mkdir(parents=True, exist_ok=True)
        return full

    async def upload(self, bucket: str, path: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        self._path(bucket, path).write_bytes(data)
        return path

    async def download(self, bucket: str, path: str) -> bytes:
        fp = self._path(bucket, path)
        if not fp.exists():
            raise FileNotFoundError(f"File not found: {bucket}/{path}")
        return fp.read_bytes()

    async def delete(self, bucket: str, path: str) -> None:
        fp = self._path(bucket, path)
        if fp.exists():
            fp.unlink()
