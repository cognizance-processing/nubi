"""Local image storage for dashboard branding (logos, header images, …).

Backs the ``upload_image`` AI/MCP tool (``app.ai.tools``) and the
``GET /images/{image_id}`` serving route (``app.routes.images``).

Storage backend
----------------
Uses :class:`~app.storage.local.LocalStorageClient` (``file://`` — pure
stdlib, no credentials) rather than the S3/MinIO-backed
``NUBI_THUMBNAIL_STORAGE_URI`` this dev environment already declares, because
that MinIO instance is not actually running here. The root directory is
``NUBI_IMAGE_STORAGE_DIR`` (default ``/tmp/nubi-dashboard-images``) — same
"local dev fallback" pattern as ``ARTIFACTS_BASE_URI``'s ``/tmp/nubi-artifacts``
default.

Each image is stored as two objects under its ``image_id`` (a random hex
token, so ids are unguessable — there is no per-org access control on the
serving route, see its docstring):

- ``<image_id>``      — raw bytes
- ``<image_id>.ct``   — the image's content-type, as plain text

The ``.ct`` sidecar avoids needing a DB row just to remember one MIME string.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

from app.errors import AppError

#: Content-types this store will accept. Deliberately excludes
#: ``image/svg+xml`` — an SVG can carry a ``<script>``, which is a stored-XSS
#: vector once served back and rendered in an ``<img>``/inline context.
ALLOWED_CONTENT_TYPES: frozenset[str] = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/webp"}
)

#: Hard cap so an oversized upload/fetch can't exhaust local disk.
MAX_IMAGE_BYTES: int = 8 * 1024 * 1024  # 8 MiB


def _root_dir() -> str:
    return os.environ.get("NUBI_IMAGE_STORAGE_DIR") or "/tmp/nubi-dashboard-images"


def _storage() -> Any:
    from app.storage.local import LocalStorageClient  # noqa: PLC0415

    return LocalStorageClient(root=_root_dir())


def sniff_content_type(data: bytes) -> str | None:
    """Best-effort magic-byte sniff for the content-types in :data:`ALLOWED_CONTENT_TYPES`.

    Used when a caller uploads raw bytes without stating a content-type (e.g.
    a base64 payload from a local file with no reliable extension). Returns
    ``None`` if the bytes don't match a known signature — the caller must
    then supply ``content_type`` explicitly.
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def save_image_bytes(data: bytes, content_type: str) -> dict[str, Any]:
    """Validate and store *data*, returning ``{id, content_type, size}``.

    Raises
    ------
    AppError("invalid_image", 400)
        Unsupported content-type or the payload exceeds :data:`MAX_IMAGE_BYTES`.
    """
    content_type = (content_type or "").split(";", 1)[0].strip().lower()
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise AppError(
            "invalid_image",
            f"Unsupported image content-type {content_type!r}. "
            f"Allowed: {sorted(ALLOWED_CONTENT_TYPES)}.",
            400,
        )
    if len(data) > MAX_IMAGE_BYTES:
        raise AppError(
            "invalid_image",
            f"Image is {len(data)} bytes, exceeding the {MAX_IMAGE_BYTES}-byte limit.",
            400,
        )
    if not data:
        raise AppError("invalid_image", "Image data is empty.", 400)

    image_id = uuid.uuid4().hex
    store = _storage()
    store.upload_bytes(data, image_id)
    store.upload_bytes(content_type.encode("ascii"), f"{image_id}.ct")
    return {"id": image_id, "content_type": content_type, "size": len(data)}


def fetch_image_from_url(url: str) -> dict[str, Any]:
    """Fetch an image from *url* (SSRF-guarded, size-capped) and store it.

    Uses the same DNS-rebind-safe pinned-fetch path as the ``http_json``
    connector (``resolve_and_pin`` + the pinned ``httpx`` request) — the host
    is resolved and validated exactly once, then the connection is pinned to
    that checked IP literal so the target can't rebind to an internal address
    between the check and the socket connect.

    Returns the same shape as :func:`save_image_bytes`.

    Raises
    ------
    AppError("ssrf_blocked", 400)
        Via ``resolve_and_pin`` — disallowed scheme/host or a forbidden
        (loopback/private/link-local/cloud-metadata) resolved address.
    AppError("invalid_image", 400)
        Unsupported ``Content-Type``, oversized body, or any fetch failure.
    """
    import httpx  # noqa: PLC0415

    from app.connectors.http_json import _fetch_pinned  # noqa: PLC0415
    from app.connectors.ssrf import resolve_and_pin  # noqa: PLC0415

    pinned = resolve_and_pin(url)
    timeout = httpx.Timeout(30.0)
    try:
        response = _fetch_pinned(httpx, "GET", pinned, {}, timeout=timeout)
        response.raise_for_status()
    except (httpx.TimeoutException, httpx.RequestError, httpx.HTTPStatusError) as exc:
        raise AppError("invalid_image", f"Failed to fetch image at {url!r}: {exc}", 400) from exc

    content_type = (response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    data = response.content
    if len(data) > MAX_IMAGE_BYTES:
        raise AppError(
            "invalid_image",
            f"Fetched image is {len(data)} bytes, exceeding the {MAX_IMAGE_BYTES}-byte limit.",
            400,
        )
    return save_image_bytes(data, content_type)


def load_image(image_id: str) -> tuple[bytes, str]:
    """Return ``(bytes, content_type)`` for a previously stored *image_id*.

    Raises
    ------
    FileNotFoundError
        If *image_id* was never stored (or was deleted).
    """
    store = _storage()
    data = store.download_bytes(image_id)
    content_type = store.download_bytes(f"{image_id}.ct").decode("ascii").strip()
    return data, content_type
