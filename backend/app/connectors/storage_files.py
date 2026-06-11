"""File interface over the ``app.storage`` clients (ingestion design §2).

Rather than write new object-storage clients, this adapts the EXISTING
``StorageClient`` backends (S3 / GCS / Azure / local) to the file-connector
contract (:class:`FileConnectorMixin`).  A storage-backed connector
(``duckdb_storage``) is therefore BOTH SQL-queryable (via DuckDB httpfs) and
file-capable (via this adapter) — the design's "a bucket can be both".

``StorageFileSupport`` is a plain mixin holding a ``StorageClient`` + a base
prefix; ``DuckDBStorageConnector`` composes it so the same object answers
``execute()`` (query) and ``list_files()``/``open()`` (file) calls.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, BinaryIO

from app.connectors.base import FileStat
from app.connectors.file_support import finalize, split_pattern

if TYPE_CHECKING:
    from app.storage.base import StorageClient


class StorageFileSupport:
    """Adapt a :class:`~app.storage.base.StorageClient` to the file interface.

    Parameters
    ----------
    client:
        A constructed storage client (``LocalStorageClient`` / ``S3StorageClient``
        / …).  REUSED as-is — no new client is created here.
    base_prefix:
        Optional key prefix prepended to every path (e.g. an org / dataset
        folder).  ``list_files`` returns paths *relative* to this prefix so the
        connector's path namespace is stable regardless of the backend layout.
    """

    def __init__(self, client: "StorageClient", base_prefix: str = "") -> None:
        self._storage = client
        self._base_prefix = (base_prefix or "").strip("/")

    # ------------------------------------------------------------------
    # Key <-> path mapping
    # ------------------------------------------------------------------

    def _to_key(self, path: str) -> str:
        """Map a connector-relative *path* to a backend key (apply base prefix)."""
        path = (path or "").lstrip("/")
        if self._base_prefix:
            return f"{self._base_prefix}/{path}"
        return path

    def _to_path(self, key: str) -> str:
        """Map a backend *key* back to a connector-relative path."""
        if self._base_prefix and key.startswith(self._base_prefix + "/"):
            return key[len(self._base_prefix) + 1:]
        return key

    # ------------------------------------------------------------------
    # FileConnectorMixin
    # ------------------------------------------------------------------

    def list_files(self, pattern: str, since: datetime | None = None) -> list[FileStat]:
        """List objects matching *pattern* (newer than *since*) via the client.

        Uses the literal prefix of *pattern* as the storage ``list(prefix=…)``
        filter (cheap server-side narrowing), stats each key for size/mtime/etag
        (via the client's optional ``stat``), then applies glob + watermark
        filtering and lexicographic sort through the shared helper.
        """
        prefix, _glob = split_pattern(pattern)
        list_prefix = self._to_key(prefix) if prefix else self._base_prefix
        keys = self._storage.list(prefix=list_prefix or "")

        stats: list[FileStat] = []
        for key in keys:
            rel = self._to_path(key)
            meta = self._storage.stat(key)
            if meta is not None:
                stats.append(
                    FileStat(path=rel, size=meta.size, mtime=meta.mtime, etag=meta.etag)
                )
            else:
                # Backend without stat support — still listable/openable.
                stats.append(FileStat(path=rel, size=0, mtime=None, etag=None))
        return finalize(stats, pattern, since)

    def open(self, path: str) -> BinaryIO:
        """Open the object at *path* for streaming read (delegates to the client)."""
        return self._storage.open_read(self._to_key(path))

    def move(self, src: str, dst: str) -> None:
        """Move *src* to *dst* (copy-then-delete; object stores have no rename).

        Implemented as upload-of-download + delete via the client primitives so
        it works uniformly across every backend.  Used by ``post_action:
        move:<dir>`` to archive ingested files.
        """
        data = self._storage.download_bytes(self._to_key(src))
        self._storage.upload_bytes(data, self._to_key(dst))
        self._storage.delete(self._to_key(src))

    def delete(self, path: str) -> None:
        """Delete the object at *path* (``post_action: delete``)."""
        self._storage.delete(self._to_key(path))
