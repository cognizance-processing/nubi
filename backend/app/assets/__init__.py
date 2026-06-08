"""Avatar / static-asset hosting for Nubi.

Public API
----------
``asset_url(key)``
    Build the public URL for a stored asset key.  Returns a local API path
    (``/api/v1/assets/avatars/<key>``) when ``ASSET_SERVE_MODE=local`` (default),
    or the Bunny pull-zone URL when ``ASSET_SERVE_MODE=bunny``.

``ingest_avatar_from_url(src_url, kind, owner_id)``
    Download an external avatar URL (e.g. Google profile picture), store it
    via ``app.storage``, and return the served URL on our own domain.

Import example::

    from app.assets import ingest_avatar_from_url, asset_url
"""

from __future__ import annotations

from app.assets.config import asset_url
from app.assets.service import ingest_avatar_from_url

__all__ = [
    "asset_url",
    "ingest_avatar_from_url",
]
