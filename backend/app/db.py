"""asyncpg connection-pool management and query helpers.

Public API
----------
init_db()        — create the pool (call from lifespan startup).
close_db()       — close the pool (call from lifespan shutdown).
get_pool()       — return the active pool (raises RuntimeError if not initialised).
get_connection() — async context manager yielding a single connection for
                   multi-statement transactions (use ``async with conn.transaction()``).
fetch()          — run a SELECT, return list[Record].
fetchrow()       — run a SELECT, return one Record or None.
execute()        — run an INSERT / UPDATE / DELETE, return status string.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

import asyncpg
from asyncpg import Connection, Pool, Record

from app.config import get_settings

_pool: Pool | None = None

# The event loop ``init_db()`` created the pool on (always the main uvicorn
# loop, via the FastAPI lifespan). asyncpg Pools/Connections are bound to the
# loop they were created on: awaiting one from a DIFFERENT loop (e.g. a
# throwaway loop spun up by ``asyncio.run()`` on a worker thread) corrupts the
# connection it happens to acquire for every subsequent caller, surfacing much
# later and confusingly as ``another operation is in progress`` /
# ``ConnectionDoesNotExistError``. Sync→async bridges that must call pool-
# touching coroutines from a worker thread (see ``app.ai.tools._run_sync``)
# use this to marshal the coroutine onto the pool's real loop instead.
_pool_loop: asyncio.AbstractEventLoop | None = None


async def _skip_session_reset(_conn: Connection) -> None:
    """Pool-release hook that sends nothing to the server.

    asyncpg's default release runs ``pg_advisory_unlock_all; CLOSE ALL;
    UNLISTEN *; RESET ALL`` — a full extra round trip after EVERY statement,
    which doubles the database time of each request when Postgres is in
    another region. Nothing here keeps session state across a release: there
    are no session ``SET``s, cursors or ``LISTEN``s, and the one advisory lock
    (the thumbnail backfill) is released explicitly on the connection that
    took it. asyncpg still rolls back a transaction left open before calling
    this hook, so a leaked transaction cannot reach the next caller.

    Code that DOES need session state must clean it up itself before the
    connection goes back to the pool.
    """


async def init_db() -> None:
    """Create the asyncpg connection pool.

    Uses the DATABASE_URL from settings.  Neon requires SSL; ensure the URL
    includes ``?sslmode=require`` (or equivalent).  This function is
    idempotent — calling it a second time is a no-op.
    """
    global _pool, _pool_loop
    if _pool is not None:
        return

    settings = get_settings()
    _pool = await asyncpg.create_pool(
        dsn=settings.DATABASE_URL,
        min_size=2,
        max_size=10,
        command_timeout=30,
        reset=_skip_session_reset,
        timeout=10.0,   # abort TCP handshake after 10 s; surfaces clear error instead of hanging
                        # (asyncpg's connect() param is `timeout`, not `connect_timeout`)
    )
    _pool_loop = asyncio.get_running_loop()


async def close_db() -> None:
    """Gracefully close the connection pool.

    Safe to call even if the pool was never initialised.
    """
    global _pool, _pool_loop
    if _pool is not None:
        await _pool.close()
        _pool = None
        _pool_loop = None


def get_pool_loop() -> "asyncio.AbstractEventLoop | None":
    """Return the event loop the active pool was created on, or ``None``.

    ``None`` means either the pool isn't initialised yet, or (test doubles)
    it was created without going through ``init_db()``.
    """
    return _pool_loop


def get_pool() -> Pool:
    """Return the active asyncpg Pool.

    Raises
    ------
    RuntimeError
        If ``init_db()`` has not been called yet.
    """
    if _pool is None:
        raise RuntimeError("Database pool is not initialised. Call init_db() first.")
    return _pool


@asynccontextmanager
async def get_connection() -> AsyncIterator[Connection]:
    """Yield a dedicated asyncpg connection for use in multi-statement transactions.

    Callers must wrap work in ``async with conn.transaction():`` to get
    atomicity.  This is the correct primitive for operations that require
    reading and writing as a single atomic unit (e.g. token rotation).

    Example
    -------
    ::

        async with get_connection() as conn:
            async with conn.transaction():
                row = await conn.fetchrow("SELECT ... FOR UPDATE", ...)
                await conn.execute("UPDATE ...", ...)

    """
    async with get_pool().acquire(timeout=30.0) as conn:
        yield conn  # type: ignore[misc]


async def fetch(query: str, *args: object) -> list[Record]:
    """Execute *query* with positional *args* ($1, $2, …) and return all rows.

    Parameters
    ----------
    query:
        Parameterised SQL string using asyncpg ``$N`` placeholders.
    *args:
        Positional values bound to the ``$N`` placeholders.

    Returns
    -------
    list[asyncpg.Record]
        Possibly empty list of result rows.
    """
    async with get_pool().acquire(timeout=30.0) as conn:
        return await conn.fetch(query, *args)


async def fetchrow(query: str, *args: object) -> Record | None:
    """Execute *query* and return the first row, or *None* if no rows match.

    Parameters
    ----------
    query:
        Parameterised SQL string using asyncpg ``$N`` placeholders.
    *args:
        Positional values bound to the ``$N`` placeholders.

    Returns
    -------
    asyncpg.Record | None
    """
    async with get_pool().acquire(timeout=30.0) as conn:
        return await conn.fetchrow(query, *args)


async def execute(query: str, *args: object) -> str:
    """Execute a non-SELECT statement and return the command status string.

    Parameters
    ----------
    query:
        Parameterised SQL string using asyncpg ``$N`` placeholders.
    *args:
        Positional values bound to the ``$N`` placeholders.

    Returns
    -------
    str
        asyncpg command-status tag, e.g. ``"INSERT 0 1"``.
    """
    async with get_pool().acquire(timeout=30.0) as conn:
        return await conn.execute(query, *args)
