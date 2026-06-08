"""Asset-serving configuration for Nubi.

Controls how stored avatar/asset bytes are exposed publicly.

Environment variables
---------------------
ASSET_SERVE_MODE
    ``local``  (default) — assets are served directly by the Nubi API at
    ``/api/v1/assets/avatars/<key>``.  Works out-of-the-box with the
    ``file://`` storage backend and requires no external accounts.

    ``bunny``  — assets are stored in a Bunny.net storage zone and served
    via a Bunny pull-zone CDN URL.  Requires the three ``BUNNY_*`` variables
    below.

BUNNY_STORAGE_ZONE
    Name of the Bunny.net storage zone (e.g. ``"nubi-avatars"``).
    Required when ``ASSET_SERVE_MODE=bunny``.

BUNNY_STORAGE_API_KEY
    Bunny.net storage zone API key (the FTP / HTTP API password).
    Required when ``ASSET_SERVE_MODE=bunny``.

BUNNY_PULL_ZONE_URL
    Base URL of the Bunny pull-zone CDN (e.g.
    ``"https://nubi-avatars.b-cdn.net"``).  Required when
    ``ASSET_SERVE_MODE=bunny``.
"""

from __future__ import annotations

import os


def get_asset_serve_mode() -> str:
    """Return the configured asset-serving mode (``'local'`` or ``'bunny'``)."""
    return os.getenv("ASSET_SERVE_MODE", "local").lower().strip()


def get_bunny_storage_zone() -> str:
    """Return the Bunny storage zone name."""
    return os.getenv("BUNNY_STORAGE_ZONE", "")


def get_bunny_storage_api_key() -> str:
    """Return the Bunny storage zone API key."""
    return os.getenv("BUNNY_STORAGE_API_KEY", "")


def get_bunny_pull_zone_url() -> str:
    """Return the Bunny pull-zone CDN base URL (no trailing slash)."""
    return os.getenv("BUNNY_PULL_ZONE_URL", "").rstrip("/")


def asset_url(key: str) -> str:
    """Build the publicly accessible URL for a stored asset *key*.

    Parameters
    ----------
    key:
        The relative storage key for the asset (e.g.
        ``"avatars/user/abc123.jpg"``).

    Returns
    -------
    str
        A fully-qualified URL in Bunny mode, or a root-relative API path
        in local mode (``/api/v1/assets/<key>``).  The local path is
        resolved by the frontend against the API origin so it works for
        both same-origin and explicit ``VITE_API_URL`` configurations.
    """
    mode = get_asset_serve_mode()
    if mode == "bunny":
        pull_zone = get_bunny_pull_zone_url()
        clean_key = key.lstrip("/")
        return f"{pull_zone}/{clean_key}"

    # Default: local mode — served by GET /api/v1/assets/<key>
    clean_key = key.lstrip("/")
    return f"/api/v1/assets/{clean_key}"
