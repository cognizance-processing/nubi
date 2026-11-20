"""Request-scoped memo for read-only lookups.

One request often asks the same question several times from layers that do
not know about each other: "is this user in this org?", "what is their
role?", "load this datastore row". Each ask is a database round trip, and with
Postgres in another region every round trip is user-visible latency.

The memo lives for exactly one request (``RequestMemoMiddleware`` opens it),
so nothing here can serve a stale answer to a *later* request. Outside a
request — background jobs, scripts, unit tests that call helpers directly —
there is no memo and every call goes to the loader, unchanged.

Only positive answers are kept unless ``keep_none=True``: a request that adds
a membership and then checks it must see the new row.
"""

from __future__ import annotations

import contextvars
from typing import Any, Awaitable, Callable, Hashable

_memo: contextvars.ContextVar[dict[Hashable, Any] | None] = contextvars.ContextVar(
    "request_memo", default=None
)


async def memoized(
    key: Hashable,
    loader: Callable[[], Awaitable[Any]],
    *,
    keep_none: bool = False,
) -> Any:
    """Return the request's cached value for *key*, loading it on first use."""
    memo = _memo.get()
    if memo is None:
        return await loader()
    if key in memo:
        return memo[key]
    value = await loader()
    if value is not None or keep_none:
        memo[key] = value
    return value


def peek(key: Hashable) -> Any:
    """Return the cached value for *key*, or ``None`` when absent / no memo."""
    memo = _memo.get()
    return None if memo is None else memo.get(key)


def remember(key: Hashable, value: Any) -> None:
    """Record a value another lookup already paid for. No-op outside a request."""
    memo = _memo.get()
    if memo is not None:
        memo[key] = value


def forget(key: Hashable) -> None:
    """Drop a cached value after the row behind it was written or deleted."""
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
