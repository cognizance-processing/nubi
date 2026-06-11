"""Local filesystem storage backend for Nubi (``file://`` scheme).

Implements the full :class:`~app.storage.base.StorageClient` interface against
the local filesystem so that local-run and CI tests have true parity with
cloud backends — no credentials needed.

URI convention
--------------
``file://<root-dir>/<key>``

The ``bucket`` segment of the URI is used as the **root directory** on the
local filesystem.  For example:

    ``file:///tmp/nubi-data/exports/report.parquet``
     → root = ``/tmp/nubi-data``, key = ``exports/report.parquet``

An empty bucket (i.e. ``file:///path/to/file.txt`` with a double slash after
the scheme separator) uses the absolute path directly; the leading slash is
preserved by treating the root as ``""`` and the key as the absolute path
without its leading slash.

For practical usage, the recommended form is::

    LocalStorageClient(root="/some/absolute/dir")

and then all keys are relative paths under that root.
"""

from __future__ import annotations

import io
import os
import shutil
from typing import BinaryIO

from app.storage.base import StorageClient


class LocalStorageClient(StorageClient):
    """``file://`` storage backend — pure stdlib, no external dependencies.

    Parameters
    ----------
    root:
        The root directory under which all objects are stored.  Created
        automatically on first write if it does not exist.
    """

    #: URI scheme used when constructing full URIs in return values.
    SCHEME = "file"

    def __init__(self, root: str) -> None:
        self._root = root

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _abs(self, key: str) -> str:
        """Return the absolute filesystem path for *key*."""
        # Prevent path traversal
        key = key.lstrip("/")
        return os.path.join(self._root, key)

    def _ensure_parent(self, path: str) -> None:
        """Create parent directories for *path* if they do not exist."""
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)

    def _full_uri(self, key: str) -> str:
        return f"{self.SCHEME}://{self._root}/{key.lstrip('/')}"

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def upload_bytes(self, data: bytes, key: str) -> str:
        """Write *data* to ``<root>/<key>``; create parent dirs as needed."""
        path = self._abs(key)
        self._ensure_parent(path)
        with open(path, "wb") as fh:
            fh.write(data)
        return self._full_uri(key)

    def upload_file(self, local_path: str, key: str) -> str:
        """Copy *local_path* to ``<root>/<key>``; create parent dirs as needed."""
        dest = self._abs(key)
        self._ensure_parent(dest)
        shutil.copy2(local_path, dest)
        return self._full_uri(key)

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def download_bytes(self, key: str) -> bytes:
        """Read and return the contents of ``<root>/<key>`` as bytes.

        Raises
        ------
        FileNotFoundError
            If the file does not exist.
        """
        path = self._abs(key)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Storage object not found: {self._full_uri(key)}")
        with open(path, "rb") as fh:
            return fh.read()

    def download_to_file(self, key: str, local_path: str) -> str:
        """Copy ``<root>/<key>`` to *local_path*; create parent dirs as needed."""
        src = self._abs(key)
        if not os.path.isfile(src):
            raise FileNotFoundError(f"Storage object not found: {self._full_uri(key)}")
        self._ensure_parent(local_path)
        shutil.copy2(src, local_path)
        return local_path

    def open_read(self, key: str) -> BinaryIO:
        """Return an open binary file handle for ``<root>/<key>``.

        The caller must close the handle (or use it as a context manager).

        Raises
        ------
        FileNotFoundError
            If the file does not exist.
        """
        path = self._abs(key)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Storage object not found: {self._full_uri(key)}")
        return open(path, "rb")  # noqa: SIM115  — caller closes

    # ------------------------------------------------------------------
    # Listing / inspection
    # ------------------------------------------------------------------

    def list(self, prefix: str = "") -> list[str]:
        """Return all keys (relative paths) under *root* filtered by *prefix*.

        Returns
        -------
        list[str]
            Sorted list of matching relative keys.  Directories are not
            included — only leaf files.
        """
        if not os.path.isdir(self._root):
            return []

        results: list[str] = []
        for dirpath, _dirnames, filenames in os.walk(self._root):
            for fname in filenames:
                full = os.path.join(dirpath, fname)
                rel = os.path.relpath(full, self._root)
                # Normalise to forward slashes for portability
                rel = rel.replace(os.sep, "/")
                if rel.startswith(prefix):
                    results.append(rel)

        return sorted(results)

    def exists(self, key: str) -> bool:
        """Return ``True`` if ``<root>/<key>`` exists as a file."""
        return os.path.isfile(self._abs(key))

    def stat(self, key: str):
        """Return size/mtime/etag for ``<root>/<key>``, or ``None`` if absent.

        ``mtime`` is the filesystem modification time (UTC); ``etag`` is a
        cheap ``"<size>:<mtime_ns>"`` surrogate since local files have no real
        content tag.
        """
        from datetime import datetime, timezone  # noqa: PLC0415

        from app.storage.base import ObjectStat  # noqa: PLC0415

        path = self._abs(key)
        if not os.path.isfile(path):
            return None
        st = os.stat(path)
        return ObjectStat(
            size=int(st.st_size),
            mtime=datetime.fromtimestamp(st.st_mtime, tz=timezone.utc),
            etag=f"{st.st_size}:{st.st_mtime_ns}",
        )

    def delete(self, key: str) -> None:
        """Delete ``<root>/<key>`` (no-op if it does not exist)."""
        path = self._abs(key)
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
