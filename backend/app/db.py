import os
import json
import asyncpg

_pool: asyncpg.Pool | None = None

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost:5432/nubi")


async def _init_connection(conn):
    await conn.set_type_codec(
        'jsonb', encoder=json.dumps, decoder=json.loads, schema='pg_catalog'
    )
    await conn.set_type_codec(
        'json', encoder=json.dumps, decoder=json.loads, schema='pg_catalog'
    )


async def init_pool():
    global _pool
    print(f"Connecting to database...")
    try:
        _pool = await asyncpg.create_pool(
            DATABASE_URL, min_size=0, max_size=10,
            init=_init_connection, timeout=10,
        )
        print("Database pool ready")
    except Exception as e:
        print(f"FATAL: Database connection failed: {e}")
        raise


async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool not initialised — call init_pool() first")
    return _pool
