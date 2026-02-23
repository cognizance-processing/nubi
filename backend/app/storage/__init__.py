import os
from abc import ABC, abstractmethod


class StorageProvider(ABC):
    @abstractmethod
    async def upload(self, bucket: str, path: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        """Upload a file and return the storage path."""
        ...

    @abstractmethod
    async def download(self, bucket: str, path: str) -> bytes:
        """Download a file and return its bytes."""
        ...

    @abstractmethod
    async def delete(self, bucket: str, path: str) -> None:
        """Delete a file."""
        ...


def get_storage_provider() -> StorageProvider:
    provider = os.getenv("STORAGE_PROVIDER", "local").lower()
    if provider == "gcs":
        from .gcs import GCSStorageProvider
        return GCSStorageProvider()
    else:
        from .local import LocalStorageProvider
        return LocalStorageProvider()
