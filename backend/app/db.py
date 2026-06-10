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

from contextlib import asynccontextmanager
from typing import AsyncIterator

import asyncpg
from asyncpg import Connection, Pool, Record

from app.config import get_settings

_pool: Pool | None = None


async def init_db() -> None:
    """Create the asyncpg connection pool.

    Uses the DATABASE_URL from settings.  Neon requires SSL; ensure the URL
    includes ``?sslmode=require`` (or equivalent).  This function is
    idempotent — calling it a second time is a no-op.
    """
    global _pool
    if _pool is not None:
        return

    settings = get_settings()
    _pool = await asyncpg.create_pool(
        dsn=settings.DATABASE_URL,
        min_size=2,
        max_size=10,
        command_timeout=30,
        timeout=10.0,   # abort TCP handshake after 10 s; surfaces clear error instead of hanging
                        # (asyncpg's connect() param is `timeout`, not `connect_timeout`)
    )


async def close_db() -> None:
    """Gracefully close the connection pool.

    Safe to call even if the pool was never initialised.
    """
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


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
