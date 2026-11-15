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

#: jwks_uri -> monotonic time of the last failed fetch (negative cache).
_failures: dict[str, float] = {}
_NEGATIVE_TTL_SECONDS = 60.0
_FETCH_TIMEOUT_SECONDS = 5.0
_MAX_JWKS_BYTES = 256 * 1024

# Imported at module level so tests can substitute them.
from app.connectors.http_json import _fetch_pinned  # noqa: E402
from app.connectors.ssrf import resolve_and_pin  # noqa: E402

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

    invalid = AppError("invalid_token", "Token is invalid or has expired.", 401)

    # A recent failure is remembered, so a stream of tokens naming an
    # unreachable key set costs one outbound attempt, not one per request.
    failed_at = _failures.get(jwks_uri)
    if failed_at is not None and now - failed_at < _NEGATIVE_TTL_SECONDS:
        raise invalid

    # Cache miss or expired — fetch from the network, through a target that
    # has been resolved once, checked (no loopback / private / link-local /
    # metadata address) and pinned, so the host cannot be re-pointed between
    # the check and the connection.
    try:
        import httpx  # lazy import: keeps startup fast and simplifies test mocking

        pinned = resolve_and_pin(jwks_uri)
        response = _fetch_pinned(
            httpx, "GET", pinned, {"Accept": "application/json"},
            timeout=httpx.Timeout(_FETCH_TIMEOUT_SECONDS),
        )
        response.raise_for_status()
        if len(getattr(response, "content", b"") or b"") > _MAX_JWKS_BYTES:
            raise ValueError("JWKS document too large")
        jwks: dict[str, Any] = response.json()
        if not isinstance(jwks, dict) or not isinstance(jwks.get("keys"), list):
            raise ValueError("not a JWKS document")
    except Exception:
        _failures[jwks_uri] = now
        raise invalid from None

    _failures.pop(jwks_uri, None)
    _cache[jwks_uri] = (now, jwks)
    return jwks


def get_cached(jwks_uri: str, ttl_seconds: float = DEFAULT_TTL_SECONDS) -> dict[str, Any] | None:
    """The cached key set for *jwks_uri* if it is still fresh — never fetches."""
    entry = _cache.get(jwks_uri)
    if entry is None:
        return None
    fetched_at, jwks = entry
    return jwks if time.monotonic() - fetched_at < ttl_seconds else None


async def get_jwks_async(jwks_uri: str, ttl_seconds: float = DEFAULT_TTL_SECONDS) -> dict[str, Any]:
    """:func:`get_jwks` for async callers: a cache hit returns immediately and a
    network fetch runs in a worker thread, so the event loop is never blocked."""
    import asyncio  # noqa: PLC0415

    cached = get_cached(jwks_uri, ttl_seconds)
    if cached is not None:
        return cached
    return await asyncio.to_thread(get_jwks, jwks_uri, ttl_seconds)


def invalidate(jwks_uri: str) -> None:
    """Remove the cached JWKS entry for *jwks_uri* (idempotent)."""
    _cache.pop(jwks_uri, None)


def clear_cache() -> None:
    """Evict all cached JWKS entries.  Call this in test teardown."""
    _cache.clear()
    _failures.clear()
