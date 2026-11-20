"""Request-scoped memo for read-only lookups.

One request often asks the same question several times from layers that do
not know about each other: "is this user in this org?", "what is their
role?", "load this datastore row". Each ask is a database round trip, and with
Postgres in another region every round trip is user-visible latency.

The memo lives for exactly one request (``RequestMemoMiddleware`` opens it),
so nothing here can serve a stale answer to a *later* request. Outside a
request — background jobs, scripts, unit tests that call helpers directly —
there is no memo and every call goes to the loader, unchanged.

Only positive answers are kept unless ``keep_none=True`` (or a ``keep``
predicate says otherwise): a request that adds a membership and then checks it
must see the new row. A failed load is not remembered. Values are handed out
as deep copies, so a caller that mutates its row can never change what the
next caller (in this request, or a later one via the shared layer) sees.

Shared layer (``shared_memoized``)
----------------------------------
A few auth answers are asked by *every* request: "is this token revoked?",
"load this user", "is this user in this org, with what role?". Those may also
be reused across requests on the same worker for ``AUTH_CACHE_TTL_SECONDS``
(30 s by default). That is a deliberate, bounded staleness: a logout, member
removal or role change made on another worker takes effect here within the
window. Only answers that *grant* something are kept ("not a member" is never
cached, so a new member gets in at once), the worker that writes a change
drops its own copy immediately (``forget`` / ``forget_matching``), and a load
that raced an invalidation is not stored. Outside a request there is no shared
cache either: background jobs and scripts always read the database.
"""

from __future__ import annotations

import contextvars
import copy
import time
from typing import Any, Awaitable, Callable, Hashable

_memo: contextvars.ContextVar[dict[Hashable, Any] | None] = contextvars.ContextVar(
    "request_memo", default=None
)


async def memoized(
    key: Hashable,
    loader: Callable[[], Awaitable[Any]],
    keep: Callable[[Any], bool] | None = None,
    *,
    keep_none: bool = False,
) -> Any:
    """Return the request's cached value for *key*, loading it on first use.

    By default only non-``None`` values are remembered (``keep_none=True``
    keeps ``None`` too); a *keep* predicate replaces that rule.
    """
    memo = _memo.get()
    if memo is None:
        return await loader()
    if key in memo:
        return _copy(memo[key])
    value = await loader()
    if keep(value) if keep is not None else (value is not None or keep_none):
        memo[key] = value
    return _copy(value)


def _copy(value: Any) -> Any:
    """A private copy of a remembered value.

    asyncpg ``Record`` rows (and anything else deepcopy refuses) are
    immutable or treated as such, so they are handed out as they are.
    """
    if value is None or isinstance(value, (bool, int, float, str, bytes)):
        return value
    try:
        return copy.deepcopy(value)
    except Exception:  # noqa: BLE001 — e.g. TypeError: cannot pickle Record
        return value


# ── Shared (cross-request, per-worker) layer ───────────────────────────────

_shared: dict[Hashable, tuple[float, Any]] = {}
#: Bumped by every invalidation; a load that started before one is not stored.
_generation = 0
_SHARED_MAX = 20_000


def _shared_ttl() -> float:
    try:
        from app.config import get_settings  # noqa: PLC0415

        return max(0.0, float(get_settings().AUTH_CACHE_TTL_SECONDS))
    except Exception:  # noqa: BLE001 — no settings, no shared cache
        return 0.0


def _shared_get(key: Hashable) -> Any:
    hit = _shared.get(key)
    if hit is None:
        return None
    if hit[0] <= time.monotonic():
        _shared.pop(key, None)
        return None
    return hit[1]


def _shared_put(key: Hashable, value: Any, generation: int) -> None:
    ttl = _shared_ttl()
    if ttl <= 0 or generation != _generation:
        return
    now = time.monotonic()
    if len(_shared) >= _SHARED_MAX:
        for k in [k for k, (exp, _) in _shared.items() if exp <= now]:
            _shared.pop(k, None)
        if len(_shared) >= _SHARED_MAX:
            _shared.clear()
    _shared[key] = (now + ttl, value)


async def shared_memoized(key: Hashable, loader: Callable[[], Awaitable[Any]]) -> Any:
    """Like :func:`memoized`, but also reuse the answer across requests for the TTL.

    Only non-``None`` answers are kept. Use it solely for answers whose
    staleness is bounded by ``AUTH_CACHE_TTL_SECONDS`` by design (see module
    docstring). Callers get a copy.
    """
    memo = _memo.get()
    if memo is None:
        return await loader()
    if key in memo:
        return _copy(memo[key])
    value = _shared_get(key)
    if value is None:
        generation = _generation
        value = await loader()
        if value is not None:
            _shared_put(key, value, generation)
    if value is not None:
        memo[key] = value
    return _copy(value)


def clear_shared() -> None:
    """Drop every shared entry (tests, and an emergency lever)."""
    global _generation
    _generation += 1
    _shared.clear()


def forget_matching(predicate: Callable[[Hashable], bool]) -> None:
    """Drop every cached entry (request and shared) whose key matches *predicate*."""
    global _generation
    _generation += 1
    for k in [k for k in _shared if predicate(k)]:
        _shared.pop(k, None)
    memo = _memo.get()
    if memo is not None:
        for k in [k for k in memo if predicate(k)]:
            memo.pop(k, None)


def peek(key: Hashable) -> Any:
    """Return the cached value for *key* (request memo, then shared), or ``None``."""
    memo = _memo.get()
    if memo is None:
        return None
    if key in memo:
        return memo[key]
    return _shared_get(key)


def remember(key: Hashable, value: Any, *, shared: bool = False) -> None:
    """Record a value another lookup already paid for. No-op outside a request.

    ``shared=True`` also offers it to the cross-request layer (same rules as
    :func:`shared_memoized`).
    """
    memo = _memo.get()
    if memo is not None:
        memo[key] = value
        if shared and value is not None:
            _shared_put(key, value, _generation)


def forget(key: Hashable) -> None:
    """Drop a cached value after the row behind it was written or deleted."""
    global _generation
    _generation += 1
    _shared.pop(key, None)
    memo = _memo.get()
    if memo is not None:
        memo.pop(key, None)


class RequestMemoMiddleware:
    """Pure-ASGI middleware: one fresh memo per HTTP request."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        token = _memo.set({})
        try:
            await self.app(scope, receive, send)
        finally:
            _memo.reset(token)


def register_request_memo(application: Any) -> None:
    """Install the middleware (outermost, so every route sees the memo)."""
    application.add_middleware(RequestMemoMiddleware)
