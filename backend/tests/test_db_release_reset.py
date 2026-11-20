"""The pool's release reset (app/db.py).

asyncpg's default release reset is a full extra round trip after every lookup.
Nubi keeps no session state on its pool connections, so the pool is created
with a reset that sends nothing (asyncpg still rolls back an open transaction
itself before calling it).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_pool_is_created_with_the_round_trip_free_reset():
    from app import db

    created = AsyncMock(return_value=MagicMock())
    with patch.object(db, "_pool", None), patch.object(db, "_pool_loop", None), \
            patch.object(db.asyncpg, "create_pool", created):
        await db.init_db()
    assert created.await_args.kwargs["reset"] is db._release_reset

    conn = MagicMock()
    conn.execute = AsyncMock()
    await db._release_reset(conn)
    conn.execute.assert_not_awaited()
