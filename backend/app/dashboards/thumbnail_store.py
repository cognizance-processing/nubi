"""Durable storage for rendered board thumbnails.

Rendering a board thumbnail is expensive — it runs every widget's query and
shells out to Node twice, ~8s for a real board. The route used to keep results
in a process-local dict, which meant a restart or a second worker paid that cost
again, and a gallery of cards could stall for a minute after every deploy.

This module puts the render in object storage instead, so it survives restarts
and is shared by every worker. Configure with::

    NUBI_THUMBNAIL_STORAGE_URI=s3://nubi/          # enables persistence
    NUBI_THUMBNAIL_S3_ENDPOINT=http://minio:9000   # S3-compatible only (MinIO, R2)
    NUBI_THUMBNAIL_S3_ACCESS_KEY / _SECRET_KEY     # else the default cred chain
    NUBI_THUMBNAIL_S3_REGION                       # default us-east-1

Unset ``NUBI_THUMBNAIL_STORAGE_URI`` and every function here becomes a no-op, so
the caller falls back to rendering on demand exactly as before. Any storage
error is swallowed for the same reason: a thumbnail is decorative, and a broken
bucket must never take down a dashboard list.

Keys are CONTENT-ADDRESSED::

    boards/{board_id}/{spec_hash}-{theme}-{policy_fp}.svg

so each distinct version of a board is its own immutable object. Overwriting one
fixed key per board would be simpler but reintroduces cache invalidation — the
stored bytes would change under a URL that callers and CDNs were told to cache.
An immutable key can be cached forever, and stale versions are pruned explicitly
by :func:`prune_other_versions` once a newer one lands.

SECURITY — why ``policy_fp`` is in the key: a thumbnail is *rendered data*.
Serving one viewer a picture rendered under another viewer's RLS policies would
leak rows through an image, which no query-level check would catch. The route
computes the same fingerprint for its in-process cache; see the comment above
``_THUMB_CACHE`` in ``app/routes/export_share.py``.
"""

from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger(__name__)

_URI_ENV = "NUBI_THUMBNAIL_STORAGE_URI"

# Object keys are built from a board id and hex digests, but board_id arrives
# from the URL — keep anything path-ish out of the key rather than trusting it.
_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9_.-]")
_DOT_RUN = re.compile(r"\.{2,}")


def is_enabled() -> bool:
    """True when a storage URI is configured (read live, so tests can monkeypatch)."""
    return bool(os.environ.get(_URI_ENV, "").strip())


def _storage_uri() -> str:
    return os.environ.get(_URI_ENV, "").strip()


def _creds() -> dict | None:
    """S3 credentials from env, or None to use boto3's default chain."""
    endpoint = os.environ.get("NUBI_THUMBNAIL_S3_ENDPOINT", "").strip()
    access = os.environ.get("NUBI_THUMBNAIL_S3_ACCESS_KEY", "").strip()
    secret = os.environ.get("NUBI_THUMBNAIL_S3_SECRET_KEY", "").strip()
    region = os.environ.get("NUBI_THUMBNAIL_S3_REGION", "us-east-1").strip()

    creds: dict = {}
    if access and secret:
        creds["aws_access_key_id"] = access
        creds["aws_secret_access_key"] = secret
    if region:
        creds["region_name"] = region
    if endpoint:
        # S3-compatible backends (MinIO, Cloudflare R2) are the whole reason the
        # storage client exposes endpoint_url.
        creds["endpoint_url"] = endpoint
    return creds or None


def _client():
    """Return a StorageClient, or None if storage is off or unusable."""
    if not is_enabled():
        return None
    try:
        from app.storage.base import get_storage_client  # noqa: PLC0415

        return get_storage_client(_storage_uri(), _creds())
    except Exception as exc:  # noqa: BLE001 - decorative feature, never fatal
        logger.warning("thumbnail storage unavailable (%s): %s", _storage_uri(), exc)
        return None


def _safe(value: str) -> str:
    """Reduce *value* to a single, separator-free key segment.

    Replacing the separator alone would still leave ``..`` intact. S3 keys are a
    flat namespace so that could not traverse anywhere, but some backends and
    every local-filesystem mirror treat a key as a path — collapse dot runs so
    the segment is inert whatever sits behind the StorageClient.
    """
    cleaned = _SAFE_SEGMENT.sub("_", str(value))
    return _DOT_RUN.sub("_", cleaned)


def object_key(board_id: str, spec_hash: str, theme: str, policy_fp: str) -> str:
    """Content-addressed key for one rendered version of a board."""
    safe_board = _safe(board_id)
    safe_hash = _safe(spec_hash)
    safe_theme = "dark" if str(theme).strip().lower() == "dark" else "light"
    safe_fp = _safe(policy_fp)[:32] or "none"
    return f"boards/{safe_board}/{safe_hash}-{safe_theme}-{safe_fp}.svg"


def load(board_id: str, spec_hash: str, theme: str, policy_fp: str) -> str | None:
    """Return a previously stored SVG, or None on any miss/failure."""
    client = _client()
    if client is None:
        return None
    key = object_key(board_id, spec_hash, theme, policy_fp)
    try:
        return client.download_bytes(key).decode("utf-8")
    except Exception:  # noqa: BLE001 - a miss and an outage are the same to the caller
        return None


def save(board_id: str, spec_hash: str, theme: str, policy_fp: str, svg: str) -> str | None:
    """Store *svg*. Returns the storage URI, or None if storage is off/failed."""
    client = _client()
    if client is None:
        return None
    key = object_key(board_id, spec_hash, theme, policy_fp)
    try:
        uri = client.upload_bytes(svg.encode("utf-8"), key)
        logger.debug("stored board thumbnail %s", uri)
        return uri
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not store thumbnail for board %s: %s", board_id, exc)
        return None


def prune_other_versions(board_id: str, keep_keys: set[str]) -> int:
    """Delete this board's stored thumbnails except *keep_keys*.

    Content-addressed keys accumulate one object per edit, so the current
    version is written first and superseded ones are dropped after — a board
    keeps only its live renders (one per theme) plus whatever a concurrent
    writer just added.

    Returns the number of objects deleted (0 if storage is off or errored).
    """
    client = _client()
    if client is None:
        return 0
    safe_board = _safe(board_id)
    try:
        existing = client.list(f"boards/{safe_board}/")
    except Exception:  # noqa: BLE001
        return 0

    deleted = 0
    for key in existing:
        if key in keep_keys:
            continue
        try:
            client.delete(key)
            deleted += 1
        except Exception:  # noqa: BLE001 - best effort; a leftover object is harmless
            continue
    return deleted
