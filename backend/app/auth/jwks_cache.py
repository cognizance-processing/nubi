"""JWKS fetch-and-cache layer.

Fetches JSON Web Key Sets from remote ``jwks_uri`` endpoints and caches the raw
JSON dicts in-process with a configurable TTL.  ``httpx`` is imported lazily
inside the fetch function so that the module can be imported in environments
where ``httpx`` is not installed (it is listed in requirements.txt for the app
but kept lazy here to keep import side-effects small and to simplify mocking in
tests).

Public API
----------
get_jwks(jwks_uri, ttl_seconds) -> dict
    Fetch (or return cached) the JWKS from *jwks_uri*.

invalidate(jwks_uri)
    Evict the cached entry for *jwks_uri* (useful after a key-rotation event).

clear_cache()
    Evict all cached entries (test helper).
"""

from __future__ import annotations

import time
from typing import Any


# ---------------------------------------------------------------------------
# In-memory cache: jwks_uri -> (fetched_at_monotonic, jwks_dict)
# ---------------------------------------------------------------------------

_cache: dict[str, tuple[float, dict[str, Any]]] = {}

DEFAULT_TTL_SECONDS: int = 300  # 5 minutes


def get_jwks(
    jwks_uri: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> dict[str, Any]:
    """Return the JWKS from *jwks_uri*, using the in-memory cache.

    The first call for a given *jwks_uri* fetches the document over HTTPS (via
    ``httpx``).  Subsequent calls within *ttl_seconds* return the cached copy.
    After the TTL expires the document is re-fetched transparently.

    Parameters
    ----------
    jwks_uri:
        The full URL of the JWKS endpoint (e.g.
        ``"https://example.com/.well-known/jwks.json"``).
    ttl_seconds:
        How long to cache the fetched JWKS before re-fetching.

    Returns
    -------
    dict
        The parsed JWKS JSON object (``{"keys": [...]}``)

    Raises
    ------
    AppError("invalid_token", 401)
        If the JWKS cannot be fetched or parsed.
    """
    from app.errors import AppError  # local import avoids circular deps at module load

    # SECURITY: restrict jwks_uri to HTTPS only — block SSRF to internal HTTP
    # services, metadata endpoints (169.254.169.254), and local file paths.
    # http:// and file:// are never valid for a production JWKS endpoint.
    if not jwks_uri.lower().startswith("https://"):
        raise AppError(
            "invalid_token",
            "Token is invalid or has expired.",
            401,
        )

    now = time.monotonic()
    entry = _cache.get(jwks_uri)
    if entry is not None:
        fetched_at, jwks = entry
        if now - fetched_at < ttl_seconds:
            return jwks

    # Cache miss or expired — fetch from network.
    try:
        import httpx  # lazy import: keeps startup fast and simplifies test mocking

        response = httpx.get(jwks_uri, timeout=10.0)
        response.raise_for_status()
        jwks: dict[str, Any] = response.json()
    except Exception:
        raise AppError(
            "invalid_token",
            "Token is invalid or has expired.",
            401,
        )

    if not isinstance(jwks, dict) or "keys" not in jwks:
        raise AppError(
            "invalid_token",
            "Token is invalid or has expired.",
            401,
        )

    _cache[jwks_uri] = (now, jwks)
    return jwks


def invalidate(jwks_uri: str) -> None:
    """Remove the cached JWKS entry for *jwks_uri* (idempotent)."""
    _cache.pop(jwks_uri, None)


def clear_cache() -> None:
    """Evict all cached JWKS entries.  Call this in test teardown."""
    _cache.clear()
