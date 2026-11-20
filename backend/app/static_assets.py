"""Static file serving that keeps very large assets deliverable.

Some hosting front ends refuse a response whose declared length exceeds a
fixed cap (32 MiB is a common one), and the in-browser query engine's
WebAssembly module is larger than that.  ``LargeAssetStaticFiles`` behaves
like Starlette's ``StaticFiles`` except for files above ``LARGE_ASSET_BYTES``:

* a client that accepts gzip gets a compressed copy (built once per file
  version, then served from memory), which is several times smaller;
* any other client gets the file streamed without a declared length.

Every file under ``/assets`` also gets:

* ``Cache-Control: public, max-age=31536000, immutable`` — Vite puts a content
  hash in every asset filename, so a URL never changes meaning. Without this,
  browsers re-validated each chunk on every visit (40% of asset requests in
  prod were 304s, each a round trip before the app could start);
* the build's pre-compressed ``<file>.gz`` sibling when the client accepts
  gzip. The image build writes these (see ``Dockerfile``), so no worker spends
  memory or CPU compressing; without one, the file is served as before.
"""

from __future__ import annotations

import gzip
import mimetypes
import os
import threading
from typing import Iterator

from starlette.concurrency import run_in_threadpool
from starlette.responses import Response, StreamingResponse
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

LARGE_ASSET_BYTES = 16 * 1024 * 1024
_CHUNK = 256 * 1024

_cache: dict[str, tuple[str, bytes]] = {}
_cache_lock = threading.Lock()


def _etag(stat: os.stat_result) -> str:
    return f'"{int(stat.st_mtime_ns):x}-{stat.st_size:x}"'


def _compressed(path: str, etag: str) -> bytes:
    with _cache_lock:
        hit = _cache.get(path)
        if hit and hit[0] == etag:
            return hit[1]
        with open(path, "rb") as fh:
            body = gzip.compress(fh.read(), compresslevel=6)
        _cache[path] = (etag, body)
        return body


def _read_chunks(path: str) -> Iterator[bytes]:
    with open(path, "rb") as fh:
        while chunk := fh.read(_CHUNK):
            yield chunk


def _header(scope: Scope, name: bytes) -> str:
    for key, value in scope.get("headers", []):
        if key == name:
            return value.decode("latin-1")
    return ""


IMMUTABLE = "public, max-age=31536000, immutable"


class LargeAssetStaticFiles(StaticFiles):
    def file_response(self, full_path, stat_result, scope: Scope, status_code: int = 200) -> Response:
        response = self._file_response(full_path, stat_result, scope, status_code)
        if 200 <= response.status_code < 400:
            response.headers["Cache-Control"] = IMMUTABLE
        return response

    def _file_response(self, full_path, stat_result, scope: Scope, status_code: int) -> Response:
        if scope.get("method") == "GET" and not _header(scope, b"range"):
            pre = _precompressed(full_path, stat_result, scope)
            if pre is not None:
                return pre
        if stat_result.st_size <= LARGE_ASSET_BYTES or scope.get("method") != "GET":
            return super().file_response(full_path, stat_result, scope, status_code)
        if _header(scope, b"range"):
            return super().file_response(full_path, stat_result, scope, status_code)

        path = os.fspath(full_path)
        etag = _etag(stat_result)
        media_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        headers = {"ETag": etag, "Vary": "Accept-Encoding"}
        if etag in _header(scope, b"if-none-match"):
            return Response(status_code=304, headers=headers)

        if "gzip" in _header(scope, b"accept-encoding").lower():
            return _GzipAsset(path, etag, media_type, headers)
        return StreamingResponse(_read_chunks(path), media_type=media_type, headers=headers)


def _precompressed(full_path, stat_result: os.stat_result, scope: Scope) -> Response | None:
    """Serve ``<file>.gz`` from the build, or ``None`` to fall back."""
    if "gzip" not in _header(scope, b"accept-encoding").lower():
        return None
    path = os.fspath(full_path)
    gz_path = path + ".gz"
    try:
        gz_stat = os.stat(gz_path)
    except OSError:
        return None
    # Identity of the ORIGINAL file, so the ETag is the same whichever encoding
    # a client got, and a rebuilt file invalidates it.
    etag = _etag(stat_result)
    headers = {
        "ETag": etag,
        "Vary": "Accept-Encoding",
        "Content-Encoding": "gzip",
        "Content-Length": str(gz_stat.st_size),
    }
    if etag in _header(scope, b"if-none-match"):
        return Response(status_code=304, headers={"ETag": etag, "Vary": "Accept-Encoding"})
    media_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
    return StreamingResponse(_read_chunks(gz_path), media_type=media_type, headers=headers)


class _GzipAsset(Response):
    """Compresses off the event loop on first use, then replies from memory."""

    def __init__(self, path: str, etag: str, media_type: str, headers: dict[str, str]) -> None:
        super().__init__(content=b"", media_type=media_type, headers=headers)
        self._path = path
        self._etag = etag

    async def __call__(self, scope, receive, send) -> None:
        body = await run_in_threadpool(_compressed, self._path, self._etag)
        self.body = body
        self.headers["Content-Encoding"] = "gzip"
        self.headers["Content-Length"] = str(len(body))
        await super().__call__(scope, receive, send)
