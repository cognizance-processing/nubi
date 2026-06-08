"""Avatar ingest service for Nubi.

Downloads an external avatar URL, validates it (size + content-type), stores
it via ``app.storage``, and returns the served URL on our own domain.

For ``ASSET_SERVE_MODE=bunny`` the bytes are uploaded to the Bunny.net storage
zone via their HTTP API (PUT request).  For ``local`` mode the bytes are written
to the configured storage backend (``file://`` by default).
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Literal

from app.assets.config import (
    asset_url,
    get_asset_serve_mode,
    get_bunny_pull_zone_url,
    get_bunny_storage_api_key,
    get_bunny_storage_zone,
)

logger = logging.getLogger(__name__)

# Maximum avatar file size we will download (2 MiB).
_MAX_AVATAR_BYTES = 2 * 1024 * 1024

# Allowed MIME type prefixes for avatars.
_ALLOWED_CONTENT_TYPES = ("image/jpeg", "image/png", "image/gif", "image/webp", "image/svg+xml")

# Extension map from MIME type.
_MIME_TO_EXT: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
}



def _content_type_to_ext(content_type: str) -> str:
    """Map a MIME type to a file extension, defaulting to ``.bin``."""
    ct = content_type.split(";")[0].strip().lower()
    return _MIME_TO_EXT.get(ct, ".bin")


def _derive_key(src_url: str, kind: str, owner_id: str, ext: str) -> str:
    """Derive a stable, collision-resistant storage key for an avatar.

    Uses a short SHA-256 of the source URL so the same external URL always
    maps to the same stored object (idempotent re-ingestion).
    """
    digest = hashlib.sha256(src_url.encode()).hexdigest()[:16]
    return f"avatars/{kind}/{owner_id}/{digest}{ext}"


async def ingest_avatar_from_url(
    src_url: str,
    kind: Literal["user", "org"],
    owner_id: str,
) -> str | None:
    """Download *src_url*, store it, and return the served URL.

    This function is **absent-safe**: it never raises — on any error it logs a
    warning and returns ``None`` so callers can treat it as best-effort.

    Parameters
    ----------
    src_url:
        The external URL to download (e.g. a Google profile-picture URL).
        Must be HTTP or HTTPS.
    kind:
        ``"user"`` or ``"org"`` — used to namespace the storage path.
    owner_id:
        The UUID of the user or org that owns this avatar.

    Returns
    -------
    str | None
        The served URL on our own domain, or ``None`` if ingest failed.
    """
    if not src_url or not src_url.startswith(("http://", "https://")):
        logger.debug("ingest_avatar_from_url: skipping non-HTTP URL %r", src_url)
        return None

    try:
        import httpx  # noqa: PLC0415

        async with httpx.AsyncClient(follow_redirects=True, timeout=10) as http:
            response = await http.get(src_url)

        if response.status_code != 200:
            logger.warning(
                "ingest_avatar_from_url: HTTP %d fetching %r",
                response.status_code,
                src_url,
            )
            return None

        data = response.content
        if len(data) > _MAX_AVATAR_BYTES:
            logger.warning(
                "ingest_avatar_from_url: avatar too large (%d bytes) from %r",
                len(data),
                src_url,
            )
            return None

        content_type = response.headers.get("content-type", "")
        ct_lower = content_type.split(";")[0].strip().lower()
        if not any(ct_lower.startswith(allowed) for allowed in _ALLOWED_CONTENT_TYPES):
            logger.warning(
                "ingest_avatar_from_url: disallowed content-type %r from %r",
                content_type,
                src_url,
            )
            return None

        ext = _content_type_to_ext(content_type)
        key = _derive_key(src_url, kind, owner_id, ext)

        mode = get_asset_serve_mode()

        if mode == "bunny":
            served = await _store_bunny(data, key, content_type)
        else:
            served = await _store_local(data, key)

        return served

    except Exception as exc:  # noqa: BLE001 — best-effort, never break callers
        logger.warning("ingest_avatar_from_url: failed for %r: %s", src_url, exc)
        return None


async def _store_local(data: bytes, key: str) -> str:
    """Store *data* under *key* in the local storage backend.

    Uses ``asyncio.to_thread`` so the blocking filesystem I/O does not block
    the event loop.  Creates the storage root directory automatically if it
    does not yet exist.

    Returns
    -------
    str
        The served URL for the stored asset.
    """
    import asyncio  # noqa: PLC0415

    from app.storage.local import LocalStorageClient  # noqa: PLC0415

    default_root = os.path.join(os.path.dirname(__file__), "..", "..", "storage_data")
    root = os.path.abspath(os.getenv("LOCAL_STORAGE_ROOT", default_root))
    client = LocalStorageClient(root=root)

    def _write() -> str:
        client.upload_bytes(data, key)
        return asset_url(key)

    return await asyncio.to_thread(_write)


async def _store_bunny(data: bytes, key: str, content_type: str) -> str:
    """Upload *data* to the Bunny.net storage zone and return the CDN URL.

    Uses the Bunny HTTP API::

        PUT https://storage.bunnycdn.com/<zone>/<key>
        AccessKey: <api_key>
        Content-Type: <content_type>

    Returns
    -------
    str
        The Bunny pull-zone CDN URL for the uploaded asset.
    """
    import httpx  # noqa: PLC0415

    zone = get_bunny_storage_zone()
    api_key = get_bunny_storage_api_key()
    clean_key = key.lstrip("/")

    put_url = f"https://storage.bunnycdn.com/{zone}/{clean_key}"
    headers = {
        "AccessKey": api_key,
        "Content-Type": content_type or "application/octet-stream",
    }

    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.put(put_url, content=data, headers=headers)
        resp.raise_for_status()

    pull_zone = get_bunny_pull_zone_url()
    return f"{pull_zone}/{clean_key}"
