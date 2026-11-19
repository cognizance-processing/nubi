"""Remember a few read-only lookups for the rest of ONE request.

Why: a single board tile's ``POST /api/v1/query`` used to ask the database the
same questions several times while it resolved the caller's org and the
datastore — "is this user a member of this org?" four times, and "fetch this
datastore row" twice. The answers cannot change within one request, and on a
hosted deploy whose database sits a region away every repeat is a full network
round trip (measured on the hosted service: ~0.35 s per lookup including the
pool's release reset). A board opens with a burst of these requests, so the
repeats added seconds to every tile.

How: :class:`RequestMemoMiddleware` gives every HTTP request its own empty dict
in a context variable, and :func:`memoized` looks a key up in it before running
the real lookup. Nothing is ever shared between requests. Code that runs
outside an HTTP request — the worker, startup jobs, websocket handlers, tests
that call helpers directly — sees no memo and always queries, exactly as before.

Only use this for lookups whose answer cannot change during the request and
that the request itself does not write. Rows are handed out as deep copies, so a
caller that mutates its row can never change what the next caller sees.
"""
from __future__ import annotations

import contextvars
import copy
from collections.abc import Awaitable, Callable, Hashable
from typing import Any, TypeVar

T = TypeVar("T")

_memo: contextvars.ContextVar[dict[Hashable, Any] | None] = contextvars.ContextVar(
    "nubi_request_memo", default=None
)


async def memoized(
    key: Hashable,
    load: Callable[[], Awaitable[T]],
    keep: Callable[[T], bool] | None = None,
) -> T:
    """Return the request's remembered value for *key*, loading it on first use.

    Outside an HTTP request this simply awaits ``load()``. A failed load is not
    remembered, so the next caller tries again. With *keep*, only values for
    which ``keep(value)`` is true are remembered (e.g. remember "yes, a member"
    but re-ask after a "no", in case the same request then adds the member).
    """
    memo = _memo.get()
    if memo is None:
        return await load()
    if key in memo:
        return copy.deepcopy(memo[key])
    value = await load()
    if keep is None or keep(value):
        memo[key] = value
    return copy.deepcopy(value)


class RequestMemoMiddleware:
    """Pure ASGI middleware: a fresh, empty memo for every HTTP request."""

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
